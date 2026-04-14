# Telephony (Twilio) Transport

## Overview

The telephony transport connects VoiceCheck to voice agents over real phone calls using [Twilio Media Streams](https://www.twilio.com/docs/voice/media-streams). Unlike other VoiceCheck transports that act as WebSocket clients, the telephony transport runs a **local HTTP and WebSocket server** that Twilio connects back to.

The call flow works as follows:

1. VoiceCheck starts a local `aiohttp` web server on a configurable port (default `8765`).
2. VoiceCheck places an outbound call via the Twilio REST API, pointing TwiML at the local server's `/twiml` endpoint (exposed through a public URL, typically via ngrok).
3. Twilio answers and fetches the TwiML from `GET /twiml`, which contains a `<Connect><Stream>` instruction pointing at the server's `/stream` WebSocket endpoint.
4. Twilio opens a Media Stream WebSocket back to VoiceCheck's `/stream` endpoint.
5. Audio flows bidirectionally over that WebSocket: inbound audio arrives as base64-encoded G.711 mu-law at 8 kHz, and outbound audio is encoded the same way.
6. VoiceCheck automatically transcodes between Twilio's format (8 kHz mu-law) and its internal format (16 kHz 16-bit PCM).
7. On disconnect, VoiceCheck hangs up the call via the Twilio REST API and shuts down the local server.

## Installation

```bash
pip install voicecheck[telephony]
```

This installs:
- `twilio >= 9.0` (Twilio Python SDK)
- `aiohttp >= 3.9` (async HTTP/WebSocket server)

## Prerequisites

### 1. Twilio Account

You need a Twilio account with:
- An **Account SID** and **Auth Token** (found in the [Twilio Console](https://console.twilio.com/))
- A **Twilio phone number** capable of making outbound calls
- The **target phone number** of your voice agent

### 2. Public URL (ngrok)

Twilio must be able to reach your local server over the public internet. The most common approach is [ngrok](https://ngrok.com/):

```bash
# Install ngrok (if not already installed)
brew install ngrok      # macOS
# or download from https://ngrok.com/download

# Start ngrok tunnel on the same port as server_port (default 8765)
ngrok http 8765
```

ngrok will display a public URL like `https://abc123.ngrok-free.app`. Use this as your `public_url` config value.

**Important:** The ngrok tunnel must be running before you start VoiceCheck, and it must remain active for the duration of the test.

## Step-by-Step Setup

1. **Start ngrok** to expose your local port:
   ```bash
   ngrok http 8765
   ```
   Note the `https://` forwarding URL.

2. **Set environment variables:**
   ```bash
   export TWILIO_ACCOUNT_SID="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   export TWILIO_AUTH_TOKEN="your_auth_token_here"
   export TWILIO_FROM_NUMBER="+15551234567"
   export AGENT_PHONE_NUMBER="+15559876543"
   export VOICECHECK_PUBLIC_URL="https://abc123.ngrok-free.app"
   ```

3. **Create your scenario YAML** (see example below).

4. **Run VoiceCheck:**
   ```bash
   voicecheck run scenario.yaml
   ```

VoiceCheck will start its local server, place the outbound call, wait for Twilio to connect the Media Stream, then begin the test.

## Codec Handling

Twilio Media Streams use **G.711 mu-law** encoding at **8 kHz mono**. VoiceCheck's internal pipeline uses **16-bit PCM** at **16 kHz mono**. The transport handles all conversions transparently:

| Direction | Conversion Pipeline |
|-----------|-------------------|
| **Inbound** (Twilio to VoiceCheck) | Base64 decode -> mu-law bytes -> PCM 8 kHz -> Resample to PCM 16 kHz |
| **Outbound** (VoiceCheck to Twilio) | PCM 16 kHz -> Resample to PCM 8 kHz -> mu-law encode -> Base64 encode -> JSON media message |

Outbound audio is wrapped in a Twilio media event JSON structure:
```json
{
  "event": "media",
  "streamSid": "MZxxxxxxxxx",
  "media": {
    "payload": "<base64-encoded-mulaw>"
  }
}
```

## Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `account_sid` | string | -- | Twilio Account SID (required) |
| `auth_token` | string | -- | Twilio Auth Token (required) |
| `from_number` | string | -- | Twilio phone number to call from, E.164 format (required) |
| `to_number` | string | -- | Phone number of the voice agent to call, E.164 format (required) |
| `public_url` | string | -- | Publicly accessible URL pointing to the local server, e.g., ngrok URL (required) |
| `server_port` | int | `8765` | Local port for the HTTP/WebSocket server |
| `call_connect_timeout` | float | `30.0` | Seconds to wait for Twilio to open the Media Stream after placing the call |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TWILIO_ACCOUNT_SID` | Yes | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Yes | Twilio Auth Token |
| `TWILIO_FROM_NUMBER` | Yes | Twilio phone number (E.164 format, e.g., `+15551234567`) |
| `AGENT_PHONE_NUMBER` | Yes | Target voice agent phone number (E.164 format) |
| `VOICECHECK_PUBLIC_URL` | Yes | Public URL for the local server (e.g., `https://abc123.ngrok-free.app`) |

## Server Endpoints

The transport starts two endpoints on the local server:

### `GET /twiml`

Serves TwiML XML that instructs Twilio to open a Media Stream WebSocket:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://abc123.ngrok-free.app/stream" />
  </Connect>
</Response>
```

The `wss://` URL is derived from the `public_url` config value.

### `GET /stream` (WebSocket)

Handles the Twilio Media Stream WebSocket connection. Processes the following Twilio events:

| Event | Description |
|-------|-------------|
| `connected` | Stream connected. Logged with protocol version |
| `start` | Stream started. Contains `streamSid` used for outbound messages |
| `media` | Audio data. Base64-encoded mu-law payload is decoded and queued |
| `stop` | Stream stopped. Signals end of audio capture |
| `mark` | Acknowledgement event. Ignored |

## Complete Example

```yaml
name: telephony-ivr-test
description: Test a phone-based voice agent via Twilio

transport:
  type: telephony
  mode: twilio
  config:
    account_sid: "${TWILIO_ACCOUNT_SID}"
    auth_token: "${TWILIO_AUTH_TOKEN}"
    from_number: "${TWILIO_FROM_NUMBER}"
    to_number: "${AGENT_PHONE_NUMBER}"
    public_url: "${VOICECHECK_PUBLIC_URL}"
    server_port: 8765
    call_connect_timeout: 30.0

turns:
  - user: "I need to check my account balance."
    evaluators:
      - type: latency
        config:
          max_first_byte_ms: 5000
      - type: semantic
        config:
          expected: "The agent should ask for account identification or provide the balance."
```

## Troubleshooting

### `ConnectionError: Timed out waiting for Twilio Media Stream`

- **Cause:** Twilio could not reach your local server's `/twiml` or `/stream` endpoint within the timeout period.
- **Fixes:**
  1. Verify ngrok is running and the tunnel is active: `curl https://your-ngrok-url.ngrok-free.app/twiml` should return TwiML XML.
  2. Ensure `public_url` in your config matches the ngrok forwarding URL exactly (include `https://`, no trailing slash).
  3. Ensure `server_port` matches the port ngrok is forwarding to (default `8765`).
  4. Check that your Twilio account has sufficient balance and the `from_number` is valid.
  5. Increase `call_connect_timeout` if the agent takes a long time to answer.

### Call is placed but no Media Stream connects

- **Cause:** The TwiML was not served correctly, or the `<Stream>` URL is wrong.
- **Fixes:**
  1. Test the TwiML endpoint manually: `curl https://your-ngrok-url.ngrok-free.app/twiml`. It should return valid XML with a `<Stream url="wss://...">` element.
  2. Check ngrok's web inspector at `http://127.0.0.1:4040` for incoming requests and responses.
  3. Verify the `public_url` host matches the ngrok domain -- the transport extracts the hostname for the `wss://` URL in the TwiML.

### `ValueError: Telephony transport requires config key 'public_url'`

- **Cause:** A required config key is missing.
- **Fix:** All five keys (`account_sid`, `auth_token`, `from_number`, `to_number`, `public_url`) are required. Set the corresponding environment variables.

### `ImportError: aiohttp is not installed`

- **Fix:** Run `pip install voicecheck[telephony]`. This installs both `aiohttp` and `twilio`.

### `ImportError: Twilio SDK is not installed`

- **Fix:** Run `pip install voicecheck[telephony]`.

### `ConnectionError: Failed to place Twilio call`

- **Cause:** The Twilio REST API rejected the call.
- **Fixes:**
  1. Verify `account_sid` and `auth_token` are correct.
  2. Ensure `from_number` is a valid Twilio phone number in your account.
  3. Ensure `to_number` is a valid E.164 phone number.
  4. Check your Twilio account for geographic permissions (international calls may need to be enabled).

### Audio quality is poor or garbled

- **Cause:** G.711 mu-law at 8 kHz is inherently lower quality than other transports. The double resampling (8 kHz <-> 16 kHz) may introduce minor artifacts.
- **Mitigation:** This is expected for telephony. Adjust your latency and quality thresholds accordingly -- telephony tests typically need more lenient evaluator settings.

### No audio captured from agent

- **Symptom:** `No non-silent audio captured from agent`.
- **Possible causes:**
  - The call was not answered by the agent.
  - The agent responded but the audio was too quiet after mu-law decoding and resampling.
  - The Media Stream stopped before the agent spoke.
- **Fix:** Check Twilio call logs in the console for the call SID. Verify the agent's phone number is correct and the agent is running. Check ngrok logs for WebSocket connection activity.

### Port already in use

- **Cause:** Another process is using the configured `server_port`.
- **Fix:** Change `server_port` in your config to an available port, and update your ngrok command to match: `ngrok http <new-port>`.
