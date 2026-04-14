# YAML Schema Reference

This document describes every field available in a VoiceCheck scenario YAML file. The schema is defined by Pydantic models in `src/voicecheck/core/scenario.py`.

## Environment Variable Expansion

All string values in YAML files support `${ENV_VAR}` expansion. VoiceCheck recursively expands environment variables before parsing:

```yaml
config:
  api_key: "${LIVEKIT_API_KEY}"          # expanded from environment
  url: "${LIVEKIT_URL}"                   # expanded from environment
  room_name: "test-${BUILD_NUMBER}"       # partial expansion works too
```

If an environment variable is not set, the `${VAR}` reference is left as-is (not expanded). The `validate` command can detect unexpanded variables in transport config.

---

## Top-Level Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | `"unnamed"` | Human-readable scenario name. Appears in reports and dashboard. |
| `description` | string | `""` | Optional description of the scenario's purpose. |
| `transport` | TransportConfig | *(see below)* | Transport configuration for connecting to the voice agent. |
| `audio` | AudioConfig | *(see below)* | TTS/STT provider configuration. |
| `turns` | list of TurnConfig | `[]` | Scripted conversation turns (scripted mode). |
| `questions` | list of strings | `[]` | Fixed user messages (questions mode). |
| `persona` | PersonaConfig or null | `null` | Persona configuration (persona/guided mode). |
| `conversation_eval` | ConversationEvalConfig or null | `null` | Post-conversation LLM evaluation criteria. |
| `per_turn_expect` | list of ExpectConfig | `[]` | Evaluators applied to every turn in persona/questions/guided mode. |
| `flow` | list of FlowStepConfig | `[]` | Guided conversation flow steps (guided mode). |
| `settings` | SettingsConfig | *(see below)* | Timeout and silence detection settings. |

### Mode determination

The testing mode is automatically determined by which fields are present:

- `questions` is non-empty --> **Questions mode**
- `persona` is set, `flow` and `questions` are empty --> **Persona mode**
- `persona` is set and `flow` is non-empty --> **Guided flow mode**
- `turns` is non-empty (and none of the above) --> **Scripted mode**

---

## TransportConfig

```yaml
transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | `"livekit"` | Transport type. Available: `livekit`, `daily`, `vapi`, `retell`, `telephony`. |
| `mode` | string | `"direct"` | Connection mode. Options depend on the transport type (see below). |
| `config` | dict | `{}` | Transport-specific configuration. Keys depend on type and mode. |

### Transport modes and config keys

#### livekit

| Mode | Required config keys | Optional config keys |
|---|---|---|
| `direct` | `url`, `api_key`, `api_secret` | `agent_name`, `agent_metadata`, `room_name`, `agent_connect_timeout` |
| `token_server` | `token_url` | `token_request`, `token_headers`, `response_mapping`, `token_timeout` |
| `token` | `url`, `token` | |

**`token_server` response_mapping:**

```yaml
response_mapping:
  url_field: "server_url"        # JSON key for LiveKit URL (default: "server_url")
  token_field: "participant_token"  # JSON key for token (default: "participant_token")
```

#### daily

| Mode | Required config keys | Optional config keys |
|---|---|---|
| `api_key` | `api_key` | `room_name`, `agent_connect_timeout` |
| `room_url` | `room_url` | `meeting_token`, `agent_connect_timeout` |
| `token` | `room_url`, `meeting_token` | `agent_connect_timeout` |

#### vapi

| Mode | Required config keys | Optional config keys |
|---|---|---|
| `web_call` | `api_key`, and one of `assistant_id` or `assistant_config` | `audio_format`, `api_timeout` |

**`audio_format`**: `"pcm_s16le"` (default) or `"mulaw"`.

#### retell

| Mode | Required config keys | Optional config keys |
|---|---|---|
| `web_call` | `api_key`, `agent_id` | `retell_sample_rate`, `metadata`, `retell_llm_dynamic_variables`, `request_timeout` |

**`retell_sample_rate`**: Retell's native PCM rate (default `24000`). VoiceCheck resamples automatically.

#### telephony

| Mode | Required config keys | Optional config keys |
|---|---|---|
| `twilio` | `account_sid`, `auth_token`, `from_number`, `to_number`, `public_url` | `server_port`, `call_connect_timeout` |

**`public_url`**: Must be reachable from the internet (e.g., ngrok URL).
**`server_port`**: Local port for the HTTP/WebSocket server (default `8765`).

---

## AudioConfig

```yaml
audio:
  tts_provider: edge
  stt_provider: whisper
  sample_rate: 16000
  channels: 1
  language: "es"             # auto-selects TTS voice + STT language
  degradation:               # simulate real-world audio conditions
    noise_snr_db: 15
    bandwidth: narrowband
    packet_loss_pct: 5
    codec: mulaw
  tts_kwargs:
    voice: "en-US-AriaNeural"
  stt_kwargs:
    model_size: "small"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `tts_provider` | string | `"edge"` | Text-to-speech provider. Available: `edge`, `openai`, `file`. |
