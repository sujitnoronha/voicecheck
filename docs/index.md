# VoiceCheck Documentation

**The open-source E2E testing framework for voice agents.**

VoiceCheck tests the full audio loop — TTS, transport, agent processing, STT, and evaluation — across any voice platform. It catches the issues that text-only tests miss: audio encoding bugs, real-world latency, turn-taking failures, and transport-level connection problems.

## Getting Started

- [Getting Started Guide](guides/getting-started.md) — Install, configure, and run your first test

## Guides

- [Testing Modes](guides/testing-modes.md) — Scripted, Questions, Persona, and Guided Flow modes
- [Evaluators](guides/evaluators.md) — Latency, keyword, turn count, LLM judge, and custom evaluators
- [Soak Testing](guides/soak-testing.md) — Long-running stress tests with aggregate statistics
- [CI/CD Integration](guides/ci-cd.md) — pytest plugin, GitHub Actions, cost optimization

## Transport Providers

VoiceCheck supports 4 transport providers out of the box, with a plugin registry for adding your own:

| Transport | Type | Install extra | Documentation |
|-----------|------|--------------|---------------|
| **LiveKit** | WebRTC | `livekit` | [LiveKit Transport](transports/livekit.md) |
| **Daily / Pipecat** | WebRTC | `daily` | [Daily Transport](transports/daily.md) |
| **VAPI** | WebSocket | `vapi` | [VAPI Transport](transports/vapi.md) |
| **Retell** | WebSocket | `retell` | [Retell Transport](transports/retell.md) |

## Reference

- [YAML Schema Reference](reference/yaml-schema.md) — Complete scenario YAML field documentation
- [CLI Reference](reference/cli.md) — All commands, options, and flags
- [Python API Reference](reference/python-api.md) — Programmatic usage and extension

## Architecture

```
voicecheck/
├── core/              # Types, ABCs, scenario runner, report generation
│   ├── transport.py   # Abstract Transport base class + registry
│   ├── evaluator.py   # Abstract Evaluator base class + registry
│   ├── scenario.py    # YAML loading, validation, ScenarioRunner orchestrator
│   ├── types.py       # AudioFrame, TransportMetrics, TurnResult, EvalResult
│   ├── report.py      # Console and JSON report generation
│   └── soak.py        # Soak/stress testing runner
│
├── transports/        # Transport implementations
│   ├── livekit.py     # LiveKit (WebRTC)
│   ├── daily.py       # Daily/Pipecat (WebRTC)
│   ├── vapi.py        # VAPI (REST API + WebSocket)
│   ├── retell.py      # Retell (REST API + WebSocket)
│   └── websocket_base.py  # Base class for WebSocket transports
│
├── audio/             # Audio processing
│   ├── tts.py         # Edge TTS, OpenAI TTS, File provider
│   ├── stt.py         # Local Whisper, OpenAI Whisper
│   └── utils.py       # Silence detection, G.711 mu-law, PCM resampling
│
├── evaluators/        # Turn-level evaluators
│   ├── latency.py     # Response time thresholds
│   ├── keyword.py     # Word matching
│   ├── turn_count.py  # Response length
│   └── llm_judge.py   # LLM-powered semantic evaluation
│
├── conversation/      # Persona engine
│   └── engine.py      # LLM-driven dynamic conversation generation
│
├── storage/           # Persistence
│   ├── store.py       # SQLite result store
│   └── dashboard.py   # Static HTML dashboard generator
│
├── web/               # Live dashboard
│   └── app.py         # FastAPI web application
│
├── cli.py             # Click CLI
└── pytest_plugin.py   # pytest marker integration
```

## How It Works

For each turn in a scenario:

1. **TTS**: Synthesize user text into PCM audio frames
2. **Send**: Publish audio to the voice agent via the transport
3. **Receive**: Capture the agent's audio response (silence detection determines when agent stops)
4. **STT**: Transcribe agent audio back to text
5. **Evaluate**: Run each evaluator (latency, keyword, LLM judge, etc.)

The transport layer is pluggable — the same scenario YAML works across providers by changing the `transport.type` field.

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines on adding transports, evaluators, and providers.
