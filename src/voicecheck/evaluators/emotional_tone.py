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

import json
import logging
import re
from typing import Any

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult

logger = logging.getLogger("voicecheck.evaluators.emotional_tone")

SYSTEM_PROMPT = """You are an expert at analyzing emotional tone in conversations.
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

USER_PROMPT = """## Conversation Context
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
        self.model = model or self._default_model(provider)
        self._openai_client: Any = None
        self._anthropic_client: Any = None

    async def evaluate(self, context: EvalContext) -> EvalResult:
        conv_lines = []
        for msg in context.conversation:
            conv_lines.append(f"{msg.get('role', 'unknown')}: {msg.get('text', '')}")
        conversation_context = "\n".join(conv_lines) if conv_lines else "(first turn)"

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

        user_prompt = USER_PROMPT.format(
            conversation_context=conversation_context,
            user_text=context.user_text,
            agent_text=context.agent_text,
            expected_section=expected_section,
            forbidden_section=forbidden_section,
            min_score=self.min_score,
        )

        try:
            response_text = await self._call_llm(user_prompt)
            result = json.loads(_strip_markdown_fences(response_text))
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

    async def _call_llm(self, user_prompt: str) -> str:
        if self.provider == "openai":
            return await self._call_openai(user_prompt)
        elif self.provider == "anthropic":
            return await self._call_anthropic(user_prompt)
        raise ValueError(f"Unknown provider: {self.provider}")

    async def _call_openai(self, user_prompt: str) -> str:
        if self._openai_client is None:
            from openai import AsyncOpenAI
            self._openai_client = AsyncOpenAI()
        response = await self._openai_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"

    async def _call_anthropic(self, user_prompt: str) -> str:
        if self._anthropic_client is None:
            from anthropic import AsyncAnthropic
            self._anthropic_client = AsyncAnthropic()
        response = await self._anthropic_client.messages.create(
            model=self.model,
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.1,
        )
        return response.content[0].text

    @staticmethod
    def _default_model(provider: str) -> str:
        if provider == "anthropic":
            return "claude-sonnet-4-5-20250929"
        return "gpt-4o-mini"


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


register_evaluator("emotional_tone", EmotionalToneEvaluator)
