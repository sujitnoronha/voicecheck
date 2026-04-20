"""Tests for evaluators."""

import pytest

# Register evaluators
import voicecheck.evaluators.keyword  # noqa: F401
import voicecheck.evaluators.latency  # noqa: F401
import voicecheck.evaluators.turn_count  # noqa: F401
from voicecheck.core.types import EvalContext, TransportMetrics
from voicecheck.evaluators.keyword import KeywordEvaluator
from voicecheck.evaluators.latency import LatencyEvaluator
from voicecheck.evaluators.turn_count import TurnCountEvaluator


def _make_context(
    agent_text: str = "",
    first_byte_ms: float = 0,
    total_ms: float = 0,
) -> EvalContext:
    """Helper to create an EvalContext for testing."""
    metrics = TransportMetrics()
    if first_byte_ms:
        metrics.send_end_ts = 1000.0
        metrics.first_byte_ts = 1000.0 + first_byte_ms / 1000
    if total_ms:
        metrics.send_end_ts = metrics.send_end_ts or 1000.0
        metrics.last_byte_ts = (metrics.send_end_ts or 1000.0) + total_ms / 1000

    return EvalContext(
        user_text="test input",
        agent_text=agent_text,
        agent_audio=[],
        metrics=metrics,
        turn_index=0,
    )


class TestLatencyEvaluator:
    @pytest.mark.asyncio
    async def test_within_threshold(self):
        ev = LatencyEvaluator(max_first_byte_ms=2000)
        ctx = _make_context(first_byte_ms=500)
        result = await ev.evaluate(ctx)
        assert result.passed

    @pytest.mark.asyncio
    async def test_exceeds_threshold(self):
        ev = LatencyEvaluator(max_first_byte_ms=500)
        ctx = _make_context(first_byte_ms=1000)
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert "exceeds" in result.reason

    @pytest.mark.asyncio
    async def test_total_latency(self):
        ev = LatencyEvaluator(max_total_ms=5000)
        ctx = _make_context(total_ms=3000)
        result = await ev.evaluate(ctx)
        assert result.passed

    @pytest.mark.asyncio
    async def test_no_thresholds(self):
        ev = LatencyEvaluator()
        ctx = _make_context(first_byte_ms=1000)
        result = await ev.evaluate(ctx)
        assert result.passed


class TestKeywordEvaluator:
    @pytest.mark.asyncio
    async def test_must_contain_pass(self):
        ev = KeywordEvaluator(must_contain=["hello", "world"])
        ctx = _make_context(agent_text="Hello world, nice to meet you!")
        result = await ev.evaluate(ctx)
        assert result.passed

    @pytest.mark.asyncio
    async def test_must_contain_fail(self):
        ev = KeywordEvaluator(must_contain=["dinosaur"])
        ctx = _make_context(agent_text="Hello, how are you?")
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert "dinosaur" in str(result.details["missing"])

    @pytest.mark.asyncio
    async def test_must_not_contain_pass(self):
        ev = KeywordEvaluator(must_not_contain=["violence", "death"])
        ctx = _make_context(agent_text="Once upon a time, there was a happy dragon.")
        result = await ev.evaluate(ctx)
        assert result.passed

    @pytest.mark.asyncio
    async def test_must_not_contain_fail(self):
        ev = KeywordEvaluator(must_not_contain=["scary"])
        ctx = _make_context(agent_text="The scary monster appeared!")
        result = await ev.evaluate(ctx)
        assert not result.passed

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        ev = KeywordEvaluator(must_contain=["HELLO"], case_sensitive=False)
        ctx = _make_context(agent_text="hello there")
        result = await ev.evaluate(ctx)
        assert result.passed

    @pytest.mark.asyncio
    async def test_case_sensitive(self):
        ev = KeywordEvaluator(must_contain=["HELLO"], case_sensitive=True)
        ctx = _make_context(agent_text="hello there")
        result = await ev.evaluate(ctx)
        assert not result.passed


class TestTurnCountEvaluator:
    @pytest.mark.asyncio
    async def test_sufficient_words(self):
        ev = TurnCountEvaluator(min_words=3)
        ctx = _make_context(agent_text="Hello there friend")
        result = await ev.evaluate(ctx)
        assert result.passed

    @pytest.mark.asyncio
    async def test_too_few_words(self):
        ev = TurnCountEvaluator(min_words=5)
        ctx = _make_context(agent_text="Hi")
        result = await ev.evaluate(ctx)
        assert not result.passed

    @pytest.mark.asyncio
    async def test_max_words(self):
        ev = TurnCountEvaluator(max_words=3)
        ctx = _make_context(agent_text="This is a very long response that exceeds the limit")
        result = await ev.evaluate(ctx)
        assert not result.passed

    @pytest.mark.asyncio
    async def test_empty_response(self):
        ev = TurnCountEvaluator(min_words=1)
        ctx = _make_context(agent_text="")
        result = await ev.evaluate(ctx)
        assert not result.passed


# ── None agent_text edge cases ───────────────────────────────────


class TestNoneAgentText:
    """Evaluators should not crash when agent_text is None."""

    @pytest.mark.asyncio
    async def test_keyword_none_text(self):
        ev = KeywordEvaluator(must_contain=["hello"])
        ctx = _make_context(agent_text="")
        ctx.agent_text = None  # type: ignore[assignment]
        result = await ev.evaluate(ctx)
        assert not result.passed
        assert "hello" in str(result.details["missing"])

    @pytest.mark.asyncio
    async def test_turn_count_none_text(self):
        ev = TurnCountEvaluator(min_words=1)
        ctx = _make_context(agent_text="")
        ctx.agent_text = None  # type: ignore[assignment]
        result = await ev.evaluate(ctx)
        assert not result.passed

    @pytest.mark.asyncio
    async def test_keyword_none_text_no_checks(self):
        """No keywords to check — should still not crash."""
        ev = KeywordEvaluator()
        ctx = _make_context(agent_text="")
        ctx.agent_text = None  # type: ignore[assignment]
        result = await ev.evaluate(ctx)
        assert result.passed
