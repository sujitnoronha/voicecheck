# Changelog

## 0.1.0 (unreleased)

Initial release of VoiceCheck — E2E testing framework for voice agents.

### Features
- **LiveKit transport** with direct, token_server, and pre-made token connection modes
- **Multi-turn conversation testing** with scripted turns or persona-driven dynamic conversations
- **TTS providers**: Edge TTS (free), OpenAI TTS (high-quality)
- **STT providers**: Local Whisper (faster-whisper), OpenAI Whisper API
- **Evaluators**: latency, keyword matching, word count, LLM-as-judge
- **Persona engine**: LLM-generated user messages for free-flowing conversation tests
- **Post-conversation evaluation**: Score full conversations against custom criteria
- **Results storage**: SQLite-backed with run history and per-turn metrics
- **HTML dashboard**: Self-contained dashboard with Chart.js latency trends and pass/fail timeline
- **CLI**: `voicecheck run`, `voicecheck validate`, `voicecheck dashboard`
- **pytest plugin**: `@pytest.mark.voicecheck("scenario.yaml")` marker
- **YAML scenario format** with `${ENV_VAR}` expansion
