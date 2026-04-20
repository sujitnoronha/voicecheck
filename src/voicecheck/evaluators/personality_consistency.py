"""Personality consistency evaluator — checks response matches expected traits.

Hybrid evaluator: regex scan for forbidden formatting patterns (markdown,
em dashes, bullet lists) then LLM judge for personality trait adherence.

YAML usage:
    expect:
      - type: personality_consistency
        personality_traits:
          - "Speaks in first person as a real person"
          - "Warm and conversational tone"
          - "No markdown formatting"
        forbidden_patterns:
          - "\\*\\*"
          - "^\\s*[-*]\\s"
          - "\\u2014"
        min_score: 0.7
"""

from __future__ import annotations

import logging
import re

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult
from voicecheck.evaluators._llm_service import (
    build_conversation_context,
    call_llm_judge,
)

logger = logging.getLogger("voicecheck.evaluators.personality_consistency")

_JUDGE_SYSTEM = """You are an expert evaluator for conversational agent personality consistency.
You will score the agent's response against a list of expected personality traits.

Respond with ONLY a JSON object:
{
  "score": <float 0.0-1.0>,
  "passed": <bool>,
  "reason": "<brief overall assessment>",
  "trait_scores": [
    {"trait": "<trait description>", "score": <float>, "note": "<brief note>"}
  ]
}

The overall score should be the average of individual trait scores.
A trait scores 1.0 if fully matched, 0.5 if partially, 0.0 if violated."""

_JUDGE_USER = """## Conversation History
{conversation_context}

## Current Turn
User said: "{user_text}"
Agent responded: "{agent_text}"

## Expected Personality Traits
{traits_list}

## Formatting Violations Already Detected
{violations_section}

Score each trait individually, then provide an overall score.
Minimum passing score: {min_score}"""


class PersonalityConsistencyEvaluator(Evaluator):
    """Check if the agent's response matches expected personality traits."""

    def __init__(
        self,
        personality_traits: list[str] | None = None,
        forbidden_patterns: list[str] | None = None,
        min_score: float = 0.7,
        model: str = "gpt-4o-mini",
        provider: str = "openai",
    ) -> None:
        self.personality_traits = personality_traits or []
        self.forbidden_patterns = forbidden_patterns or []
        self.min_score = min_score
        self.model = model
        self.provider = provider

    async def evaluate(self, context: EvalContext) -> EvalResult:
        # Deterministic: check forbidden formatting patterns
        violations: list[str] = []
        for pattern in self.forbidden_patterns:
            if re.search(pattern, context.agent_text, re.MULTILINE):
                violations.append(pattern)

        # If no traits to judge, just use deterministic result
        if not self.personality_traits:
            passed = len(violations) == 0
            score = 1.0 if passed else 0.0
            return EvalResult(
                evaluator_type="personality_consistency",
                passed=passed,
                score=score,
                reason=f"{'No violations' if passed else f'{len(violations)} formatting violation(s)'}",
                details={"violations": violations},
            )

        # LLM judge for trait scoring
        traits_list = "\n".join(f"- {t}" for t in self.personality_traits)
        violations_section = (
            "\n".join(f"- Matched forbidden pattern: {v}" for v in violations)
            if violations
            else "(none)"
        )

        user_prompt = _JUDGE_USER.format(
            conversation_context=build_conversation_context(context.conversation),
            user_text=context.user_text,
            agent_text=context.agent_text,
            traits_list=traits_list,
            violations_section=violations_section,
            min_score=self.min_score,
        )

        try:
            result = await call_llm_judge(
                _JUDGE_SYSTEM, user_prompt, model=self.model, provider=self.provider
            )
            llm_score = float(result["score"])
            trait_scores = result.get("trait_scores", [])
            reason = result.get("reason", "")
        except Exception as e:
            logger.error("Personality consistency judge failed: %s", e)
            return EvalResult(
                evaluator_type="personality_consistency",
                passed=False,
                score=0.0,
                reason=f"Judge error: {e}",
            )

        # Penalize for formatting violations: each violation reduces score by 0.15
        penalty = len(violations) * 0.15
        final_score = max(0.0, llm_score - penalty)
        passed = final_score >= self.min_score

        return EvalResult(
            evaluator_type="personality_consistency",
            passed=passed,
            score=final_score,
            reason=reason,
            details={
                "trait_scores": trait_scores,
                "formatting_violations": violations,
                "llm_score": llm_score,
                "penalty": penalty,
                "model": self.model,
            },
        )


register_evaluator("personality_consistency", PersonalityConsistencyEvaluator)
