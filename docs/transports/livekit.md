# LiveKit Transport

## Overview

The LiveKit transport connects VoiceCheck to voice agents running inside [LiveKit](https://livekit.io/) rooms. VoiceCheck joins a room as a participant named `voicecheck-tester`, publishes a local audio track that simulates user speech, and subscribes to the agent's audio track to capture and analyze the response.

Under the hood, the transport uses the `livekit.rtc` SDK to manage WebRTC connections and the `livekit-api` SDK (in `direct` mode) to generate access tokens with `RoomAgentDispatch` for targeted agent dispatch.

Audio flows through the standard LiveKit WebRTC pipeline: VoiceCheck publishes PCM audio via an `AudioSource`, and receives the agent's audio through an `AudioStream` attached to the agent's subscribed track. Speech detection uses RMS-based silence analysis with a multi-frame confirmation window (3 consecutive non-silent frames) to filter out codec warmup noise.

## Installation

```bash
pip install voicecheck[livekit]
```

This installs:
- `livekit >= 1.0.0` (RTC SDK)
- `livekit-api >= 1.0.0` (server-side token generation)

For `token_server` mode, you also need `httpx`:

```bash
pip install httpx
```

## Connection Modes

LiveKit transport supports three connection modes, selected via the `mode` config key.

### 1. `direct` (default)

Generates an access token locally using your LiveKit API key and secret. This is the most common mode for testing. It supports `RoomAgentDispatch` to target a specific agent and pass metadata.

**Required config:** `url`, `api_key`, `api_secret`
**Optional config:** `agent_name`, `agent_metadata`, `room_name`

```yaml
transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "my-voice-agent"
    agent_metadata:
      scenario: "greeting"
      language: "en"
    room_name: "voicecheck-test-room"
```

When `agent_name` is provided, the token includes a `RoomConfiguration` with a `RoomAgentDispatch` entry, which tells LiveKit to dispatch that specific agent into the room. The `agent_metadata` dict is serialized to JSON and passed as the dispatch metadata string, allowing you to send arbitrary context to your agent at connection time.

When `agent_name` is omitted, the token is created without agent dispatch configuration, relying on LiveKit's automatic agent dispatch.

### 2. `token_server`

Calls an external HTTP token server (your backend) to obtain a connection URL and participant token. Useful when your infrastructure already has a token endpoint.

**Required config:** `token_url`
**Optional config:** `token_request`, `token_headers`, `response_mapping`, `token_timeout`

```yaml
transport:
  type: livekit
  mode: token_server
  config:
    token_url: "https://my-backend.example.com/api/livekit/token"
    token_request:
      room_name: "test-room"
      participant_name: "voicecheck-tester"
    token_headers:
      Authorization: "Bearer ${MY_BACKEND_TOKEN}"
    response_mapping:
      url_field: "server_url"
      token_field: "participant_token"
    token_timeout: 15.0
```

The transport sends a `POST` request with `token_request` as the JSON body and `token_headers` as HTTP headers. It expects a JSON response and extracts the LiveKit URL and token using the field names in `response_mapping` (defaults: `server_url` and `participant_token`).

### 3. `token`

Uses a pre-made LiveKit URL and access token directly. Useful for quick tests or when tokens are generated externally.

**Required config:** `url`, `token`

```yaml
transport:
  type: livekit
  mode: token
  config:
    url: "${LIVEKIT_URL}"
    token: "${LIVEKIT_TOKEN}"
```

## Config Reference

| Key | Type | Default | Mode(s) | Description |
|-----|------|---------|---------|-------------|
| `mode` | string | `"direct"` | all | Connection mode: `direct`, `token_server`, or `token` |
| `url` | string | -- | direct, token | LiveKit server URL (e.g., `wss://my-app.livekit.cloud`) |
| `api_key` | string | -- | direct | LiveKit API key |
| `api_secret` | string | -- | direct | LiveKit API secret |
| `agent_name` | string | `""` | direct | Agent name for `RoomAgentDispatch`. Omit for auto-dispatch |
| `agent_metadata` | dict | `{}` | direct | Key-value metadata passed to the agent via dispatch (serialized to JSON) |
| `room_name` | string | `"voicecheck-{timestamp}"` | direct | Room name. Auto-generated with Unix timestamp if omitted |
| `token` | string | -- | token | Pre-made LiveKit access token |
| `token_url` | string | -- | token_server | URL of the external token server endpoint |
| `token_request` | dict | `{}` | token_server | JSON body sent to the token server |
| `token_headers` | dict | `{}` | token_server | HTTP headers sent with the token request |
| `response_mapping` | dict | see below | token_server | Maps response JSON fields to `url` and `token` |
| `response_mapping.url_field` | string | `"server_url"` | token_server | JSON field name for the LiveKit URL in the token server response |
| `response_mapping.token_field` | string | `"participant_token"` | token_server | JSON field name for the access token in the token server response |
| `token_timeout` | float | `15.0` | token_server | HTTP request timeout in seconds for the token server call |
| `sample_rate` | int | `16000` | all | Audio sample rate in Hz |
| `num_channels` | int | `1` | all | Number of audio channels |
| `agent_connect_timeout` | float | `15.0` | all | Seconds to wait for the agent participant to join the room |

## Environment Variables

The following environment variables are typically used with `${VAR}` substitution in YAML configs:

| Variable | Required For | Description |
|----------|-------------|-------------|
| `LIVEKIT_URL` | direct, token | LiveKit server WebSocket URL |
| `LIVEKIT_API_KEY` | direct | LiveKit API key |
| `LIVEKIT_API_SECRET` | direct | LiveKit API secret |
| `LIVEKIT_TOKEN` | token | Pre-generated access token |

## Agent Dispatch with Metadata

In `direct` mode, VoiceCheck can dispatch a specific agent and pass structured metadata. This is useful for configuring agent behavior per test scenario:

```yaml
transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "customer-service-agent"
    agent_metadata:
      scenario: "refund_request"
      customer_tier: "premium"
      language: "en-US"
```

The `agent_metadata` dict is serialized to a JSON string and attached to the `RoomAgentDispatch`. Your agent can read this metadata on the server side to customize its behavior for each test.

## Complete Example

```yaml
name: livekit-greeting-test
description: Test agent greeting via LiveKit

transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "my-voice-agent"
    sample_rate: 16000
    num_channels: 1
    agent_connect_timeout: 20.0
    agent_metadata:
      test_mode: true

turns:
  - user: "Hello, I need help with my account."
    expect:
      - type: latency
        max_first_byte_ms: 2000
      - type: llm_judge
        criteria: "The agent should greet the user and offer assistance."
        min_score: 0.7
```

## Troubleshooting

### Agent does not join the room

- **Symptom:** Log shows `Agent did not join within 15s -- proceeding anyway`.
- **Cause:** The agent process is not running, or `agent_name` does not match the agent registered with LiveKit.
- **Fix:** Verify the agent is running and registered. Check `agent_name` matches exactly. Increase `agent_connect_timeout` if the agent takes longer to start.

### `ImportError: LiveKit SDK not installed`

- **Fix:** Run `pip install voicecheck[livekit]`. This installs both `livekit` and `livekit-api`.

### `ImportError: livekit-api not installed`

- **Cause:** The `livekit` RTC package is installed but `livekit-api` is missing (needed for token generation in `direct` mode).
- **Fix:** Run `pip install voicecheck[livekit]` or `pip install livekit-api`.

### Unexpanded environment variable errors

- **Symptom:** Validation error like `'api_key' is missing or has unexpanded env var: '${LIVEKIT_API_KEY}'`.
- **Cause:** The environment variable is not set in your shell.
- **Fix:** Export the variable before running VoiceCheck: `export LIVEKIT_API_KEY="your-key-here"`.

### Token server returns an error

- **Symptom:** `ConnectionError: Token server returned HTTP 401`.
- **Fix:** Check `token_url`, `token_headers`, and `token_request` in your config. Ensure the server is reachable and your credentials are valid.

### No audio captured from agent

- **Symptom:** `No non-silent audio captured from agent`.
- **Cause:** The agent joined but did not publish an audio track, or the agent's response was entirely below the silence detection threshold.
- **Fix:** Verify the agent is configured to publish audio. Check that the agent is receiving and responding to your test audio. Try increasing the `receive_audio` timeout in your scenario.

### Agent track not found after first turn

- **Cause:** The agent track reference persists across turns within a single room connection. If the agent disconnects and reconnects between turns, the stale reference may cause issues.
- **Fix:** This is handled automatically -- `reset_metrics()` clears timing data but preserves the track reference. If problems persist, check that your agent maintains its room connection across the full test.
