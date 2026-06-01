---
name: write-voicecheck-test
description: >-
  Author a VoiceCheck test for a voice agent — turn a description of what the
  agent should do into a scenario YAML with the right evaluators, validate and
  dry-run it, then optionally wire it into pytest. Use when someone wants to
  write a VoiceCheck scenario, add a voice-agent test, assert latency / tone /
  tool calls / no prompt leaks, or test a voice agent in CI.
---

# Write a VoiceCheck test

Turn "here's what my agent should do" into a runnable VoiceCheck scenario and, if
they want it in CI, a pytest test. Assume VoiceCheck is already installed; if not,
run `/setup-voicecheck` first.

A VoiceCheck **test is a YAML scenario**: a transport (how to reach the agent),
audio providers, and a list of `turns`, where each turn sends `user:` text and
lists `expect:` evaluators that must pass. Run it with `voicecheck run`, or attach
it to pytest with `@pytest.mark.voicecheck("file.yaml")`.

Shared references (in the sibling setup skill — present when the repo is checked out):
- `../setup-voicecheck/references/transports.md` — `transport:` block per provider
- `../setup-voicecheck/references/evaluators-and-providers.md` — the 13 evaluators + audio providers

The authoritative field list is always `voicecheck schema` (emits the JSON schema).

## Step 1 — Understand what to test

Ask the user (or infer from the agent's code/prompt) before writing YAML:
- **Transport**: which stack hosts the agent (LiveKit / Daily / VAPI / Retell), or
  use `echo` for a plumbing-only test.
- **The conversation**: what does a real user say, and what must the agent do each
  turn? Get 2-4 concrete turns.
- **What would count as a failure?** This drives evaluator choice — latency budget,
  required info, forbidden disclosures, tone, tool calls, staying in character.

## Step 2 — Choose evaluators per turn

Map each "must / must-not" to an evaluator (full catalog in the reference). Common picks:

| Concern | Evaluator |
|--------|-----------|
| Responds fast enough | `latency` (`max_first_byte_ms`) |
| Says / avoids specific words | `keyword` |
| Did the right thing (free-form) | `llm_judge` (`criteria`, `min_score`) |
| Right tone | `emotional_tone` (`expected_emotions`) |
| No system-prompt / tool-name leaks | `info_leakage` |
| Called a tool (correctly / in order) | `tool_called`, `tool_sequence` |
| Stayed in character (roleplay agents) | `character_break` |
| Quality rubric (PII, policy, brand…) | `rubric_judge` |

Prefer cheap deterministic evaluators (`latency`, `keyword`, `turn_count`, `tool_*`)
for CI gates; use `llm_judge` / `rubric_judge` for behavior that needs judgment.

## Step 3 — Write the scenario

Start from the template and edit it (Claude Code expands `${CLAUDE_SKILL_DIR}` to
this skill's directory; in Codex, the template sits next to this `SKILL.md`):
```bash
cp "${CLAUDE_SKILL_DIR}/templates/scenario_template.yaml" ./<name>.yaml
```
- Paste the `transport:` block for their provider from the transports reference
  (keep `${ENV_VAR}` references).
- Keep `audio: {tts_provider: edge, stt_provider: whisper}` unless they want OpenAI.
- Write one `turns:` entry per exchange, each with a focused `expect:` list. Keep
  thresholds realistic (start loose, tighten after a baseline run).

For multi-turn realism beyond a fixed script, VoiceCheck also supports `questions`,
`persona` (LLM-driven user), and `guided` (goal-driven) modes — see the project
README's "Testing Modes". Default to scripted turns unless the user asks for those.

## Step 4 — Validate, then dry-run

Always validate first (schema only, no keys, no cost):
```bash
voicecheck validate <name>.yaml
```
Then dry-run the pipeline without spending LLM credits:
```bash
voicecheck run <name>.yaml --skip-llm-judge
```
Iterate on errors. Drop `--skip-llm-judge` once an LLM key is set and the judged
evaluators should actually score.

## Step 5 — Wire into pytest (optional, for CI)

The plugin auto-registers when VoiceCheck is installed. Simplest form:
```python
import pytest

@pytest.mark.voicecheck("tests/voice/<name>.yaml")
def test_<name>():
    """Fails if any evaluator in the scenario fails."""
```
For assertions on specific metrics, drive the runner directly:
```python
import pytest
from voicecheck.core.scenario import ScenarioRunner

@pytest.mark.asyncio
async def test_<name>_latency():
    report = await ScenarioRunner.from_yaml("tests/voice/<name>.yaml").run()
    assert report.passed
    assert report.turns[0].metrics.first_byte_ms < 2000
```
Run: `pytest -m voicecheck`. In CI, store the YAML next to other tests and gate the
build on it. Note these tests make real audio/transport calls, so CI needs the same
env vars and network access as a local run (use `echo` transport for hermetic plumbing tests).

## Step 6 — Confirm

End by stating: the scenario file written, which evaluators guard which behavior,
that it validates + dry-runs, and how to run it (`voicecheck run` / `pytest -m voicecheck`).
