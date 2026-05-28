"""Observability layer tests.

Drives a real ScenarioRunner against the echo transport with an in-memory
OTel exporter, then asserts on the captured span tree, attributes, and
tool-call events. Verifies both the OTel-active path and the no-op
fallback when tracing isn't initialized.
"""

from __future__ import annotations

import pytest

# Skip the whole module if the OTel SDK isn't available (matches voicecheck's
# soft-dep policy — the runner still works without it).
pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import voicecheck.transports.echo  # noqa: F401  # ensure registration
from voicecheck.core.scenario import (
    AudioConfig,
    ExpectConfig,
    Scenario,
    ScenarioRunner,
    SettingsConfig,
    TransportConfig,
    TurnConfig,
)
from voicecheck.observability import (
    SPAN_EVALUATOR,
    SPAN_SCENARIO,
    SPAN_STT,
    SPAN_TRANSPORT_CONNECT,
    SPAN_TRANSPORT_DISCONNECT,
    SPAN_TRANSPORT_RECEIVE,
    SPAN_TRANSPORT_SEND,
    SPAN_TTS,
    SPAN_TURN,
    record_tool_call_event,
)
from voicecheck.transports.echo import EchoTransport


@pytest.fixture
def in_memory_exporter():
    """Install a fresh in-memory exporter as the global tracer provider.

    Each test gets a clean provider so spans don't leak between cases.
    Resetting trace._TRACER_PROVIDER directly is the only way to swap
    providers in OTel — ``set_tracer_provider`` no-ops if one is already
    set after a real exporter was installed.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    yield exporter
    exporter.clear()
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]


def _scripted_scenario() -> Scenario:
    """A minimal scenario that runs end-to-end against the echo transport.

    No TTS/STT providers — we use silence frames + skip semantic evals so
    the test doesn't need OPENAI_API_KEY or whisper.
    """
    return Scenario(
        name="obs-test",
        transport=TransportConfig(
            type="echo",
            mode="direct",
            config={
                "response_text": "",  # → echo emits silent PCM
                "synthesize": False,
                "first_byte_delay_ms": 0,
            },
        ),
        audio=AudioConfig(
            tts_provider="edge",  # never invoked when turn.silence is set
            stt_provider="whisper",  # never invoked — agent_audio is silence
            sample_rate=16000,
            channels=1,
        ),
        turns=[
            TurnConfig(
                user="hello",
                expect=[ExpectConfig(type="latency", max_ms=10000)],
                silence=None,
            )
        ],
        settings=SettingsConfig(turn_timeout=2.0, silence_threshold=0.2),
    )


@pytest.mark.asyncio
async def test_scenario_emits_expected_span_tree(in_memory_exporter, monkeypatch):
    """Full run produces scenario → connect/turn/disconnect spans, with
    tts/send/receive/stt/evaluator nested under the turn span."""
    # Stub TTS/STT so we don't hit the network.
    from voicecheck.audio import stt as stt_mod
    from voicecheck.audio import tts as tts_mod
    from voicecheck.core.types import AudioFrame, TranscriptSegment

    class _StubTTS:
        async def synthesize(self, text):
            return [AudioFrame(data=b"\x00" * 320, sample_rate=16000, num_channels=1)]

    class _StubSTT:
        async def transcribe(self, frames):
            return TranscriptSegment(text="agent reply")

    monkeypatch.setitem(tts_mod._TTS_PROVIDERS, "edge", lambda **kw: _StubTTS())
    monkeypatch.setitem(stt_mod._STT_PROVIDERS, "whisper", lambda **kw: _StubSTT())

    scenario = _scripted_scenario()
    runner = ScenarioRunner(scenario, skip_llm_judge=True)
    await runner.run()

    spans = in_memory_exporter.get_finished_spans()
    names = [s.name for s in spans]

    # Required spans, in any order (BatchSpanProcessor can flush in flush order).
    for required in (
        SPAN_SCENARIO,
        SPAN_TRANSPORT_CONNECT,
        SPAN_TURN,
        SPAN_TTS,
        SPAN_TRANSPORT_SEND,
        SPAN_TRANSPORT_RECEIVE,
        SPAN_STT,
        SPAN_EVALUATOR,
        SPAN_TRANSPORT_DISCONNECT,
    ):
        assert required in names, f"missing span {required} (got {names})"


@pytest.mark.asyncio
async def test_scenario_span_carries_mode_and_pass_attrs(in_memory_exporter, monkeypatch):
    """The root scenario span exposes mode, transport, and final pass count."""
    from voicecheck.audio import stt as stt_mod
    from voicecheck.audio import tts as tts_mod
    from voicecheck.core.types import AudioFrame, TranscriptSegment

    class _StubTTS:
        async def synthesize(self, text):
            return [AudioFrame(data=b"\x00" * 320, sample_rate=16000, num_channels=1)]

    class _StubSTT:
        async def transcribe(self, frames):
            return TranscriptSegment(text="ok")

    monkeypatch.setitem(tts_mod._TTS_PROVIDERS, "edge", lambda **kw: _StubTTS())
    monkeypatch.setitem(stt_mod._STT_PROVIDERS, "whisper", lambda **kw: _StubSTT())

    scenario = _scripted_scenario()
    runner = ScenarioRunner(scenario, skip_llm_judge=True)
    await runner.run()

    spans = in_memory_exporter.get_finished_spans()
    scenario_span = next(s for s in spans if s.name == SPAN_SCENARIO)

    assert scenario_span.attributes["voicecheck.scenario.name"] == "obs-test"
    assert scenario_span.attributes["voicecheck.scenario.mode"] == "scripted"
    assert scenario_span.attributes["voicecheck.transport.type"] == "echo"
    assert scenario_span.attributes["voicecheck.scenario.total_turns"] == 1


@pytest.mark.asyncio
async def test_evaluator_span_records_pass_score(in_memory_exporter, monkeypatch):
    """Each evaluator gets its own span with passed/score/reason attrs."""
    from voicecheck.audio import stt as stt_mod
    from voicecheck.audio import tts as tts_mod
    from voicecheck.core.types import AudioFrame, TranscriptSegment

    class _StubTTS:
        async def synthesize(self, text):
            return [AudioFrame(data=b"\x00" * 320, sample_rate=16000, num_channels=1)]

    class _StubSTT:
        async def transcribe(self, frames):
            return TranscriptSegment(text="ok")

    monkeypatch.setitem(tts_mod._TTS_PROVIDERS, "edge", lambda **kw: _StubTTS())
    monkeypatch.setitem(stt_mod._STT_PROVIDERS, "whisper", lambda **kw: _StubSTT())

    runner = ScenarioRunner(_scripted_scenario(), skip_llm_judge=True)
    await runner.run()

    spans = in_memory_exporter.get_finished_spans()
    eval_spans = [s for s in spans if s.name == SPAN_EVALUATOR]
    assert eval_spans, "expected at least one evaluator span"

    latency_span = next(
        s for s in eval_spans if s.attributes["voicecheck.evaluator.type"] == "latency"
    )
    assert "voicecheck.evaluator.passed" in latency_span.attributes
    assert "voicecheck.evaluator.score" in latency_span.attributes


@pytest.mark.asyncio
async def test_transport_emit_tool_call_records_span_event(in_memory_exporter):
    """Transport.emit_tool_call attaches a 'tool_call' event to the active span."""
    from voicecheck.observability import span as span_cm

    transport = EchoTransport()
    with span_cm("voicecheck.turn"):
        transport.emit_tool_call(
            "lookup_balance",
            args={"account_id": "abc-123"},
            result={"balance": 42.0},
        )

    # The buffered list is also populated for inclusion in TurnResult.
    events = transport.take_tool_calls()
    assert len(events) == 1
    assert events[0].name == "lookup_balance"
    assert events[0].args == {"account_id": "abc-123"}

    # Span event should be on the most recently completed span.
    spans = in_memory_exporter.get_finished_spans()
    turn_span = next(s for s in spans if s.name == "voicecheck.turn")
    tool_events = [e for e in turn_span.events if e.name == "tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0].attributes["voicecheck.tool.name"] == "lookup_balance"


def test_record_tool_call_event_is_noop_outside_span(in_memory_exporter):
    """Calling record_tool_call_event with no active span must not crash."""
    record_tool_call_event("orphan_call", args={"x": 1})  # no exception
    # And no orphan spans appear.
    assert len(in_memory_exporter.get_finished_spans()) == 0


@pytest.mark.asyncio
async def test_observability_helpers_are_noop_when_otel_not_initialized():
    """When the global tracer provider is the proxy (no SDK init), span()
    yields a no-op stand-in that quietly accepts attribute writes.

    This is the cold-start path: a user runs voicecheck without enabling
    observability and pays zero cost.
    """
    # Reset to the proxy provider so trace.get_tracer returns the noop tracer.
    trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]

    from voicecheck.observability import record_tool_call_event, set_attrs
    from voicecheck.observability import span as span_cm

    with span_cm("voicecheck.turn", attrs={"a": 1}) as s:
        # Doesn't matter what we call — none of these should raise.
        set_attrs(s, {"b": 2})
        record_tool_call_event("noop_tool", args={"k": "v"})


def test_init_tracing_returns_false_without_endpoint(monkeypatch):
    """Init silently returns False when there's no exporter to install."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    # Reset module-level state so init can run cleanly.
    from voicecheck.observability import tracing as tracing_mod

    tracing_mod._PROVIDER = None
    tracing_mod._ENABLED = False

    from voicecheck.observability import ObservabilityConfig, init_tracing, is_enabled

    cfg = ObservabilityConfig(enabled=True, console=False, endpoint=None)
    assert init_tracing(cfg) is False
    assert is_enabled() is False
