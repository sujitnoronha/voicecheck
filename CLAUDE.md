# CLAUDE.md — VoiceCheck

Guidance for AI coding agents (Claude Code, Codex) working in this repo. VoiceCheck
is an end-to-end testing framework for voice agents: a YAML scenario synthesizes
real audio, streams it through a real transport, captures the agent's reply, and
grades every turn.

## Skills in this repo

Two Agent Skills ship with the repo **and inside the pip package** (canonical source:
`src/voicecheck/skills/`). They auto-load in Claude Code when the repo is open
(`.claude/skills/` symlinks to the package copy) and in Codex (`.agents/skills/`).
Pip users (who didn't clone the repo) get them via `voicecheck install-skill`, which
copies them into `~/.claude/skills/` (add `--codex` for `~/.agents/skills/`).

- **`/setup-voicecheck`** — install VoiceCheck, smoke-test with the zero-key `echo`
  transport, configure a transport + audio providers, scaffold and run a first
  scenario. Use for onboarding / "how do I get started".
- **`/write-voicecheck-test`** — turn a description of an agent's expected behavior
  into a scenario YAML with the right evaluators, validate + dry-run it, and wire it
  into pytest. Use for "write a voice test / add a scenario / test this agent".

The skills' `references/` files (transport env vars, the evaluator catalog) are the
quickest accurate source when authoring scenarios.

## How testing works (the model to keep in mind)

A **test is a YAML scenario**, not Python:
- `transport:` — how to reach the agent (`livekit`, `daily`, `vapi`, `retell`, or
  built-in `echo` for keyless plumbing tests).
- `audio:` — `tts_provider` / `stt_provider`. Defaults `edge` + `whisper` need no key.
- `turns:` — a list; each turn sends `user:` text and lists `expect:` evaluators that
  must all pass. 13 evaluators: `latency`, `keyword`, `turn_count`, `llm_judge`,
  `rubric_judge`, `emotional_tone`, `fact_accuracy`, `info_leakage`, `memory_recall`,
  `character_break`, `personality_consistency`, `tool_called`, `tool_sequence`.

Authoring loop: write YAML → `voicecheck validate <f>` (schema only, no keys) →
`voicecheck run <f> --skip-llm-judge` (dry-run, no LLM cost) → `voicecheck run <f>`.
The authoritative field list is `voicecheck schema`. Don't invent evaluator names or
config keys — check the schema or the skill references.

pytest integration is auto-registered: `@pytest.mark.voicecheck("scenario.yaml")`
runs a scenario as a test; or drive `ScenarioRunner.from_yaml(path).run()` and assert
on `report.passed` / `report.turns[i].metrics`.

## Key commands

```bash
voicecheck run <file>            # run a scenario (add --skip-llm-judge to dry-run)
voicecheck validate <file>      # schema check, no credentials
voicecheck serve                # dashboard at http://localhost:8989
voicecheck schema               # emit the scenario JSON schema
voicecheck install-skill        # copy the bundled skills into ~/.claude/skills (--codex too)
pytest -m voicecheck            # run scenario-marked tests
```

## Dev notes

- **Python 3.10+.** Install dev deps: `pip install -e ".[all,dev]"`.
- **Tests:** `pytest tests/unit/`. **Lint (CI gates on both):** `ruff check src/ tests/`
  and `ruff format --check src/ tests/` — run `ruff format src/ tests/` before committing.
- Never write real API keys into files. Scenarios reference secrets via `${ENV_VAR}`,
  expanded at run time; values go in a local (gitignored) `.env` or the shell.

## Skill routing

When a request matches a skill, invoke it. Setup/onboarding → `/setup-voicecheck`.
Writing or adding a voice-agent test → `/write-voicecheck-test`.
