"""FastAPI application for VoiceCheck live dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from voicecheck import __version__
from voicecheck.storage.store import DEFAULT_OUTPUT_DIR, ResultStore

logger = logging.getLogger("voicecheck.web")

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def create_app(
    db_path: str | None = None,
    output_dir: str | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    # Register transports + evaluators so validate/run paths can resolve types.
    from voicecheck.cli import _ensure_registrations

    _ensure_registrations()

    store = ResultStore(db_path)
    out_base = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    scenarios_dir = out_base / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    def _scenario_path(name: str) -> Path:
        # Resolve and confine to scenarios_dir — blocks path traversal via name.
        p = (scenarios_dir / f"{name}.yaml").resolve()
        if scenarios_dir.resolve() not in p.parents:
            raise HTTPException(400, f"Invalid scenario name: {name}")
        return p

    # In-process state for live runs (single-process dashboard only)
    run_queues: dict[str, asyncio.Queue] = {}
    run_cancel: dict[str, bool] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        store.close()

    app = FastAPI(title="VoiceCheck Dashboard", version=__version__, lifespan=lifespan)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── HTML page routes ────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request):
        return templates.TemplateResponse(request, "history.html")

    @app.get("/scenarios", response_class=HTMLResponse)
    async def scenarios_list_page(request: Request):
        return templates.TemplateResponse(request, "scenarios.html")

    @app.get("/scenarios/new", response_class=HTMLResponse)
    async def scenario_new_page(request: Request):
        return templates.TemplateResponse(
            request, "scenario_builder.html", {"scenario_name": None, "scenario_yaml": ""}
        )

    @app.get("/scenarios/{name:path}/edit", response_class=HTMLResponse)
    async def scenario_edit_page(request: Request, name: str):
        yaml_path = _scenario_path(name)
        yaml_text = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else ""
        return templates.TemplateResponse(
            request, "scenario_builder.html", {"scenario_name": name, "scenario_yaml": yaml_text}
        )

    @app.get("/runs", response_class=HTMLResponse)
    async def all_runs_page(request: Request):
        return templates.TemplateResponse(request, "runs.html", {"scenario_name": None})

    @app.get("/scenario/{name:path}", response_class=HTMLResponse)
    async def scenario_runs_page(request: Request, name: str):
        return templates.TemplateResponse(request, "runs.html", {"scenario_name": name})

    @app.get("/run/{run_id}", response_class=HTMLResponse)
    async def run_detail_page(request: Request, run_id: str):
        return templates.TemplateResponse(request, "run_detail.html", {"run_id": run_id})

    @app.get("/runs/{run_id}/live", response_class=HTMLResponse)
    async def run_live_page(request: Request, run_id: str):
        return templates.TemplateResponse(request, "run_live.html", {"run_id": run_id})

    @app.get("/compare", response_class=HTMLResponse)
    async def compare_page(request: Request):
        return templates.TemplateResponse(request, "compare.html")

    @app.get("/baselines", response_class=HTMLResponse)
    async def baselines_page(request: Request):
        return templates.TemplateResponse(request, "baselines.html")

    # ── JSON API: scenario files ────────────────────────────────

    @app.get("/api/scenario-files")
    async def api_scenario_files():
        """List YAML scenario files in the scenarios directory."""
        files = []
        for p in sorted(scenarios_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                files.append(
                    {
                        "filename": p.name,
                        "name": raw.get("name", p.stem),
                        "description": raw.get("description", ""),
                        "transport_type": raw.get("transport", {}).get("type", ""),
                        "mode": _detect_mode(raw),
                    }
                )
            except Exception:
                files.append(
                    {
                        "filename": p.name,
                        "name": p.stem,
                        "description": "",
                        "transport_type": "",
                        "mode": "",
                    }
                )
        return files

    @app.get("/api/scenario-files/{name}")
    async def api_get_scenario_file(name: str):
        p = _scenario_path(name)
        if not p.exists():
            raise HTTPException(404, f"Scenario '{name}' not found")
        return {"name": name, "yaml": p.read_text(encoding="utf-8")}

    @app.post("/api/scenario-files")
    async def api_create_scenario(request: Request):
        body: dict[str, Any] = await request.json()
        yaml_text = body.get("yaml", "").strip()
        if not yaml_text:
            raise HTTPException(422, "yaml is required")
        try:
            raw = yaml.safe_load(yaml_text)
            if not isinstance(raw, dict):
                raise ValueError("Must be a YAML mapping")
        except Exception as e:
            raise HTTPException(422, f"Invalid YAML: {e}")
        name = raw.get("name", "").strip().replace(" ", "-").replace("/", "-")
        if not name:
            raise HTTPException(422, "Scenario must have a 'name' field")
        errors = _validate_yaml_text(yaml_text)
        if errors:
            raise HTTPException(422, detail={"message": "Scenario is invalid", "errors": errors})
        p = _scenario_path(name)
        p.write_text(yaml_text, encoding="utf-8")
        return {"name": name, "filename": p.name}

    @app.put("/api/scenario-files/{name}")
    async def api_update_scenario(name: str, request: Request):
        body: dict[str, Any] = await request.json()
        yaml_text = body.get("yaml", "").strip()
        if not yaml_text:
            raise HTTPException(422, "yaml is required")
        errors = _validate_yaml_text(yaml_text)
        if errors:
            raise HTTPException(422, detail={"message": "Scenario is invalid", "errors": errors})
        p = _scenario_path(name)
        p.write_text(yaml_text, encoding="utf-8")
        return {"name": name}

    @app.delete("/api/scenario-files/{name}")
    async def api_delete_scenario(name: str):
        p = _scenario_path(name)
        if not p.exists():
            raise HTTPException(404, f"Scenario '{name}' not found")
        p.unlink()
        return {"deleted": True}

    @app.post("/api/scenario-files/validate")
    async def api_validate_scenario(request: Request):
        body: dict[str, Any] = await request.json()
        yaml_text = body.get("yaml", "").strip()
        if not yaml_text:
            raise HTTPException(422, "yaml is required")
        errors = _validate_yaml_text(yaml_text)
        return {"valid": not errors, "errors": errors}

    @app.post("/api/scenario-files/preview-yaml")
    async def api_preview_yaml(request: Request):
        """Convert form JSON to YAML string for the live preview pane."""
        body: dict[str, Any] = await request.json()
        cleaned = _form_to_scenario_dict(body)
        return {
            "yaml": yaml.dump(
                cleaned, allow_unicode=True, default_flow_style=False, sort_keys=False
            )
        }

    # ── JSON API: scenarios (DB-backed stats) ───────────────────

    @app.get("/api/scenarios")
    async def api_scenarios():
        return store.get_all_scenario_stats()

    @app.get("/api/scenarios/{name:path}/history")
    async def api_scenario_history(name: str, limit: int = Query(100, le=500)):
        return store.get_scenario_history(name, limit=limit)

    @app.get("/api/scenarios/{name:path}/percentiles")
    async def api_percentiles(name: str):
        fb = store.get_scenario_percentiles(name, "first_byte_ms")
        total = store.get_scenario_percentiles(name, "total_ms")
        return {"first_byte_ms": fb, "total_ms": total}

    # ── JSON API: runs ──────────────────────────────────────────

    @app.get("/api/runs")
    async def api_runs(
        scenario: str | None = Query(None),
        status: str | None = Query(None),
        tag: str | None = Query(None),
        limit: int = Query(50, le=200),
        offset: int = Query(0, ge=0),
    ):
        runs = store.list_runs(scenario_name=scenario, limit=limit, offset=offset)
        if tag:
            runs = [r for r in runs if tag in json.loads(r.get("tags") or "[]")]
        if status in ("passed", "failed"):
            want = status == "passed"
            runs = [r for r in runs if bool(r["passed"]) == want]
        total = store.count_runs(scenario_name=scenario)
        return {"runs": runs, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/runs/{run_id}")
    async def api_run(run_id: str):
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        return run

    @app.delete("/api/runs/{run_id}")
    async def api_delete_run(run_id: str):
        deleted = store.delete_run(run_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        return {"deleted": True}

    @app.get("/api/runs/{run_id}/artifacts")
    async def api_run_artifacts(run_id: str):
        if not store.get_run(run_id):
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        artifacts = store.get_run_artifacts(run_id)
        if not artifacts:
            return {"available": False, "turns": [], "full_conversation": None}
        return {
            "available": True,
            "full_conversation": artifacts["full_conversation"],
            "turns": artifacts["turns"],
        }

    @app.get("/api/runs/{run_id}/calllog")
    async def api_run_calllog(run_id: str):
        if not store.get_run(run_id):
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        events = store.get_call_log(run_id)
        if events is None:
            return {"available": False, "events": []}
        return {"available": True, "events": events}

    @app.get("/api/runs/{run_id}/scenario")
    async def api_run_scenario(run_id: str):
        if not store.get_run(run_id):
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        yaml_text = store.get_scenario_file(run_id)
        if yaml_text is None:
            raise HTTPException(status_code=404, detail="Scenario snapshot not available")
        return {"yaml": yaml_text}

    # ── JSON API: run trigger + live SSE ───────────────────────

    @app.post("/api/runs")
    async def api_trigger_run(request: Request, background_tasks: BackgroundTasks):
        body: dict[str, Any] = await request.json()
        scenario_name = body.get("scenario_name", "").strip()
        yaml_text = body.get("yaml", "").strip()
        tags = body.get("tags", [])

        if not scenario_name and not yaml_text:
            raise HTTPException(422, "scenario_name or yaml is required")

        run_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue()
        run_queues[run_id] = q
        run_cancel[run_id] = False

        # Insert a placeholder row so the live page can find it immediately
        _insert_pending_run(store, run_id, scenario_name or "custom", tags)

        background_tasks.add_task(
            _execute_run,
            run_id=run_id,
            scenario_name=scenario_name,
            yaml_text=yaml_text,
            tags=tags,
            store=store,
            out_base=out_base,
            scenarios_dir=scenarios_dir,
            queue=q,
            cancel_flags=run_cancel,
        )

        return {"run_id": run_id, "live_url": f"/runs/{run_id}/live"}

    @app.get("/api/runs/{run_id}/events")
    async def api_run_events(run_id: str):
        """SSE stream — sends turn-by-turn events until the run finishes."""
        q = run_queues.get(run_id)
        if q is None:
            # Run finished before client connected — check DB
            run = store.get_run(run_id)
            if run:

                async def _replay():
                    data = json.dumps(
                        {
                            "type": "run_completed",
                            "passed": bool(run["passed"]),
                            "total_turns": run["total_turns"],
                            "passed_turns": run["passed_turns"],
                        }
                    )
                    yield f"data: {data}\n\n"

                return StreamingResponse(_replay(), media_type="text/event-stream")
            raise HTTPException(404, f"Run not found: {run_id}")

        async def _stream():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=30)
                    except asyncio.TimeoutError:
                        yield 'data: {"type":"ping"}\n\n'
                        continue
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                run_queues.pop(run_id, None)
                run_cancel.pop(run_id, None)

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/runs/{run_id}/cancel")
    async def api_cancel_run(run_id: str):
        if run_id not in run_cancel:
            raise HTTPException(404, "Run not active")
        run_cancel[run_id] = True
        return {"cancelled": True}

    # ── JSON API: baselines ─────────────────────────────────────

    @app.get("/api/baselines")
    async def api_baselines(scenario: str | None = Query(None)):
        return store.baselines.list(scenario_name=scenario)

    @app.post("/api/baselines")
    async def api_save_baseline(request: Request):
        body: dict[str, Any] = await request.json()
        run_id = body.get("run_id")
        name = body.get("name", "").strip()
        notes = body.get("notes", "")
        if not run_id or not name:
            raise HTTPException(status_code=422, detail="run_id and name are required")
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        from types import SimpleNamespace

        from voicecheck.core.types import EvalResult
        from voicecheck.storage.baselines import metrics_from_report

        turns = []
        for t in run.get("turns", []):
            evals = [
                EvalResult(
                    evaluator_type=e["type"],
                    passed=e["passed"],
                    score=e.get("score", 0.0),
                    reason=e.get("reason", ""),
                    details=e.get("details"),
                )
                for e in t.get("evaluations", [])
            ]
            turns.append(
                SimpleNamespace(
                    turn_index=t["turn_index"],
                    user_text=t["user_text"],
                    agent_text=t.get("agent_text") or "",
                    passed=bool(t["passed"]),
                    metrics=SimpleNamespace(
                        first_byte_ms=t.get("first_byte_ms") or 0.0,
                        total_ms=t.get("total_ms") or 0.0,
                    ),
                    eval_results=evals,
                    tool_calls=[],
                )
            )
        fake_report = SimpleNamespace(
            scenario_name=run["scenario_name"],
            turns=turns,
            conversation_eval=run.get("conversation_eval"),
            passed=bool(run["passed"]),
            total_turns=run["total_turns"],
            passed_turns=run["passed_turns"],
        )
        metrics = metrics_from_report(fake_report)
        bid = store.baselines.save(
            name=name,
            scenario_name=run["scenario_name"],
            run_id=run_id,
            metrics=metrics,
            notes=notes,
        )
        return {"id": bid, "name": name, "scenario_name": run["scenario_name"]}

    @app.delete("/api/baselines/{baseline_id}")
    async def api_delete_baseline(baseline_id: str):
        rows = store.baselines.list()
        row = next((r for r in rows if r["id"] == baseline_id), None)
        if not row:
            raise HTTPException(status_code=404, detail="Baseline not found")
        store.baselines.delete(row["name"], row["scenario_name"])
        return {"deleted": True}

    @app.post("/api/baselines/{baseline_id}/compare")
    async def api_compare_baseline(baseline_id: str, request: Request):
        body: dict[str, Any] = await request.json()
        run_id = body.get("run_id")
        if not run_id:
            raise HTTPException(status_code=422, detail="run_id is required")
        rows = store.baselines.list()
        row = next((r for r in rows if r["id"] == baseline_id), None)
        if not row:
            raise HTTPException(status_code=404, detail="Baseline not found")
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        from dataclasses import asdict
        from types import SimpleNamespace

        from voicecheck.core.types import EvalResult
        from voicecheck.storage.baselines import (
            BaselineMetrics,
            compare_metrics,
            metrics_from_report,
        )

        turns = []
        for t in run.get("turns", []):
            evals = [
                EvalResult(
                    evaluator_type=e["type"],
                    passed=e["passed"],
                    score=e.get("score", 0.0),
                    reason=e.get("reason", ""),
                    details=e.get("details"),
                )
                for e in t.get("evaluations", [])
            ]
            turns.append(
                SimpleNamespace(
                    turn_index=t["turn_index"],
                    user_text=t["user_text"],
                    agent_text=t.get("agent_text") or "",
                    passed=bool(t["passed"]),
                    metrics=SimpleNamespace(
                        first_byte_ms=t.get("first_byte_ms") or 0.0,
                        total_ms=t.get("total_ms") or 0.0,
                    ),
                    eval_results=evals,
                    tool_calls=[],
                )
            )
        fake_report = SimpleNamespace(
            scenario_name=run["scenario_name"],
            turns=turns,
            conversation_eval=run.get("conversation_eval"),
            passed=bool(run["passed"]),
            total_turns=run["total_turns"],
            passed_turns=run["passed_turns"],
        )
        bl_m = json.loads(row["metrics_json"])
        baseline_metrics = BaselineMetrics(**bl_m)
        current_metrics = metrics_from_report(fake_report)
        regressions = compare_metrics(baseline_metrics, current_metrics)
        return {
            "baseline": asdict(baseline_metrics),
            "current": asdict(current_metrics),
            "regressions": [
                {
                    "metric": r.metric,
                    "baseline_value": r.baseline_value,
                    "current_value": r.current_value,
                    "delta": r.delta,
                    "threshold_desc": r.threshold_desc,
                    "is_ci_failure": r.is_ci_failure,
                    "description": r.description,
                }
                for r in regressions
            ],
            "has_ci_failure": any(r.is_ci_failure for r in regressions),
        }

    # ── Audio serving ───────────────────────────────────────────

    @app.get("/audio/{run_id}/{filename}")
    async def serve_audio(run_id: str, filename: str):
        artifacts = store.get_run_artifacts(run_id)
        if not artifacts:
            raise HTTPException(status_code=404, detail="Artifacts unavailable")
        allowed = {artifacts["full_conversation"]}
        for turn in artifacts["turns"]:
            allowed.add(turn.get("user"))
            allowed.add(turn.get("agent"))
        allowed.discard(None)
        if filename not in allowed:
            raise HTTPException(status_code=404, detail="Audio file not found")
        file_path = Path(artifacts["dir"]) / filename
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="Audio file missing on disk")
        return FileResponse(file_path, media_type="audio/wav")

    return app


# ── Helpers ─────────────────────────────────────────────────────


def _validate_yaml_text(yaml_text: str) -> list[str]:
    """Run validate_scenario on a YAML string by writing it to a tempfile."""
    import tempfile

    from voicecheck.core.scenario import validate_scenario

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_text)
        tmp = f.name
    try:
        return validate_scenario(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _detect_mode(raw: dict) -> str:
    if raw.get("flow"):
        return "guided"
    if raw.get("questions"):
        return "questions"
    if raw.get("persona"):
        return "persona"
    return "scripted"


def _coerce_scalar(v: Any) -> Any:
    """Coerce a form string to int/float when it parses cleanly.

    Form inputs always arrive as strings; YAML evaluators expect numbers.
    Returns the original value untouched if it's not a numeric string.
    """
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return v
    # int first (no decimal); reject things like "01" → keep as string
    try:
        if s.lstrip("-").isdigit():
            return int(s)
        return float(s)
    except ValueError:
        return v


def _form_to_scenario_dict(body: dict) -> dict:
    """Convert builder form JSON to a clean scenario dict for YAML serialization."""
    d: dict[str, Any] = {}
    if body.get("name"):
        d["name"] = body["name"]
    if body.get("description"):
        d["description"] = body["description"]

    transport_type = body.get("transport_type", "echo")
    transport_config = {
        k: _coerce_scalar(v)
        for k, v in (body.get("transport_config") or {}).items()
        if v not in (None, "")
    }
    d["transport"] = {
        "type": transport_type,
        "mode": body.get("transport_mode", "web_call"),
        "config": transport_config,
    }

    audio: dict = {}
    if body.get("tts_provider"):
        audio["tts_provider"] = body["tts_provider"]
    if body.get("stt_provider"):
        audio["stt_provider"] = body["stt_provider"]
    if body.get("language"):
        audio["language"] = body["language"]
    if audio:
        d["audio"] = audio

    mode = body.get("mode", "scripted")
    if mode == "scripted":
        turns = []
        for t in body.get("turns", []):
            turn: dict = {"user": t.get("user", "")}
            expects = _build_expects(t.get("evaluators", []))
            if expects:
                turn["expect"] = expects
            turns.append(turn)
        if turns:
            d["turns"] = turns
    elif mode == "questions":
        qs = [q.strip() for q in body.get("questions_text", "").splitlines() if q.strip()]
        if qs:
            d["questions"] = qs
    elif mode in ("persona", "guided"):
        p = body.get("persona", {})
        if p:
            d["persona"] = {k: v for k, v in p.items() if v}
        if mode == "guided":
            steps = []
            for s in body.get("flow_steps", []):
                step: dict = {"goal": s.get("goal", "")}
                if s.get("name"):
                    step["name"] = s["name"]
                expects = _build_expects(s.get("evaluators", []))
                if expects:
                    step["expect"] = expects
                steps.append(step)
            if steps:
                d["flow"] = steps

    global_evals = _build_expects(body.get("global_evaluators", []))
    if global_evals:
        d["per_turn_expect"] = global_evals

    return d


def _build_expects(evaluators: list[dict]) -> list[dict]:
    out = []
    for ev in evaluators:
        ev_type = ev.get("type", "").strip()
        if not ev_type:
            continue
        entry: dict = {"type": ev_type}
        for k, v in (ev.get("fields") or {}).items():
            if v in (None, "", []):
                continue
            # Coerce comma-separated strings to lists for list fields
            if isinstance(v, str) and k in (
                "must_contain",
                "must_not_contain",
                "sequence",
                "dimensions",
                "expected_emotions",
                "forbidden_emotions",
                "known_facts",
                "false_facts",
                "private_info",
                "forbidden_patterns",
                "personality_traits",
            ):
                v = [x.strip() for x in v.split(",") if x.strip()]
            # Coerce JSON-like strings to dicts
            elif isinstance(v, str) and k in ("args_must_contain", "args_must_not_contain"):
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            else:
                # Numeric form fields ship as strings — coerce so evaluator
                # arithmetic (e.g. latency thresholds) doesn't TypeError.
                v = _coerce_scalar(v)
            entry[k] = v
        out.append(entry)
    return out


def _insert_pending_run(store: ResultStore, run_id: str, scenario_name: str, tags: list) -> None:
    """Insert a placeholder run row with status='running' so the live page can find it."""
    from datetime import datetime, timezone

    conn = store._get_conn()
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            """INSERT INTO runs (id, scenario_name, passed, total_turns, passed_turns,
               avg_first_byte_ms, avg_total_ms, transport_type, tags, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, scenario_name, 0, 0, 0, 0.0, 0.0, "", json.dumps(tags), "running", now),
        )
        conn.commit()
    except Exception:
        pass


