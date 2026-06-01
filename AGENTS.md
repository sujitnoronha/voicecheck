# AGENTS.md — VoiceCheck

Instructions for Codex (and other agents reading AGENTS.md). The full guidance lives
in [CLAUDE.md](CLAUDE.md) — read it; the two files are kept in sync.

VoiceCheck is an end-to-end testing framework for voice agents. A **test is a YAML
scenario**: a `transport` (how to reach the agent — `livekit`/`daily`/`vapi`/`retell`,
or keyless `echo`), an `audio` block (`edge` TTS + `whisper` STT need no keys), and
`turns`, where each turn sends `user:` text and lists `expect:` evaluators that must
pass (e.g. `latency`, `keyword`, `llm_judge`, `info_leakage`, `tool_called`).

## Skills

Agent Skills are in `.agents/skills/` (mirrors `.claude/skills/`). Invoke via `/skills`
or `$skillname`:
- **`setup-voicecheck`** — install, smoke-test, configure a transport, run a first scenario.
- **`write-voicecheck-test`** — author a scenario YAML + evaluators, validate/dry-run, wire pytest.

## Authoring loop

`voicecheck validate <f>` (schema only) → `voicecheck run <f> --skip-llm-judge`
(dry-run, no cost) → `voicecheck run <f>`. The authoritative field list is
`voicecheck schema` — don't invent evaluator names or config keys. pytest:
`@pytest.mark.voicecheck("scenario.yaml")`.

## Repo rules

- Python 3.10+. CI gates on `ruff check src/ tests/` AND `ruff format --check src/ tests/`
  (run `ruff format src/ tests/` before committing) and `pytest tests/unit/`.
- Never write real API keys into files; scenarios use `${ENV_VAR}`, set in a local `.env`.
- Do not modify files under `.claude/` or `.agents/` unless the task is specifically
  about the skills.
