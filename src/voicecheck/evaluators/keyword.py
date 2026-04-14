"""Keyword evaluator — checks for required/forbidden words in agent response."""

from __future__ import annotations

import re

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult


class KeywordEvaluator(Evaluator):
    """Check that agent response contains (or doesn't contain) specific keywords."""

    def __init__(
        self,
        must_contain: list[str] | None = None,
        must_not_contain: list[str] | None = None,
        case_sensitive: bool = False,
    ) -> None:
        self.must_contain = must_contain or []
        self.must_not_contain = must_not_contain or []
        self.case_sensitive = case_sensitive

    async def evaluate(self, context: EvalContext) -> EvalResult:
        text = context.agent_text or ""
        if not self.case_sensitive:
            text = text.lower()

        reasons: list[str] = []
        passed = True
        missing: list[str] = []
        found_forbidden: list[str] = []

        for keyword in self.must_contain:
            pattern = keyword if self.case_sensitive else keyword.lower()
            if not re.search(re.escape(pattern), text):
                missing.append(keyword)

        for keyword in self.must_not_contain:
            pattern = keyword if self.case_sensitive else keyword.lower()
            if re.search(re.escape(pattern), text):
                found_forbidden.append(keyword)

        if missing:
            passed = False
            reasons.append(f"Missing required keywords: {missing}")

        if found_forbidden:
            passed = False
            reasons.append(f"Found forbidden keywords: {found_forbidden}")

        if not reasons:
            reasons.append("All keyword checks passed")

        total_checks = len(self.must_contain) + len(self.must_not_contain)
        failed_checks = len(missing) + len(found_forbidden)
        score = (total_checks - failed_checks) / total_checks if total_checks > 0 else 1.0

        return EvalResult(
            evaluator_type="keyword",
            passed=passed,
            score=score,
            reason="; ".join(reasons),
            details={
                "missing": missing,
                "found_forbidden": found_forbidden,
                "agent_text_preview": (context.agent_text or "")[:200],
            },
        )


register_evaluator("keyword", KeywordEvaluator)
