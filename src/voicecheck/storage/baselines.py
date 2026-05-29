"""Baseline storage and regression comparison for VoiceCheck."""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _synchronized(method):
    # Guard the shared sqlite connection (held jointly with ResultStore) so a
    # baseline write cannot commit mid-way through ResultStore.save_report.
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper

DEFAULT_TOLERANCE: dict[str, dict[str, Any]] = {
    "pass_rate": {"max_drop": 0.0, "fail_ci": True},
    "p95_first_byte_ms": {"max_increase_pct": 20.0, "fail_ci": False},
    "p95_total_ms": {"max_increase_pct": 20.0, "fail_ci": False},
    "avg_first_byte_ms": {"max_increase_pct": 20.0, "fail_ci": False},
    "avg_total_ms": {"max_increase_pct": 20.0, "fail_ci": False},
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS baselines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scenario_name TEXT NOT NULL,
    run_id TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    tolerance_json TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(scenario_name, name)
);
CREATE INDEX IF NOT EXISTS idx_baselines_scenario ON baselines(scenario_name);
"""


@dataclass
class BaselineMetrics:
    pass_rate: float = 0.0
    passed_turns: int = 0
    total_turns: int = 0
    avg_first_byte_ms: float = 0.0
    avg_total_ms: float = 0.0
    p50_first_byte_ms: float = 0.0
    p95_first_byte_ms: float = 0.0
    p50_total_ms: float = 0.0
    p95_total_ms: float = 0.0
    evaluator_pass_rates: dict[str, float] = field(default_factory=dict)
    tool_call_count_avg: float = 0.0
    conversation_eval_passed: bool | None = None


@dataclass
class Regression:
    metric: str
    baseline_value: float
    current_value: float
    delta: float
    threshold_desc: str
    is_ci_failure: bool
    description: str


def metrics_from_report(report: Any) -> BaselineMetrics:
    """Extract BaselineMetrics from a ScenarioReport."""
    turns = report.turns
    total = len(turns)
    passed = sum(1 for t in turns if t.passed)

    fbs = [t.metrics.first_byte_ms for t in turns if t.metrics.first_byte_ms > 0]
    tots = [t.metrics.total_ms for t in turns if t.metrics.total_ms > 0]
    avg_fb = sum(fbs) / len(fbs) if fbs else 0.0
    avg_total = sum(tots) / len(tots) if tots else 0.0

    def _pct(vals: list[float], p: float) -> float:
        if not vals:
            return 0.0
        s = sorted(vals)
        return s[min(int(len(s) * p), len(s) - 1)]

    eval_counts: dict[str, list[bool]] = {}
    tool_call_counts: list[int] = []
    for t in turns:
        for ev in t.eval_results:
            eval_counts.setdefault(ev.evaluator_type, []).append(ev.passed)
        tool_call_counts.append(len(t.tool_calls) if t.tool_calls else 0)

    eval_pass_rates = {k: sum(v) / len(v) for k, v in eval_counts.items()}

    conv_passed: bool | None = None
    if report.conversation_eval:
        conv_passed = report.conversation_eval.get("overall_passed")

    return BaselineMetrics(
        pass_rate=passed / total if total else 0.0,
        passed_turns=passed,
        total_turns=total,
        avg_first_byte_ms=avg_fb,
        avg_total_ms=avg_total,
        p50_first_byte_ms=_pct(fbs, 0.50),
        p95_first_byte_ms=_pct(fbs, 0.95),
        p50_total_ms=_pct(tots, 0.50),
        p95_total_ms=_pct(tots, 0.95),
        evaluator_pass_rates=eval_pass_rates,
        tool_call_count_avg=sum(tool_call_counts) / len(tool_call_counts)
        if tool_call_counts
        else 0.0,
        conversation_eval_passed=conv_passed,
    )


def compare_metrics(
    baseline: BaselineMetrics,
    current: BaselineMetrics,
    tolerance: dict[str, Any] | None = None,
) -> list[Regression]:
    """Return regressions between baseline and current metrics."""
    tol = {**DEFAULT_TOLERANCE, **(tolerance or {})}
    regressions: list[Regression] = []

    # Pass rate: any drop beyond max_drop is a regression
    pr_tol = tol.get("pass_rate", {"max_drop": 0.0, "fail_ci": True})
    pr_delta = current.pass_rate - baseline.pass_rate
    if pr_delta < -pr_tol.get("max_drop", 0.0):
        regressions.append(
            Regression(
                metric="pass_rate",
                baseline_value=baseline.pass_rate,
                current_value=current.pass_rate,
                delta=pr_delta,
                threshold_desc=f"max_drop={pr_tol['max_drop']}",
                is_ci_failure=pr_tol.get("fail_ci", True),
                description=(
                    f"Pass rate dropped {abs(pr_delta) * 100:.1f}% "
                    f"(baseline {baseline.pass_rate * 100:.0f}% → current {current.pass_rate * 100:.0f}%)"
                ),
            )
        )

    # Latency: percentage increase beyond max_increase_pct is a regression
    for metric, attr in (
        ("p95_first_byte_ms", "p95_first_byte_ms"),
        ("p95_total_ms", "p95_total_ms"),
        ("avg_first_byte_ms", "avg_first_byte_ms"),
        ("avg_total_ms", "avg_total_ms"),
    ):
        t = tol.get(metric)
        if not t:
            continue
        base_val: float = getattr(baseline, attr)
        curr_val: float = getattr(current, attr)
        if base_val <= 0:
            continue
        pct = (curr_val - base_val) / base_val * 100
        max_pct: float = t.get("max_increase_pct", 20.0)
        if pct > max_pct:
            regressions.append(
                Regression(
                    metric=metric,
                    baseline_value=base_val,
                    current_value=curr_val,
                    delta=curr_val - base_val,
                    threshold_desc=f"max_increase={max_pct}%",
                    is_ci_failure=t.get("fail_ci", False),
                    description=(
                        f"{metric} increased {pct:.1f}% "
                        f"(baseline {base_val:.0f}ms → current {curr_val:.0f}ms)"
                    ),
                )
            )

    return regressions


def format_comparison_table(regressions: list[Regression], baseline_name: str) -> str:
    """Return a human-readable regression table for CLI output."""
    if not regressions:
        return f"  ✓ No regressions vs baseline '{baseline_name}'"

    lines = [f"  Regressions vs baseline '{baseline_name}':"]
    for r in regressions:
        ci_flag = " [CI FAILURE]" if r.is_ci_failure else " [warn]"
        lines.append(f"    {ci_flag} {r.description}")
    return "\n".join(lines)


class BaselineStore:
    """CRUD for baselines table — can share a DB connection with ResultStore."""

    def __init__(
        self, conn: sqlite3.Connection, lock: threading.RLock | None = None
    ) -> None:
        self._conn = conn
        self._lock = lock or threading.RLock()
        self._ensure_schema()

    @_synchronized
    def _ensure_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @_synchronized
    def save(
        self,
        name: str,
        scenario_name: str,
        run_id: str,
        metrics: BaselineMetrics,
        tolerance: dict[str, Any] | None = None,
        notes: str = "",
    ) -> str:
        bid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT OR REPLACE INTO baselines
               (id, name, scenario_name, run_id, metrics_json, tolerance_json, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bid,
                name,
                scenario_name,
                run_id,
                json.dumps(asdict(metrics)),
                json.dumps(tolerance or {}),
                notes,
                now,
            ),
        )
        self._conn.commit()
        return bid

    @_synchronized
    def get(self, name: str, scenario_name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM baselines WHERE scenario_name = ? AND name = ?",
            (scenario_name, name),
        ).fetchone()
        return dict(row) if row else None

    @_synchronized
    def list(self, scenario_name: str | None = None) -> list[dict[str, Any]]:
        if scenario_name:
            rows = self._conn.execute(
                "SELECT * FROM baselines WHERE scenario_name = ? ORDER BY created_at DESC",
                (scenario_name,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM baselines ORDER BY scenario_name, created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def delete(self, name: str, scenario_name: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM baselines WHERE scenario_name = ? AND name = ?",
            (scenario_name, name),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def metrics_for(self, name: str, scenario_name: str) -> BaselineMetrics | None:
        row = self.get(name, scenario_name)
        if not row:
            return None
        m = json.loads(row["metrics_json"])
        return BaselineMetrics(**m)
