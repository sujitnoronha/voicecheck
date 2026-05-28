"""Tests for tool-call surfacing (VAPI / Retell) and tool-aware evaluators."""

from __future__ import annotations

import json

import pytest

from voicecheck.core.types import EvalContext, ToolCallEvent, TransportMetrics
from voicecheck.evaluators.tool_called import ToolCalledEvaluator
from voicecheck.evaluators.tool_sequence import ToolSequenceEvaluator

# ── VAPI parsing ─────────────────────────────────────────────────


def test_vapi_decodes_new_style_tool_calls():
    """Newer VAPI assistants emit ``tool-calls`` arrays — every call in
    the array should land as a buffered ToolCallEvent."""
    from voicecheck.transports.vapi import VAPITransport

    transport = VAPITransport()
    msg = json.dumps(
        {
            "type": "tool-calls",
            "toolCalls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "lookup_balance",
                        "arguments": json.dumps({"account_id": "acct-123"}),
                    },
                },
                {
                    "id": "call_2",
                    "function": {
                        "name": "send_receipt",
                        "arguments": {"email": "user@example.com"},
                    },
                },
            ],
        }
    )

    transport._decode_inbound_message(msg)

    calls = transport.take_tool_calls()
    assert [c.name for c in calls] == ["lookup_balance", "send_receipt"]
    assert calls[0].args == {"account_id": "acct-123"}
    assert calls[1].args == {"email": "user@example.com"}


def test_vapi_decodes_legacy_function_call_shape():
    """Older VAPI assistants emit a single ``function-call`` event."""
    from voicecheck.transports.vapi import VAPITransport

    transport = VAPITransport()
    msg = json.dumps(
        {
            "type": "function-call",
            "functionCall": {
                "name": "transfer_call",
                "parameters": {"to": "+15551234567"},
            },
        }
    )

    transport._decode_inbound_message(msg)
    calls = transport.take_tool_calls()
    assert len(calls) == 1
    assert calls[0].name == "transfer_call"
    assert calls[0].args == {"to": "+15551234567"}


def test_vapi_attaches_result_to_existing_call():
    """A ``tool-call-result`` event should patch the matching invocation."""
    from voicecheck.transports.vapi import VAPITransport

    transport = VAPITransport()
    transport._decode_inbound_message(
        json.dumps(
            {
                "type": "tool-calls",
                "toolCalls": [
                    {
                        "id": "abc",
                        "function": {
                            "name": "lookup_balance",
                            "arguments": '{"id":"x"}',
                        },
                    }
                ],
            }
        )
    )
    transport._decode_inbound_message(
        json.dumps(
            {
                "type": "tool-call-result",
                "toolCallId": "abc",
                "result": {"balance": 42.0},
            }
        )
    )

    calls = transport.take_tool_calls()
    assert len(calls) == 1
    assert calls[0].result == {"balance": 42.0}


def test_vapi_audio_passthrough_unchanged():
    """Adding tool-call parsing must not break audio passthrough."""
    from voicecheck.transports.vapi import VAPITransport

    transport = VAPITransport()
    transport._audio_format = "pcm_s16le"
    pcm = b"\x00\x01" * 160
    out = transport._decode_inbound_message(pcm)
    assert out == pcm


# ── Retell parsing ───────────────────────────────────────────────


def test_retell_decodes_tool_call_invocation():
    """Retell sends ``tool_call_invocation`` with arguments as JSON string."""
    from voicecheck.transports.retell import RetellTransport

    transport = RetellTransport()
    transport._config = {"sample_rate": 16000}
    msg = json.dumps(
        {
            "response_type": "tool_call_invocation",
            "tool_call_id": "tc_42",
            "name": "schedule_callback",
            "arguments": json.dumps({"when": "tomorrow at 3pm"}),
        }
    )

    out = transport._decode_inbound_message(msg)
    assert out is None  # control frame, not audio

    calls = transport.take_tool_calls()
    assert len(calls) == 1
    assert calls[0].name == "schedule_callback"
    assert calls[0].args == {"when": "tomorrow at 3pm"}
    # The provider call id is tracked separately so it doesn't pollute args.
    assert calls[0].call_id == "tc_42"


def test_retell_attaches_result_to_existing_call():
    from voicecheck.transports.retell import RetellTransport

    transport = RetellTransport()
    transport._config = {"sample_rate": 16000}
    transport._decode_inbound_message(
        json.dumps(
            {
                "response_type": "tool_call_invocation",
                "tool_call_id": "id1",
                "name": "lookup",
                "arguments": "{}",
            }
        )
    )
    transport._decode_inbound_message(
        json.dumps(
            {
                "response_type": "tool_call_result",
                "tool_call_id": "id1",
                "result": "ok",
            }
        )
    )

    calls = transport.take_tool_calls()
    assert len(calls) == 1
    assert calls[0].result == "ok"


# ── tool_called evaluator ────────────────────────────────────────


