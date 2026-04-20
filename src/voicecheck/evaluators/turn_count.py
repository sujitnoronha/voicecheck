"""Turn count evaluator — validates conversation flow structure."""

from __future__ import annotations

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult


class TurnCountEvaluator(Evaluator):
    """Validate that the agent responded (non-empty) at this turn."""

    def __init__(
        self,
        min_words: int = 1,
        max_words: int = 0,
    ) -> None:
        self.min_words = min_words
        self.max_words = max_words

    async def evaluate(self, context: EvalContext) -> EvalResult:
        word_count = len((context.agent_text or "").split())
        reasons: list[str] = []
        passed = True

        if word_count < self.min_words:
            passed = False
            reasons.append(f"Agent response too short: {word_count} words (min {self.min_words})")

        if self.max_words > 0 and word_count > self.max_words:
            passed = False
            reasons.append(f"Agent response too long: {word_count} words (max {self.max_words})")

        if not reasons:
            reasons.append(f"Response length OK ({word_count} words)")

        return EvalResult(
            evaluator_type="turn_count",
            passed=passed,
            score=1.0 if passed else 0.0,
            reason="; ".join(reasons),
            details={"word_count": word_count},
        )


register_evaluator("turn_count", TurnCountEvaluator)
