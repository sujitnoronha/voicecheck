# Contributing to VoiceCheck

Thanks for your interest in contributing! VoiceCheck is an open-source E2E testing framework for voice agents, and we welcome contributions of all kinds.

## Getting Started

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) or pip

### Development Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_ORG/voicecheck.git
cd voicecheck

# Install in development mode with all extras
pip install -e ".[all,dev]"

# Run tests
pytest tests/unit/ -v
```

### Project Structure

```
src/voicecheck/
├── core/              # Types, ABCs, scenario runner, reports
├── transports/        # LiveKit, Daily, VAPI, Retell transports
│   └── websocket_base.py  # Base class for WebSocket-based transports
├── audio/             # TTS/STT providers + shared audio utilities
├── evaluators/        # Latency, keyword, LLM judge, turn count
├── conversation/      # Persona-driven dynamic conversation engine
├── storage/           # SQLite result store + HTML dashboard
├── web/               # FastAPI live dashboard
└── cli.py             # Click CLI entrypoint
```

## How to Contribute

### Adding a New Transport

VoiceCheck uses a plugin registry for transports. There are two base classes to choose from:

- **`Transport`** (direct subclass) — for WebRTC-based transports (like LiveKit, Daily)
- **`WebSocketTransport`** (extends Transport) — for transports that stream audio over WebSocket (like VAPI, Retell)

Steps:

1. Create `src/voicecheck/transports/your_transport.py`
2. Implement the `Transport` ABC from `voicecheck.core.transport`:
   - `connect(config)` — establish connection
   - `send_audio(frames)` — send PCM audio to the agent
   - `receive_audio(timeout, silence_threshold)` — capture agent response
   - `disconnect()` — clean up
   - `metrics` property — return `TransportMetrics`
   - `validate_config(config)` — validate required config keys
3. Call `register_transport("name", YourTransport)` at module level
4. Add lazy import in `cli.py` `_ensure_registrations()` and `pytest_plugin.py`
5. Add optional dependencies to `pyproject.toml`
6. Add an example YAML in `examples/`
7. Add documentation in `docs/transports/`
8. Add integration tests in `tests/integration/`

If your transport uses WebSocket, extend `WebSocketTransport` from `voicecheck.transports.websocket_base` and implement the abstract hooks:
- `_get_ws_url(config)` — return the WebSocket URL
- `_on_ws_connected(ws, config)` — post-connect setup (auth, config)
- `_encode_outbound_frame(frame)` — PCM to wire format
- `_decode_inbound_message(message)` — wire format to PCM
- `_on_ws_disconnecting(ws, config)` — pre-close cleanup

Use shared audio utilities from `voicecheck.audio.utils`:
- `is_silent(data, threshold)` — silence detection
- `pcm_to_mulaw(data)` / `mulaw_to_pcm(data)` — G.711 codec
- `resample_pcm(data, source_rate, target_rate)` — sample rate conversion

### Adding a New Evaluator

1. Create `src/voicecheck/evaluators/your_evaluator.py`
2. Implement the `Evaluator` ABC from `voicecheck.core.evaluator`:
   - `evaluate(context: EvalContext) -> EvalResult`
3. Call `register_evaluator("name", YourEvaluator)` at module level
4. Add lazy import in `cli.py` `_ensure_registrations()`
5. Add unit tests in `tests/unit/test_evaluators.py`
6. Document the evaluator in `docs/guides/evaluators.md`

### Adding a New TTS/STT Provider

1. Add your provider class to `audio/tts.py` or `audio/stt.py`
2. Register it in the provider dict at the bottom of the file
3. Add any new dependencies to `pyproject.toml` optional-dependencies

## Code Style

- Type hints on all public functions
- Docstrings on all public classes and functions
- No unused imports
- Keep modules focused — one responsibility per file
- Use lazy imports for optional dependencies with clear error messages
- Follow the existing patterns (registry, lazy imports, logging)

We use [ruff](https://docs.astral.sh/ruff/) for linting:

```bash
ruff check src/
```

## Running Tests

```bash
# Unit tests only (fast, no external services needed)
pytest tests/unit/ -v

# Integration tests (requires running services + credentials)
pytest tests/integration/ -v

# All tests
pytest -v
```

## Pull Request Process

1. Fork the repo and create a branch from `main`
2. Add tests for any new functionality
3. Ensure all tests pass: `pytest tests/unit/ -v`
4. Ensure linting passes: `ruff check src/`
5. Update README.md and docs if you've added user-facing features
6. Submit a PR with a clear description of the changes

## Reporting Issues

Please use GitHub Issues and include:
- VoiceCheck version (`voicecheck --version`)
- Python version
- Transport provider and version
- Steps to reproduce
- Expected vs actual behavior
- Relevant YAML scenario (if applicable)