async def _execute_run(
    *,
    run_id: str,
    scenario_name: str,
    yaml_text: str,
    tags: list,
    store: ResultStore,
    out_base: Path,
    scenarios_dir: Path,
    queue: asyncio.Queue,
    cancel_flags: dict[str, bool],
) -> None:
    """Background task: load + run scenario, stream events to queue."""
    from voicecheck.core.scenario import ScenarioRunner, load_scenario
    from voicecheck.storage.output import RunOutputWriter

    writer: RunOutputWriter | None = None

    async def _turn_cb(turn_result: Any, turn_idx: int, total: int | None) -> None:
        evals = [
            {"type": e.evaluator_type, "passed": e.passed, "score": e.score, "reason": e.reason}
            for e in turn_result.eval_results
        ]
        tool_calls = [
            {"name": tc.name, "args": tc.args, "result": tc.result}
            for tc in (turn_result.tool_calls or [])
        ]
        event = {
            "type": "turn_completed",
            "turn": turn_idx,
            "total_turns": total,
            "user_text": turn_result.user_text,
            "agent_text": turn_result.agent_text or "",
            "passed": turn_result.passed,
            "first_byte_ms": turn_result.metrics.first_byte_ms,
            "total_ms": turn_result.metrics.total_ms,
            "evaluations": evals,
            "tool_calls": tool_calls,
            "error": turn_result.error or "",
            "ts": time.time(),
        }
        await queue.put(event)
        # Write to call log incrementally
        if writer:
            writer.log(
                "turn_completed",
                turn=turn_idx,
                passed=turn_result.passed,
                user_text=turn_result.user_text,
                agent_text=turn_result.agent_text or "",
                first_byte_ms=turn_result.metrics.first_byte_ms,
                total_ms=turn_result.metrics.total_ms,
            )
        # Check for cancellation
        if cancel_flags.get(run_id):
            raise asyncio.CancelledError("Run cancelled by user")

    try:
        await queue.put(
            {"type": "run_started", "run_id": run_id, "scenario": scenario_name, "ts": time.time()}
        )

        # Load scenario
        import tempfile

        if yaml_text:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, encoding="utf-8"
            ) as f:
                f.write(yaml_text)
                tmp_path = Path(f.name)
            scenario_yaml = yaml_text
        else:
            p = scenarios_dir / f"{scenario_name}.yaml"
            if not p.exists():
                raise FileNotFoundError(f"Scenario '{scenario_name}' not found in {scenarios_dir}")
            tmp_path = p
            scenario_yaml = p.read_text(encoding="utf-8")

        # Start output writer
        writer = RunOutputWriter(out_base, run_id)
        writer.write_scenario(scenario_yaml)
        writer.log("run_started", scenario=scenario_name)

        scenario = load_scenario(tmp_path)
        if yaml_text:
            tmp_path.unlink(missing_ok=True)

        runner = ScenarioRunner(scenario, turn_callback=_turn_cb)
        report = await runner.run()

        # Save to DB
        writer.write_report(report)
        writer.reconstruct_call_log(report)
        run_dir = writer.path
        store.save_report(
            report,
            transport_type=scenario.transport.type,
            tags=tags,
            run_id=run_id,
            run_dir=run_dir,
            artifacts_dir=run_dir,
        )

        # Update status to completed
        _update_run_status(store, run_id, "completed" if report.passed else "failed")

        await queue.put(
            {
                "type": "run_completed",
                "run_id": run_id,
                "passed": report.passed,
                "total_turns": report.total_turns,
                "passed_turns": report.passed_turns,
                "ts": time.time(),
            }
        )

    except asyncio.CancelledError:
        _update_run_status(store, run_id, "cancelled")
        await queue.put({"type": "run_cancelled", "run_id": run_id, "ts": time.time()})

    except Exception as e:
        logger.exception("Run %s failed: %s", run_id[:8], e)
        _update_run_status(store, run_id, "failed")
        await queue.put(
            {"type": "run_failed", "run_id": run_id, "error": str(e)[:300], "ts": time.time()}
        )

    finally:
        if writer:
            writer.close()
        await queue.put(None)  # sentinel — closes SSE stream


def _update_run_status(store: ResultStore, run_id: str, status: str) -> None:
    try:
        conn = store._get_conn()
        conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
        conn.commit()
    except Exception:
        pass
