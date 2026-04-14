"""Fact accuracy evaluator — checks agent claims against known ground truth.

Prevents hallucination and confabulation by verifying the agent's response
against a set of known-true and known-false facts.

YAML usage:
    expect:
      - type: fact_accuracy
        known_facts:
          - "Marco is the user's brother"
          - "Marco is 30 years old"
          - "Marco works as a chef in Lyon"
        false_facts:
          - "Marco lives in New York"
        min_score: 0.8
"""

from __future__ import annotations

import logging

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult

from voicecheck.evaluators._llm_service import (
    build_conversation_context,
    call_llm_judge,
)

logger = logging.getLogger("voicecheck.evaluators.fact_accuracy")

_JUDGE_SYSTEM = """You are an expert evaluator for factual accuracy in conversational AI.
You will check whether the agent's response is consistent with known true facts
and does not assert any known false facts.

Respond with ONLY a JSON object:
{
  "score": <float 0.0-1.0>,
  "passed": <bool>,
  "reason": "<brief explanation>",
  "confirmed_facts": ["<facts correctly referenced>"],
  "contradictions": ["<facts contradicted by the response>"],
  "hallucinations": ["<claims not supported by any known fact>"],
  "false_claims": ["<known-false facts asserted by the response>"]
}

Scoring:
- 1.0 = all claims consistent with known facts, no hallucinations
- 0.7-0.9 = minor unsupported claims but no contradictions
- 0.3-0.6 = contains contradictions or significant hallucinations
- 0.0-0.2 = asserts known-false facts or fundamentally contradicts truth"""

_JUDGE_USER = """## Conversation History
{conversation_context}

## Current Turn
User said: "{user_text}"
Agent responded: "{agent_text}"

## Known True Facts
{known_facts_section}

## Known False Facts (agent must NOT assert these)
{false_facts_section}

Check the agent's response for accuracy. Flag contradictions, hallucinations,
and false claims. Only evaluate factual claims — skip greetings and filler.

Minimum passing score: {min_score}"""


class FactAccuracyEvaluator(Evaluator):
    """Verify agent's factual claims against known ground truth."""

    def __init__(
        self,
        known_facts: list[str] | None = None,
        false_facts: list[str] | None = None,
        min_score: float = 0.8,
        model: str = "gpt-4o-mini",
        provider: str = "openai",
    ) -> None:
        self.known_facts = known_facts or []
        self.false_facts = false_facts or []
        self.min_score = min_score
        self.model = model
        self.provider = provider

    async def evaluate(self, context: EvalContext) -> EvalResult:
        known_section = (
            "\n".join(f"- {f}" for f in self.known_facts)
            if self.known_facts
            else "(no known facts provided)"
        )
        false_section = (
            "\n".join(f"- {f}" for f in self.false_facts)
            if self.false_facts
            else "(none)"
        )

        user_prompt = _JUDGE_USER.format(
            conversation_context=build_conversation_context(context.conversation),
            user_text=context.user_text,
            agent_text=context.agent_text,
            known_facts_section=known_section,
            false_facts_section=false_section,
            min_score=self.min_score,
        )

        try:
            result = await call_llm_judge(
                _JUDGE_SYSTEM, user_prompt, model=self.model, provider=self.provider
            )
            score = float(result["score"])
            passed = score >= self.min_score
            reason = result.get("reason", "")
        except Exception as e:
            logger.error("Fact accuracy judge failed: %s", e)
            return EvalResult(
                evaluator_type="fact_accuracy",
                passed=False,
                score=0.0,
                reason=f"Judge error: {e}",
            )

        return EvalResult(
            evaluator_type="fact_accuracy",
            passed=passed,
            score=score,
            reason=reason,
            details={
                "confirmed_facts": result.get("confirmed_facts", []),
                "contradictions": result.get("contradictions", []),
                "hallucinations": result.get("hallucinations", []),
                "false_claims": result.get("false_claims", []),
                "model": self.model,
            },
        )


register_evaluator("fact_accuracy", FactAccuracyEvaluator)
