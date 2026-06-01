# Audio providers and evaluators

## Audio providers

Set in the scenario's `audio:` block. The defaults need no API key — prefer them
for a first run; switch to OpenAI when the user wants higher quality.

```yaml
audio:
  tts_provider: edge        # text-to-speech
  stt_provider: whisper     # speech-to-text
  sample_rate: 16000
  channels: 1
```

| Role | Provider | Key needed | Notes |
|------|----------|-----------|-------|
| TTS | `edge`   | none | Free Microsoft Edge voices. Default. |
| TTS | `openai` | `OPENAI_API_KEY` | Higher quality, paid. |
| STT | `whisper`| none | Local model. First run downloads ~150 MB. Default. |
| STT | `openai` | `OPENAI_API_KEY` | Cloud, faster, no local model. |

Multi-language: set `audio.language: "es"` (etc.) to auto-select voice + STT.

## Evaluators

Each turn carries an `expect:` list. Every entry is one evaluator. All 13 registered
types:

| `type` | What it checks |
|--------|----------------|
| `latency` | First-byte / total response latency vs a max (`max_first_byte_ms`). |
| `turn_count` | Response length, e.g. `min_words`. |
| `keyword` | Required / forbidden keywords in the reply. |
| `llm_judge` | Free-form `criteria` graded by an LLM, with `min_score`. Needs `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`. |
| `rubric_judge` | Scored against a named rubric dimension (task completion, PII handling, policy compliance, brand voice, empathy, …). LLM-backed. |
| `emotional_tone` | Detected tone matches `expected_emotions`. |
| `fact_accuracy` | Reply is factually consistent with given facts. |
| `info_leakage` | No system-prompt / tool-name / internal disclosures. |
| `memory_recall` | Agent remembers earlier context across turns. |
| `character_break` | Roleplay agent never admits it's an AI / breaks persona. |
| `personality_consistency` | Tone/personality stays consistent across the conversation. |
| `tool_called` | A specific tool was (or wasn't) called, with optional arg checks. |
| `tool_sequence` | Tools were called in an expected order. |

Example turn:
```yaml
turns:
  - user: "Can you help me book a flight?"
    expect:
      - type: latency
        max_first_byte_ms: 2000
      - type: emotional_tone
        expected_emotions: ["helpful", "friendly"]
      - type: llm_judge
        criteria: "Agent acknowledges the request and asks for details"
        min_score: 0.7
```

LLM-backed evaluators (`llm_judge`, `rubric_judge`, and the judged checks) cost API
calls. During iteration, run with `voicecheck run <file> --skip-llm-judge` to exercise
the full audio pipeline without spending credits.

For the authoritative field list, emit the JSON schema: `voicecheck schema`.
