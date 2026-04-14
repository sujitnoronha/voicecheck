"""Tests for result storage and dashboard generation."""

import tempfile
from pathlib import Path

import pytest

from voicecheck.core.scenario import ScenarioReport
from voicecheck.core.types import EvalResult, TransportMetrics, TurnResult
from voicecheck.storage.store import ResultStore


def _make_report(name: str = "test-scenario", passed: bool = True) -> ScenarioReport:
    """Create a sample ScenarioReport for testing."""
    metrics = TransportMetrics()
    metrics.send_end_ts = 1000.0
    metrics.first_byte_ts = 1001.5  # 1500ms
    metrics.last_byte_ts = 1003.0   # 3000ms

    turn = TurnResult(
        turn_index=0,
        user_text="Hello there",
        agent_text="Hi! How can I help you?",
        metrics=metrics,
        eval_results=[
            EvalResult(evaluator_type="latency", passed=True, score=1.0, reason="OK"),
            EvalResult(
                evaluator_type="keyword",
                passed=passed,
                score=1.0 if passed else 0.0,
                reason="Found keywords" if passed else "Missing keywords",
            ),
        ],
    )

    return ScenarioReport(scenario_name=name, turns=[turn])


class TestResultStore:
    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = ResultStore(self.tmp.name)

    def teardown_method(self):
        self.store.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_save_and_retrieve(self):
        report = _make_report()
        run_id = self.store.save_report(report, transport_type="livekit", tags=["ci"])

        assert run_id
        assert len(run_id) == 36  # UUID

        run = self.store.get_run(run_id)
        assert run is not None
        assert run["scenario_name"] == "test-scenario"
        assert run["passed"] == 1
        assert run["total_turns"] == 1
        assert run["passed_turns"] == 1
        assert run["transport_type"] == "livekit"
        assert run["tags"] == ["ci"]
        assert len(run["turns"]) == 1
        assert run["turns"][0]["user_text"] == "Hello there"
        assert run["turns"][0]["agent_text"] == "Hi! How can I help you?"
        assert len(run["turns"][0]["evaluations"]) == 2

    def test_list_runs(self):
        self.store.save_report(_make_report("scenario-a"))
        self.store.save_report(_make_report("scenario-b"))
        self.store.save_report(_make_report("scenario-a"))

        all_runs = self.store.list_runs()
        assert len(all_runs) == 3

        filtered = self.store.list_runs(scenario_name="scenario-a")
        assert len(filtered) == 2

    def test_get_scenarios(self):
        self.store.save_report(_make_report("scenario-a", passed=True))
        self.store.save_report(_make_report("scenario-a", passed=False))
        self.store.save_report(_make_report("scenario-b", passed=True))

        scenarios = self.store.get_scenarios()
        assert len(scenarios) == 2

        a = next(s for s in scenarios if s["scenario_name"] == "scenario-a")
        assert a["run_count"] == 2
        assert a["pass_count"] == 1

    def test_get_scenario_history(self):
        for _ in range(5):
            self.store.save_report(_make_report("trend-test"))

        history = self.store.get_scenario_history("trend-test", limit=3)
        assert len(history) == 3

    def test_delete_run(self):
        run_id = self.store.save_report(_make_report())
        assert self.store.get_run(run_id) is not None

        deleted = self.store.delete_run(run_id)
        assert deleted
        assert self.store.get_run(run_id) is None

    def test_latency_averages(self):
        report = _make_report()
        self.store.save_report(report)

        runs = self.store.list_runs()
        assert runs[0]["avg_first_byte_ms"] == pytest.approx(1500.0, abs=1)
        assert runs[0]["avg_total_ms"] == pytest.approx(3000.0, abs=1)

    def test_empty_db(self):
        assert self.store.list_runs() == []
        assert self.store.get_scenarios() == []
        assert self.store.get_run("nonexistent") is None


class TestDashboard:
    def test_generate_empty(self):
        tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_db.close()
        store = ResultStore(tmp_db.name)

        from voicecheck.storage.dashboard import generate_dashboard

        out_path = Path(tempfile.mktemp(suffix=".html"))
        try:
            result = generate_dashboard(store, out_path)
            assert result.exists()
            content = result.read_text()
            assert "VoiceCheck Dashboard" in content
            assert "No test results yet" in content
        finally:
            store.close()
            out_path.unlink(missing_ok=True)
            Path(tmp_db.name).unlink(missing_ok=True)

    def test_generate_with_data(self):
        tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_db.close()
        store = ResultStore(tmp_db.name)
        store.save_report(_make_report("my-agent-test", passed=True))
        store.save_report(_make_report("my-agent-test", passed=False))

        from voicecheck.storage.dashboard import generate_dashboard

        out_path = Path(tempfile.mktemp(suffix=".html"))
        try:
            result = generate_dashboard(store, out_path)
            content = result.read_text()
            assert "my-agent-test" in content
            assert "chart.js" in content.lower() or "Chart" in content
            assert "50%" in content  # 1/2 pass rate
        finally:
            store.close()
            out_path.unlink(missing_ok=True)
            Path(tmp_db.name).unlink(missing_ok=True)
