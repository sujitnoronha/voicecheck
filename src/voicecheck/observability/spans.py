"""Span helpers + semantic-convention attribute names for voicecheck.

All helpers degrade to no-ops when OpenTelemetry isn't installed or
tracing was never initialized. The runner can call ``span(...)`` and
``set_attrs(...)`` unconditionally without worrying about whether OTel
is present.

Naming follows OTel-style ``noun.verb`` lowercase dotted convention so
attributes line up cleanly in Datadog / Tempo / Langfuse UIs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("voicecheck.observability")

# ── Span names ───────────────────────────────────────────────────

SPAN_SCENARIO = "voicecheck.scenario"
SPAN_TURN = "voicecheck.turn"
SPAN_TTS = "voicecheck.tts"
SPAN_STT = "voicecheck.stt"
SPAN_TRANSPORT_CONNECT = "voicecheck.transport.connect"
SPAN_TRANSPORT_DISCONNECT = "voicecheck.transport.disconnect"
SPAN_TRANSPORT_SEND = "voicecheck.transport.send"
SPAN_TRANSPORT_RECEIVE = "voicecheck.transport.receive"
SPAN_EVALUATOR = "voicecheck.evaluator"
SPAN_TOOL_CALL = "voicecheck.tool_call"
SPAN_CONVERSATION_EVAL = "voicecheck.conversation_eval"

# ── Attribute names ──────────────────────────────────────────────

ATTR_SCENARIO_NAME = "voicecheck.scenario.name"
ATTR_SCENARIO_MODE = "voicecheck.scenario.mode"
ATTR_SCENARIO_TAGS = "voicecheck.scenario.tags"
ATTR_TRANSPORT_TYPE = "voicecheck.transport.type"
ATTR_TRANSPORT_MODE = "voicecheck.transport.mode"

ATTR_TURN_INDEX = "voicecheck.turn.index"
ATTR_TURN_PASSED = "voicecheck.turn.passed"
ATTR_TURN_ERROR = "voicecheck.turn.error"
ATTR_USER_TEXT = "voicecheck.user.text"
ATTR_AGENT_TEXT = "voicecheck.agent.text"

ATTR_FIRST_BYTE_MS = "voicecheck.metrics.first_byte_ms"
ATTR_TOTAL_MS = "voicecheck.metrics.total_ms"
ATTR_TTS_DURATION_MS = "voicecheck.metrics.tts_duration_ms"
ATTR_STT_DURATION_MS = "voicecheck.metrics.stt_duration_ms"
ATTR_USER_AUDIO_DURATION_MS = "voicecheck.metrics.user_audio_duration_ms"
ATTR_AGENT_AUDIO_DURATION_MS = "voicecheck.metrics.agent_audio_duration_ms"
ATTR_AGENT_AUDIO_FRAMES = "voicecheck.metrics.agent_audio_frames"

ATTR_TTS_PROVIDER = "voicecheck.tts.provider"
ATTR_STT_PROVIDER = "voicecheck.stt.provider"

ATTR_EVAL_TYPE = "voicecheck.evaluator.type"
ATTR_EVAL_PASSED = "voicecheck.evaluator.passed"
ATTR_EVAL_SCORE = "voicecheck.evaluator.score"
ATTR_EVAL_REASON = "voicecheck.evaluator.reason"

ATTR_TOOL_NAME = "voicecheck.tool.name"
ATTR_TOOL_ARGS = "voicecheck.tool.args"
ATTR_TOOL_RESULT = "voicecheck.tool.result"
ATTR_TOOL_ERROR = "voicecheck.tool.error"

ATTR_CONV_SCORE = "voicecheck.conversation_eval.score"
ATTR_CONV_PASSED = "voicecheck.conversation_eval.passed"
ATTR_CONV_REASON = "voicecheck.conversation_eval.reason"


_ATTR_TEXT_LIMIT = 4000  # cap large text attributes to avoid blowing up backends


def _try_import_trace() -> Any:
    """Return the ``opentelemetry.trace`` module, or None if not installed."""
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]

        return trace
    except ImportError:
        return None


def _coerce_attr_value(value: Any) -> Any:
    """OTel only accepts str/bool/int/float and homogeneous lists of those.

    Anything else (dicts, custom objects) is JSON-encoded. Big strings
    are truncated so a runaway agent_text doesn't push the span over
    the OTLP message-size limit.
    """
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _ATTR_TEXT_LIMIT:
            return value[:_ATTR_TEXT_LIMIT] + "…[truncated]"
        return value
    if value is None:
        return ""
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError):
        encoded = str(value)
    if len(encoded) > _ATTR_TEXT_LIMIT:
        encoded = encoded[:_ATTR_TEXT_LIMIT] + "…[truncated]"
    return encoded


def set_attrs(span_obj: Any, attrs: dict[str, Any]) -> None:
    """Set attributes on ``span_obj`` if it is a real OTel span.

    Silently no-ops on the dummy ``_NoopSpan`` so callers don't need to
    branch on whether tracing is enabled.
    """
    if span_obj is None or not hasattr(span_obj, "set_attribute"):
        return
    for key, value in attrs.items():
        try:
            span_obj.set_attribute(key, _coerce_attr_value(value))
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("Failed to set span attribute %s: %s", key, e)


class _NoopSpan:
    """Stand-in returned when OTel isn't available.

    Mirrors enough of the OTel Span surface that calling ``set_attribute``,
    ``add_event``, ``record_exception``, etc. silently succeeds.
    """

    def set_attribute(self, *args: Any, **kwargs: Any) -> None: ...
    def set_attributes(self, *args: Any, **kwargs: Any) -> None: ...
    def add_event(self, *args: Any, **kwargs: Any) -> None: ...
    def record_exception(self, *args: Any, **kwargs: Any) -> None: ...
    def set_status(self, *args: Any, **kwargs: Any) -> None: ...
    def end(self, *args: Any, **kwargs: Any) -> None: ...
    def is_recording(self) -> bool:
        return False


_NOOP_SPAN = _NoopSpan()


@contextmanager
def span(name: str, attrs: dict[str, Any] | None = None) -> Iterator[Any]:
    """Open a span scoped to the with-block.

    No-op when OTel isn't installed or tracing wasn't initialized.

    Args:
        name: Span name (use the SPAN_* constants).
        attrs: Optional attributes to set on the new span.

    Yields:
        The active OTel span (or a no-op stand-in).
    """
    trace = _try_import_trace()
    if trace is None:
        yield _NOOP_SPAN
        return

    tracer = trace.get_tracer("voicecheck")
    with tracer.start_as_current_span(name) as s:
        if attrs:
            set_attrs(s, attrs)
        try:
            yield s
        except Exception as exc:
            try:
                s.record_exception(exc)
                from opentelemetry.trace import Status, StatusCode  # type: ignore[import-not-found]

                s.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            except Exception:  # pragma: no cover
                pass
            raise


def record_tool_call_event(
    name: str,
    args: dict[str, Any] | None = None,
    result: Any = None,
    error: str | None = None,
) -> None:
    """Attach a tool-call event to whatever span is currently active.

    Transports call this when they observe a tool/function-call message
    from the agent (VAPI ``function-call``, Retell ``tool_call_invocation``,
    custom websocket events, etc.). Captured as a span event under the
    current ``voicecheck.turn`` so the trace shows tool calls inline
    with the audio timeline.

    No-op if OTel isn't active.
    """
    trace = _try_import_trace()
    if trace is None:
        return
    current = trace.get_current_span()
    if current is None or not current.is_recording():
        return

    event_attrs: dict[str, Any] = {ATTR_TOOL_NAME: name}
    if args is not None:
        event_attrs[ATTR_TOOL_ARGS] = _coerce_attr_value(args)
    if result is not None:
        event_attrs[ATTR_TOOL_RESULT] = _coerce_attr_value(result)
    if error:
        event_attrs[ATTR_TOOL_ERROR] = error[:500]
    try:
        current.add_event("tool_call", attributes=event_attrs)
    except Exception as e:  # pragma: no cover
        logger.debug("Failed to record tool_call event: %s", e)
