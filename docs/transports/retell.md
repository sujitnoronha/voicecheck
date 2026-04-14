# Retell Transport

## Overview

The Retell transport connects VoiceCheck to voice agents hosted on [Retell AI](https://www.retellai.com/) by creating a web call through the Retell REST API and streaming raw PCM audio bidirectionally over a WebSocket connection.

The call flow works as follows:

1. VoiceCheck sends a `POST /v2/create-web-call` request to the Retell REST API with your `agent_id`.
2. Retell returns an `access_token` and `call_id`.
3. VoiceCheck opens a WebSocket connection to `wss://api.retellai.com/audio-websocket/{call_id}?access_token=...`.
4. A one-shot JSON config frame is sent to disable auto-reconnect and enable call detail events.
5. Audio flows bidirectionally as raw PCM binary frames.
6. When VoiceCheck disconnects the WebSocket, Retell ends the call automatically -- no explicit REST teardown is needed.

The Retell transport extends VoiceCheck's `WebSocketTransport` base class, inheriting the background receive loop, audio buffering, silence detection, and timing metrics.

## Installation

```bash
pip install voicecheck[retell]
```

This installs:
- `websockets >= 12.0`
- `httpx >= 0.27`

## Automatic Audio Resampling

Retell's native audio format is raw PCM at **24 kHz**, while VoiceCheck's internal pipeline operates at **16 kHz**. The transport handles this mismatch transparently:

- **Outbound (VoiceCheck to Retell):** 16 kHz PCM frames are resampled to 24 kHz before sending.
- **Inbound (Retell to VoiceCheck):** 24 kHz PCM frames from Retell are resampled down to 16 kHz for analysis.

Both directions use the `resample_pcm` utility. If you configure `retell_sample_rate` and `sample_rate` to the same value, no resampling occurs.

The resampling rates are controlled by two config keys:

| Direction | Config Key | Default | Description |
|-----------|-----------|---------|-------------|
| Retell side | `retell_sample_rate` | `24000` | The PCM sample rate Retell expects and produces |
| VoiceCheck side | `sample_rate` | `16000` | VoiceCheck's internal processing rate |

## Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `api_key` | string | -- | Retell API key (required) |
| `agent_id` | string | -- | Retell agent ID to connect to (required) |
| `retell_sample_rate` | int | `24000` | PCM sample rate for Retell's WebSocket audio |
| `sample_rate` | int | `16000` | VoiceCheck's internal sample rate |
| `num_channels` | int | `1` | Number of audio channels |
| `metadata` | dict | -- | Optional metadata dict passed to the `create-web-call` request |
| `retell_llm_dynamic_variables` | dict | -- | Optional LLM template variables passed to the `create-web-call` request |
| `request_timeout` | float | `15.0` | HTTP timeout in seconds for the Retell REST API call |
| `silence_rms_threshold` | int | `500` | RMS energy threshold for silence detection |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `RETELL_API_KEY` | Yes | Retell API key for authentication |
| `RETELL_AGENT_ID` | Yes | Retell agent ID to test |

## WebSocket Message Types

Retell sends both binary and JSON text messages over the WebSocket. The transport decodes them automatically:

| Event Type | Format | Description |
|------------|--------|-------------|
| Raw audio | Binary | PCM audio at `retell_sample_rate`. Resampled to `sample_rate` automatically |
| `audio` | JSON | Base64-encoded audio payload (alternative to binary). Decoded and resampled |
| `ping` | JSON | Keep-alive signal. Ignored (protocol-level pings are handled by `websockets`) |
| `call_details` / `call_detail` | JSON | Call metadata. Logged at info level |
| `transcript` | JSON | Real-time transcription. Logged at info level |
| `metadata` | JSON | Arbitrary metadata from Retell. Logged at info level |
| `error` | JSON | Error message from Retell. Logged as warning |

## Complete Example

```yaml
name: retell-booking-agent
description: Test a Retell appointment booking agent

transport:
  type: retell
  mode: web_call
  config:
    api_key: "${RETELL_API_KEY}"
    agent_id: "${RETELL_AGENT_ID}"
    retell_sample_rate: 24000
    sample_rate: 16000
    metadata:
      test_run: true
      scenario: "appointment_booking"
    retell_llm_dynamic_variables:
      customer_name: "Jane Doe"
      preferred_date: "next Monday"

turns:
  - user: "I'd like to book an appointment for next Monday."
    expect:
      - type: latency
        max_first_byte_ms: 3000
      - type: llm_judge
        criteria: "The agent should confirm the appointment date and ask for a preferred time."
        min_score: 0.7
```

## Troubleshooting

### `ImportError: httpx is required for the Retell transport`

- **Fix:** Run `pip install voicecheck[retell]`. This installs both `websockets` and `httpx`.

### `ConnectionError: Retell create-web-call timed out`

- **Cause:** The Retell API did not respond within the timeout period.
- **Fix:** Check your network connection. Increase `request_timeout` if needed. Verify the API key is valid.

### `ConnectionError: Retell create-web-call returned HTTP 401`

- **Cause:** Invalid or expired API key.
- **Fix:** Verify your Retell API key in the Retell dashboard.

### `ConnectionError: Retell create-web-call returned HTTP 404`

- **Cause:** The `agent_id` does not exist or is not accessible with the provided API key.
- **Fix:** Double-check the `RETELL_AGENT_ID` value. Ensure the agent exists in your Retell account.

### Audio sounds distorted or pitched incorrectly

- **Cause:** Sample rate mismatch. If `retell_sample_rate` does not match Retell's actual output rate, the resampling will produce artifacts.
- **Fix:** Retell's default is 24 kHz. Ensure `retell_sample_rate: 24000` unless you have configured your Retell agent differently.

### No audio captured from agent

- **Symptom:** `No non-silent audio captured from agent`.
- **Possible causes:**
  - The agent did not respond (check Retell dashboard for call logs).
  - The WebSocket connection was closed prematurely.
  - Resampling is producing very low energy frames that fall below the silence threshold.
- **Fix:** Check call logs in the Retell dashboard. Try lowering `silence_rms_threshold` if the resampled audio has low amplitude.

### `KeyError: 'access_token'` or `KeyError: 'call_id'`

- **Cause:** The Retell API response did not contain the expected fields.
- **Fix:** This usually indicates an API error. Check the Retell API status page and your account limits.
