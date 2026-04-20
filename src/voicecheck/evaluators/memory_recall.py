"""Memory recall evaluator — checks if the agent remembered a previously shared fact.

Uses LLM-as-judge for semantic recall assessment. Keyword matching fails here
because the twin may paraphrase ("I grew up in Portland" vs "My hometown is Portland").

YAML usage:
    turns:
      - user: "My sister is named Ninon and she lives in Paris."
        expect:
          - type: turn_count
            min_words: 3

      - user: "Do you remember anything about my sister?"
        expect:
          - type: memory_recall
            fact: "User's sister is named Ninon"
            shared_in_turn: 0
            min_score: 0.7
"""

from __future__ import annotations

import logging

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult
from voicecheck.evaluators._llm_service import (
    build_conversation_context,
    call_llm_judge,
)

logger = logging.getLogger("voicecheck.evaluators.memory_recall")

_JUDGE_SYSTEM = """You are an expert evaluator for memory recall in conversational AI.
You will assess whether the agent successfully recalled a specific fact that was
shared earlier in the conversation.

Respond with ONLY a JSON object:
{
  "score": <float 0.0-1.0>,
  "passed": <bool>,
  "reason": "<brief explanation>",
  "recall_type": "exact|paraphrased|partial|missing"
}

Scoring:
- 1.0 = exact or clearly paraphrased recall of the fact
- 0.7-0.9 = partial recall (got the gist but missed key details)
- 0.3-0.6 = vague reference that could be coincidence
- 0.0-0.2 = no evidence of recall, or contradicted the fact"""

_JUDGE_USER = """## Full Conversation History
{conversation_context}

## Current Turn (Turn {turn_index})
User said: "{user_text}"
Agent responded: "{agent_text}"

## Fact to Verify
The following fact was shared in turn {shared_in_turn}:
"{fact}"

## Task
Did the agent's current response demonstrate recall of this fact?
Consider exact matches, paraphrases, and indirect references.
The agent does NOT need to quote it verbatim — semantic recall counts.

Minimum passing score: {min_score}"""


class MemoryRecallEvaluator(Evaluator):
    """Check if the agent recalled a fact shared in a previous turn."""

    def __init__(
        self,
        fact: str = "",
        shared_in_turn: int = 0,
        min_score: float = 0.7,
        model: str = "gpt-4o-mini",
        provider: str = "openai",
    ) -> None:
        self.fact = fact
        self.shared_in_turn = shared_in_turn
        self.min_score = min_score
        self.model = model
        self.provider = provider

    async def evaluate(self, context: EvalContext) -> EvalResult:
        user_prompt = _JUDGE_USER.format(
            conversation_context=build_conversation_context(context.conversation),
            turn_index=context.turn_index,
            user_text=context.user_text,
            agent_text=context.agent_text,
            shared_in_turn=self.shared_in_turn,
            fact=self.fact,
            min_score=self.min_score,
        )

        try:
            result = await call_llm_judge(
                _JUDGE_SYSTEM, user_prompt, model=self.model, provider=self.provider
            )
            score = float(result["score"])
            passed = score >= self.min_score
            reason = result.get("reason", "")
            recall_type = result.get("recall_type", "unknown")
        except Exception as e:
            logger.error("Memory recall judge failed: %s", e)
            return EvalResult(
                evaluator_type="memory_recall",
                passed=False,
                score=0.0,
                reason=f"Judge error: {e}",
            )

        return EvalResult(
            evaluator_type="memory_recall",
            passed=passed,
            score=score,
            reason=reason,
            details={
                "fact": self.fact,
                "shared_in_turn": self.shared_in_turn,
                "recall_type": recall_type,
                "model": self.model,
            },
        )


register_evaluator("memory_recall", MemoryRecallEvaluator)
