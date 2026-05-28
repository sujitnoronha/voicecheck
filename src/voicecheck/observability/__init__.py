"""OpenTelemetry observability layer for voicecheck.

Emits spans for every scenario, turn, audio phase (TTS/transport/STT),
evaluator, and tool call. Compatible with any OTel-aware backend
(Datadog, Grafana Tempo, Langfuse, Honeycomb, New Relic, etc.) via OTLP.

The OTel SDK is an optional dependency. If it's not installed (or
``init_tracing`` is never called), all helpers in this package become
zero-cost no-ops — the runner keeps working unchanged.

Configure via:
- YAML ``observability:`` block in the scenario file
- CLI flags (``--otel-endpoint``, ``--otel-console``, ``--otel-service``)
- Standard ``OTEL_EXPORTER_OTLP_*`` env vars (honoured automatically)
"""

from voicecheck.observability.spans import (
    ATTR_AGENT_TEXT,
    ATTR_EVAL_PASSED,
    ATTR_EVAL_REASON,
    ATTR_EVAL_SCORE,
    ATTR_EVAL_TYPE,
    ATTR_FIRST_BYTE_MS,
    ATTR_SCENARIO_MODE,
    ATTR_SCENARIO_NAME,
    ATTR_TOOL_ARGS,
    ATTR_TOOL_NAME,
    ATTR_TOOL_RESULT,
    ATTR_TOTAL_MS,
    ATTR_TRANSPORT_TYPE,
    ATTR_TURN_INDEX,
    ATTR_TURN_PASSED,
    ATTR_USER_TEXT,
    SPAN_CONVERSATION_EVAL,
    SPAN_EVALUATOR,
    SPAN_SCENARIO,
    SPAN_STT,
    SPAN_TOOL_CALL,
    SPAN_TRANSPORT_CONNECT,
    SPAN_TRANSPORT_DISCONNECT,
    SPAN_TRANSPORT_RECEIVE,
    SPAN_TRANSPORT_SEND,
    SPAN_TTS,
    SPAN_TURN,
    record_tool_call_event,
    set_attrs,
    span,
)
from voicecheck.observability.tracing import (
    ObservabilityConfig,
    init_tracing,
    is_enabled,
    shutdown_tracing,
)

__all__ = [
    "ATTR_AGENT_TEXT",
    "ATTR_EVAL_PASSED",
    "ATTR_EVAL_REASON",
    "ATTR_EVAL_SCORE",
    "ATTR_EVAL_TYPE",
    "ATTR_FIRST_BYTE_MS",
    "ATTR_SCENARIO_MODE",
    "ATTR_SCENARIO_NAME",
    "ATTR_TOOL_ARGS",
    "ATTR_TOOL_NAME",
    "ATTR_TOOL_RESULT",
    "ATTR_TOTAL_MS",
    "ATTR_TRANSPORT_TYPE",
    "ATTR_TURN_INDEX",
    "ATTR_TURN_PASSED",
    "ATTR_USER_TEXT",
    "ObservabilityConfig",
    "SPAN_CONVERSATION_EVAL",
    "SPAN_EVALUATOR",
    "SPAN_SCENARIO",
    "SPAN_STT",
    "SPAN_TOOL_CALL",
    "SPAN_TRANSPORT_CONNECT",
    "SPAN_TRANSPORT_DISCONNECT",
    "SPAN_TRANSPORT_RECEIVE",
    "SPAN_TRANSPORT_SEND",
    "SPAN_TTS",
    "SPAN_TURN",
    "init_tracing",
    "is_enabled",
    "record_tool_call_event",
    "set_attrs",
    "shutdown_tracing",
    "span",
]
