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


def _mock_llm_response(**kwargs):
    """Create a mock return value for call_llm_judge."""
    defaults = {
        "score": 0.8,
        "passed": True,
        "detected_emotions": [],
        "expected_present": [],
        "expected_missing": [],
        "forbidden_present": [],
        "reason": "test",
    }
    defaults.update(kwargs)
    return defaults


class TestEmotionalToneEvaluator:
    @pytest.mark.asyncio
    @patch("voicecheck.evaluators.emotional_tone.call_llm_judge")
    async def test_passes_when_emotions_match(self, mock_llm):
        mock_llm.return_value = _mock_llm_response(
            score=0.9, detected_emotions=["empathetic", "warm"],
            expected_present=["empathetic", "warm"], reason="Agent showed warmth",
        )
        ev = EmotionalToneEvaluator(expected_emotions=["empathetic", "warm"], min_score=0.7)
        ctx = _make_context("I understand how you feel. That must be tough.")
        result = await ev.evaluate(ctx)
        assert result.passed
        assert result.score == 0.9
        assert result.evaluator_type == "emotional_tone"

    @pytest.mark.asyncio
    @patch("voicecheck.evaluators.emotional_tone.call_llm_judge")
    async def test_fails_when_score_below_threshold(self, mock_llm):
        mock_llm.return_value = _mock_llm_response(score=0.4, reason="Agent was dismissive")
        ev = EmotionalToneEvaluator(expected_emotions=["empathetic"], min_score=0.8)
        ctx = _make_context("Whatever.")
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert result.score == 0.4

    @pytest.mark.asyncio
    @patch("voicecheck.evaluators.emotional_tone.call_llm_judge")
    async def test_forbidden_emotions_detected(self, mock_llm):
        mock_llm.return_value = _mock_llm_response(
            score=0.2, detected_emotions=["dismissive"],
            forbidden_present=["dismissive"], reason="Agent was dismissive",
        )
        ev = EmotionalToneEvaluator(forbidden_emotions=["dismissive", "cold"], min_score=0.7)
        ctx = _make_context("I don't care about your problem.")
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert "dismissive" in result.details.get("forbidden_present", [])

    @pytest.mark.asyncio
    @patch("voicecheck.evaluators.emotional_tone.call_llm_judge")
    async def test_handles_llm_error(self, mock_llm):
        mock_llm.side_effect = Exception("API error")
        ev = EmotionalToneEvaluator(expected_emotions=["warm"])
        ctx = _make_context("Hello")
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert result.score == 0.0
        assert "error" in result.reason.lower()

    @pytest.mark.asyncio
    @patch("voicecheck.evaluators.emotional_tone.call_llm_judge")
    async def test_no_emotions_specified(self, mock_llm):
        mock_llm.return_value = _mock_llm_response(
            score=0.8, detected_emotions=["neutral"], reason="Neutral tone",
        )
        ev = EmotionalToneEvaluator(min_score=0.5)
        ctx = _make_context("The weather is fine today.")
        result = await ev.evaluate(ctx)
        assert result.passed

    @pytest.mark.asyncio
    @patch("voicecheck.evaluators.emotional_tone.call_llm_judge")
    async def test_conversation_context_included(self, mock_llm):
        mock_llm.return_value = _mock_llm_response(score=0.9)
        conversation = [
            {"role": "user", "text": "I'm feeling down"},
            {"role": "agent", "text": "I'm sorry to hear that"},
        ]
        ev = EmotionalToneEvaluator(expected_emotions=["supportive"])
        ctx = _make_context("Thank you for understanding", conversation=conversation)
        result = await ev.evaluate(ctx)
        mock_llm.assert_called_once()

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
