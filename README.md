# VoiceCheck

**The open-source E2E testing framework for voice agents** — test the full audio loop across any provider.

```
Your text ──► TTS ──► Audio ──► Transport ──► Agent ──► Audio ──► STT ──► Evaluate
                                   │
                    LiveKit / Daily / VAPI / Retell / Telephony
```

---

## Why VoiceCheck?

Most voice agent testing stops at the text layer. But real users **speak**, and the agent **speaks back**. VoiceCheck tests the full audio loop end-to-end, catching issues that text-only tests miss:

- Audio encoding/decoding bugs
- Real-world latency (first byte, total response time)
- Silence detection and turn-taking
- TTS pronunciation failures
- Transport-level connection issues
- Cross-provider regressions

**Works with any voice platform** — LiveKit, Daily/Pipecat, VAPI, Retell, or any agent with a phone number.

## Install

```bash
# Everything (recommended to start)
pip install voicecheck[all]

# Or pick your transport + audio providers
pip install voicecheck[livekit,tts,stt]      # LiveKit + Edge TTS + local Whisper
pip install voicecheck[vapi,tts,stt]         # VAPI + Edge TTS + local Whisper
pip install voicecheck[retell,tts,stt]       # Retell + Edge TTS + local Whisper
pip install voicecheck[daily,tts,stt]        # Daily/Pipecat + Edge TTS + local Whisper
pip install voicecheck[telephony,tts,stt]    # Twilio phone calls + Edge TTS + local Whisper
```

**Extras breakdown:**

| Extra | What it installs | When you need it |
|-------|-----------------|-----------------|
| `livekit` | `livekit`, `livekit-api` | Testing agents on LiveKit |
| `daily` | `daily-python` | Testing agents on Daily/Pipecat |
| `vapi` | `websockets`, `httpx` | Testing agents on VAPI |
| `retell` | `websockets`, `httpx` | Testing agents on Retell |
| `telephony` | `twilio`, `aiohttp` | Testing any agent via phone call |
| `tts` | `edge-tts`, `pydub` | Free TTS via Microsoft Edge (no API key) |
| `stt` | `faster-whisper` | Local speech-to-text (no API key, downloads model) |
| `llm` | `openai`, `anthropic` | OpenAI TTS/STT, LLM judge, persona conversations |
| `dashboard` | `fastapi`, `uvicorn`, `jinja2` | Live web dashboard |

## Quick Start

### 1. Set environment variables

```bash
# Pick the variables for your transport (see Transport Providers below)
export LIVEKIT_URL=ws://your-server:7880
export LIVEKIT_API_KEY=your-api-key
export LIVEKIT_API_SECRET=your-api-secret

# Required for OpenAI TTS/STT, LLM judge, or persona mode
export OPENAI_API_KEY=sk-your-openai-key
```

VoiceCheck validates all required keys before running and tells you exactly which ones are missing.

### 2. Write a scenario

Create `my_test.yaml`:

```yaml
name: "Greeting test"

transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "${VOICECHECK_AGENT_NAME}"

audio:
  tts_provider: edge       # free, no API key needed
  stt_provider: whisper     # local model, no API key needed

turns:
  - user: "Hi there!"
    expect:
      - type: latency
        max_first_byte_ms: 3000
      - type: turn_count
        min_words: 3

  - user: "Tell me a joke"
    expect:
      - type: llm_judge
        criteria: "The agent tells a joke or something humorous"
        min_score: 0.7

settings:
  turn_timeout: 15.0
  silence_threshold: 1.5
```

`${ENV_VAR}` references are expanded automatically from your environment.

### 3. Run it

```bash
voicecheck run my_test.yaml
```

Output:

```
============================================================
  VoiceCheck Report: Greeting test
  Status: PASSED
  Turns: 2/2 passed
============================================================

Turn 1: [PASS]
  User: Hi there!
  Agent: Hey! How are you doing today?
  Latency: first_byte=850ms, total=2100ms
  [+] latency: First byte 850ms within 3000ms limit (score=1.00)
  [+] turn_count: Response has 6 words, meets minimum of 3 (score=1.00)

Turn 2: [PASS]
  User: Tell me a joke
  Agent: Why don't scientists trust atoms? Because they make up everything!
  Latency: first_byte=1200ms, total=3400ms
  [+] llm_judge: Agent told a clear, age-appropriate joke (score=0.90)

============================================================
Result: PASSED
============================================================
```

## Transport Providers

VoiceCheck supports 5 transport providers. All share the same scenario format — just change the `transport` section.

