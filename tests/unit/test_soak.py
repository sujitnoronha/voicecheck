"""Tests for soak test aggregation and CLI duration parsing."""

from __future__ import annotations

import io

import pytest

from voicecheck.core.scenario import ScenarioReport
from voicecheck.core.soak import SoakResult, SoakSummary, build_soak_summary, print_soak_summary
from voicecheck.core.types import TransportMetrics, TurnResult


# ── helpers ──────────────────────────────────────────────────────


def _make_metrics(first_byte_ms: float = 200, total_ms: float = 500) -> TransportMetrics:
    """Create TransportMetrics with the specified latency values."""
    # send_end_ts=1.0, then first_byte_ts and last_byte_ts calculated from ms values
    return TransportMetrics(
        send_start_ts=0.5,
        send_end_ts=1.0,
        first_byte_ts=1.0 + first_byte_ms / 1000,
        last_byte_ts=1.0 + total_ms / 1000,
    )


def _make_turn(index: int = 0, passed: bool = True, first_byte_ms: float = 200, total_ms: float = 500) -> TurnResult:
    return TurnResult(
        turn_index=index,
        user_text=f"turn {index} text",
        agent_text=f"agent response {index}",
        metrics=_make_metrics(first_byte_ms, total_ms),
        eval_results=[],
    )


def _make_report(name: str = "test_scenario", num_turns: int = 2, all_pass: bool = True) -> ScenarioReport:
    turns = [_make_turn(i, passed=all_pass, first_byte_ms=200 + i * 50, total_ms=500 + i * 100) for i in range(num_turns)]
    return ScenarioReport(scenario_name=name, turns=turns)


# ── build_soak_summary ───────────────────────────────────────────


class TestBuildSoakSummary:
    def test_all_passing(self):
        results = [
            SoakResult(iteration=1, scenario_name="greeting", report=_make_report("greeting")),
            SoakResult(iteration=1, scenario_name="farewell", report=_make_report("farewell")),
            SoakResult(iteration=2, scenario_name="greeting", report=_make_report("greeting")),
            SoakResult(iteration=2, scenario_name="farewell", report=_make_report("farewell")),
        ]
        summary = build_soak_summary(results, duration_seconds=120.0, total_iterations=2)

        assert summary.total_iterations == 2
        assert summary.total_runs == 4
        assert summary.passed_runs == 4
        assert summary.failed_runs == 0
        assert summary.error_runs == 0
        assert summary.pass_rate == 100.0
        assert summary.total_turns == 8  # 4 reports * 2 turns each
        assert summary.passed_turns == 8
        assert len(summary.all_first_byte_ms) == 8
        assert len(summary.all_total_ms) == 8
        assert summary.avg_first_byte_ms > 0
        assert summary.p95_first_byte_ms > 0

    def test_mixed_results(self):
        passing = _make_report("greeting", num_turns=2, all_pass=True)
        failing = _make_report("greeting", num_turns=2, all_pass=True)
        # Manually mark one turn as failed
        failing.turns[0].eval_results.append(
            type("EvalResult", (), {"passed": False, "score": 0.0, "reason": "fail"})()
        )

        results = [
            SoakResult(iteration=1, scenario_name="greeting", report=passing),
            SoakResult(iteration=2, scenario_name="greeting", report=failing),
        ]
        summary = build_soak_summary(results, duration_seconds=60.0, total_iterations=2)

        assert summary.total_runs == 2
        assert summary.passed_runs == 1
        assert summary.failed_runs == 1
        assert summary.pass_rate == 50.0

    def test_error_runs(self):
        results = [
            SoakResult(iteration=1, scenario_name="broken", error="connection refused"),
            SoakResult(iteration=2, scenario_name="broken", error="timeout"),
        ]
        summary = build_soak_summary(results, duration_seconds=30.0, total_iterations=2)

        assert summary.total_runs == 2
        assert summary.error_runs == 2
        assert summary.passed_runs == 0
        assert summary.failed_runs == 0
        assert summary.pass_rate == 0.0
        assert summary.total_turns == 0
        assert len(summary.all_first_byte_ms) == 0

    def test_empty_input(self):
        summary = build_soak_summary([], duration_seconds=0.0, total_iterations=0)

        assert summary.total_runs == 0
        assert summary.pass_rate == 0.0
        assert summary.avg_first_byte_ms == 0.0
        assert summary.p95_first_byte_ms == 0.0
        assert summary.avg_total_ms == 0.0

    def test_per_scenario_breakdown(self):
        results = [
            SoakResult(iteration=1, scenario_name="alpha", report=_make_report("alpha")),
            SoakResult(iteration=1, scenario_name="beta", report=_make_report("beta")),
            SoakResult(iteration=2, scenario_name="alpha", report=_make_report("alpha")),
            SoakResult(iteration=2, scenario_name="beta", error="boom"),
        ]
        summary = build_soak_summary(results, duration_seconds=90.0, total_iterations=2)

        assert "alpha" in summary.per_scenario
        assert "beta" in summary.per_scenario
        assert summary.per_scenario["alpha"]["runs"] == 2
        assert summary.per_scenario["alpha"]["passed"] == 2
        assert summary.per_scenario["beta"]["runs"] == 2
        assert summary.per_scenario["beta"]["passed"] == 1
        assert summary.per_scenario["beta"]["errors"] == 1

    def test_latency_stats(self):
        """P95 should be >= average for realistic data."""
        results = [
            SoakResult(
                iteration=i,
                scenario_name="latency_test",
                report=_make_report("latency_test", num_turns=3),
            )
            for i in range(10)
        ]
        summary = build_soak_summary(results, duration_seconds=300.0, total_iterations=10)

        assert summary.avg_first_byte_ms > 0
        assert summary.p95_first_byte_ms >= summary.avg_first_byte_ms
        assert summary.avg_total_ms > 0


