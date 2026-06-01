# Changelog

## Unreleased

### Added
- **Agent Skills for Claude Code & Codex**: `setup-voicecheck` (install → smoke test → configure a transport → run a first scenario) and `write-voicecheck-test` (author a scenario with the right evaluators, validate/dry-run, wire into pytest). They ship inside the pip package; run `voicecheck install-skill` (add `--codex`) to drop them into your agent's skills directory, or get them automatically when you clone the repo.
- `voicecheck install-skill` command, and a richer `voicecheck --help` overview.
- `CLAUDE.md` / `AGENTS.md` so coding agents understand the testing model (scenario YAML, evaluators, validate→run, pytest marker) without invoking a skill.

## 0.2.1

### Fixed
- **Path traversal in run trigger**: `POST /api/runs` confines the body-supplied `scenario_name` to the scenarios directory, closing an arbitrary `.yaml` file-read hole (the `{name}` routes were already guarded; the body field was not).
- **Dashboard run stability**: serialize the shared SQLite connection so a concurrent write can no longer commit a half-written run, and always clean up the inline-YAML temp file and live-run queue when a run ends (no more leaks when no client connects).

## 0.2.0

### Added
- **Observability**: tracing module (`voicecheck.observability`) plus a guide at `docs/guides/observability.md`.
- **Tool-call evaluators**: `tool_called` and `tool_sequence` for asserting agent tool usage.
- **Baselines**: store, compare, and report metric baselines (`storage/baselines.py`, `storage/output.py`).
- **Scenario builder UI**: create/edit scenarios from the dashboard, with live YAML preview, history, live-run, baselines, and scenarios pages.
- **`voicecheck.schema.json`**: JSON schema for scenario files.
- Expanded CLI commands and richer VAPI/Retell transport support.

### Changed
- Sync `__version__` and the dashboard's reported version to the package version.

### Fixed
- Confine scenario-file web routes to the scenarios directory, blocking path traversal via the scenario name (read/write/delete).
- Version mismatch where the CLI reported `0.1.0` while the package was `0.1.1`.

## 0.1.1

- Replace kid-specific `persona_kid.yaml` example with `persona_frustrated_customer.yaml` — a broader de-escalation / empathy / resolution scenario.
- Polish README hero, add "Why VoiceCheck" section, fill missing env vars in `.env.example`.
- Dashboard first-version polish: KPI hero row, relative time on cards, percentile mini-row, nav brand mark, empty-state quick-start. Fix compare page duplicate-column bug.
- Add 7 industry example scenarios under `examples/industries/` (banking, healthcare, insurance, e-commerce, hotel, restaurant, appointment).
- Remove internal `ROADMAP.md` and untrack `RELEASING.md` (maintainer-only).
- Untrack `.claude/` local tooling state.
- Satisfy ruff lint + format for CI.

## 0.1.0

Initial release of VoiceCheck — E2E testing framework for voice agents.

### Features
- **4 transports**: LiveKit, Daily, VAPI, Retell with pluggable registry
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
