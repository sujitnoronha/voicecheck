"""LLM-as-judge evaluator — uses an LLM to score agent responses."""

from __future__ import annotations

import json
import logging
import re

from typing import Any

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult

logger = logging.getLogger("voicecheck.evaluators.llm_judge")

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator for voice agent conversations.
You will be given a conversation turn and evaluation criteria.
Score the agent's response on a scale of 0.0 to 1.0.

Respond with ONLY a JSON object (no markdown, no explanation outside JSON):
{
  "score": <float 0.0-1.0>,
  "passed": <bool>,
  "reason": "<brief explanation>"
}"""

JUDGE_USER_PROMPT = """## Conversation Context
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
        self.model = model or self._default_model(provider)
        self._openai_client: Any = None
        self._anthropic_client: Any = None

    async def evaluate(self, context: EvalContext) -> EvalResult:
        # Build conversation context string
        conv_lines: list[str] = []
        for msg in context.conversation:
            role = msg.get("role", "unknown")
            text = msg.get("text", "")
            conv_lines.append(f"{role}: {text}")
        conversation_context = "\n".join(conv_lines) if conv_lines else "(first turn)"

        user_prompt = JUDGE_USER_PROMPT.format(
            conversation_context=conversation_context,
            user_text=context.user_text,
            agent_text=context.agent_text,
            criteria=self.criteria,
            min_score=self.min_score,
        )

        try:
            response_text = await self._call_llm(user_prompt)
            result = json.loads(_strip_markdown_fences(response_text))
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

    async def _call_llm(self, user_prompt: str) -> str:
        """Call the LLM and return the response text."""
        if self.provider == "openai":
            return await self._call_openai(user_prompt)
        elif self.provider == "anthropic":
            return await self._call_anthropic(user_prompt)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    async def _call_openai(self, user_prompt: str) -> str:
        if self._openai_client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError(
                    "openai not installed. Run: pip install voicecheck[llm]"
                )
            self._openai_client = AsyncOpenAI()

        client = self._openai_client
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"

    async def _call_anthropic(self, user_prompt: str) -> str:
        if self._anthropic_client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError:
                raise ImportError(
                    "anthropic not installed. Run: pip install voicecheck[llm]"
                )
            self._anthropic_client = AsyncAnthropic()

        client = self._anthropic_client
        response = await client.messages.create(
            model=self.model,
            max_tokens=256,
            system=JUDGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.1,
        )
        return response.content[0].text

    @staticmethod
    def _default_model(provider: str) -> str:
        if provider == "openai":
            return "gpt-4o-mini"
        elif provider == "anthropic":
            return "claude-sonnet-4-5-20250929"
        return "gpt-4o-mini"


def _strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from LLM responses.

    Some models (especially Anthropic) wrap JSON in ```json ... ``` fences
    despite being told not to.
    """
    text = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


register_evaluator("llm_judge", LLMJudgeEvaluator)
