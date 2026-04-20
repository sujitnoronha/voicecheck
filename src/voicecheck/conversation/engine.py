"""Persona-driven conversation engine.

Instead of scripted turns, an LLM generates realistic user messages based on a
persona profile and the ongoing conversation. This enables free-flowing,
dynamic testing that covers a wider range of agent behaviors.

The engine uses OpenAI to simulate a user with a specific personality, age,
communication style, and goals — then evaluates the entire conversation at the end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("voicecheck.conversation.engine")


# ── Persona YAML model ──────────────────────────────────────────


class PersonaConfig(BaseModel):
    """Defines who the simulated user is and how they behave."""

    name: str = "Test User"
    description: str = ""
    age: int | None = None
    personality: str = "friendly and curious"
    communication_style: str = "casual, short sentences"
    goals: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    # Optional: specific things the persona should try to do
    instructions: str = ""
    # The LLM model to use for generating user messages
    model: str = "gpt-4o-mini"
    # How many turns the conversation should last
    max_turns: int = 5
    # Opening line (if empty, the engine generates one)
    opening: str = ""


class ConversationEvalConfig(BaseModel):
    """Evaluation criteria applied to the full conversation (not per-turn)."""

    criteria: list[str] = Field(default_factory=list)
    min_score: float = 0.7
    model: str = "gpt-4o-mini"


# ── System prompts ───────────────────────────────────────────────


def _build_persona_system_prompt(persona: PersonaConfig) -> str:
    """Build the system prompt that makes the LLM act as the persona."""
    parts = [
        f"You are simulating a user named {persona.name} in a voice conversation with an AI agent.",
        f"Personality: {persona.personality}.",
        f"Communication style: {persona.communication_style}.",
    ]

    if persona.age:
        parts.append(f"Age: {persona.age} years old. Speak appropriately for this age.")

    if persona.description:
        parts.append(f"Background: {persona.description}")

    if persona.goals:
        parts.append(f"Your goals in this conversation: {', '.join(persona.goals)}.")

    if persona.topics:
        parts.append(f"Topics you're interested in discussing: {', '.join(persona.topics)}.")

    if persona.instructions:
        parts.append(f"Special instructions: {persona.instructions}")

    parts.extend(
        [
            "",
            "Rules:",
            "- Stay in character at all times.",
            "- Respond naturally as this person would in a voice conversation.",
            "- Keep responses concise (1-3 sentences) — this is a spoken conversation, not text.",
            "- React naturally to what the agent says. Ask follow-up questions, share opinions.",
            "- If the agent says something unexpected or wrong, respond how your character would.",
            "- Do NOT break character or mention that you are an AI or a test.",
            "",
            "Respond with ONLY the next thing you would say out loud. No quotes, no stage directions, no metadata.",
        ]
    )

    return "\n".join(parts)


CONVERSATION_EVAL_SYSTEM = """You are an expert conversation quality evaluator.
You will be given a full conversation between a user and a voice agent, along with evaluation criteria.

For each criterion, score it from 0.0 to 1.0.
Then provide an overall score and assessment.

Respond with ONLY a JSON object:
{
  "overall_score": <float 0.0-1.0>,
  "overall_passed": <bool>,
  "overall_reason": "<brief overall assessment>",
  "criteria_scores": [
    {"criterion": "<text>", "score": <float>, "reason": "<explanation>"}
  ]
}"""

CONVERSATION_EVAL_USER = """## Full Conversation Transcript
{transcript}

## Evaluation Criteria
{criteria}

## Minimum passing score: {min_score}