### LiveKit

Connect to a voice agent running in a LiveKit room via WebRTC.

```bash
pip install voicecheck[livekit]
```

Three connection modes:

**Direct mode** (recommended) — generates its own token:

```yaml
transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "my-agent"
    agent_metadata:
      user_name: "Test User"
```

**Token server mode** — tests your full auth stack:

```yaml
transport:
  type: livekit
  mode: token_server
  config:
    token_url: "https://your-api.com/token"
    token_request:
      user_id: "test-user-123"
    token_headers:
      Authorization: "Bearer ${AUTH_TOKEN}"
    response_mapping:
      url_field: "server_url"
      token_field: "participant_token"
```

**Pre-made token mode** — quick one-off tests:

```yaml
transport:
  type: livekit
  mode: token
  config:
    url: "ws://localhost:7880"
    token: "${LIVEKIT_TOKEN}"
```

### Daily / Pipecat

Connect to a Pipecat voice agent running in a Daily room via WebRTC.

```bash
pip install voicecheck[daily]
```

```yaml
transport:
  type: daily
  mode: api_key
  config:
    api_key: "${DAILY_API_KEY}"
    room_name: "voicecheck-test"
    agent_connect_timeout: 15.0
```

Three modes: `api_key` (creates a room), `room_url` (joins existing room), `token` (pre-made token).

```yaml
# Join an existing room
transport:
  type: daily
  mode: room_url
  config:
    room_url: "https://yourdomain.daily.co/my-room"
    meeting_token: "${DAILY_MEETING_TOKEN}"  # optional
```

### VAPI

Test a VAPI voice agent via web call. Creates a call through the VAPI REST API and streams audio over WebSocket.

```bash
pip install voicecheck[vapi]
```

```yaml
transport:
  type: vapi
  mode: web_call
  config:
    api_key: "${VAPI_API_KEY}"
    assistant_id: "${VAPI_ASSISTANT_ID}"
    audio_format: "pcm_s16le"          # pcm_s16le (default) or mulaw
```

You can also pass an inline assistant config instead of an ID:

```yaml
transport:
  type: vapi
  mode: web_call
  config:
    api_key: "${VAPI_API_KEY}"
    assistant_config:
      model:
        provider: "openai"
        model: "gpt-4o"
      firstMessage: "Hello, how can I help you?"
```

### Retell

Test a Retell AI voice agent via web call. Creates a call through the Retell REST API and streams audio over WebSocket.

```bash
pip install voicecheck[retell]
```

```yaml
transport:
  type: retell
  mode: web_call
  config:
    api_key: "${RETELL_API_KEY}"
    agent_id: "${RETELL_AGENT_ID}"
```

Retell's native audio rate is 24kHz — VoiceCheck automatically resamples to/from its internal 16kHz format.

### Telephony (Twilio)

Test **any** voice agent by calling its phone number. Provider-agnostic — works with agents on VAPI, Retell, LiveKit, or any platform with a phone number.

```bash
pip install voicecheck[telephony]
```

```yaml
transport:
  type: telephony
  mode: twilio
  config:
    account_sid: "${TWILIO_ACCOUNT_SID}"
    auth_token: "${TWILIO_AUTH_TOKEN}"
    from_number: "${TWILIO_FROM_NUMBER}"
    to_number: "${AGENT_PHONE_NUMBER}"
    public_url: "${VOICECHECK_PUBLIC_URL}"   # e.g., https://abc123.ngrok.io
    server_port: 8765
    call_connect_timeout: 30.0
```

**How it works:**
1. VoiceCheck starts a local WebSocket server
2. Twilio places an outbound call to the agent's phone number
3. Audio streams bidirectionally via Twilio Media Streams (G.711 mu-law at 8kHz)
4. VoiceCheck handles all codec conversion (mu-law to PCM, 8kHz to 16kHz) automatically

