---
name: setup-voicecheck
description: >-
  Set up VoiceCheck end to end — install the package with the right extras, run a
  zero-key smoke test to prove the pipeline works, configure a transport
  (LiveKit, Daily, VAPI, or Retell) and audio providers, then scaffold, validate,
  and run a first scenario. Use when someone wants to install, onboard onto, or
  get started with VoiceCheck, or asks "how do I set this up / run my first test".
---

# Set up VoiceCheck

Drive the user from nothing to a passing voice-agent test. Go one step at a time,
confirm each step worked before moving on, and never write secrets into files.

This skill bundles supporting files next to this `SKILL.md`:
- `references/transports.md` — exact env vars + `transport:` block for each transport
- `references/evaluators-and-providers.md` — audio providers and the evaluator catalog
- `templates/first_scenario.yaml` — a starter scenario
- `scripts/preflight.sh` — environment check
- `scripts/check_env.py` — report which env vars a transport needs and which are set

Read a reference file only when the step needs it. Prefer the bundled files over
guessing. Paths below use `${CLAUDE_SKILL_DIR}` — Claude Code expands it to this
skill's directory whether the skill is bundled in the repo or installed under
`~/.claude/skills/`. In Codex or other tools the bundled files sit next to this
`SKILL.md`; read them by relative path or use the inline fallback shown.

## Step 0 — Preflight

Confirm the basics before installing anything. Run the bundled check (or the inline
equivalent if the path doesn't resolve):

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/preflight.sh" \
  || { python3 --version; python3 -c "import voicecheck,sys;print('voicecheck',voicecheck.__version__)" 2>/dev/null || echo "voicecheck: not installed"; }
```

VoiceCheck needs **Python 3.10+**. If `python3` is older, stop and tell the user to
upgrade (e.g. `pyenv install 3.12` or their OS package manager) before continuing.

If a virtualenv isn't active, recommend one so the install stays isolated:
```bash
python3 -m venv .venv && source .venv/bin/activate
```

## Step 1 — Install

Ask the user which path they want. Recommend the first one to start:

- **Everything (recommended for first run):** `pip install "voicecheck[all]"`
- **Targeted** (smaller install): pick a transport + audio extras, e.g.
  `pip install "voicecheck[livekit,tts,stt]"` — see the extras table in the project
  README for every combination (`livekit`, `daily`, `vapi`, `retell`, `tts`, `stt`, `dashboard`).

Install is idempotent — re-running is safe. Verify:
```bash
python3 -c "import voicecheck; print('voicecheck', voicecheck.__version__)"
```

## Step 2 — Zero-key smoke test (do this before any credentials)

Prove the install and pipeline work with the built-in **echo** transport. No API
keys, no real agent, no tokens spent. If you're inside the VoiceCheck repo:

```bash
voicecheck run examples/echo_smoke.yaml --skip-llm-judge
```

If you're not in the repo, copy the bundled template (echo-based) and run it:
```bash
cp "${CLAUDE_SKILL_DIR}/templates/first_scenario.yaml" ./first_scenario.yaml
voicecheck run first_scenario.yaml --skip-llm-judge
```

Expect `Status: PASSED`. If it fails here, the problem is the install or the audio
extras (TTS/STT) — fix that before touching transports. The first STT run downloads
a ~150 MB Whisper model; that's expected.

## Step 3 — Pick and configure a transport

Ask which voice stack the user's agent runs on:

| Transport | Use when |
|-----------|----------|
| **LiveKit** | Agent runs in a LiveKit room (WebRTC); self-hosted or LiveKit Cloud |
| **Daily** | Agent built on Daily / Pipecat |
| **VAPI** | Agent hosted on VAPI |
| **Retell** | Agent hosted on Retell |

Open `references/transports.md` and use the section for their choice. It lists the
exact environment variables and the `transport:` YAML block. Then:

1. Tell the user which env vars to set. **Never write secret values into a file
   for them.** Have them put values in a local `.env` (point them at the repo's
   `.env.example` for the names) or `export` them in the shell. Confirm `.env` is
   gitignored.
2. Check what's set without printing secret values:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/check_env.py" livekit   # or daily|vapi|retell
   ```
   The script prints each required var as SET or MISSING — never the value.
3. For OpenAI TTS/STT, the LLM judge, or persona mode, they'll also need
   `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`). The free defaults (`edge` TTS +
   local `whisper` STT) need no key — prefer those unless the user wants cloud quality.

## Step 4 — Scaffold the first real scenario

VoiceCheck has no `init` command; scenarios are plain YAML you write. Start from the
template and adapt it to the chosen transport:

```bash
cp "${CLAUDE_SKILL_DIR}/templates/first_scenario.yaml" ./my_test.yaml
```

Edit `my_test.yaml`:
- Replace the `transport:` block with the one for their transport from
  `references/transports.md` (keep `${ENV_VAR}` references — they expand at runtime).
- Keep `audio.tts_provider: edge` / `stt_provider: whisper` for a no-key start, or
  switch to `openai` (see `references/evaluators-and-providers.md`).
- Adjust the `turns:` and `expect:` blocks to something real for their agent. The
  evaluator catalog is in `references/evaluators-and-providers.md`.

## Step 5 — Validate, then run

Validate first — schema-only, needs no credentials:
```bash
voicecheck validate my_test.yaml
```
Fix any reported errors, then run it for real:
```bash
voicecheck run my_test.yaml
```
VoiceCheck checks required env vars up front and names any that are missing.

## Step 6 — Where to go next

Tell the user what they can now do:
- **Dashboard:** `pip install "voicecheck[dashboard]"` then `voicecheck serve`
  (http://localhost:8989) for run history, latency trends, and transcripts.
- **CI / pytest:** `@pytest.mark.voicecheck("my_test.yaml")` runs it as a test.
- **More power:** personas, audio degradation (noise / packet loss), `--concurrent`
  load tests, `rubric_judge` presets — all in the project README and `docs/`.

## Completion checklist

End by confirming what's set up:
- [ ] Python 3.10+ and VoiceCheck installed (version printed)
- [ ] Echo smoke test passed
- [ ] Transport chosen and its env vars reported SET by `check_env.py`
- [ ] `my_test.yaml` validates and runs
- [ ] User knows the dashboard + pytest next steps