def _ctx(tool_calls: list[ToolCallEvent]) -> EvalContext:
    return EvalContext(
        user_text="hi",
        agent_text="hello",
        agent_audio=[],
        metrics=TransportMetrics(),
        turn_index=0,
        tool_calls=tool_calls,
    )


@pytest.mark.asyncio
async def test_tool_called_passes_when_tool_invoked():
    evaluator = ToolCalledEvaluator(name="lookup_balance")
    ctx = _ctx([ToolCallEvent(name="lookup_balance", args={"id": "x"})])
    result = await evaluator.evaluate(ctx)
    assert result.passed
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_tool_called_fails_when_tool_not_invoked():
    evaluator = ToolCalledEvaluator(name="lookup_balance")
    ctx = _ctx([ToolCallEvent(name="other_tool")])
    result = await evaluator.evaluate(ctx)
    assert not result.passed
    assert "Observed tool names" in result.reason


@pytest.mark.asyncio
async def test_tool_called_args_must_contain_filters_calls():
    evaluator = ToolCalledEvaluator(
        name="lookup_balance",
        args_must_contain={"account_id": "acct-123"},
    )
    # Same tool, wrong args.
    ctx = _ctx([ToolCallEvent(name="lookup_balance", args={"account_id": "OTHER"})])
    result = await evaluator.evaluate(ctx)
    assert not result.passed

    ctx = _ctx([ToolCallEvent(name="lookup_balance", args={"account_id": "acct-123"})])
    result = await evaluator.evaluate(ctx)
    assert result.passed


@pytest.mark.asyncio
async def test_tool_called_args_must_not_contain_blocks_pii():
    evaluator = ToolCalledEvaluator(
        name="send_receipt",
        args_must_not_contain={"email": "leaked@example.com"},
    )
    ctx = _ctx([ToolCallEvent(name="send_receipt", args={"email": "leaked@example.com"})])
    result = await evaluator.evaluate(ctx)
    assert not result.passed
    assert "forbidden" in result.reason.lower()


@pytest.mark.asyncio
async def test_tool_called_min_calls_enforced():
    evaluator = ToolCalledEvaluator(name="ping", min_calls=2)
    ctx = _ctx([ToolCallEvent(name="ping"), ToolCallEvent(name="ping")])
    result = await evaluator.evaluate(ctx)
    assert result.passed

    ctx = _ctx([ToolCallEvent(name="ping")])
    result = await evaluator.evaluate(ctx)
    assert not result.passed


def test_tool_called_rejects_empty_name():
    with pytest.raises(ValueError):
        ToolCalledEvaluator(name="")


# ── tool_sequence evaluator ──────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_sequence_subsequence_passes_with_extra_calls_between():
    evaluator = ToolSequenceEvaluator(sequence=["a", "b", "c"], mode="subsequence")
    ctx = _ctx(
        [
            ToolCallEvent(name="a"),
            ToolCallEvent(name="noise1"),
            ToolCallEvent(name="b"),
            ToolCallEvent(name="noise2"),
            ToolCallEvent(name="c"),
        ]
    )
    result = await evaluator.evaluate(ctx)
    assert result.passed


@pytest.mark.asyncio
async def test_tool_sequence_subsequence_fails_on_wrong_order():
    evaluator = ToolSequenceEvaluator(sequence=["a", "b"], mode="subsequence")
    ctx = _ctx([ToolCallEvent(name="b"), ToolCallEvent(name="a")])
    result = await evaluator.evaluate(ctx)
    assert not result.passed
    assert "Sequence broken" in result.reason


@pytest.mark.asyncio
async def test_tool_sequence_strict_rejects_extra_calls():
    evaluator = ToolSequenceEvaluator(sequence=["a", "b"], mode="strict")
    ctx = _ctx([ToolCallEvent(name="a"), ToolCallEvent(name="extra"), ToolCallEvent(name="b")])
    result = await evaluator.evaluate(ctx)
    assert not result.passed


@pytest.mark.asyncio
async def test_tool_sequence_strict_passes_on_exact_match():
    evaluator = ToolSequenceEvaluator(sequence=["a", "b"], mode="strict")
    ctx = _ctx([ToolCallEvent(name="a"), ToolCallEvent(name="b")])
    result = await evaluator.evaluate(ctx)
    assert result.passed


@pytest.mark.asyncio
async def test_tool_sequence_partial_match_score_proportional():
    evaluator = ToolSequenceEvaluator(sequence=["a", "b", "c", "d"], mode="subsequence")
    ctx = _ctx([ToolCallEvent(name="a"), ToolCallEvent(name="b")])
    result = await evaluator.evaluate(ctx)
    assert not result.passed
    assert 0 < result.score < 1


def test_tool_sequence_rejects_invalid_mode():
    with pytest.raises(ValueError):
        ToolSequenceEvaluator(sequence=["a"], mode="bogus")


def test_tool_sequence_rejects_empty_sequence():
    with pytest.raises(ValueError):
        ToolSequenceEvaluator(sequence=[])