| `stt_provider` | string | `"whisper"` | Speech-to-text provider. Available: `whisper`, `openai`. |
| `sample_rate` | int | `16000` | Audio sample rate in Hz. |
| `channels` | int | `1` | Number of audio channels. |
| `language` | string | `""` | Language code (e.g., `"es"`, `"fr"`, `"ja"`). Sets TTS voice and STT language automatically. See [supported languages](#supported-languages). |
| `degradation` | DegradationConfig | `null` | Audio degradation settings. Applied to user audio after TTS, before sending. See [DegradationConfig](#degradationconfig). |
| `tts_kwargs` | dict | `{}` | Extra keyword arguments passed to the TTS provider constructor. |
| `stt_kwargs` | dict | `{}` | Extra keyword arguments passed to the STT provider constructor. |

### Supported languages

When `language` is set, VoiceCheck auto-selects the appropriate Edge TTS voice and configures STT. Supported codes:

| Code | Language | Edge TTS Voice |
|---|---|---|
| `en` | English | en-US-JennyNeural |
| `es` | Spanish | es-ES-ElviraNeural |
| `fr` | French | fr-FR-DeniseNeural |
| `de` | German | de-DE-KatjaNeural |
| `pt` | Portuguese | pt-BR-FranciscaNeural |
| `ja` | Japanese | ja-JP-NanamiNeural |
| `ko` | Korean | ko-KR-SunHiNeural |
| `zh` | Chinese | zh-CN-XiaoxiaoNeural |
| `it` | Italian | it-IT-ElsaNeural |
| `hi` | Hindi | hi-IN-SwaraNeural |
| `ar` | Arabic | ar-SA-ZariyahNeural |
| `ru` | Russian | ru-RU-SvetlanaNeural |
| `nl` | Dutch | nl-NL-ColetteNeural |
| `pl` | Polish | pl-PL-AgnieszkaNeural |
| `sv` | Swedish | sv-SE-SofieNeural |
| `tr` | Turkish | tr-TR-EmelNeural |
| `th` | Thai | th-TH-PremwadeeNeural |
| `vi` | Vietnamese | vi-VN-HoaiMyNeural |

If `voice` is also specified in `tts_kwargs`, it takes precedence over the language mapping.

### DegradationConfig

Simulate real-world audio conditions. Effects are chained in order: noise --> bandwidth --> codec --> packet loss.

| Field | Type | Default | Description |
|---|---|---|---|
| `noise_snr_db` | float | `null` | Add Gaussian noise at this signal-to-noise ratio (dB). Lower = noisier. 20 = light, 10 = noisy, 5 = very noisy. |
| `bandwidth` | string | `null` | `"narrowband"` (3400 Hz cutoff, telephony) or `"wideband"` (7000 Hz). |
| `packet_loss_pct` | float | `null` | Percentage of audio frames to zero out (0-100). Simulates network packet loss. |
| `codec` | string | `null` | `"mulaw"` for G.711 mu-law codec round-trip. Introduces telephony quantization artifacts. |

### TTS provider options

#### edge (default)

Microsoft Edge TTS. Free, no API key required.

| tts_kwargs key | Default | Description |
|---|---|---|
| `voice` | `"en-US-JennyNeural"` | Voice name. See [Edge TTS voices](https://github.com/rany2/edge-tts). |

#### openai

OpenAI TTS API. Requires `OPENAI_API_KEY`.

| tts_kwargs key | Default | Description |
|---|---|---|
| `voice` | `"alloy"` | Voice: `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`. |
| `model` | `"tts-1"` | Model: `tts-1` or `tts-1-hd`. |

#### file

Load audio from a WAV file. The `user` text in turns is ignored.

| tts_kwargs key | Required | Description |
|---|---|---|
| `file_path` | yes | Path to a WAV file. |

### STT provider options

#### whisper (default)

Local transcription via faster-whisper.

| stt_kwargs key | Default | Description |
|---|---|---|
| `model_size` | `"base"` | Model: `tiny`, `base`, `small`, `medium`, `large-v3`. |
| `language` | `"en"` | Language code. |
| `device` | `"auto"` | Device: `auto`, `cpu`, `cuda`. |

#### openai

OpenAI Whisper API. Requires `OPENAI_API_KEY`.

| stt_kwargs key | Default | Description |
|---|---|---|
| `model` | `"whisper-1"` | API model name. |
| `language` | `"en"` | Language code. |

---

## TurnConfig (Scripted Mode)

```yaml
turns:
  # Standard speech turn
  - user: "Hello, who are you?"
    expect:
      - type: latency
        max_first_byte_ms: 3000

  # Silence turn — test how agent handles no input
  - silence:
      duration_s: 10
    expect:
      - type: turn_count
        min_words: 1

  # Turn with pre-pause
  - user: "Sorry, I'm back"
    pause_before_ms: 3000

  # Turn with mid-response interruption
  - user: "Tell me a long story"
    interrupt:
      after_ms: 2000
      with: "Wait, stop. Tell me about Mars instead."
    expect:
      - type: keyword
        must_contain: ["mars"]
```

| Field | Type | Default | Description |
|---|---|---|---|
| `user` | string | `""` | Text to synthesize and send as user speech. Empty when using `silence`. |
| `expect` | list of ExpectConfig | `[]` | Evaluators to run on this turn's agent response. |
| `silence` | SilenceConfig | `null` | Send silence instead of speech. Mutually exclusive with `user`. |
| `pause_before_ms` | int | `0` | Milliseconds to wait before this turn starts. Simulates user thinking time. |
| `interrupt` | InterruptConfig | `null` | Interrupt the agent mid-response. See below. |

### SilenceConfig

Send silent audio frames instead of synthesized speech. Tests how the agent handles extended user silence (e.g., prompting, timeout behavior).

| Field | Type | Description |
|---|---|---|
| `duration_s` | float | Duration of silence in seconds. |

### InterruptConfig

Send additional audio while the agent is still responding, simulating user barge-in. VoiceCheck starts receiving agent audio, waits `after_ms`, then synthesizes and sends the interrupt text.

| Field | Type | Description |
|---|---|---|
| `after_ms` | int | Milliseconds after agent starts responding before sending the interrupt. |
| `with` | string | Text to synthesize and send as the interrupting speech. |

The `turn_metadata` field on `EvalContext` is populated with `{"interrupted": true, "interrupt_after_ms": N, "interrupt_text": "..."}` so custom evaluators can detect interruption context.

---

## ExpectConfig (Evaluator)

```yaml
expect:
  - type: latency
    max_first_byte_ms: 3000
    max_total_ms: 15000
```

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | **(required)** | Evaluator type: `latency`, `keyword`, `turn_count`, `llm_judge`, or any registered custom evaluator. |
| *(additional fields)* | any | varies | All other fields are passed as keyword arguments to the evaluator constructor. |

The `model_config = {"extra": "allow"}` setting on this Pydantic model means any extra fields are accepted and forwarded to the evaluator. See the [Evaluators guide](../guides/evaluators.md) for all parameters per evaluator type.

### latency parameters

| Field | Type | Default |
|---|---|---|
| `max_first_byte_ms` | float | `0` |
| `max_total_ms` | float | `0` |

### keyword parameters

| Field | Type | Default |
|---|---|---|
| `must_contain` | list of strings | `[]` |
| `must_not_contain` | list of strings | `[]` |
| `case_sensitive` | bool | `false` |

### turn_count parameters

| Field | Type | Default |
|---|---|---|
| `min_words` | int | `1` |
| `max_words` | int | `0` |

### llm_judge parameters

| Field | Type | Default |
|---|---|---|
| `criteria` | string | `""` |
| `min_score` | float | `0.7` |
| `provider` | string | `"openai"` |
| `model` | string | provider default |

### emotional_tone parameters

| Field | Type | Default |
|---|---|---|
| `expected_emotions` | list of strings | `[]` |
| `forbidden_emotions` | list of strings | `[]` |
| `min_score` | float | `0.7` |
| `provider` | string | `"openai"` |
| `model` | string | provider default |

Scores how well the agent's emotional tone matches expectations. Detects emotions like `"empathetic"`, `"warm"`, `"dismissive"`, `"cold"`, etc. Skipped with `--skip-llm-judge`.

---

## PersonaConfig

```yaml
persona:
  name: "Emma"
  age: 7
  personality: "curious, excitable"
  communication_style: "short sentences"
  goals:
    - "Learn something fun"
  topics:
    - "space"
  instructions: "Start by saying hi."
  model: gpt-4o-mini
  max_turns: 5
  opening: "Hi there!"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | `"Test User"` | Persona's name. |
| `description` | string | `""` | Background information. |
| `age` | int or null | `null` | Age (affects LLM prompt for age-appropriate speech). |
| `personality` | string | `"friendly and curious"` | Personality traits. |
| `communication_style` | string | `"casual, short sentences"` | How the persona speaks. |
| `goals` | list of strings | `[]` | Conversation goals. |
| `topics` | list of strings | `[]` | Topics of interest. |
| `instructions` | string | `""` | Freeform instructions for the persona LLM. |
| `model` | string | `"gpt-4o-mini"` | OpenAI model for generating messages. |
| `max_turns` | int | `5` | Number of conversation turns (persona mode only; ignored in guided mode). |
| `opening` | string | `""` | Fixed opening line. Empty means LLM generates one. |

---

## ConversationEvalConfig

```yaml
conversation_eval:
  criteria:
    - "Agent was helpful and accurate"
    - "Responses were appropriate length"
  min_score: 0.7
  model: gpt-4o-mini
```

| Field | Type | Default | Description |
|---|---|---|---|
| `criteria` | list of strings | `[]` | Evaluation criteria. Each is scored individually (0.0-1.0). |
| `min_score` | float | `0.7` | Minimum overall score to pass. |
| `model` | string | `"gpt-4o-mini"` | OpenAI model for evaluation. |

Conversation evaluation runs after all turns complete. It receives the full conversation transcript and scores it holistically. Skipped when `--skip-llm-judge` is used.

---

## FlowStepConfig (Guided Mode)

```yaml
flow:
  - name: "greeting"
    goal: "Say hello and ask the agent's name"
    expect:
      - type: turn_count
        min_words: 5

  - name: "main-question"
    goal: "Ask about the product return policy"
    expect:
      - type: llm_judge
        criteria: "Agent explains the return policy clearly"
        min_score: 0.7
```

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | `""` | Human-readable step label (appears in logs). |
| `goal` | string | **(required)** | What the persona should accomplish. Sent to the persona LLM as a steering instruction. |
| `expect` | list of ExpectConfig | `[]` | Step-specific evaluators. Combined with `per_turn_expect` at runtime. |

---

## SettingsConfig

```yaml
settings:
  turn_timeout: 20.0
  silence_threshold: 2.0
```

| Field | Type | Default | Description |
|---|---|---|---|
| `turn_timeout` | float | `15.0` | Maximum seconds to wait for agent audio per turn. |
| `silence_threshold` | float | `1.5` | Seconds of silence before considering agent done speaking. |

### Tuning guidance

- **turn_timeout**: Increase for agents that take a while to respond or for long responses. 15-30 seconds is typical.
- **silence_threshold**: Increase if the agent pauses mid-response (e.g., for thinking). Decrease for faster conversation pacing. 1.0-3.0 seconds is typical.

---

## Complete Example

This example demonstrates every section in a single scenario file:

```yaml
name: "Complete example"
description: "Demonstrates all YAML schema sections"

transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "${VOICECHECK_AGENT_NAME}"

audio:
  tts_provider: edge
  stt_provider: whisper
  sample_rate: 16000
  channels: 1
  tts_kwargs:
    voice: "en-US-AriaNeural"
  stt_kwargs:
    model_size: "small"
    language: "en"

persona:
  name: "Test User"
  age: 25
  personality: "friendly and direct"
  communication_style: "clear, conversational"
  goals:
    - "Get information about the product"
    - "Understand the return policy"
  topics:
    - "products"
    - "returns"
  instructions: "Be polite but get to the point quickly."
  model: gpt-4o-mini
  max_turns: 4
  opening: "Hi, I have a question about your products."

flow:
  - name: "greeting"
    goal: "Introduce yourself and ask a general question"
    expect:
      - type: turn_count
        min_words: 5

  - name: "product-question"
    goal: "Ask about a specific product feature"
    expect:
      - type: turn_count
        min_words: 10
      - type: keyword
        must_not_contain: ["error"]

  - name: "farewell"
    goal: "Thank the agent and say goodbye"
    expect:
      - type: turn_count
        min_words: 2

per_turn_expect:
  - type: latency
    max_first_byte_ms: 4000

conversation_eval:
  criteria:
    - "Agent was helpful and informative"
    - "Agent maintained a professional tone"
  min_score: 0.7
  model: gpt-4o-mini

settings:
  turn_timeout: 20.0
  silence_threshold: 2.0
```
