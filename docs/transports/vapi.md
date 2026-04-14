# VAPI Transport

## Overview

The VAPI transport connects VoiceCheck to voice agents hosted on [VAPI](https://vapi.ai/) by creating a web call through the VAPI REST API and streaming audio bidirectionally over the resulting WebSocket connection.

The call flow works as follows:

1. VoiceCheck sends a `POST /call/web` request to the VAPI REST API with your `assistant_id` or inline `assistant_config`.
2. VAPI returns a call ID and a WebSocket transport URL.
3. VoiceCheck opens a WebSocket connection to that URL and sends a `session.update` message to configure the audio format and sample rate.
4. Audio frames flow bidirectionally as raw binary WebSocket messages (PCM or mu-law encoded).
5. On disconnect, VoiceCheck calls `DELETE /call/{call_id}` to explicitly end the call and release server-side resources.

The VAPI transport extends VoiceCheck's `WebSocketTransport` base class, which handles the common patterns of WebSocket-based audio streaming: background receive loop, audio buffering, silence detection with multi-frame speech confirmation, and timing metrics collection.

## Installation

```bash
pip install voicecheck[vapi]
```

This installs:
- `websockets >= 12.0`
- `httpx >= 0.27`

## Audio Formats

VAPI supports two audio formats, configured via the `audio_format` option:

### `pcm_s16le` (default)

16-bit signed little-endian PCM. This is VoiceCheck's native internal format, so frames pass through with zero conversion overhead. Use this when latency measurement accuracy is important.

### `mulaw`

G.711 mu-law encoding, typically used in telephony scenarios. When this format is selected, VoiceCheck automatically converts outbound PCM frames to mu-law before sending, and converts inbound mu-law data back to PCM for analysis. The conversion uses the `pcm_to_mulaw` and `mulaw_to_pcm` utilities.

## Assistant Configuration

You can reference an existing VAPI assistant or provide a full inline configuration:

### Using `assistant_id`

Point to a pre-configured assistant in your VAPI dashboard:

```yaml
transport:
  type: vapi
  mode: web_call
  config:
    api_key: "${VAPI_API_KEY}"
    assistant_id: "${VAPI_ASSISTANT_ID}"
```

The transport sends `{"assistantId": "..."}` in the web call creation request.

### Using `assistant_config`

Override the assistant configuration inline. This is useful for testing different prompts or voice settings without creating separate assistants in the VAPI dashboard:

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
      voice:
        provider: "11labs"
        voiceId: "rachel"
      firstMessage: "Hello! How can I help you today?"
      transcriber:
        provider: "deepgram"
        model: "nova-2"
```

The transport sends `{"assistant": {...}}` in the web call creation request. The `assistant_config` dict is passed through as-is to the VAPI API.

You must provide exactly one of `assistant_id` or `assistant_config`. The transport raises a `ValueError` if neither is specified.

## Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `api_key` | string | -- | VAPI API key (required) |
| `assistant_id` | string | -- | ID of a pre-configured VAPI assistant |
| `assistant_config` | dict | -- | Full inline assistant configuration (alternative to `assistant_id`) |
| `audio_format` | string | `"pcm_s16le"` | Audio format: `"pcm_s16le"` or `"mulaw"` |
| `sample_rate` | int | `16000` | Audio sample rate in Hz (sent in session config) |
| `api_timeout` | float | `15.0` | HTTP timeout in seconds for the VAPI REST API call |
| `num_channels` | int | `1` | Number of audio channels |
| `silence_rms_threshold` | int | `500` | RMS energy threshold for silence detection |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VAPI_API_KEY` | Yes | VAPI API key for authentication |
| `VAPI_ASSISTANT_ID` | No | VAPI assistant ID (when using `assistant_id` mode) |

## WebSocket Message Types

The VAPI WebSocket sends both binary (audio) and text (JSON control) messages. The transport handles these automatically:

| Message Type | Direction | Format | Description |
|--------------|-----------|--------|-------------|
| Audio data | Both | Binary | Raw PCM or mu-law audio bytes |
| `session.update` | Outbound | JSON | Initial session configuration (audio format, sample rate) |
| `speech-update` | Inbound | JSON | Speech status changes (logged at debug level) |
| `transcript` | Inbound | JSON | Real-time transcription events (logged at info level) |
| `hang` | Inbound | JSON | Call hang-up signal from VAPI |
| `error` | Inbound | JSON | Error messages from VAPI (logged as errors) |

## Complete Example

```yaml
name: vapi-customer-support
description: Test a VAPI customer support agent

transport:
  type: vapi
  mode: web_call
  config:
    api_key: "${VAPI_API_KEY}"
    assistant_id: "${VAPI_ASSISTANT_ID}"
    audio_format: "pcm_s16le"
    sample_rate: 16000
    api_timeout: 20.0

turns:
  - user: "I need to cancel my subscription."
    evaluators:
      - type: latency
        config:
          max_first_byte_ms: 3000
      - type: semantic
        config:
          expected: "The agent should acknowledge the cancellation request and ask for account details."
```

## Troubleshooting

### `ImportError: httpx is required for the VAPI transport`

- **Fix:** Run `pip install voicecheck[vapi]`. This installs both `websockets` and `httpx`.

### `ValueError: VAPI transport requires 'api_key' in config`

- **Cause:** The `api_key` field is missing or the environment variable was not expanded.
- **Fix:** Ensure `VAPI_API_KEY` is set: `export VAPI_API_KEY="your-key"`.

### `ValueError: VAPI transport requires either 'assistant_id' or 'assistant_config'`

- **Cause:** Neither an assistant ID nor an inline config was provided.
- **Fix:** Add `assistant_id` or `assistant_config` to your transport config.

### `ConnectionError: VAPI API returned HTTP 401`

- **Cause:** Invalid or expired API key.
- **Fix:** Verify your VAPI API key is correct and active in the VAPI dashboard.

### `ConnectionError: VAPI API response does not contain a WebSocket URL`

- **Cause:** The API response structure is unexpected, possibly due to a plan limitation or API version change.
- **Fix:** Check that your VAPI plan supports web calls. The transport tries both `data.transport.websocket.url` and `data.webCallUrl` response paths.

### No audio captured from agent

- **Symptom:** `No non-silent audio captured from agent`.
- **Possible causes:**
  - The assistant did not respond (check VAPI dashboard for call logs).
  - Audio format mismatch: ensure `audio_format` matches what the assistant is configured to output.
  - The agent's first message was very short and fell within the silence detection window.
- **Fix:** Check call logs in the VAPI dashboard. Try increasing the receive timeout. Verify `audio_format` is set correctly.

### VAPI call teardown warning

- **Symptom:** Log shows `Failed to end VAPI call via API`.
- **Cause:** The `DELETE /call/{call_id}` cleanup request failed. This is non-fatal -- the WebSocket is still closed properly.
- **Impact:** The server-side call may linger until VAPI's own timeout cleans it up. No action required for test results.
