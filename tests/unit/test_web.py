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


class TestAudioArtifacts:
    @pytest.fixture
    def client_with_audio(self, tmp_path):
        from fastapi.testclient import TestClient

        from voicecheck.web.app import create_app

        db = tmp_path / "results.db"
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "turn_1_user.wav").write_bytes(b"RIFF_user")
        (artifacts / "turn_1_agent.wav").write_bytes(b"RIFF_agent")
        (artifacts / "full_conversation.wav").write_bytes(b"RIFF_full")
        (artifacts / "secret.txt").write_text("should not be servable")

        store = ResultStore(str(db))
        run_id = store.save_report(
            _make_report("with-audio"), transport_type="echo", artifacts_dir=artifacts
        )
        run_id_no_audio = store.save_report(_make_report("no-audio"), transport_type="echo")
        store.close()

        app = create_app(db_path=str(db))
        with TestClient(app) as c:
            yield c, run_id, run_id_no_audio

    def test_artifacts_endpoint_lists_wavs(self, client_with_audio):
        client, run_id, _ = client_with_audio
        data = client.get(f"/api/runs/{run_id}/artifacts").json()
        assert data["available"] is True
        assert data["full_conversation"] == "full_conversation.wav"
        assert data["turns"][0]["user"] == "turn_1_user.wav"
        assert data["turns"][0]["agent"] == "turn_1_agent.wav"

    def test_artifacts_endpoint_unavailable(self, client_with_audio):
        client, _, run_id_no_audio = client_with_audio
        data = client.get(f"/api/runs/{run_id_no_audio}/artifacts").json()
        assert data["available"] is False

    def test_artifacts_endpoint_404_for_unknown_run(self, client_with_audio):
        client, _, _ = client_with_audio
        resp = client.get("/api/runs/does-not-exist/artifacts")
        assert resp.status_code == 404

    def test_audio_endpoint_serves_wav(self, client_with_audio):
        client, run_id, _ = client_with_audio
        resp = client.get(f"/audio/{run_id}/turn_1_agent.wav")
        assert resp.status_code == 200
        assert resp.content == b"RIFF_agent"
        assert resp.headers["content-type"] == "audio/wav"

    def test_audio_endpoint_rejects_files_outside_allowlist(self, client_with_audio):
        """Only WAVs listed by get_run_artifacts are servable — blocks traversal and sibling files."""
        client, run_id, _ = client_with_audio
        # File exists in the dir but isn't part of the per-turn allowlist
        assert client.get(f"/audio/{run_id}/secret.txt").status_code == 404
        # Classic traversal attempt
        assert client.get(f"/audio/{run_id}/../secret.txt").status_code in (404, 400)
        # Unknown turn index file
        assert client.get(f"/audio/{run_id}/turn_99_user.wav").status_code == 404

    def test_audio_endpoint_404_for_run_without_artifacts(self, client_with_audio):
        client, _, run_id_no_audio = client_with_audio
        resp = client.get(f"/audio/{run_id_no_audio}/turn_1_user.wav")
        assert resp.status_code == 404


VALID_SCENARIO_YAML = """\
name: scenario-builder-test
description: roundtrip smoke
transport:
  type: echo
  config:
    response_text: hello
turns:
  - user: hi
    expect:
      - type: latency
        max_first_byte_ms: 3000
"""

INVALID_SCENARIO_YAML = """\
name: missing-turns
transport:
  type: echo
"""


@pytest.fixture
def client_writable(tmp_path):
    """Fresh client with empty db + writable output dir for scenario-file CRUD."""
    from fastapi.testclient import TestClient

    from voicecheck.web.app import create_app

    db = tmp_path / "results.db"
    out_dir = tmp_path / "vc"
    app = create_app(db_path=str(db), output_dir=str(out_dir))
    with TestClient(app) as c:
        yield c, out_dir


