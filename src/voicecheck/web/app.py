"""FastAPI application for VoiceCheck live dashboard."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from voicecheck.storage.store import ResultStore

logger = logging.getLogger("voicecheck.web")

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def create_app(db_path: str | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    store = ResultStore(db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        store.close()

    app = FastAPI(title="VoiceCheck Dashboard", version="0.1.0", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── HTML page routes ────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    @app.get("/runs", response_class=HTMLResponse)
    async def all_runs_page(request: Request):
        return templates.TemplateResponse(request, "runs.html", {
            "scenario_name": None,
        })

    @app.get("/scenario/{name:path}", response_class=HTMLResponse)
    async def scenario_page(request: Request, name: str):
        return templates.TemplateResponse(request, "runs.html", {
            "scenario_name": name,
        })

    @app.get("/run/{run_id}", response_class=HTMLResponse)
    async def run_detail_page(request: Request, run_id: str):
        return templates.TemplateResponse(request, "run_detail.html", {
            "run_id": run_id,
        })

    @app.get("/compare", response_class=HTMLResponse)
    async def compare_page(request: Request):
        return templates.TemplateResponse(request, "compare.html")

    # ── JSON API routes ─────────────────────────────────────────

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

    @app.get("/api/runs")
    async def api_runs(
        scenario: str | None = Query(None),
        limit: int = Query(50, le=200),
        offset: int = Query(0, ge=0),
    ):
        runs = store.list_runs(scenario_name=scenario, limit=limit, offset=offset)
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

    return app
