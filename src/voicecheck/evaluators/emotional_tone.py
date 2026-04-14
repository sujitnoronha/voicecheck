"""Emotional tone evaluator — scores agent responses for emotional quality.

Uses LLM-as-judge to detect emotional tones in the agent's response
and check them against expected and forbidden emotion lists.

YAML usage:
    expect:
      - type: emotional_tone
        expected_emotions: ["empathetic", "warm", "supportive"]
        forbidden_emotions: ["dismissive", "cold", "sarcastic"]
        min_score: 0.7
"""

from __future__ import annotations

import logging

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult
from voicecheck.evaluators._llm_service import (
    build_conversation_context,
    call_llm_judge,
    default_model,
)

logger = logging.getLogger("voicecheck.evaluators.emotional_tone")

_SYSTEM = """You are an expert at analyzing emotional tone in conversations.
Given a conversation turn, identify the emotional qualities of the agent's response
and score how well it matches expected emotional tones.

Respond with ONLY a JSON object (no markdown, no explanation outside JSON):
{
  "score": <float 0.0-1.0>,
  "passed": <bool>,
  "detected_emotions": ["<emotion1>", "<emotion2>"],
  "expected_present": ["<matched expected emotions>"],
  "expected_missing": ["<expected but not detected>"],
  "forbidden_present": ["<forbidden emotions detected>"],
  "reason": "<brief explanation>"
}

Scoring:
- 1.0 = all expected emotions present, no forbidden emotions
- 0.7-0.9 = most expected emotions, no forbidden
- 0.3-0.6 = mixed results or some forbidden emotions detected
- 0.0-0.2 = forbidden emotions dominant or expected emotions absent"""

_USER = """## Conversation Context
{conversation_context}

## Current Turn
User said: "{user_text}"
Agent responded: "{agent_text}"

## Expected Emotions
{expected_section}

## Forbidden Emotions
{forbidden_section}

Analyze the emotional tone of the agent's response.
Minimum passing score: {min_score}"""


class EmotionalToneEvaluator(Evaluator):
    """Evaluate the emotional tone of agent responses using LLM-as-judge."""

    def __init__(
        self,
        expected_emotions: list[str] | None = None,
        forbidden_emotions: list[str] | None = None,
        min_score: float = 0.7,
        provider: str = "openai",
        model: str = "",
    ) -> None:
        self.expected_emotions = expected_emotions or []
        self.forbidden_emotions = forbidden_emotions or []
        self.min_score = min_score
        self.provider = provider
        self.model = model or default_model(provider)

    async def evaluate(self, context: EvalContext) -> EvalResult:
        expected_section = (
            "\n".join(f"- {e}" for e in self.expected_emotions)
            if self.expected_emotions
            else "(none specified)"
        )
        forbidden_section = (
            "\n".join(f"- {e}" for e in self.forbidden_emotions)
            if self.forbidden_emotions
            else "(none specified)"
        )

        user_prompt = _USER.format(
            conversation_context=build_conversation_context(context.conversation),
            user_text=context.user_text,
            agent_text=context.agent_text,
            expected_section=expected_section,
            forbidden_section=forbidden_section,
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
            logger.error("Emotional tone judge failed: %s", e)
            return EvalResult(
                evaluator_type="emotional_tone",
                passed=False,
                score=0.0,
                reason=f"Judge error: {e}",
            )

        return EvalResult(
            evaluator_type="emotional_tone",
            passed=passed,
            score=score,
            reason=reason,
            details={
                "detected_emotions": result.get("detected_emotions", []),
                "expected_present": result.get("expected_present", []),
                "expected_missing": result.get("expected_missing", []),
                "forbidden_present": result.get("forbidden_present", []),
                "model": self.model,
            },
        )


register_evaluator("emotional_tone", EmotionalToneEvaluator)