class TestScenarioFileCRUD:
    def test_create_valid_scenario(self, client_writable):
        c, out_dir = client_writable
        resp = c.post("/api/scenario-files", json={"yaml": VALID_SCENARIO_YAML})
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "scenario-builder-test"
        assert (out_dir / "scenarios" / "scenario-builder-test.yaml").is_file()

    def test_create_invalid_scenario_returns_422_with_errors(self, client_writable):
        c, out_dir = client_writable
        resp = c.post("/api/scenario-files", json={"yaml": INVALID_SCENARIO_YAML})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["errors"], "expected structured errors"
        # Should NOT have written the file
        assert not (out_dir / "scenarios" / "missing-turns.yaml").exists()

    def test_create_requires_yaml(self, client_writable):
        c, _ = client_writable
        resp = c.post("/api/scenario-files", json={"yaml": ""})
        assert resp.status_code == 422

    def test_create_requires_name_field(self, client_writable):
        c, _ = client_writable
        resp = c.post("/api/scenario-files", json={"yaml": "description: no name\n"})
        assert resp.status_code == 422

    def test_scenario_path_traversal_is_blocked(self, client_writable):
        c, out_dir = client_writable
        sentinel = out_dir.parent / "escaped.yaml"
        sentinel.write_text("name: pre-existing\n")
        # The edit page uses a {name:path} route, so slashes reach the handler —
        # _scenario_path must confine it to scenarios_dir and reject traversal.
        resp = c.get("/scenarios/..%2F..%2Fescaped/edit")
        assert resp.status_code == 400, resp.text
        # API write/delete routes must never escape scenarios_dir either.
        put = c.put(
            "/api/scenario-files/..%2F..%2Fescaped",
            json={"yaml": VALID_SCENARIO_YAML},
        )
        assert put.status_code != 200, put.text
        assert sentinel.read_text() == "name: pre-existing\n"  # untouched
        assert c.delete("/api/scenario-files/..%2F..%2Fescaped").status_code != 200

    def test_list_then_get_then_update_then_delete(self, client_writable):
        c, out_dir = client_writable

        c.post("/api/scenario-files", json={"yaml": VALID_SCENARIO_YAML}).raise_for_status()

        listed = c.get("/api/scenario-files").json()
        assert any(f["name"] == "scenario-builder-test" for f in listed)

        got = c.get("/api/scenario-files/scenario-builder-test").json()
        assert "yaml" in got and "scenario-builder-test" in got["yaml"]

        # Update to a still-valid YAML
        updated = VALID_SCENARIO_YAML.replace("hello", "hi again")
        resp = c.put("/api/scenario-files/scenario-builder-test", json={"yaml": updated})
        assert resp.status_code == 200
        assert "hi again" in (out_dir / "scenarios" / "scenario-builder-test.yaml").read_text()

        # Reject invalid update — file content must not change
        resp = c.put(
            "/api/scenario-files/scenario-builder-test",
            json={"yaml": INVALID_SCENARIO_YAML},
        )
        assert resp.status_code == 422
        assert "hi again" in (out_dir / "scenarios" / "scenario-builder-test.yaml").read_text()

        resp = c.delete("/api/scenario-files/scenario-builder-test")
        assert resp.status_code == 200
        assert not (out_dir / "scenarios" / "scenario-builder-test.yaml").exists()

    def test_validate_endpoint_returns_errors_without_writing(self, client_writable):
        c, out_dir = client_writable
        resp = c.post("/api/scenario-files/validate", json={"yaml": INVALID_SCENARIO_YAML})
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert body["errors"]

    def test_preview_yaml_serializes_form(self, client_writable):
        c, _ = client_writable
        resp = c.post(
            "/api/scenario-files/preview-yaml",
            json={
                "name": "preview-test",
                "transport_type": "echo",
                "transport_config": {"response_text": "hi"},
                "mode": "scripted",
                "turns": [{"user": "hello", "evaluators": []}],
            },
        )
        assert resp.status_code == 200
        yaml_text = resp.json()["yaml"]
        assert "name: preview-test" in yaml_text
        assert "type: echo" in yaml_text

    def test_preview_coerces_numeric_fields_to_numbers(self, client_writable):
        """Form inputs are strings; the YAML must contain numbers so evaluators don't TypeError at runtime."""
        c, _ = client_writable
        resp = c.post(
            "/api/scenario-files/preview-yaml",
            json={
                "name": "numeric-test",
                "transport_type": "echo",
                "transport_config": {
                    "response_text": "hi",
                    "first_byte_delay_ms": "400",
                },
                "mode": "scripted",
                "turns": [
                    {
                        "user": "x",
                        "evaluators": [
                            {
                                "type": "latency",
                                "fields": {
                                    "max_first_byte_ms": "3000",
                                    "max_total_ms": "8000",
                                },
                            },
                            {"type": "turn_count", "fields": {"min_words": "3"}},
                        ],
                    }
                ],
            },
        )
        yaml_text = resp.json()["yaml"]
        # Numeric values must NOT be quoted strings
        assert "max_first_byte_ms: 3000" in yaml_text
        assert "max_first_byte_ms: '3000'" not in yaml_text
        assert "first_byte_delay_ms: 400" in yaml_text
        assert "min_words: 3" in yaml_text

    def test_preview_uses_evaluator_canonical_field_names(self, client_writable):
        """Builder must emit must_contain / min_words — the actual evaluator kwargs."""
        c, _ = client_writable
        resp = c.post(
            "/api/scenario-files/preview-yaml",
            json={
                "name": "fieldname-test",
                "transport_type": "echo",
                "transport_config": {"response_text": "hi"},
                "mode": "scripted",
                "turns": [
                    {
                        "user": "hello",
                        "evaluators": [
                            {"type": "keyword", "fields": {"must_contain": "hi, hello"}},
                            {"type": "turn_count", "fields": {"min_words": "2"}},
                        ],
                    }
                ],
            },
        )
        yaml_text = resp.json()["yaml"]
        assert "must_contain:" in yaml_text
        assert "min_words: 2" in yaml_text
        # Old wrong names must NOT appear
        assert "required:" not in yaml_text
        assert "blocked:" not in yaml_text

    def test_save_then_validate_resulting_yaml_passes(self, client_writable):
        """End-to-end: form → preview-yaml → save must produce a YAML the validator accepts.
        Regression guard for the keyword `required`→`must_contain` field-name bug.
        """
        c, _ = client_writable
        form = {
            "name": "e2e-builder",
            "transport_type": "echo",
            "transport_config": {"response_text": "hi"},
            "mode": "scripted",
            "turns": [
                {
                    "user": "hello",
                    "evaluators": [
                        {"type": "latency", "fields": {"max_first_byte_ms": "3000"}},
                        {"type": "keyword", "fields": {"must_contain": "hi"}},
                        {"type": "turn_count", "fields": {"min_words": "2"}},
                    ],
                }
            ],
        }
        yaml_text = c.post("/api/scenario-files/preview-yaml", json=form).json()["yaml"]
        # validate-endpoint must say it's clean
        v = c.post("/api/scenario-files/validate", json={"yaml": yaml_text}).json()
        assert v["valid"], f"validation failed: {v['errors']}"
        # And save must accept it (validate-on-save uses the same path)
        save = c.post("/api/scenario-files", json={"yaml": yaml_text})
        assert save.status_code == 200, save.text


