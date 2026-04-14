"""Shared LLM judge service for all evaluators.

Single module for calling LLMs (OpenAI or Anthropic) and parsing JSON responses.
All LLM-based evaluators use this instead of managing their own clients.

The module-level clients are lazy-initialized and shared across evaluators
within a run, so there's one connection pool regardless of how many
evaluators use LLM calls.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("voicecheck.evaluators._llm_service")

_openai_client: Any = None
_anthropic_client: Any = None


async def call_llm_judge(
    system_prompt: str,
    user_prompt: str,
    model: str = "gpt-4o-mini",
    provider: str = "openai",
) -> dict:
    """Call an LLM and return parsed JSON response.

    This is the single entry point for all LLM-based evaluators.
    Handles client setup, API calls, markdown fence stripping, and JSON parsing.

    Args:
        system_prompt: System message for the judge.
        user_prompt: User message with evaluation context.
        model: Model name (default gpt-4o-mini).
        provider: "openai" or "anthropic".

    Returns:
        Parsed JSON dict from the LLM response.

    Raises:
        ImportError: If the provider's SDK is not installed.
        ValueError: If provider is unknown.
        Exception: If the LLM call or JSON parsing fails.
    """
    if provider == "openai":
        text = await _call_openai(system_prompt, user_prompt, model)
    elif provider == "anthropic":
        text = await _call_anthropic(system_prompt, user_prompt, model)
    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Use 'openai' or 'anthropic'.")

    return json.loads(_strip_markdown_fences(text))


def default_model(provider: str) -> str:
    """Return the default model for a provider."""
    if provider == "anthropic":
        return "claude-sonnet-4-5-20250929"
    return "gpt-4o-mini"


def build_conversation_context(conversation: list[dict]) -> str:
    """Format conversation history for judge prompts."""
    if not conversation:
        return "(first turn — no prior conversation)"
    lines = []
    for msg in conversation:
        role = msg.get("role", "unknown")
        text = msg.get("text", "")
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


async def _call_openai(system_prompt: str, user_prompt: str, model: str) -> str:
    global _openai_client
    if _openai_client is None:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError(
                "openai not installed. Run: pip install voicecheck[llm]"
            )
        _openai_client = AsyncOpenAI()

    response = await _openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or "{}"


async def _call_anthropic(system_prompt: str, user_prompt: str, model: str) -> str:
    global _anthropic_client
    if _anthropic_client is None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError(
                "anthropic not installed. Run: pip install voicecheck[llm]"
            )
        _anthropic_client = AsyncAnthropic()

    response = await _anthropic_client.messages.create(
        model=model,
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.1,
    )
    return response.content[0].text


def _strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences from LLM responses."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text
