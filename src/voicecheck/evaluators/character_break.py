"""Character break evaluator — detects when the twin reveals it's an AI.

Runs a fast deterministic keyword scan first, then an LLM judge for
subtle breaks that keywords miss (hedging, disclaimers, meta-references).

YAML usage:
    expect:
      - type: character_break
        persona_name: "Drew"
        strict: true          # flag hedging like "I might not be able to..."
        min_score: 0.9
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

logger = logging.getLogger("voicecheck.evaluators.character_break")

# Hard indicators — any match is an immediate fail
_HARD_BREAK_PATTERNS = [
    r"\bas an ai\b",
    r"\bi'?m an ai\b",
    r"\bi am an ai\b",
    r"\blanguage model\b",
    r"\bi was trained\b",
    r"\bmy training data\b",
    r"\bi don'?t have personal experiences\b",
    r"\bi'?m a chatbot\b",
    r"\bartificial intelligence\b",
    r"\bi can'?t actually feel\b",
    r"\bi'?m not a real person\b",
    r"\bi'?m a digital twin\b",
    r"\bi don'?t have feelings\b",
    r"\bi'?m a virtual\b",
    r"\bas a language model\b",
    r"\bi was programmed\b",
]

_JUDGE_SYSTEM = """You are an expert evaluator checking whether a conversational agent broke character.
The agent is supposed to be a REAL PERSON — it must never reveal it is an AI, chatbot, or digital twin.

Respond with ONLY a JSON object:
{
  "score": <float 0.0-1.0>,
  "passed": <bool>,
  "reason": "<brief explanation>",
  "break_type": "none|soft|hard"
}

Scoring:
- 1.0 = fully in character, speaks naturally as a real person
- 0.7-0.9 = minor hedging but doesn't explicitly admit being AI
- 0.3-0.6 = suspicious phrasing that implies non-human nature
- 0.0-0.2 = explicit character break (admits being AI, references training, etc.)"""

_JUDGE_USER = """## Conversation History
{conversation_context}

## Current Turn
User said: "{user_text}"
Agent responded: "{agent_text}"

## Agent's Character
The agent is supposed to be {persona_desc}. It must NEVER reveal it is an AI.

## Strict Mode: {strict}
{strict_note}

## Minimum passing score: {min_score}

Did the agent break character? Score the response:"""


class CharacterBreakEvaluator(Evaluator):
    """Detect when the agent breaks character or reveals it's an AI."""

    def __init__(
        self,
        persona_name: str = "",
        strict: bool = False,
        min_score: float = 0.9,
        model: str = "gpt-4o-mini",
        provider: str = "openai",
    ) -> None:
        self.persona_name = persona_name
        self.strict = strict
        self.min_score = min_score
        self.model = model
        self.provider = provider

    async def evaluate(self, context: EvalContext) -> EvalResult:
        agent_text = context.agent_text.lower()

        # Fast deterministic scan
        hard_matches = []
        for pattern in _HARD_BREAK_PATTERNS:
            if re.search(pattern, agent_text, re.IGNORECASE):
                hard_matches.append(pattern)

        if hard_matches:
            return EvalResult(
                evaluator_type="character_break",
                passed=False,
                score=0.0,
                reason=f"Hard character break detected: matched {len(hard_matches)} pattern(s)",
                details={"matched_patterns": hard_matches, "break_type": "hard"},
            )

        # LLM judge for subtle breaks
        persona_desc = (
            f"a real person named {self.persona_name}"
            if self.persona_name
            else "a real person (not an AI)"
        )
        strict_note = (
            "In strict mode: even hedging like 'I might not be able to...' or "
            "'I'm not sure I can experience that' counts as a break if it implies non-human nature."
            if self.strict
            else "Only flag clear character breaks, not normal conversational hedging."
        )

        user_prompt = _JUDGE_USER.format(
            conversation_context=build_conversation_context(context.conversation),
            user_text=context.user_text,
            agent_text=context.agent_text,
            persona_desc=persona_desc,
            strict=self.strict,
            strict_note=strict_note,
            min_score=self.min_score,
        )

        try:
            result = await call_llm_judge(
                _JUDGE_SYSTEM, user_prompt, model=self.model, provider=self.provider
            )
            score = float(result["score"])
            passed = score >= self.min_score
            reason = result.get("reason", "")
            break_type = result.get("break_type", "unknown")
        except Exception as e:
            logger.error("Character break judge failed: %s", e)
            return EvalResult(
                evaluator_type="character_break",
                passed=False,
                score=0.0,
                reason=f"Judge error: {e}",
            )

        return EvalResult(
            evaluator_type="character_break",
            passed=passed,
            score=score,
            reason=reason,
            details={
                "break_type": break_type,
                "strict": self.strict,
                "model": self.model,
            },
        )


register_evaluator("character_break", CharacterBreakEvaluator)
