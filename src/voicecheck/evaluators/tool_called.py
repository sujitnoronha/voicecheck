"""tool_called evaluator — assert the agent invoked a specific tool/function.

Reads the ``tool_calls`` list that transports populate via ``emit_tool_call``.
Currently surfaced by the VAPI and Retell transports out of the box; custom
transports plug in by calling ``self.emit_tool_call(name, args, result)``
when they observe the corresponding event on their control channel.

Example YAML:

    expect:
      - type: tool_called
        name: lookup_balance        # required
        args_must_contain:          # all keys required, scalar match
          account_id: "acct-123"
        args_must_not_contain: []   # optional
        min_calls: 1                # optional, defaults to 1
"""

from __future__ import annotations

from typing import Any

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult, ToolCallEvent


class ToolCalledEvaluator(Evaluator):
    """Pass when the agent invoked ``name`` at least ``min_calls`` times.

    Args:
        name: Tool/function name to look for. Required.
        args_must_contain: Optional dict of ``{key: value}`` pairs. The
            evaluator passes only if at least one matched call has every
            listed key set to the listed value.
        args_must_not_contain: Optional dict of ``{key: value}`` pairs.
            The evaluator fails if any matched call has any of these
            forbidden args.
        min_calls: Minimum number of times the tool must have been
            invoked this turn. Defaults to 1.
    """

    def __init__(
        self,
        name: str,
        args_must_contain: dict[str, Any] | None = None,
        args_must_not_contain: dict[str, Any] | None = None,
        min_calls: int = 1,
    ) -> None:
        if not name:
            raise ValueError("tool_called requires a non-empty 'name'")
        self.name = name
        self.args_must_contain = args_must_contain or {}
        self.args_must_not_contain = args_must_not_contain or {}
        self.min_calls = max(1, int(min_calls))

    async def evaluate(self, context: EvalContext) -> EvalResult:
        calls = [c for c in context.tool_calls if c.name == self.name]
        matching = [c for c in calls if self._args_match(c)]
        forbidden = [c for c in calls if self._args_forbidden(c)]

        if forbidden:
            offending = [c.args for c in forbidden]
            return EvalResult(
                evaluator_type="tool_called",
                passed=False,
                score=0.0,
                reason=(
                    f"Tool {self.name!r} called with forbidden args: "
                    f"{offending} (forbid={self.args_must_not_contain})"
                ),
                details={
                    "tool_name": self.name,
                    "forbidden_calls": offending,
                    "all_calls": [self._summarize(c) for c in calls],
                },
            )

        if len(matching) >= self.min_calls:
            return EvalResult(
                evaluator_type="tool_called",
                passed=True,
                score=1.0,
                reason=(
                    f"Tool {self.name!r} called {len(matching)} time(s) (min={self.min_calls})"
                ),
                details={
                    "tool_name": self.name,
                    "matching_calls": [self._summarize(c) for c in matching],
                },
            )

        observed_names = sorted({c.name for c in context.tool_calls})
        return EvalResult(
            evaluator_type="tool_called",
            passed=False,
            score=0.0,
            reason=(
                f"Tool {self.name!r} called {len(matching)}/{self.min_calls} time(s) "
                f"with required args {self.args_must_contain}. "
                f"Observed tool names this turn: {observed_names or '(none)'}"
            ),
            details={
                "tool_name": self.name,
                "min_calls": self.min_calls,
                "matching_calls": [self._summarize(c) for c in matching],
                "all_calls": [self._summarize(c) for c in context.tool_calls],
            },
        )

    def _args_match(self, call: ToolCallEvent) -> bool:
        """True iff the call's args contain every required key/value pair.

        Empty ``args_must_contain`` matches every call — this is what makes
        the simple form (just ``name``) work.
        """
        if not self.args_must_contain:
            return True
        for key, expected in self.args_must_contain.items():
            if call.args.get(key) != expected:
                return False
        return True

    def _args_forbidden(self, call: ToolCallEvent) -> bool:
        """True iff the call has any forbidden key/value pair."""
        for key, forbidden in self.args_must_not_contain.items():
            if call.args.get(key) == forbidden:
                return True
        return False

    @staticmethod
    def _summarize(call: ToolCallEvent) -> dict[str, Any]:
        return {"name": call.name, "args": call.args, "result": call.result}


register_evaluator("tool_called", ToolCalledEvaluator)
