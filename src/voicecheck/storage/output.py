"""Per-run output directory — report.json, scenario.yaml, call_log.jsonl."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TextIO


class RunOutputWriter:
    """Writes structured artifacts for a single run to ~/.voicecheck/runs/{run_id}/."""

    def __init__(self, output_dir: Path, run_id: str) -> None:
        self.run_dir = output_dir / "runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._log: TextIO = (self.run_dir / "call_log.jsonl").open("w", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self.run_dir

    def log(self, event: str, **kwargs: Any) -> None:
        entry = {"event": event, "ts": time.time(), **kwargs}
        self._log.write(json.dumps(entry) + "\n")
        self._log.flush()

    def write_scenario(self, yaml_text: str) -> None:
        (self.run_dir / "scenario.yaml").write_text(yaml_text, encoding="utf-8")

    def write_report(self, report: Any) -> None:
        from voicecheck.core.report import write_json_report

        write_json_report(report, self.run_dir / "report.json")

    def reconstruct_call_log(self, report: Any) -> None:
        """Write call_log.jsonl from a completed ScenarioReport (post-hoc)."""
        self.log("run_started", scenario=report.scenario_name)
        for turn in report.turns:
            self.log("turn_started", turn=turn.turn_index)
            if turn.user_text:
                self.log("user_text", turn=turn.turn_index, text=turn.user_text)
            for tc in turn.tool_calls or []:
                self.log(
                    "tool_call",
                    turn=turn.turn_index,
                    name=tc.name,
                    args=tc.args,
                    result=tc.result,
                    error=tc.error,
                )
            self.log(
                "agent_text",
                turn=turn.turn_index,
                text=turn.agent_text or "",
                first_byte_ms=turn.metrics.first_byte_ms,
                total_ms=turn.metrics.total_ms,
            )
            for ev in turn.eval_results:
                self.log(
                    "eval_result",
                    turn=turn.turn_index,
                    type=ev.evaluator_type,
                    passed=ev.passed,
                    score=ev.score,
                    reason=ev.reason,
                )
            if turn.error:
                self.log("turn_error", turn=turn.turn_index, error=turn.error)
            self.log("turn_completed", turn=turn.turn_index, passed=turn.passed)
        if report.conversation_eval:
            self.log("conversation_eval", **report.conversation_eval)
        self.log(
            "run_completed",
            passed=report.passed,
            total_turns=report.total_turns,
            passed_turns=report.passed_turns,
        )

    def close(self) -> None:
        self._log.close()

    def read_call_log(self) -> list[dict[str, Any]]:
        """Read persisted call log back as a list of events."""
        path = self.run_dir / "call_log.jsonl"
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events
