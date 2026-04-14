# Getting Started with VoiceCheck

VoiceCheck is an end-to-end testing framework for voice agents. It tests the full audio loop: your text is synthesized to speech, sent to the agent through a transport (LiveKit, Daily, VAPI, Retell, or your own custom transport), the agent's audio response is captured, transcribed back to text, and then evaluated against your criteria.

This guide walks you through installing VoiceCheck, writing your first test scenario, running it, and understanding the output.

## Prerequisites

- Python 3.10 or later
- A running voice agent accessible via one of the supported transports
- [ffmpeg](https://ffmpeg.org/) installed on your system (required by the default TTS provider)

## Installation

Install VoiceCheck with the extras matching your voice agent platform:

```bash
# LiveKit agents
pip install voicecheck[livekit,tts,stt]

# Daily/Pipecat agents
pip install voicecheck[daily,tts,stt]

# VAPI agents
pip install voicecheck[vapi,tts,stt]

# Retell agents
pip install voicecheck[retell,tts,stt]

# Everything (all transports + LLM judge + dashboard)
pip install voicecheck[all]
```

The `tts` extra installs Edge TTS (free, no API key) and the `stt` extra installs faster-whisper for local transcription.

### Optional extras

| Extra | What it adds |
|---|---|
| `llm` | OpenAI and Anthropic SDKs for the `llm_judge` evaluator and persona conversations |
| `dashboard` | FastAPI + Jinja2 for the live web dashboard (`voicecheck serve`) |
| `dev` | pytest, ruff, and other development tools |

## Environment Setup

Create a `.env` file (or export variables in your shell) with credentials for your transport. Here is an example for LiveKit direct mode:

```bash
# .env
LIVEKIT_URL=wss://your-livekit-server.example.com
LIVEKIT_API_KEY=APIxxxxxxxx
LIVEKIT_API_SECRET=your-api-secret
VOICECHECK_AGENT_NAME=my-voice-agent

# Required for persona mode and llm_judge evaluator
OPENAI_API_KEY=sk-...
```

VoiceCheck supports `${ENV_VAR}` syntax in YAML files, so sensitive values never need to be hardcoded.

For provider-specific setup details, see the transport documentation:
- LiveKit: `docs/transports/livekit.md`
- Daily: `docs/transports/daily.md`
- VAPI: `docs/transports/vapi.md`
- Retell: `docs/transports/retell.md`
- Custom transports: see [Python API Reference](../reference/python-api.md#creating-a-custom-transport)

## Your First Scenario

Create a file called `my_first_test.yaml`:

```yaml
name: "My first voice agent test"
description: "A simple scripted test with two turns"

transport:
  type: livekit          # or: daily, vapi, retell
  mode: direct           # mode depends on your transport
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "${VOICECHECK_AGENT_NAME}"

audio:
  tts_provider: edge     # free, no API key needed
  stt_provider: whisper   # local transcription via faster-whisper
  sample_rate: 16000

turns:
  - user: "Hello! What is your name?"
    expect:
      - type: latency
        max_first_byte_ms: 4000
      - type: turn_count
        min_words: 3
      - type: keyword
        must_not_contain: ["error", "exception"]

  - user: "Tell me something interesting."
    expect:
      - type: latency
        max_first_byte_ms: 4000
      - type: turn_count
        min_words: 5

settings:
  turn_timeout: 20.0
  silence_threshold: 2.0
```

This is a **scripted** scenario with two explicit conversation turns. Each turn specifies what the simulated user says and what evaluators to run on the agent's response.

## Validate Before Running

Check that your YAML is valid without making any API calls:

```bash
voicecheck validate my_first_test.yaml
```

If everything is correct, you will see:

```
my_first_test.yaml: OK
```

## Run the Test

```bash
voicecheck run my_first_test.yaml
```

Add `-v` for verbose logging to see the full audio pipeline in action:

```bash
voicecheck run my_first_test.yaml -v
```

## Understanding the Output

After a successful run, VoiceCheck prints a console report:

```
============================================================
  VoiceCheck Report: My first voice agent test
  Status: PASSED
  Turns: 2/2 passed
============================================================

Turn 1: [PASS]
  User: Hello! What is your name?
  Agent: Hi there! My name is Luna, nice to meet you!
  Timing: first_byte=1523ms | total=3200ms | tts=340ms | stt=210ms
  Audio:  agent=2.8s (12 words, 4.3 wps) | user=1.2s
  [+] latency: Latency OK (first_byte=1523ms, total=3200ms) (score=1.00)
  [+] turn_count: Response length OK (12 words) (score=1.00)
  [+] keyword: All keyword checks passed (score=1.00)

Turn 2: [PASS]
  User: Tell me something interesting.
  Agent: Did you know that octopuses have three hearts? Two pump blood ...
  Timing: first_byte=1891ms | total=5400ms | tts=280ms | stt=310ms
  Audio:  agent=4.5s (24 words, 5.3 wps) | user=1.0s
  [+] latency: Latency OK (first_byte=1891ms, total=5400ms) (score=1.00)
  [+] turn_count: Response length OK (24 words) (score=1.00)

============================================================
Result: PASSED
============================================================
```

Key metrics in the output:

- **first_byte**: Time from end of user speech to first agent audio (response latency)
- **total**: Time from end of user speech to last agent audio
- **tts/stt**: Time spent on text-to-speech synthesis and speech-to-text transcription
- **[+] / [x]**: Pass or fail indicator for each evaluator

### Exit codes

| Code | Meaning |
|---|---|
| 0 | All scenarios passed |
| 1 | One or more scenarios failed |

## Saving Results

By default, every run is saved to a local SQLite database at `~/.voicecheck/results.db`. You can:

- View history: `voicecheck history`
- Inspect a run: `voicecheck show <run_id>`
- Generate a dashboard: `voicecheck dashboard --open`
- Export JSON: `voicecheck run my_first_test.yaml -o results.json`
- Save audio artifacts: `voicecheck run my_first_test.yaml --save-audio ./artifacts`

## Next Steps

Now that you have a working test, explore these topics:

- **[Testing Modes](testing-modes.md)**: Learn about all four testing modes -- scripted, questions, persona, and guided flow
- **[Evaluators](evaluators.md)**: Deep dive into latency, keyword, turn_count, and llm_judge evaluators, plus how to write custom ones
- **[Soak Testing](soak-testing.md)**: Run your tests in a loop for extended periods to find intermittent issues
- **[CI/CD Integration](ci-cd.md)**: Run VoiceCheck in GitHub Actions and other CI pipelines
- **[YAML Schema Reference](../reference/yaml-schema.md)**: Complete reference for every field in a scenario file
- **[CLI Reference](../reference/cli.md)**: All commands, options, and flags
- **[Python API Reference](../reference/python-api.md)**: Use VoiceCheck programmatically as a library
