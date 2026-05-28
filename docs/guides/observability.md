# Observability

VoiceCheck emits OpenTelemetry spans for every scenario, turn, audio phase, evaluator, and tool call. Pipe them into Datadog, Grafana Tempo, Honeycomb, Langfuse, New Relic, or any OTLP-compatible backend.

The OTel SDK is an optional dependency. Without it, voicecheck behaves exactly as before — there's zero overhead until you opt in.

## Install

```bash
pip install 'voicecheck[otel]'
```

## Enable

Three ways, listed by precedence:

**1. CLI flags** (highest precedence — useful for one-off CI runs):

```bash
voicecheck run scenario.yaml \
  --otel-endpoint https://otlp.example.com/v1/traces \
  --otel-service my-voice-agent-tests
```

**2. YAML scenario block:**

```yaml
observability:
  enabled: true
  service_name: my-voice-agent-tests
  endpoint: ${OTEL_EXPORTER_OTLP_ENDPOINT}
  headers:
    authorization: ${OTEL_AUTH}
  resource_attrs:
    deployment.environment: staging
```

**3. Standard OTel env vars** (works with no other config):

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.example.com/v1/traces
export OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer%20...
voicecheck run scenario.yaml
```

For local debugging, dump spans to stderr with `--otel-console`.

## Span schema

```
voicecheck.scenario              (root)
├── voicecheck.transport.connect
├── voicecheck.turn               (one per turn)
│   ├── voicecheck.tts
│   ├── voicecheck.transport.send
│   ├── voicecheck.transport.receive
│   ├── voicecheck.tool_call      (event on the turn span)
│   ├── voicecheck.stt
│   └── voicecheck.evaluator      (one per evaluator)
├── voicecheck.conversation_eval  (questions/persona/guided modes)
└── voicecheck.transport.disconnect
```

### Key attributes

| Span | Attribute | Type |
|------|-----------|------|
| `voicecheck.scenario` | `voicecheck.scenario.name` | string |
| | `voicecheck.scenario.mode` | `scripted` \| `questions` \| `persona` \| `guided` |
| | `voicecheck.transport.type` | string |
| | `voicecheck.scenario.passed` | bool |
| | `voicecheck.scenario.passed_turns` | int |
| | `voicecheck.scenario.total_turns` | int |
| `voicecheck.turn` | `voicecheck.turn.index` | int |
| | `voicecheck.user.text` | string |
| | `voicecheck.agent.text` | string |
| | `voicecheck.turn.passed` | bool |
| | `voicecheck.turn.error` | string (empty if no error) |
| | `voicecheck.turn.tool_call_count` | int |
| `voicecheck.tts` | `voicecheck.tts.provider`, `voicecheck.tts.duration_ms`, `voicecheck.tts.text` | |
| `voicecheck.stt` | `voicecheck.stt.provider`, `voicecheck.stt.duration_ms`, `voicecheck.stt.text` | |
| `voicecheck.transport.receive` | `voicecheck.metrics.first_byte_ms`, `voicecheck.metrics.total_ms` | float |
| `voicecheck.evaluator` | `voicecheck.evaluator.type`, `voicecheck.evaluator.passed`, `voicecheck.evaluator.score`, `voicecheck.evaluator.reason` | |

### Tool calls

Tool/function calls observed by a transport are attached as a `tool_call` event on the active `voicecheck.turn` span, with these event attributes:

- `voicecheck.tool.name`
- `voicecheck.tool.args` (JSON-encoded)
- `voicecheck.tool.result` (JSON-encoded, if available)
- `voicecheck.tool.error` (if the tool errored)

**Built-in support:**
- **VAPI** — `tool-calls` and legacy `function-call` events are surfaced automatically. Results from `tool-call-result` are matched back to the invocation by `toolCallId`.
- **Retell** — `tool_call_invocation` and `tool_call_result` events are surfaced automatically.

**Custom transports** plug in by calling `self.emit_tool_call(name, args, result, call_id="...")` from their inbound-message handler.

Tool-aware evaluators ([`tool_called`](evaluators.md#tool_called), [`tool_sequence`](evaluators.md#tool_sequence)) can assert against the same `tool_calls` list, so the same data drives both your test assertions and your traces.

## Backend recipes

### Datadog

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://trace.agent.datadoghq.com/v1/traces
export OTEL_EXPORTER_OTLP_HEADERS=DD-API-KEY=$DATADOG_API_KEY
voicecheck run scenarios/
```

Spans show up in APM under the `service.name` you set. Filter by `voicecheck.scenario.passed:false` to find regressions.

### Grafana Tempo / Cloud

```yaml
observability:
  enabled: true
  endpoint: https://tempo-prod-04-prod-us-east-0.grafana.net/tempo
  headers:
    authorization: Basic ${GRAFANA_BASIC_AUTH}
```

Use the trace explorer to drill from a failing scenario down to the offending evaluator span.

### Langfuse

Langfuse accepts OTLP traces directly. Point the endpoint at your project and tag with `service.name` set to the agent under test — voicecheck spans appear in the Langfuse traces tab alongside your agent's own LLM spans, correlated by trace ID when the agent propagates W3C `traceparent`.

### Honeycomb

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://api.honeycomb.io/v1/traces
export OTEL_EXPORTER_OTLP_HEADERS=x-honeycomb-team=$HONEYCOMB_API_KEY
voicecheck run scenarios/ --otel-service voice-agent-qa
```

## What this unlocks

- **Latency drilldown**: see TTS/transport/STT/evaluator timing per turn instead of one aggregate `total_ms`.
- **Tool-call audits**: assert which tools were called with which arguments by reading span events — works alongside the existing evaluators or as a debugging aid.
- **Cross-system correlation**: when your agent emits OTel under the same trace context, voicecheck's spans nest with your agent's LLM/tool spans for end-to-end traces.
- **Regression triage**: filter on `voicecheck.scenario.passed:false` and pivot by `voicecheck.scenario.name` to find which scenarios broke after a deploy.
