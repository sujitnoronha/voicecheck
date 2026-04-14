"""Tests for the VoiceCheck web dashboard API and pages."""

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
    metrics.first_byte_ts = 1001.5
    metrics.last_byte_ts = 1003.0

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


@pytest.fixture
def client():
    """Create a test client with a temporary database seeded with data."""
    from fastapi.testclient import TestClient

    from voicecheck.web.app import create_app

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()

    # Seed data
    store = ResultStore(tmp.name)
    store.save_report(_make_report("scenario-a", True), transport_type="livekit", tags=["ci"])
    store.save_report(_make_report("scenario-a", False), transport_type="livekit")
    store.save_report(_make_report("scenario-b", True), transport_type="livekit")
    store.close()

    app = create_app(db_path=tmp.name)
    with TestClient(app) as c:
        yield c

    Path(tmp.name).unlink(missing_ok=True)


class TestAPIScenarios:
    def test_get_scenarios(self, client):
        resp = client.get("/api/scenarios")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {s["scenario_name"] for s in data}
        assert names == {"scenario-a", "scenario-b"}

    def test_scenarios_include_percentiles(self, client):
        resp = client.get("/api/scenarios")
        data = resp.json()
        for s in data:
            assert "p50_first_byte_ms" in s
            assert "p95_first_byte_ms" in s
            assert "p99_first_byte_ms" in s
            assert "p50_total_ms" in s

    def test_scenario_history(self, client):
        resp = client.get("/api/scenarios/scenario-a/history")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_scenario_percentiles(self, client):
        resp = client.get("/api/scenarios/scenario-a/percentiles")
        assert resp.status_code == 200
        data = resp.json()
        assert "first_byte_ms" in data
        assert "total_ms" in data
        assert "p50" in data["first_byte_ms"]
        assert "p95" in data["first_byte_ms"]
        assert "p99" in data["first_byte_ms"]


class TestAPIRuns:
    def test_get_all_runs(self, client):
        resp = client.get("/api/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["runs"]) == 3
        assert "limit" in data
        assert "offset" in data

    def test_get_runs_filtered(self, client):
        resp = client.get("/api/runs?scenario=scenario-a")
        data = resp.json()
        assert data["total"] == 2
        assert len(data["runs"]) == 2

    def test_get_runs_pagination(self, client):
        resp = client.get("/api/runs?limit=1&offset=0")
        data = resp.json()
        assert data["total"] == 3
        assert len(data["runs"]) == 1

        resp2 = client.get("/api/runs?limit=1&offset=1")
        data2 = resp2.json()
        assert len(data2["runs"]) == 1
        assert data2["runs"][0]["id"] != data["runs"][0]["id"]

    def test_get_run_detail(self, client):
        runs = client.get("/api/runs").json()["runs"]
        run_id = runs[0]["id"]

        resp = client.get(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == run_id
        assert "turns" in data
        assert len(data["turns"]) == 1
        assert data["turns"][0]["user_text"] == "Hello there"
        assert isinstance(data["turns"][0]["evaluations"], list)

    def test_get_run_404(self, client):
        resp = client.get("/api/runs/nonexistent-id")
        assert resp.status_code == 404

    def test_delete_run(self, client):
        runs = client.get("/api/runs").json()["runs"]
        run_id = runs[0]["id"]

        resp = client.delete(f"/api/runs/{run_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify deleted
        assert client.get(f"/api/runs/{run_id}").status_code == 404

        # Total count decreased
        assert client.get("/api/runs").json()["total"] == 2

    def test_delete_run_404(self, client):
        resp = client.delete("/api/runs/nonexistent-id")
        assert resp.status_code == 404


class TestHTMLPages:
    def test_index_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "VoiceCheck" in resp.text

    def test_runs_page(self, client):
        resp = client.get("/runs")
        assert resp.status_code == 200
        assert "All Runs" in resp.text

    def test_scenario_page(self, client):
        resp = client.get("/scenario/scenario-a")
        assert resp.status_code == 200
        assert "scenario-a" in resp.text

    def test_run_detail_page(self, client):
        runs = client.get("/api/runs").json()["runs"]
        run_id = runs[0]["id"]
        resp = client.get(f"/run/{run_id}")
        assert resp.status_code == 200

    def test_compare_page(self, client):
        resp = client.get("/compare")
        assert resp.status_code == 200
        assert "Compare" in resp.text


class TestStoreNewMethods:
    """Test the new store methods added for the dashboard."""

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = ResultStore(self.tmp.name)

    def teardown_method(self):
        self.store.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_count_runs(self):
        self.store.save_report(_make_report("a"))
        self.store.save_report(_make_report("a"))
        self.store.save_report(_make_report("b"))

        assert self.store.count_runs() == 3
        assert self.store.count_runs("a") == 2
        assert self.store.count_runs("b") == 1
        assert self.store.count_runs("nonexistent") == 0

    def test_percentiles(self):
        for _ in range(10):
            self.store.save_report(_make_report("perf-test"))

        percs = self.store.get_scenario_percentiles("perf-test", "first_byte_ms")
        assert percs["p50"] > 0
        assert percs["p95"] >= percs["p50"]
        assert percs["p99"] >= percs["p95"]

    def test_percentiles_empty(self):
        percs = self.store.get_scenario_percentiles("nonexistent")
        assert percs == {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    def test_all_scenario_stats(self):
        self.store.save_report(_make_report("x", True))
        self.store.save_report(_make_report("y", False))

        stats = self.store.get_all_scenario_stats()
        assert len(stats) == 2
        for s in stats:
            assert "p50_first_byte_ms" in s
            assert "p95_first_byte_ms" in s
            assert "p99_first_byte_ms" in s
            assert "p50_total_ms" in s
            assert "p95_total_ms" in s
            assert "p99_total_ms" in s
