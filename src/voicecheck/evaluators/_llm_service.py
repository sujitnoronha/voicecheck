"""Shared LLM judge service for all evaluators.

Single module for calling LLMs (OpenAI or Anthropic) and parsing JSON responses.
All LLM-based evaluators use this instead of managing their own clients.

The module-level clients are lazy-initialized and shared across evaluators
within a run, so there's one connection pool regardless of how many
evaluators use LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("voicecheck.evaluators._llm_service")

_openai_client: Any = None
_anthropic_client: Any = None


DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-6",
}


class LLMServiceError(RuntimeError):
    """Raised when the LLM call fails or returns unusable output."""


def _is_transient_openai_error(exc: Exception) -> bool:
    """True for retryable OpenAI errors (connection, timeout, rate limit, 5xx)."""
    try:
        import openai
    except ImportError:
        return False
    return isinstance(exc, (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
    ))


def _is_transient_anthropic_error(exc: Exception) -> bool:
    """True for retryable Anthropic errors (connection, timeout, rate limit, 5xx)."""
    try:
        import anthropic
    except ImportError:
        return False
    transient_types = tuple(
        cls for name in (
            "APIConnectionError", "APITimeoutError",
            "RateLimitError", "InternalServerError",
        )
        for cls in (getattr(anthropic, name, None),)
        if cls is not None
    )
    return bool(transient_types) and isinstance(exc, transient_types)


async def call_llm_judge(
    system_prompt: str,
    user_prompt: str,
    model: str = "",
    provider: str = "openai",
    *,
    temperature: float = 0.1,
    max_tokens: int = 800,
    response_json: bool = True,
) -> dict:
    """Call an LLM and return parsed JSON response.

    Single entry point for all LLM-based evaluators. Handles client setup,
    API calls, retries, markdown fence stripping, and JSON parsing.

    Args:
        system_prompt: System message for the judge.
        user_prompt: User message with evaluation context.
        model: Model name. Falls back to ``default_model(provider)`` when empty.
        provider: "openai" or "anthropic".
        temperature: Sampling temperature (low for judges).
        max_tokens: Max output tokens.
        response_json: Request structured JSON mode (OpenAI only).

    Returns:
        Parsed JSON dict from the LLM response.

    Raises:
        LLMServiceError: On API failure, missing API key, or unparseable output.
    """
    resolved_model = model or default_model(provider)

    if provider == "openai":
        text = await _call_openai(
            system_prompt, user_prompt, resolved_model,
            temperature=temperature, max_tokens=max_tokens, response_json=response_json,
        )
    elif provider == "anthropic":
        text = await _call_anthropic(
            system_prompt, user_prompt, resolved_model,
            temperature=temperature, max_tokens=max_tokens,
        )
    else:
        raise LLMServiceError(
            f"Unknown LLM provider: {provider!r}. Use 'openai' or 'anthropic'."
        )

    try:
        return json.loads(strip_markdown_fences(text))
    except json.JSONDecodeError as e:
        raise LLMServiceError(
            f"LLM returned non-JSON output: {text[:200]!r} ({e})"
        ) from e


def default_model(provider: str) -> str:
    """Return the default model for a provider."""
    if provider not in DEFAULT_MODELS:
        raise LLMServiceError(
            f"Unknown LLM provider: {provider!r}. "
            f"Supported: {', '.join(DEFAULT_MODELS)}"
        )
    return DEFAULT_MODELS[provider]


def build_conversation_context(
    conversation: list[dict] | None,
    *,
    max_turns: int = 20,
) -> str:
    """Format conversation history as a readable transcript string.

    Args:
        conversation: List of {"role": "user"|"agent", "text": "..."} dicts.
        max_turns: Truncate to the most recent N entries (oldest dropped first).
    """
    if not conversation:
        return "(first turn — no prior conversation)"
    dropped = max(0, len(conversation) - max_turns)
    if dropped:
        # Memory-recall / personality-consistency evaluators that look far back
        # need to know the judge didn't see the whole transcript.
        logger.warning(
            "build_conversation_context: truncated %d of %d entries "
            "(max_turns=%d) — judge will not see dropped turns",
            dropped, len(conversation), max_turns,
        )
    entries = conversation[-max_turns:]
    lines = []
    if dropped:
        lines.append(f"[... {dropped} earlier turns omitted ...]")
    for msg in entries:
        role = msg.get("role", "unknown")
        text = msg.get("text", "")
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def strip_markdown_fences(text: str) -> str:
    """Strip ```json ... ``` and ``` ... ``` wrappers from an LLM reply."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


# Backward-compat alias — some callers use the private name.
_strip_markdown_fences = strip_markdown_fences


async def _call_openai(
    system_prompt: str,
    user_prompt: str,
    model: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 800,
    response_json: bool = True,
) -> str:
    global _openai_client
    if _openai_client is None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise LLMServiceError(
                "openai not installed. Run: pip install voicecheck[llm]"
            ) from e
        if not os.environ.get("OPENAI_API_KEY"):
            raise LLMServiceError(
                "OPENAI_API_KEY is not set. "
                "Export it or pass --skip-llm-judge."
            )
        _openai_client = AsyncOpenAI()

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_json:
        kwargs["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            response = await _openai_client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or "{}"
        except Exception as e:
            last_err = e
            # Only retry on transient errors — retrying 401/400 burns quota
            # and wall time with no chance of success.
            if attempt == 0 and _is_transient_openai_error(e):
                logger.warning("OpenAI transient failure, retrying: %s", e)
                await asyncio.sleep(1)
                continue
            break
    raise LLMServiceError(f"OpenAI call failed: {last_err}")


async def _call_anthropic(
    system_prompt: str,
    user_prompt: str,
    model: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 800,
) -> str:
    global _anthropic_client
    if _anthropic_client is None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise LLMServiceError(
                "anthropic not installed. Run: pip install voicecheck[llm]"
            ) from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMServiceError(
                "ANTHROPIC_API_KEY is not set. "
                "Export it or pass --skip-llm-judge."
            )
        _anthropic_client = AsyncAnthropic()

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            response = await _anthropic_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                temperature=temperature,
            )
            parts = [block.text for block in response.content if hasattr(block, "text")]
            return "".join(parts) or "{}"
        except Exception as e:
            last_err = e
            if attempt == 0 and _is_transient_anthropic_error(e):
                logger.warning("Anthropic transient failure, retrying: %s", e)
                await asyncio.sleep(1)
                continue
            break
    raise LLMServiceError(f"Anthropic call failed: {last_err}")
