# Daily Transport

## Overview

The Daily transport connects VoiceCheck to voice agents running in [Daily](https://www.daily.co/) WebRTC rooms. This is the transport to use when your agent is built with [Pipecat](https://github.com/pipecat-ai/pipecat) or any other framework that uses Daily as its real-time communication layer.

VoiceCheck joins the Daily room as a participant, publishes audio through a virtual microphone device, and captures the agent's audio through a virtual speaker device. The `daily-python` SDK handles WebRTC negotiation, audio routing, and participant lifecycle events under the hood.

The transport creates two virtual audio devices at connect time: a `VirtualMicrophoneDevice` named `voicecheck-mic` for publishing test audio, and a `VirtualSpeakerDevice` named `voicecheck-speaker` for receiving the agent's mixed audio output. The speaker device is selected as the active output so that all subscribed remote audio is routed through it.

Participant tracking is handled through a custom `EventHandler` subclass that listens for `on_participant_joined`, `on_participant_updated`, and `on_participant_left` events to detect when the agent enters the room and begins publishing its microphone track.

## Installation

```bash
pip install voicecheck[daily]
```

This installs:
- `daily-python >= 0.10.0`

For `api_key` mode, you also need `httpx`:

```bash
pip install httpx
```

### Platform Note

The `daily-python` package includes native libraries. It is available on Linux (x86_64, aarch64) and macOS (x86_64, arm64). Refer to the [daily-python documentation](https://docs.daily.co/reference/daily-python) for platform-specific requirements.

## Connection Modes

Daily transport supports three connection modes, selected via the `mode` config key.

### 1. `room_url` (default)

Joins an existing Daily room by URL. Optionally accepts a meeting token for authenticated access.

**Required config:** `room_url`
**Optional config:** `meeting_token`

```yaml
transport:
  type: daily
  mode: room_url
  config:
    room_url: "https://your-domain.daily.co/test-room"
    meeting_token: "${DAILY_MEETING_TOKEN}"  # optional
```

### 2. `api_key`

Creates a new Daily room via the REST API, generates a meeting token, then joins. The room is created with a 1-hour expiry and `eject_at_room_exp` enabled. If a room with the given name already exists, it is reused.

**Required config:** `api_key`
**Optional config:** `room_name`

```yaml
transport:
  type: daily
  mode: api_key
  config:
    api_key: "${DAILY_API_KEY}"
    room_name: "voicecheck-agent-test"
```

The transport makes two API calls to `https://api.daily.co/v1`:
1. `POST /rooms` -- creates the room (or detects it already exists)
2. `POST /meeting-tokens` -- generates a token for the `voicecheck-tester` participant

### 3. `token`

Joins an existing room using a pre-made meeting token. Both the room URL and token are required.

**Required config:** `room_url`, `meeting_token`

```yaml
transport:
  type: daily
  mode: token
  config:
    room_url: "https://your-domain.daily.co/test-room"
    meeting_token: "${DAILY_MEETING_TOKEN}"
```

## Config Reference

| Key | Type | Default | Mode(s) | Description |
|-----|------|---------|---------|-------------|
| `mode` | string | `"room_url"` | all | Connection mode: `api_key`, `room_url`, or `token` |
| `room_url` | string | -- | room_url, token | Full Daily room URL (e.g., `https://your-domain.daily.co/room-name`) |
| `meeting_token` | string | -- | room_url (opt), token (req) | Daily meeting token for authentication |
| `api_key` | string | -- | api_key | Daily REST API key for room creation |
| `room_name` | string | `"voicecheck-{timestamp}"` | api_key | Room name to create. Auto-generated with Unix timestamp if omitted |
| `sample_rate` | int | `16000` | all | Audio sample rate in Hz for virtual devices |
| `num_channels` | int | `1` | all | Number of audio channels |
| `agent_connect_timeout` | float | `15.0` | all | Seconds to wait for the agent participant to join the room |

## Environment Variables

| Variable | Required For | Description |
|----------|-------------|-------------|
| `DAILY_API_KEY` | api_key mode | Daily REST API key |
| `DAILY_MEETING_TOKEN` | token mode, room_url (optional) | Pre-generated Daily meeting token |

## How It Works with Pipecat Agents

Pipecat agents typically join a Daily room and publish audio through the standard Daily SDK. When VoiceCheck joins the same room:

1. VoiceCheck initializes `Daily.init()` and creates virtual audio devices.
2. The `CallClient` joins the room with camera disabled and microphone enabled (routed through `voicecheck-mic`).
3. Subscription profiles are configured to subscribe only to remote microphone tracks (`camera: "unsubscribed"`).
4. When the Pipecat agent joins and starts publishing its microphone, VoiceCheck's event handler detects it.
5. Test audio is written to the virtual microphone via `write_frames()`, and agent audio is read from the virtual speaker via `read_frames()` in 20ms polling chunks.

The virtual speaker device mixes audio from all subscribed remote participants. In a typical test setup with one VoiceCheck tester and one agent, the speaker output contains only the agent's audio.

## Complete Example

```yaml
name: daily-pipecat-test
description: Test a Pipecat voice agent over Daily

transport:
  type: daily
  mode: api_key
  config:
    api_key: "${DAILY_API_KEY}"
    room_name: "voicecheck-pipecat-test"
    sample_rate: 16000
    num_channels: 1
    agent_connect_timeout: 20.0

turns:
  - user: "Hi, what can you help me with?"
    evaluators:
      - type: latency
        config:
          max_first_byte_ms: 2500
      - type: semantic
        config:
          expected: "The agent should introduce itself and describe its capabilities."
```

## Troubleshooting

### `ImportError: Daily SDK not installed`

- **Fix:** Run `pip install voicecheck[daily]`. This installs `daily-python`.

### Agent does not join the room

- **Symptom:** Log shows `Agent did not join within 15s -- proceeding anyway`.
- **Cause:** The Pipecat agent is not running, or it is configured to join a different room.
- **Fix:** Ensure the agent is running and pointing at the same room URL. If using `api_key` mode, make sure the agent is configured to auto-join newly created rooms (or start the agent after the room is created). Increase `agent_connect_timeout` if needed.

### Room already exists (api_key mode)

- **Symptom:** The room creation returns HTTP 400 with "already exists".
- **Behavior:** This is handled automatically. The transport detects the existing room and reuses it. No action needed.

### `httpx` not installed (api_key mode)

- **Symptom:** `ImportError: httpx not installed`.
- **Fix:** Run `pip install httpx`. The `voicecheck[daily]` extra does not include `httpx` since it is only needed for `api_key` mode.

### No audio captured from agent

- **Symptom:** `No non-silent audio captured from agent`.
- **Cause:** The agent joined but did not publish a microphone track, or the audio is below the silence detection threshold.
- **Fix:** Verify the agent is configured to publish audio. Ensure `start_audio_off` is not enabled on the agent's side. Check that the virtual speaker device is receiving data by enabling debug logging.

### Daily.init() or Daily.deinit() errors

- **Cause:** `Daily.init()` can only be called once per process. If a previous test run did not clean up properly, calling it again may fail.
- **Fix:** The transport calls `Daily.deinit()` during disconnect. Ensure `disconnect()` is always called, even on test failure. If running multiple tests in sequence, the framework handles init/deinit lifecycle automatically.

### Platform-specific native library issues

- **Cause:** `daily-python` ships native binaries that may have system-level dependencies.
- **Fix:** Check the [daily-python installation guide](https://docs.daily.co/reference/daily-python) for your platform. On Linux, ensure required system libraries (like `libopus`) are available.