# ── print_soak_summary ──────────────────────────────────────────


class TestPrintSoakSummary:
    def test_does_not_crash(self):
        results = [
            SoakResult(iteration=1, scenario_name="greeting", report=_make_report("greeting")),
        ]
        summary = build_soak_summary(results, duration_seconds=60.0, total_iterations=1)
        buf = io.StringIO()
        print_soak_summary(summary, file=buf)
        output = buf.getvalue()
        assert "SOAK TEST SUMMARY" in output
        assert "Pass rate" in output

    def test_empty_summary(self):
        summary = build_soak_summary([], duration_seconds=0.0, total_iterations=0)
        buf = io.StringIO()
        print_soak_summary(summary, file=buf)
        output = buf.getvalue()
        assert "SOAK TEST SUMMARY" in output
        assert "0.0%" in output

    def test_per_scenario_shown(self):
        results = [
            SoakResult(iteration=1, scenario_name="alpha", report=_make_report("alpha")),
            SoakResult(iteration=1, scenario_name="beta", report=_make_report("beta")),
        ]
        summary = build_soak_summary(results, duration_seconds=60.0, total_iterations=1)
        buf = io.StringIO()
        print_soak_summary(summary, file=buf)
        output = buf.getvalue()
        assert "alpha" in output
        assert "beta" in output


# ── _parse_duration (via CLI) ────────────────────────────────────


class TestParseDuration:
    def test_minutes(self):
        from voicecheck.cli import _parse_duration
        assert _parse_duration("20m") == 1200

    def test_hours(self):
        from voicecheck.cli import _parse_duration
        assert _parse_duration("1h") == 3600

    def test_seconds(self):
        from voicecheck.cli import _parse_duration
        assert _parse_duration("90s") == 90

    def test_bare_number_is_seconds(self):
        from voicecheck.cli import _parse_duration
        assert _parse_duration("120") == 120

    def test_invalid_raises(self):
        from voicecheck.cli import _parse_duration
        with pytest.raises(Exception):
            _parse_duration("abc")

    def test_whitespace_handling(self):
        from voicecheck.cli import _parse_duration
        assert _parse_duration("  30m  ") == 1800
