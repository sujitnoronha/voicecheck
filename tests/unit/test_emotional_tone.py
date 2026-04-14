"""Tests for the emotional tone evaluator."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from voicecheck.core.types import EvalContext, TransportMetrics
from voicecheck.evaluators.emotional_tone import EmotionalToneEvaluator

import voicecheck.evaluators.emotional_tone  # noqa: F401


def _make_context(agent_text: str = "", conversation: list | None = None) -> EvalContext:
    return EvalContext(
        user_text="test input",
        agent_text=agent_text,
        agent_audio=[],
        metrics=TransportMetrics(),
        turn_index=0,
        scenario_name="test",
        conversation=conversation or [],
    )


def _mock_openai_response(score: float, detected: list, reason: str = "test"):
    """Create a mock OpenAI response with the expected JSON."""
    response_json = json.dumps({
        "score": score,
        "passed": score >= 0.7,
        "detected_emotions": detected,
        "expected_present": detected,
        "expected_missing": [],
        "forbidden_present": [],
        "reason": reason,
    })

    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = AsyncMock(
        choices=[AsyncMock(message=AsyncMock(content=response_json))]
    )
    return mock_client


class TestEmotionalToneEvaluator:
    @pytest.mark.asyncio
    async def test_passes_when_emotions_match(self):
        ev = EmotionalToneEvaluator(
            expected_emotions=["empathetic", "warm"],
            min_score=0.7,
        )
        ev._openai_client = _mock_openai_response(
            0.9, ["empathetic", "warm"], "Agent showed warmth"
        )
        ctx = _make_context("I understand how you feel. That must be tough.")
        result = await ev.evaluate(ctx)
        assert result.passed
        assert result.score == 0.9
        assert result.evaluator_type == "emotional_tone"

    @pytest.mark.asyncio
    async def test_fails_when_score_below_threshold(self):
        ev = EmotionalToneEvaluator(
            expected_emotions=["empathetic"],
            min_score=0.8,
        )
        ev._openai_client = _mock_openai_response(0.4, [], "Agent was dismissive")
        ctx = _make_context("Whatever.")
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert result.score == 0.4

    @pytest.mark.asyncio
    async def test_forbidden_emotions_detected(self):
        ev = EmotionalToneEvaluator(
            forbidden_emotions=["dismissive", "cold"],
            min_score=0.7,
        )
        response_json = json.dumps({
            "score": 0.2,
            "passed": False,
            "detected_emotions": ["dismissive"],
            "expected_present": [],
            "expected_missing": [],
            "forbidden_present": ["dismissive"],
            "reason": "Agent was dismissive",
        })
        ev._openai_client = AsyncMock()
        ev._openai_client.chat.completions.create.return_value = AsyncMock(
            choices=[AsyncMock(message=AsyncMock(content=response_json))]
        )
        ctx = _make_context("I don't care about your problem.")
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert "dismissive" in result.details.get("forbidden_present", [])

    @pytest.mark.asyncio
    async def test_handles_llm_error(self):
        ev = EmotionalToneEvaluator(expected_emotions=["warm"])
        ev._openai_client = AsyncMock()
        ev._openai_client.chat.completions.create.side_effect = Exception("API error")
        ctx = _make_context("Hello")
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert result.score == 0.0
        assert "error" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_handles_malformed_json(self):
        ev = EmotionalToneEvaluator(expected_emotions=["warm"])
        ev._openai_client = AsyncMock()
        ev._openai_client.chat.completions.create.return_value = AsyncMock(
            choices=[AsyncMock(message=AsyncMock(content="not json"))]
        )
        ctx = _make_context("Hello")
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_no_emotions_specified(self):
        ev = EmotionalToneEvaluator(min_score=0.5)
        ev._openai_client = _mock_openai_response(0.8, ["neutral"], "Neutral tone")
        ctx = _make_context("The weather is fine today.")
        result = await ev.evaluate(ctx)
        assert result.passed

    @pytest.mark.asyncio
    async def test_conversation_context_included(self):
        ev = EmotionalToneEvaluator(expected_emotions=["supportive"])
        ev._openai_client = _mock_openai_response(0.9, ["supportive"])
        conversation = [
            {"role": "user", "text": "I'm feeling down"},
            {"role": "agent", "text": "I'm sorry to hear that"},
        ]
        ctx = _make_context("Thank you for understanding", conversation=conversation)
        result = await ev.evaluate(ctx)
        # Verify the LLM was called (conversation context was built)
        ev._openai_client.chat.completions.create.assert_called_once()

    def test_default_model_openai(self):
        ev = EmotionalToneEvaluator(provider="openai")
        assert ev.model == "gpt-4o-mini"

    def test_default_model_anthropic(self):
        ev = EmotionalToneEvaluator(provider="anthropic")
        assert "claude" in ev.model

    def test_custom_model(self):
        ev = EmotionalToneEvaluator(model="gpt-4o")
        assert ev.model == "gpt-4o"


class TestEmotionalToneRegistration:
    def test_registered(self):
        from voicecheck.core.evaluator import get_evaluator
        cls = get_evaluator("emotional_tone")
        assert cls is EmotionalToneEvaluator