class TestBaselineRoutes:
    def test_save_baseline_and_compare(self, client):
        # Use an existing seeded run
        run_id = client.get("/api/runs").json()["runs"][0]["id"]

        resp = client.post(
            "/api/baselines",
            json={"run_id": run_id, "name": "v1", "notes": "first save"},
        )
        assert resp.status_code == 200, resp.text
        bid = resp.json()["id"]

        listed = client.get("/api/baselines").json()
        assert any(b["id"] == bid for b in listed)

        # Compare same run against its own baseline → no regressions
        cmp = client.post(f"/api/baselines/{bid}/compare", json={"run_id": run_id}).json()
        assert cmp["has_ci_failure"] is False
        assert "baseline" in cmp and "current" in cmp

        # Delete baseline
        resp = client.delete(f"/api/baselines/{bid}")
        assert resp.status_code == 200
        assert not any(b["id"] == bid for b in client.get("/api/baselines").json())

    def test_baseline_save_requires_known_run(self, client):
        resp = client.post(
            "/api/baselines",
            json={"run_id": "does-not-exist", "name": "x", "notes": ""},
        )
        assert resp.status_code == 404

    def test_baseline_save_requires_name(self, client):
        run_id = client.get("/api/runs").json()["runs"][0]["id"]
        resp = client.post("/api/baselines", json={"run_id": run_id, "name": "", "notes": ""})
        assert resp.status_code == 422


class TestRunTrigger:
    def test_trigger_run_returns_run_id_and_live_url(self, client_writable):
        """POST /api/runs queues a run and returns identifiers immediately."""
        c, out_dir = client_writable
        # Save a valid scenario first
        c.post("/api/scenario-files", json={"yaml": VALID_SCENARIO_YAML}).raise_for_status()

        resp = c.post(
            "/api/runs",
            json={"scenario_name": "scenario-builder-test", "tags": ["ui-test"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["run_id"]
        assert body["live_url"].endswith("/live")

    def test_trigger_run_requires_scenario_or_yaml(self, client_writable):
        c, _ = client_writable
        resp = c.post("/api/runs", json={})
        assert resp.status_code == 422


class TestCallLogEndpoint:
    """The Trace tab in run_detail.html depends on /api/runs/{id}/calllog."""

    def test_calllog_unavailable_for_seeded_run(self, client):
        """Seeded test runs have no run_dir, so call log should be unavailable."""
        run_id = client.get("/api/runs").json()["runs"][0]["id"]
        resp = client.get(f"/api/runs/{run_id}/calllog")
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["events"] == []

    def test_calllog_404_for_unknown_run(self, client):
        resp = client.get("/api/runs/no-such-run/calllog")
        assert resp.status_code == 404


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
