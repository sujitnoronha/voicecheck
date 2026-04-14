"""Tests for concurrent session load testing."""

import pytest

from voicecheck.core.load import (
    LoadTestResult,
    LoadTestSummary,
    _percentile,
    build_load_summary,
)
from voicecheck.core.scenario import ScenarioReport
from voicecheck.core.types import TransportMetrics, TurnResult, EvalResult


def _make_metrics(first_byte_ms: float = 500, total_ms: float = 2000) -> TransportMetrics:
    m = TransportMetrics()
    m.send_end_ts = 1000.0
    m.first_byte_ts = 1000.0 + first_byte_ms / 1000
    m.last_byte_ts = 1000.0 + total_ms / 1000
    return m


def _make_report(passed: bool = True, num_turns: int = 2,
                 first_byte_ms: float = 500) -> ScenarioReport:
    turns = []
    for i in range(num_turns):
        turns.append(TurnResult(
            turn_index=i,
            user_text=f"Turn {i}",
            agent_text=f"Response {i}",
            metrics=_make_metrics(first_byte_ms=first_byte_ms),
            eval_results=[EvalResult(
                evaluator_type="test", passed=passed, score=1.0 if passed else 0.0,
            )],
        ))
    return ScenarioReport(scenario_name="test", turns=turns)


class TestLoadTestResult:
    def test_duration(self):
        r = LoadTestResult(session_id=0, start_ts=100.0, end_ts=105.0)
        assert r.duration_s == 5.0

    def test_duration_unset(self):
        r = LoadTestResult(session_id=0)
        assert r.duration_s == 0.0

    def test_passed_with_report(self):
        r = LoadTestResult(session_id=0, report=_make_report(passed=True))
        assert r.passed

    def test_failed_with_report(self):
        r = LoadTestResult(session_id=0, report=_make_report(passed=False))
        assert not r.passed

    def test_error_not_passed(self):
        r = LoadTestResult(session_id=0, error="connection failed")
        assert not r.passed


class TestPercentile:
    def test_empty(self):
        assert _percentile([], 50) == 0.0

    def test_single_value(self):
        assert _percentile([100.0], 50) == 100.0

    def test_p50(self):
        data = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        p50 = _percentile([float(x) for x in data], 50)
        assert p50 == 60.0  # 50th percentile of 10 items

    def test_p95(self):
        data = [float(i) for i in range(1, 101)]
        p95 = _percentile(data, 95)
        assert p95 == 96.0

    def test_p99(self):
        data = [float(i) for i in range(1, 101)]
        p99 = _percentile(data, 99)
        assert p99 == 100.0


class TestBuildLoadSummary:
    def test_all_passing(self):
        results = [
            LoadTestResult(session_id=i, report=_make_report(passed=True),
                           start_ts=0, end_ts=5)
            for i in range(5)
        ]
        summary = build_load_summary(results, duration=10.0)
        assert summary.concurrent_sessions == 5
        assert summary.sessions_completed == 5
        assert summary.sessions_failed == 0
        assert summary.sessions_errored == 0
        assert summary.pass_rate == 100.0

    def test_mixed_results(self):
        results = [
            LoadTestResult(session_id=0, report=_make_report(passed=True)),
            LoadTestResult(session_id=1, report=_make_report(passed=False)),
            LoadTestResult(session_id=2, error="timeout"),
        ]
        summary = build_load_summary(results, duration=10.0)
        assert summary.sessions_completed == 1
        assert summary.sessions_failed == 1
        assert summary.sessions_errored == 1

    def test_latency_collection(self):
        results = [
            LoadTestResult(session_id=0,
                           report=_make_report(passed=True, first_byte_ms=100)),
            LoadTestResult(session_id=1,
                           report=_make_report(passed=True, first_byte_ms=300)),
        ]
        summary = build_load_summary(results, duration=5.0)
        assert len(summary.all_first_byte_ms) == 4  # 2 reports * 2 turns each
        assert summary.avg_first_byte_ms == pytest.approx(200.0)

    def test_empty_results(self):
        summary = build_load_summary([], duration=0.0)
        assert summary.concurrent_sessions == 0
        assert summary.pass_rate == 0.0
        assert summary.avg_first_byte_ms == 0.0

    def test_throughput(self):
        results = [
            LoadTestResult(session_id=i, report=_make_report())
            for i in range(10)
        ]
        summary = build_load_summary(results, duration=60.0)
        assert summary.throughput_per_min == pytest.approx(10.0)

    def test_turn_counts(self):
        results = [
            LoadTestResult(session_id=0,
                           report=_make_report(passed=True, num_turns=3)),
            LoadTestResult(session_id=1,
                           report=_make_report(passed=False, num_turns=2)),
        ]
        summary = build_load_summary(results, duration=5.0)
        assert summary.total_turns == 5
        assert summary.passed_turns == 3  # 3 passed turns from report 1

    def test_error_sessions_counted(self):
        results = [
            LoadTestResult(session_id=0, error="fail 1"),
            LoadTestResult(session_id=1, error="fail 2"),
        ]
        summary = build_load_summary(results, duration=1.0)
        assert summary.sessions_errored == 2
        assert summary.total_turns == 0

    def test_percentile_properties(self):
        results = [
            LoadTestResult(session_id=i,
                           report=_make_report(passed=True, first_byte_ms=100 * (i + 1)))
            for i in range(10)
        ]
        summary = build_load_summary(results, duration=10.0)
        assert summary.p50_first_byte_ms > 0
        assert summary.p95_first_byte_ms >= summary.p50_first_byte_ms
        assert summary.p99_first_byte_ms >= summary.p95_first_byte_ms


class TestLoadTestSummaryProperties:
    def test_pass_rate_zero_sessions(self):
        s = LoadTestSummary(concurrent_sessions=0, sessions_completed=0,
                            sessions_failed=0, sessions_errored=0,
                            total_turns=0, passed_turns=0)
        assert s.pass_rate == 0.0

    def test_throughput_zero_duration(self):
        s = LoadTestSummary(concurrent_sessions=5, sessions_completed=5,
                            sessions_failed=0, sessions_errored=0,
                            total_turns=10, passed_turns=10,
                            duration_seconds=0.0)
        assert s.throughput_per_min == 0.0