**Prerequisites:**
- A Twilio account with a phone number
- A publicly accessible URL for Twilio to reach VoiceCheck (e.g., [ngrok](https://ngrok.com))
- The agent's phone number

```bash
# Start ngrok in a separate terminal
ngrok http 8765

# Set the public URL
export VOICECHECK_PUBLIC_URL=https://abc123.ngrok.io
```

### Provider Comparison

| Transport | Type | Connection | Best for |
|-----------|------|-----------|----------|
| **LiveKit** | WebRTC | Room-based | Self-hosted voice agents, high volume |
| **Daily** | WebRTC | Room-based | Pipecat agents, Daily-hosted agents |
| **VAPI** | WebSocket | API call | VAPI-managed agents |
| **Retell** | WebSocket | API call | Retell-managed agents |
| **Telephony** | Phone (Twilio) | Phone call | Any agent with a phone number |

## Audio Providers

### TTS (Text-to-Speech)

VoiceCheck synthesizes your scripted text into audio to send to the agent.

| Provider | Config value | API key? | Quality | Notes |
|----------|-------------|----------|---------|-------|
| **Edge TTS** | `edge` | No | Good | Free Microsoft TTS, default |
| **OpenAI TTS** | `openai` | `OPENAI_API_KEY` | Excellent | 6 voices, `tts-1` or `tts-1-hd` |
| **File** | `file` | No | N/A | Load a pre-recorded WAV file |

```yaml
# Free (default)
audio:
  tts_provider: edge

# High quality
audio:
  tts_provider: openai
  tts_kwargs:
    voice: "nova"          # alloy, echo, fable, onyx, nova, shimmer
    model: "tts-1-hd"      # tts-1 (faster) or tts-1-hd (higher quality)
```

### STT (Speech-to-Text)

VoiceCheck transcribes the agent's audio response into text for evaluation.

| Provider | Config value | API key? | Speed | Notes |
|----------|-------------|----------|-------|-------|
| **Local Whisper** | `whisper` | No | Medium | Uses `faster-whisper`, downloads model on first run |
| **OpenAI Whisper** | `openai` | `OPENAI_API_KEY` | Fast | Cloud API, no local model download |

```yaml
# Local (default) — no API key, downloads ~150MB model on first run
audio:
  stt_provider: whisper

# Cloud — faster, no model download
audio:
  stt_provider: openai
```

## Testing Modes

### Scripted Mode

Define exact user messages and per-turn expectations. Best for regression testing specific behaviors.

```yaml
turns:
  - user: "What's the weather like?"
    expect:
      - type: keyword
        must_contain: ["weather", "temperature"]
      - type: latency
        max_first_byte_ms: 2000
      - type: turn_count
        min_words: 5
```

### Questions Mode

Send a fixed list of questions with shared evaluators. Simpler than scripted mode — no per-turn expectations needed.

```yaml
questions:
  - "What are your business hours?"
  - "Do you offer free shipping?"
  - "How do I return an item?"

per_turn_expect:
  - type: latency
    max_first_byte_ms: 3000
  - type: turn_count
    min_words: 5

conversation_eval:
  criteria:
    - "Agent answered all questions accurately"
    - "Agent was professional and helpful"
  min_score: 0.7
```

### Persona Mode

Let an LLM simulate a realistic user with a specific personality, age, and goals. Best for exploring edge cases and testing conversational quality. Requires `OPENAI_API_KEY`.

```yaml
persona:
  name: "Emma"
  age: 7
  personality: "curious, excitable, loves animals"
  communication_style: "short sentences, lots of questions"
  goals:
    - "Learn something new about dolphins"
    - "Ask the agent to tell a story"
  topics:
    - "dolphins"
    - "ocean animals"
  model: gpt-4o-mini
  max_turns: 4
  opening: "Hi! Do you know anything about dolphins?"

per_turn_expect:
  - type: latency
    max_first_byte_ms: 3000
  - type: turn_count
    min_words: 5

conversation_eval:
  criteria:
    - "Agent maintained a warm, age-appropriate tone"
    - "Agent provided accurate, educational content"
    - "Agent kept the conversation engaging"
  min_score: 0.7
  model: gpt-4o-mini
```

### Guided Flow Mode

Combine a persona with structured steps. Each step has a specific goal for the persona LLM, plus per-step evaluators.

```yaml
persona:
  name: "Alex"
  personality: "polite but busy"
  communication_style: "concise, to the point"

flow:
  - name: greeting
    goal: "Greet the agent and ask about appointment availability"
    expect:
      - type: keyword
        must_contain: ["appointment", "available"]

  - name: booking
    goal: "Book an appointment for next Tuesday at 2pm"
    expect:
      - type: llm_judge
        criteria: "Agent confirms the appointment details"

  - name: confirmation
    goal: "Confirm the booking and say goodbye"
    expect:
      - type: turn_count
        min_words: 3
```

## Evaluators

| Evaluator | What it checks | Key params |
|-----------|---------------|------------|
| `latency` | Response time thresholds | `max_first_byte_ms`, `max_total_ms` |
| `keyword` | Words present/absent in response | `must_contain`, `must_not_contain`, `case_sensitive` |
| `turn_count` | Response length | `min_words`, `max_words` |
| `llm_judge` | Semantic quality via LLM | `criteria`, `min_score`, `provider`, `model` |

### LLM Judge

Uses an LLM to score the agent's response against your criteria. Supports OpenAI and Anthropic.

```yaml
- type: llm_judge
  criteria: "Agent explains the concept in simple terms for a 7-year-old"
  min_score: 0.8
  provider: openai       # or "anthropic"
  model: gpt-4o-mini     # or "claude-sonnet-4-5-20250929"
```

### Custom Evaluators

Create your own evaluator and register it:

```python
from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult

class SentimentEvaluator(Evaluator):
    def __init__(self, min_score: float = 0.5):
        self.min_score = min_score

    async def evaluate(self, context: EvalContext) -> EvalResult:
        score = your_sentiment_function(context.agent_text)
        return EvalResult(
            evaluator_type="sentiment",
            passed=score >= self.min_score,
            score=score,
            reason=f"Sentiment score: {score:.2f}",
        )

register_evaluator("sentiment", SentimentEvaluator)
```

Then use it in your YAML:
```yaml
expect:
  - type: sentiment
    min_score: 0.6
```

## Soak Testing

Run scenarios repeatedly over a time window to measure stability and catch intermittent failures.

```bash
voicecheck run my_test.yaml --duration 20m              # run for 20 minutes
voicecheck run my_test.yaml --duration 1h --parallel 4  # 1 hour, 4 concurrent
```

Output includes aggregate statistics: pass rate, average latency, P95 latency, and per-scenario breakdowns.

## Results & Dashboards

Results are automatically saved to `~/.voicecheck/results.db` (SQLite). Use `--no-save` to skip.

### View history

```bash
voicecheck history                        # recent runs
voicecheck history -s "Greeting test"     # filter by scenario name
voicecheck history -n 50                  # show more runs
voicecheck show abc123                    # details of a specific run (partial IDs work)
```

### Static dashboard

```bash
voicecheck dashboard                      # writes voicecheck_dashboard.html
voicecheck dashboard --open               # generate and open in browser
voicecheck dashboard -s "Greeting test"   # filter by scenario
```

### Live web dashboard

```bash
pip install voicecheck[dashboard]
voicecheck serve                          # http://localhost:8989
voicecheck serve -p 3000                  # custom port
```

The dashboard includes:
- Per-scenario pass rate, average latency, and run count
- Latency trend charts over time
- Pass/fail timeline
- Expandable conversation transcripts with evaluator results

## CLI Reference

```bash
voicecheck run <path>                 # run scenario file or directory
  -v, --verbose                       # debug logging
  -o, --output <path>                 # write JSON report
  --parallel <n>                      # run N scenarios concurrently
  --duration <time>                   # soak test (e.g., 20m, 1h, 90s)
  --tag <tag>                         # tag this run (repeatable)
  --save-audio <dir>                  # save audio artifacts (WAV files)
  --skip-llm-judge                    # skip LLM evaluators (saves API cost)
  -q, --questions <text>              # override user messages (repeatable)
  --auto                              # switch to persona mode
  --no-save                           # skip saving to database
  --db <path>                         # custom database path

voicecheck validate <path>            # check YAML without running

voicecheck history                    # show recent runs
  -n, --limit <n>                     # number of runs to show
  -s, --scenario <name>               # filter by scenario

voicecheck show <run_id>              # show run details (supports partial IDs)

voicecheck dashboard                  # generate HTML dashboard
  -o, --output <path>                 # output file
  -s, --scenario <name>               # filter by scenario
  --open                              # open in browser

voicecheck serve                      # launch live web dashboard
  -p, --port <n>                      # port (default: 8989)
  --host <host>                       # host (default: 127.0.0.1)
```

## pytest Integration

VoiceCheck includes a pytest plugin that's automatically registered when installed.

```python
import pytest

@pytest.mark.voicecheck("examples/livekit_basic.yaml")
def test_greeting():
    """Runs the scenario — fails if any evaluator fails."""
    pass
```

Or use the runner directly for more control:

```python
import pytest
from voicecheck.core.scenario import ScenarioRunner

@pytest.mark.asyncio
async def test_custom():
    runner = ScenarioRunner.from_yaml("examples/livekit_basic.yaml")
    report = await runner.run()
    assert report.passed
    assert report.turns[0].metrics.first_byte_ms < 2000
```

Run:
```bash
pytest -m voicecheck          # only VoiceCheck marker tests
pytest tests/ -v              # all tests
```

## YAML Schema Reference

Complete schema with all available fields:

```yaml
name: "My scenario"                     # Scenario name (shown in reports)
description: "What this tests"          # Optional description

# ── Transport ──
# type: livekit | daily | vapi | retell | telephony
transport:
  type: livekit
  mode: direct
  config:
    # LiveKit direct: url, api_key, api_secret, agent_name, agent_metadata
    # LiveKit token_server: token_url, token_request, token_headers, response_mapping
    # LiveKit token: url, token
    # Daily api_key: api_key, room_name
    # Daily room_url: room_url, meeting_token
    # VAPI: api_key, assistant_id (or assistant_config), audio_format
    # Retell: api_key, agent_id
    # Telephony: account_sid, auth_token, from_number, to_number, public_url

# ── Audio ──
audio:
  tts_provider: edge                     # edge | openai | file
  stt_provider: whisper                  # whisper | openai
  sample_rate: 16000                     # Audio sample rate in Hz
  channels: 1                            # Number of audio channels
  tts_kwargs: {}                         # Extra kwargs passed to TTS provider
  stt_kwargs: {}                         # Extra kwargs passed to STT provider

# ── Scripted mode ──
turns:
  - user: "Hello!"
    expect:
      - type: latency
        max_first_byte_ms: 3000
      - type: keyword
        must_contain: ["hello"]
      - type: turn_count
        min_words: 3
      - type: llm_judge
        criteria: "Agent gives a friendly greeting"
        min_score: 0.7

# ── Questions mode ──
questions:
  - "What are your hours?"
  - "Do you ship internationally?"

# ── Persona mode ──
persona:
  name: "Emma"
  age: 7
  personality: "curious and friendly"
  communication_style: "short sentences"
  goals: ["learn about animals"]
  topics: ["dolphins", "dogs"]
  instructions: ""
  model: gpt-4o-mini
  max_turns: 5
  opening: "Hi there!"

# ── Guided flow mode (persona + steps) ──
flow:
  - name: greeting
    goal: "Greet the agent"
    expect:
      - type: turn_count
        min_words: 3

# ── Shared evaluators ──
per_turn_expect:
  - type: latency
    max_first_byte_ms: 3000

conversation_eval:
  criteria:
    - "Agent was warm and age-appropriate"
  min_score: 0.7
  model: gpt-4o-mini

settings:
  turn_timeout: 15.0                     # Max seconds to wait for agent response
  silence_threshold: 1.5                 # Seconds of silence to end capture
```

## Examples

See the [examples/](examples/) directory:

- **[livekit_basic.yaml](examples/livekit_basic.yaml)** — LiveKit persona-driven conversation
- **[daily_basic.yaml](examples/daily_basic.yaml)** — Daily/Pipecat scripted test
- **[vapi_web_call.yaml](examples/vapi_web_call.yaml)** — VAPI web call test
- **[retell_web_call.yaml](examples/retell_web_call.yaml)** — Retell web call test
- **[telephony_twilio.yaml](examples/telephony_twilio.yaml)** — Phone call test via Twilio
- **[persona_kid.yaml](examples/persona_kid.yaml)** — Persona mode with a curious 7-year-old
- **[livekit_token_server.yaml](examples/livekit_token_server.yaml)** — Token server integration
- **[e2e_questions.yaml](examples/e2e_questions.yaml)** — Questions mode with shared evaluators
- **[guided_luna_test.yaml](examples/guided_luna_test.yaml)** — Guided flow mode

## Project Structure

```
voicecheck/
├── src/voicecheck/
│   ├── core/              # Types, ABCs, scenario runner, report generation
│   ├── transports/        # LiveKit, Daily, VAPI, Retell, Telephony transports
│   ├── audio/             # TTS/STT providers + shared audio utilities
│   ├── evaluators/        # latency, keyword, turn_count, llm_judge
│   ├── conversation/      # Persona-driven conversation engine
│   ├── storage/           # SQLite result store + HTML dashboard generator
│   ├── web/               # FastAPI live dashboard
│   ├── cli.py             # Click CLI
│   └── pytest_plugin.py   # pytest marker integration
├── examples/              # Example YAML scenarios for each transport
├── tests/                 # Unit + integration tests
├── pyproject.toml
├── CHANGELOG.md
├── CONTRIBUTING.md
└── LICENSE
```

## Development

```bash
pip install -e ".[dev,all]"
pytest tests/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on adding transports, evaluators, and providers.

## License

MIT — see [LICENSE](LICENSE).
