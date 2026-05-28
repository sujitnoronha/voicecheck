"""tool_sequence evaluator — assert tools were called in a specific order.

Operates on the current turn's tool calls (``context.tool_calls``).
Cross-turn sequence assertions aren't supported in v1 — model them as
multiple per-turn ``tool_sequence`` checks instead.

Example YAML:

    expect:
      - type: tool_sequence
        sequence: [authenticate, lookup_account, lookup_balance]
        mode: subsequence   # 'subsequence' (default) or 'strict'

``mode: subsequence`` matches if the listed tools appear in the given
order anywhere in the call list (other tools may appear between them).
``mode: strict`` requires the listed tools to be the *only* calls in
the turn, and in exactly the given order.
"""

from __future__ import annotations

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult

_VALID_MODES = ("subsequence", "strict")


class ToolSequenceEvaluator(Evaluator):
    """Pass when the agent's tool calls match the expected order.

    Args:
        sequence: Ordered list of tool names that must appear.
        mode: ``"subsequence"`` (default) allows other tools between
            the listed ones; ``"strict"`` requires the call list to
            equal the sequence exactly.
    """

    def __init__(self, sequence: list[str], mode: str = "subsequence") -> None:
        if not sequence:
            raise ValueError("tool_sequence requires a non-empty 'sequence'")
        if mode not in _VALID_MODES:
            raise ValueError(f"tool_sequence 'mode' must be one of {_VALID_MODES}, got {mode!r}")
        self.sequence = list(sequence)
        self.mode = mode

    async def evaluate(self, context: EvalContext) -> EvalResult:
        observed = [c.name for c in context.tool_calls]

        if self.mode == "strict":
            return self._evaluate_strict(observed)
        return self._evaluate_subsequence(observed)

    def _evaluate_strict(self, observed: list[str]) -> EvalResult:
        passed = observed == self.sequence
        reason = (
            "Tool calls match expected sequence exactly"
            if passed
            else f"Expected exactly {self.sequence}, observed {observed}"
        )
        return EvalResult(
            evaluator_type="tool_sequence",
            passed=passed,
            score=1.0 if passed else 0.0,
            reason=reason,
            details={
                "mode": "strict",
                "expected": self.sequence,
                "observed": observed,
            },
        )

    def _evaluate_subsequence(self, observed: list[str]) -> EvalResult:
        # Two-pointer scan: walk ``observed`` and advance through
        # ``sequence`` whenever the current expected name shows up.
        expected_idx = 0
        for name in observed:
            if expected_idx < len(self.sequence) and name == self.sequence[expected_idx]:
                expected_idx += 1

        matched = expected_idx
        total = len(self.sequence)
        passed = matched == total
        score = matched / total if total else 1.0

        if passed:
            reason = f"All {total} expected tools called in order"
        else:
            missing = self.sequence[matched:]
            reason = (
                f"Sequence broken — matched {matched}/{total} in order. "
                f"Next expected: {self.sequence[matched]!r}. "
                f"Missing tail: {missing}. Observed: {observed}"
            )
        return EvalResult(
            evaluator_type="tool_sequence",
            passed=passed,
            score=score,
            reason=reason,
            details={
                "mode": "subsequence",
                "expected": self.sequence,
                "observed": observed,
                "matched_in_order": matched,
            },
        )


register_evaluator("tool_sequence", ToolSequenceEvaluator)
