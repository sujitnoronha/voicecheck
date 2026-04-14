"""LLM-as-judge evaluator — uses an LLM to score agent responses."""

from __future__ import annotations

import logging

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult
from voicecheck.evaluators._llm_service import (
    build_conversation_context,
    call_llm_judge,
    default_model,
)

logger = logging.getLogger("voicecheck.evaluators.llm_judge")

_SYSTEM = """You are an expert evaluator for voice agent conversations.
You will be given a conversation turn and evaluation criteria.
Score the agent's response on a scale of 0.0 to 1.0.

Respond with ONLY a JSON object (no markdown, no explanation outside JSON):
{
  "score": <float 0.0-1.0>,
  "passed": <bool>,
  "reason": "<brief explanation>"
}"""

_USER = """## Conversation Context
{conversation_context}

## Current Turn
User said: "{user_text}"
Agent responded: "{agent_text}"

## Evaluation Criteria
{criteria}

## Minimum passing score: {min_score}

Score the agent's response:"""


class LLMJudgeEvaluator(Evaluator):
    """Use an LLM (OpenAI or Anthropic) to judge agent response quality."""

    def __init__(
        self,
        criteria: str = "",
        min_score: float = 0.7,
        provider: str = "openai",
        model: str = "",
    ) -> None:
        self.criteria = criteria
        self.min_score = min_score
        self.provider = provider
        self.model = model or default_model(provider)

    async def evaluate(self, context: EvalContext) -> EvalResult:
        user_prompt = _USER.format(
            conversation_context=build_conversation_context(context.conversation),
            user_text=context.user_text,
            agent_text=context.agent_text,
            criteria=self.criteria,
            min_score=self.min_score,
        )

        try:
            result = await call_llm_judge(
                _SYSTEM, user_prompt, model=self.model, provider=self.provider
            )
            score = float(result["score"])
            passed = score >= self.min_score
            reason = result.get("reason", "")
        except Exception as e:
            logger.error("LLM judge failed: %s", e)
            return EvalResult(
                evaluator_type="llm_judge",
                passed=False,
                score=0.0,
                reason=f"LLM judge error: {e}",
            )

        return EvalResult(
            evaluator_type="llm_judge",
            passed=passed,
            score=score,
            reason=reason,
            details={
                "criteria": self.criteria,
                "min_score": self.min_score,
                "model": self.model,
                "provider": self.provider,
            },
        )


register_evaluator("llm_judge", LLMJudgeEvaluator)
