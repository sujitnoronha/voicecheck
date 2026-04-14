"""Concurrent session load testing for voice agents.

Runs N simultaneous sessions of the same scenario to test agent behavior
under concurrent load. Collects per-session results and aggregate metrics.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from voicecheck.core.scenario import ScenarioRunner, ScenarioReport, load_scenario

logger = logging.getLogger("voicecheck.core.load")


@dataclass
class LoadTestResult:
    """Result from a single session in a concurrent load test."""

    session_id: int
    report: ScenarioReport | None = None
    error: str | None = None
    start_ts: float = 0.0
    end_ts: float = 0.0

    @property
    def duration_s(self) -> float:
        if not self.start_ts or not self.end_ts:
            return 0.0
        return self.end_ts - self.start_ts

    @property
    def passed(self) -> bool:
        return self.report is not None and self.report.passed


@dataclass
class LoadTestSummary:
    """Aggregate results across all concurrent sessions."""

    concurrent_sessions: int
    sessions_completed: int
    sessions_failed: int
    sessions_errored: int
    total_turns: int
    passed_turns: int
    all_first_byte_ms: list[float] = field(default_factory=list)
    all_total_ms: list[float] = field(default_factory=list)
    duration_seconds: float = 0.0
    per_session: list[LoadTestResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.concurrent_sessions == 0:
            return 0.0
        return (self.sessions_completed / self.concurrent_sessions) * 100

    @property
    def avg_first_byte_ms(self) -> float:
        return sum(self.all_first_byte_ms) / len(self.all_first_byte_ms) if self.all_first_byte_ms else 0.0

    @property
    def p50_first_byte_ms(self) -> float:
        return _percentile(self.all_first_byte_ms, 50)

    @property
    def p95_first_byte_ms(self) -> float:
        return _percentile(self.all_first_byte_ms, 95)

    @property
    def p99_first_byte_ms(self) -> float:
        return _percentile(self.all_first_byte_ms, 99)

    @property
    def throughput_per_min(self) -> float:
        if self.duration_seconds == 0:
            return 0.0
        return (self.concurrent_sessions / self.duration_seconds) * 60


def _percentile(data: list[float], pct: int) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = int(len(s) * pct / 100)
    return s[min(idx, len(s) - 1)]


async def run_concurrent(
    scenario_path: Path,
    concurrent: int,
    skip_llm_judge: bool = False,
) -> LoadTestSummary:
    """Run N concurrent sessions of the same scenario.

    Each session gets its own transport connection and runs independently.

    Args:
        scenario_path: Path to the YAML scenario file.
        concurrent: Number of simultaneous sessions.
        skip_llm_judge: Skip LLM-based evaluators.

    Returns:
        LoadTestSummary with aggregate results.
    """
    start = time.monotonic()

    async def _run_session(session_id: int) -> LoadTestResult:
        s = time.monotonic()
        try:
            scenario = load_scenario(scenario_path)
            runner = ScenarioRunner(scenario, skip_llm_judge=skip_llm_judge)
            report = await runner.run()
            return LoadTestResult(
                session_id=session_id, report=report,
                start_ts=s, end_ts=time.monotonic(),
            )
        except Exception as e:
            logger.error("Session %d error: %s", session_id, e)
            return LoadTestResult(
                session_id=session_id, error=str(e),
                start_ts=s, end_ts=time.monotonic(),
            )

    results = await asyncio.gather(*[_run_session(i) for i in range(concurrent)])
    duration = time.monotonic() - start

    return build_load_summary(list(results), duration)


def build_load_summary(results: list[LoadTestResult], duration: float) -> LoadTestSummary:
    """Aggregate per-session results into a summary."""
    completed = 0
    failed = 0
    errored = 0
    total_turns = 0
    passed_turns = 0
    all_fb: list[float] = []
    all_total: list[float] = []

    for r in results:
        if r.error:
            errored += 1
        elif r.report and r.report.passed:
            completed += 1
        else:
            failed += 1

        if r.report:
            total_turns += r.report.total_turns
            passed_turns += r.report.passed_turns
            for turn in r.report.turns:
                fb = turn.metrics.first_byte_ms
                t = turn.metrics.total_ms
                if fb > 0:
                    all_fb.append(fb)
                if t > 0:
                    all_total.append(t)

    return LoadTestSummary(
        concurrent_sessions=len(results),
        sessions_completed=completed,
        sessions_failed=failed,
        sessions_errored=errored,
        total_turns=total_turns,
        passed_turns=passed_turns,
        all_first_byte_ms=all_fb,
        all_total_ms=all_total,
        duration_seconds=duration,
        per_session=results,
    )


def print_load_summary(summary: LoadTestSummary) -> None:
    """Print formatted load test results to console."""
    import click

    click.echo(f"\n{'═' * 60}")
    click.echo(f"  LOAD TEST RESULTS — {summary.concurrent_sessions} concurrent sessions")
    click.echo(f"{'═' * 60}")

    # Session results
    passed_style = click.style(str(summary.sessions_completed), fg="green")
    failed_style = click.style(str(summary.sessions_failed), fg="red") if summary.sessions_failed else "0"
    error_style = click.style(str(summary.sessions_errored), fg="yellow") if summary.sessions_errored else "0"

    click.echo(f"  Passed: {passed_style}  Failed: {failed_style}  Errors: {error_style}")
    click.echo(f"  Pass rate: {summary.pass_rate:.0f}%")
    click.echo(f"  Total turns: {summary.total_turns} ({summary.passed_turns} passed)")
    click.echo(f"  Duration: {summary.duration_seconds:.1f}s")
    click.echo(f"  Throughput: {summary.throughput_per_min:.1f} sessions/min")

    # Latency
    if summary.all_first_byte_ms:
        click.echo(f"\n  First Byte Latency:")
        click.echo(f"    avg={summary.avg_first_byte_ms:.0f}ms  "
                    f"p50={summary.p50_first_byte_ms:.0f}ms  "
                    f"p95={summary.p95_first_byte_ms:.0f}ms  "
                    f"p99={summary.p99_first_byte_ms:.0f}ms")

    # Per-session table
    click.echo(f"\n  {'Session':<10} {'Status':<10} {'Turns':<12} {'Duration':<10}")
    click.echo(f"  {'-' * 42}")
    for r in summary.per_session:
        if r.error:
            status = click.style("ERROR", fg="yellow")
            turns = "—"
        elif r.passed:
            status = click.style("PASS", fg="green")
            turns = f"{r.report.passed_turns}/{r.report.total_turns}"
        else:
            status = click.style("FAIL", fg="red")
            turns = f"{r.report.passed_turns}/{r.report.total_turns}"
        click.echo(f"  {r.session_id:<10} {status:<19} {turns:<12} {r.duration_s:.1f}s")

    click.echo(f"{'═' * 60}\n")
