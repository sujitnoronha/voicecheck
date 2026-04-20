"""SQLite-backed result storage for VoiceCheck.

Stores every test run with full turn-level detail so you can:
- Track pass/fail trends over time
- Compare latency across runs
- View conversation transcripts
- Generate dashboards
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voicecheck.core.scenario import ScenarioReport

logger = logging.getLogger("voicecheck.storage")

DEFAULT_DB_PATH = Path.home() / ".voicecheck" / "results.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    scenario_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    total_turns INTEGER NOT NULL,
    passed_turns INTEGER NOT NULL,
    avg_first_byte_ms REAL,
    avg_total_ms REAL,
    transport_type TEXT,
    tags TEXT,
    conversation_eval TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    user_text TEXT NOT NULL,
    agent_text TEXT,
    first_byte_ms REAL,
    total_ms REAL,
    evaluations TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_scenario ON runs(scenario_name);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
CREATE INDEX IF NOT EXISTS idx_turns_run ON turns(run_id);
"""


class ResultStore:
    """Persistent storage for VoiceCheck test results."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        # Migration: add conversation_eval column if upgrading from older schema
        try:
            conn.execute("SELECT conversation_eval FROM runs LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE runs ADD COLUMN conversation_eval TEXT")
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def save_report(
        self,
        report: ScenarioReport,
        transport_type: str = "",
        tags: list[str] | None = None,
    ) -> str:
        """Save a scenario report to the database.

        Returns:
            The run ID (UUID string).
        """
        conn = self._get_conn()
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Compute averages
        latencies_fb = [
            t.metrics.first_byte_ms for t in report.turns if t.metrics.first_byte_ms > 0
        ]
        latencies_total = [t.metrics.total_ms for t in report.turns if t.metrics.total_ms > 0]
        avg_fb = sum(latencies_fb) / len(latencies_fb) if latencies_fb else 0.0
        avg_total = sum(latencies_total) / len(latencies_total) if latencies_total else 0.0

        conn.execute(
            """INSERT INTO runs (id, scenario_name, passed, total_turns, passed_turns,
               avg_first_byte_ms, avg_total_ms, transport_type, tags, conversation_eval,
               created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                report.scenario_name,
                int(report.passed),
                report.total_turns,
                report.passed_turns,
                avg_fb,
                avg_total,
                transport_type,
                json.dumps(tags or []),
                json.dumps(report.conversation_eval) if report.conversation_eval else None,
                now,
            ),
        )

        for turn in report.turns:
            evals = [
                {
                    "type": r.evaluator_type,
                    "passed": r.passed,
                    "score": r.score,
                    "reason": r.reason,
                    "details": r.details,
                }
                for r in turn.eval_results
            ]
            conn.execute(
                """INSERT INTO turns (id, run_id, turn_index, passed, user_text, agent_text,
                   first_byte_ms, total_ms, evaluations, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    run_id,
                    turn.turn_index,
                    int(turn.passed),
                    turn.user_text,
                    turn.agent_text,
                    turn.metrics.first_byte_ms,
                    turn.metrics.total_ms,
                    json.dumps(evals),
                    now,
                ),
            )

        conn.commit()
        logger.info("Saved run %s (%s)", run_id[:8], report.scenario_name)
        return run_id

    def list_runs(
        self,
        scenario_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List recent test runs, optionally filtered by scenario."""
        conn = self._get_conn()
        if scenario_name:
            rows = conn.execute(
                "SELECT * FROM runs WHERE scenario_name = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (scenario_name, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Get a single run with its turns."""
        conn = self._get_conn()
        run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not run_row:
            return None

        turns = conn.execute(
            "SELECT * FROM turns WHERE run_id = ? ORDER BY turn_index",
            (run_id,),
        ).fetchall()

        result = dict(run_row)
        result["turns"] = [dict(t) for t in turns]
        for t in result["turns"]:
            t["evaluations"] = json.loads(t["evaluations"])
        result["tags"] = json.loads(result["tags"])
        if result.get("conversation_eval"):
            result["conversation_eval"] = json.loads(result["conversation_eval"])
        return result

    def get_scenario_history(self, scenario_name: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get historical runs for a scenario (for trend charts)."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT id, passed, total_turns, passed_turns,
                      avg_first_byte_ms, avg_total_ms, created_at
               FROM runs WHERE scenario_name = ?
               ORDER BY created_at DESC LIMIT ?""",
            (scenario_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_scenarios(self) -> list[dict[str, Any]]:
        """Get summary stats per scenario."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT scenario_name,
                      COUNT(*) as run_count,
                      SUM(passed) as pass_count,
                      AVG(avg_first_byte_ms) as avg_latency,
                      MAX(created_at) as last_run
               FROM runs GROUP BY scenario_name
               ORDER BY last_run DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def count_runs(self, scenario_name: str | None = None) -> int:
        """Count total runs, optionally filtered by scenario."""
        conn = self._get_conn()
        if scenario_name:
            row = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE scenario_name = ?",
                (scenario_name,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
        return row[0]

    def get_scenario_percentiles(
        self,
        scenario_name: str,
        metric: str = "first_byte_ms",
    ) -> dict[str, float]:
        """Compute latency percentiles (p50, p95, p99) for a scenario."""
        conn = self._get_conn()
        _ALLOWED_METRICS = {"first_byte_ms", "total_ms"}
        if metric not in _ALLOWED_METRICS:
            raise ValueError(f"Invalid metric: {metric!r}. Allowed: {_ALLOWED_METRICS}")
        col = metric
        rows = conn.execute(
            f"""SELECT t.{col} FROM turns t
                JOIN runs r ON t.run_id = r.id
                WHERE r.scenario_name = ? AND t.{col} > 0
                ORDER BY t.{col}""",
            (scenario_name,),
        ).fetchall()
        values = [r[0] for r in rows]
        if not values:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        def _percentile(sorted_vals: list[float], p: float) -> float:
            idx = int(len(sorted_vals) * p)
            return sorted_vals[min(idx, len(sorted_vals) - 1)]

        return {
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
            "p99": _percentile(values, 0.99),
        }

    def get_all_scenario_stats(self) -> list[dict[str, Any]]:
        """Get scenarios with extended stats including percentiles."""
        scenarios = self.get_scenarios()
        for s in scenarios:
            fb = self.get_scenario_percentiles(s["scenario_name"], "first_byte_ms")
            s["p50_first_byte_ms"] = fb["p50"]
            s["p95_first_byte_ms"] = fb["p95"]
            s["p99_first_byte_ms"] = fb["p99"]
            total = self.get_scenario_percentiles(s["scenario_name"], "total_ms")
            s["p50_total_ms"] = total["p50"]
            s["p95_total_ms"] = total["p95"]
            s["p99_total_ms"] = total["p99"]
        return scenarios

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and its turns."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