Evaluate this conversation:"""


# ── Conversation Engine ──────────────────────────────────────────


class ConversationEngine:
    """Generates dynamic user messages using an LLM persona.

    Usage::

        engine = ConversationEngine(persona_config)
        # Get the opening message
        first_message = await engine.generate_opening()
        # After agent responds, get the next user message
        next_message = await engine.generate_next(agent_response)
        # After conversation ends, evaluate the full conversation
        eval_result = await engine.evaluate_conversation(conversation, eval_config)
    """

    def __init__(self, persona: PersonaConfig) -> None:
        self.persona = persona
        self._messages: list[dict[str, str]] = []
        self._system_prompt = _build_persona_system_prompt(persona)
        self._openai_client: Any = None

    async def generate_opening(self) -> str:
        """Generate the first user message to start the conversation."""
        if self.persona.opening:
            self._messages.append({"role": "assistant", "content": self.persona.opening})
            return self.persona.opening

        # Reset message history
        self._messages = []

        response = await self._call_llm(
            system=self._system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": "The conversation is starting. Say your opening line to the AI agent.",
                }
            ],
        )

        self._messages.append({"role": "assistant", "content": response})
        logger.info("Persona opening: %s", response[:80])
        return response

    async def generate_opening_guided(self, step_goal: str) -> str:
        """Generate the opening message steered toward a specific goal.

        Args:
            step_goal: What this first step should accomplish.

        Returns:
            The opening message from the persona.
        """
        if self.persona.opening:
            self._messages.append({"role": "assistant", "content": self.persona.opening})
            return self.persona.opening

        self._messages = []

        response = await self._call_llm(
            system=self._system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "The conversation is starting. "
                        f"Your goal for this message: {step_goal}\n\n"
                        "Say your opening line to the AI agent."
                    ),
                }
            ],
        )

        self._messages.append({"role": "assistant", "content": response})
        logger.info("Guided opening [%s]: %s", step_goal[:40], response[:80])
        return response

    async def generate_next_guided(self, agent_response: str, step_goal: str) -> str:
        """Generate the next message steered toward a specific goal.

        Args:
            agent_response: What the agent just said.
            step_goal: What this step should accomplish.

        Returns:
            The next message from the persona, aimed at the given goal.
        """
        self._messages.append({"role": "user", "content": f"[Agent says]: {agent_response}"})

        response = await self._call_llm(
            system=self._system_prompt,
            messages=self._messages
            + [
                {
                    "role": "user",
                    "content": (
                        f"Your goal for this message: {step_goal}\n\n"
                        "What do you say next? Stay in character and work toward your goal."
                    ),
                }
            ],
        )

        self._messages.append({"role": "assistant", "content": response})
        logger.info("Guided response [%s]: %s", step_goal[:40], response[:80])
        return response

    async def generate_next(self, agent_response: str) -> str:
        """Generate the next user message based on the agent's response.

        Args:
            agent_response: What the agent just said.

        Returns:
            The next thing the simulated user would say.
        """
        # Add agent response to history
        self._messages.append({"role": "user", "content": f"[Agent says]: {agent_response}"})

        response = await self._call_llm(
            system=self._system_prompt,
            messages=self._messages + [{"role": "user", "content": "What do you say next?"}],
        )

        self._messages.append({"role": "assistant", "content": response})
        logger.info("Persona response: %s", response[:80])
        return response

    async def evaluate_conversation(
        self,
        conversation: list[dict[str, str]],
        eval_config: ConversationEvalConfig,
    ) -> dict[str, Any]:
        """Evaluate the full conversation against criteria.

        Args:
            conversation: List of {"role": "user"|"agent", "text": "..."} dicts.
            eval_config: Evaluation criteria and settings.

        Returns:
            Dict with overall_score, overall_passed, criteria_scores.
        """
        transcript_lines = []
        for msg in conversation:
            role = "User" if msg["role"] == "user" else "Agent"
            transcript_lines.append(f"{role}: {msg['text']}")
        transcript = "\n".join(transcript_lines)

        criteria_text = "\n".join(f"- {c}" for c in eval_config.criteria)

        user_prompt = CONVERSATION_EVAL_USER.format(
            transcript=transcript,
            criteria=criteria_text,
            min_score=eval_config.min_score,
        )

        try:
            response_text = await self._call_llm(
                system=CONVERSATION_EVAL_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
                model=eval_config.model,
                json_mode=True,
            )
            result = json.loads(_strip_markdown_fences(response_text))
            result["overall_passed"] = result.get("overall_score", 0) >= eval_config.min_score
            return result
        except Exception as e:
            logger.error("Conversation evaluation failed: %s", e)
            return {
                "overall_score": 0.0,
                "overall_passed": False,
                "overall_reason": f"Evaluation error: {e}",
                "criteria_scores": [],
            }

    async def _call_llm(
        self,
        system: str,
        messages: list[dict[str, str]],
        model: str | None = None,
        json_mode: bool = False,
    ) -> str:
        """Call OpenAI chat completions."""
        if self._openai_client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("openai not installed. Run: pip install voicecheck[llm]")
            self._openai_client = AsyncOpenAI()

        client = self._openai_client
        kwargs: dict[str, Any] = {
            "model": model or self.persona.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": 0.8,
            "max_tokens": 150,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
            kwargs["max_tokens"] = 500
            kwargs["temperature"] = 0.1

        last_err: Exception | None = None
        for attempt in range(2):
            try:
                response = await client.chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as e:
                last_err = e
                if attempt == 0:
                    logger.warning("LLM call failed (attempt 1), retrying: %s", e)
                    await asyncio.sleep(1)
        raise last_err  # type: ignore[misc]


def _strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from LLM responses."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text
