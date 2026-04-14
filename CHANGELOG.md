# Changelog

## 0.1.0 (unreleased)

Initial release of VoiceCheck — E2E testing framework for voice agents.

### Features
- **5 transports**: LiveKit, Daily, VAPI, Retell, Telephony with pluggable registry
- **4 conversation modes**: scripted turns, fixed questions, persona-driven (LLM), guided flow
- **6 evaluators**: latency, keyword, turn_count, llm_judge, emotional_tone + pluggable registry
- **Multi-language support**: 18 languages with automatic TTS voice selection via `audio.language`
- **Audio degradation pipeline**: Gaussian noise, bandwidth reduction, packet loss, codec artifacts
- **Interruption testing**: Mid-response barge-in via `interrupt: {after_ms, with}`
- **Silence handling**: Test agent behavior on silence via `silence: {duration_s}`
- **Concurrent load testing**: `--concurrent N` for N simultaneous sessions with percentile reporting
- **Soak testing**: `--duration` for sustained testing with aggregate summaries
- **TTS providers**: Edge TTS (free, 18 languages), OpenAI TTS, file-based
- **STT providers**: Local Whisper (faster-whisper), OpenAI Whisper API
- **Persona engine**: LLM-generated user messages with goals, topics, and personality
- **Post-conversation evaluation**: Score full conversations against custom criteria
- **Results storage**: SQLite-backed with run history and per-turn metrics
- **HTML dashboard**: Self-contained dashboard with Chart.js latency trends
- **Live web dashboard**: FastAPI-based with scenario comparison and percentiles
- **CLI**: `run`, `validate`, `history`, `show`, `dashboard`, `serve`
- **pytest plugin**: `voicecheck` pytest entry point
- **YAML scenario format** with `${ENV_VAR}` expansion
- **Pure Python audio processing**: No numpy/scipy dependencies
