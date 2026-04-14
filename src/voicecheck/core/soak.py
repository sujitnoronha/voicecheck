"""Soak test aggregation — collect and summarize repeated scenario runs."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from voicecheck.core.scenario import ScenarioReport


@dataclass
class SoakResult:
    """A single iteration result, wrapping a ScenarioReport with metadata."""

    iteration: int
    scenario_name: str
    report: ScenarioReport | None = None
    error: str | None = None


@dataclass
class SoakSummary:
    """Aggregate statistics across all soak iterations."""

    total_iterations: int = 0
    total_runs: int = 0
    passed_runs: int = 0
    failed_runs: int = 0
    error_runs: int = 0
    total_turns: int = 0
    passed_turns: int = 0
    all_first_byte_ms: list[float] = field(default_factory=list)
    all_total_ms: list[float] = field(default_factory=list)
    duration_seconds: float = 0.0
    per_scenario: dict[str, dict] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.passed_runs / self.total_runs * 100

    @property
    def avg_first_byte_ms(self) -> float:
        if not self.all_first_byte_ms:
            return 0.0
        return sum(self.all_first_byte_ms) / len(self.all_first_byte_ms)

    @property
    def p95_first_byte_ms(self) -> float:
        if not self.all_first_byte_ms:
            return 0.0
        sorted_vals = sorted(self.all_first_byte_ms)
        idx = int(len(sorted_vals) * 0.95)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    @property
    def avg_total_ms(self) -> float:
        if not self.all_total_ms:
            return 0.0
        return sum(self.all_total_ms) / len(self.all_total_ms)


def build_soak_summary(
    results: list[SoakResult],
    duration_seconds: float,
    total_iterations: int,
) -> SoakSummary:
    """Build aggregate statistics from a list of soak results."""
    summary = SoakSummary(
        total_iterations=total_iterations,
        duration_seconds=duration_seconds,
    )

    for r in results:
        summary.total_runs += 1
        scenario = r.scenario_name

        if scenario not in summary.per_scenario:
            summary.per_scenario[scenario] = {
                "runs": 0,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "first_byte_ms": [],
            }
        ps = summary.per_scenario[scenario]
        ps["runs"] += 1

        if r.error or r.report is None:
            summary.error_runs += 1
            ps["errors"] += 1
            continue

        report = r.report
        if report.passed:
            summary.passed_runs += 1
            ps["passed"] += 1
        else:
            summary.failed_runs += 1
            ps["failed"] += 1

        summary.total_turns += report.total_turns
        summary.passed_turns += report.passed_turns

        for turn in report.turns:
            fb = turn.metrics.first_byte_ms
            tot = turn.metrics.total_ms
            if fb > 0:
                summary.all_first_byte_ms.append(fb)
                ps["first_byte_ms"].append(fb)
            if tot > 0:
                summary.all_total_ms.append(tot)

    return summary


def print_soak_summary(summary: SoakSummary, file: TextIO | None = None) -> None:
    """Print a formatted aggregate summary to the console."""
    out = file or sys.stdout

    minutes = summary.duration_seconds / 60

    out.write(f"\n{'='*60}\n")
    out.write(f"  SOAK TEST SUMMARY\n")
    out.write(f"{'='*60}\n\n")

    out.write(f"  Duration:      {minutes:.1f} minutes\n")
    out.write(f"  Iterations:    {summary.total_iterations}\n")
    out.write(f"  Total runs:    {summary.total_runs}\n")
    out.write(f"  Passed:        {summary.passed_runs}\n")
    out.write(f"  Failed:        {summary.failed_runs}\n")
    out.write(f"  Errors:        {summary.error_runs}\n")
    out.write(f"  Pass rate:     {summary.pass_rate:.1f}%\n")
    out.write(f"\n")
    out.write(f"  Turns:         {summary.passed_turns}/{summary.total_turns} passed\n")
    out.write(f"  Avg latency:   {summary.avg_first_byte_ms:.0f}ms (first byte)\n")
    out.write(f"  P95 latency:   {summary.p95_first_byte_ms:.0f}ms (first byte)\n")
    out.write(f"  Avg total:     {summary.avg_total_ms:.0f}ms\n")

    if summary.per_scenario:
        out.write(f"\n  Per-scenario breakdown:\n")
        out.write(f"  {'-'*50}\n")
        for name, ps in summary.per_scenario.items():
            rate = (ps["passed"] / ps["runs"] * 100) if ps["runs"] else 0
            avg_fb = (
                sum(ps["first_byte_ms"]) / len(ps["first_byte_ms"])
                if ps["first_byte_ms"]
                else 0
            )
            out.write(
                f"  {name:<30} {ps['passed']}/{ps['runs']} passed "
                f"({rate:.0f}%) avg={avg_fb:.0f}ms\n"
            )

    out.write(f"\n{'='*60}\n")
