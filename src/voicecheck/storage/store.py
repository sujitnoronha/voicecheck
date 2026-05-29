"""SQLite-backed result storage for VoiceCheck.

Stores every test run with full turn-level detail so you can:
- Track pass/fail trends over time
- Compare latency across runs
- View conversation transcripts
- Generate dashboards
"""

from __future__ import annotations

import functools
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voicecheck.core.scenario import ScenarioReport

logger = logging.getLogger("voicecheck.storage")


def _synchronized(method):
    # Serialize access to the shared sqlite connection. The connection is used
    # from both the async request handlers and threadpool-run sync handlers, so
    # without this a concurrent commit could flush save_report's half-written
    # run+turns transaction. The lock is an RLock — reentrant for methods that
    # call other synchronized methods (e.g. get_all_scenario_stats).
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper

DEFAULT_DB_PATH = Path.home() / ".voicecheck" / "results.db"
DEFAULT_OUTPUT_DIR = Path.home() / ".voicecheck"

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
    artifacts_dir TEXT,
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
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        # Migration: add conversation_eval column if upgrading from older schema
        try:
            conn.execute("SELECT conversation_eval FROM runs LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE runs ADD COLUMN conversation_eval TEXT")
        # Migration: add artifacts_dir column (audio replay support)
        try:
            conn.execute("SELECT artifacts_dir FROM runs LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE runs ADD COLUMN artifacts_dir TEXT")
        # Migration: add run_dir column (per-run output directory)
        try:
            conn.execute("SELECT run_dir FROM runs LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE runs ADD COLUMN run_dir TEXT")
        # Migration: add status/timing columns for async runs (Phase 5)
        try:
            conn.execute("SELECT status FROM runs LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE runs ADD COLUMN status TEXT DEFAULT 'completed'")
        conn.commit()

        # Initialise baselines table
        from voicecheck.storage.baselines import BaselineStore

        self._baselines = BaselineStore(conn, self._lock)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    @property
    def baselines(self):
        return self._baselines

    @_synchronized
    def save_report(
        self,
        report: ScenarioReport,
        transport_type: str = "",
        tags: list[str] | None = None,
        artifacts_dir: str | Path | None = None,
        run_id: str | None = None,
        run_dir: str | Path | None = None,
    ) -> str:
        """Save a scenario report to the database.

        Args:
            artifacts_dir: Optional path to a directory holding turn WAVs and
                full_conversation.wav for this run. When set, the dashboard
                exposes per-turn audio replay for this run.
            run_id: Optional pre-generated run ID. Useful when the caller needs
                to know the ID before saving (e.g. to write artifacts to a
                run-scoped directory). If omitted, a UUID is generated.

        Returns:
            The run ID (UUID string).
        """
        conn = self._get_conn()
        run_id = run_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        artifacts_dir_str = str(Path(artifacts_dir).resolve()) if artifacts_dir else None
        run_dir_str = str(Path(run_dir).resolve()) if run_dir else None

        # Compute averages
        latencies_fb = [
            t.metrics.first_byte_ms for t in report.turns if t.metrics.first_byte_ms > 0
        ]
        latencies_total = [t.metrics.total_ms for t in report.turns if t.metrics.total_ms > 0]
        avg_fb = sum(latencies_fb) / len(latencies_fb) if latencies_fb else 0.0
        avg_total = sum(latencies_total) / len(latencies_total) if latencies_total else 0.0

        conn.execute(
            """INSERT OR REPLACE INTO runs (id, scenario_name, passed, total_turns, passed_turns,
               avg_first_byte_ms, avg_total_ms, transport_type, tags, conversation_eval,
               artifacts_dir, run_dir, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                artifacts_dir_str,
                run_dir_str,
                "completed",
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
    def get_call_log(self, run_id: str) -> list[dict[str, Any]] | None:
        """Return parsed call_log.jsonl events for a run, or None if unavailable."""
        conn = self._get_conn()
        row = conn.execute("SELECT run_dir FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row or not row["run_dir"]:
            return None
        log_path = Path(row["run_dir"]) / "call_log.jsonl"
        if not log_path.exists():
            return None
        events = []
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events

    @_synchronized
    def get_scenario_file(self, run_id: str) -> str | None:
        """Return the scenario.yaml snapshot content for a run, or None."""
        conn = self._get_conn()
        row = conn.execute("SELECT run_dir FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row or not row["run_dir"]:
            return None
        p = Path(row["run_dir"]) / "scenario.yaml"
        return p.read_text(encoding="utf-8") if p.exists() else None

    @_synchronized
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
    def get_run_artifacts(self, run_id: str) -> dict[str, Any] | None:
        """Return artifact WAVs available for a run.

        Returns a dict shaped like:
            {
              "dir": "/abs/path/to/artifacts",
              "full_conversation": "full_conversation.wav" | None,
              "turns": [
                 {"index": 0, "user": "turn_1_user.wav" | None,
                              "agent": "turn_1_agent.wav" | None},
                 ...
              ],
            }

        Only filenames directly inside the artifacts dir are returned (no
        subpaths), so callers can safely serve them by name with an allowlist
        check. Returns None if the run does not exist or has no artifacts_dir
        recorded. Missing files on disk are surfaced as None entries rather
        than errors — the UI can still render the transcript.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT artifacts_dir, total_turns FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not row or not row["artifacts_dir"]:
            return None

        dir_path = Path(row["artifacts_dir"])
        if not dir_path.is_dir():
            return None

        full_name = "full_conversation.wav"
        full = full_name if (dir_path / full_name).is_file() else None

        turns: list[dict[str, Any]] = []
        for i in range(row["total_turns"]):
            n = i + 1
            user_name = f"turn_{n}_user.wav"
            agent_name = f"turn_{n}_agent.wav"
            turns.append(
                {
                    "index": i,
                    "user": user_name if (dir_path / user_name).is_file() else None,
                    "agent": agent_name if (dir_path / agent_name).is_file() else None,
                }
            )

        return {"dir": str(dir_path), "full_conversation": full, "turns": turns}

    @_synchronized
    def delete_run(self, run_id: str) -> bool:
        """Delete a run and its turns."""
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        return cursor.rowcount > 0

    @_synchronized
    def update_run_status(self, run_id: str, status: str) -> None:
        """Set the status of a run (e.g. completed/failed/cancelled)."""
        conn = self._get_conn()
        conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
        conn.commit()

    @_synchronized
    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
