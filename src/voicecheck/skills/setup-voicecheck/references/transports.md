# Transport configuration

Each transport needs (a) some environment variables and (b) a `transport:` block in
the scenario YAML. The scenario format is otherwise identical across transports —
only this block changes. Keep the `${ENV_VAR}` references; VoiceCheck expands them
from the environment at run time.

Set secret values in a local `.env` (copy names from the repo's `.env.example`) or
`export` them in the shell. Never commit real keys. Use `scripts/check_env.py <transport>`
to verify what's set without printing values.

---

## LiveKit

Agent runs in a LiveKit room (WebRTC). Two modes.

### Mode: `direct` (you have LiveKit API credentials)

Env vars:
```
LIVEKIT_URL            # e.g. wss://your-project.livekit.cloud or ws://localhost:7880
LIVEKIT_API_KEY
LIVEKIT_API_SECRET
VOICECHECK_AGENT_NAME  # name of the deployed agent to dispatch
```

```yaml
transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "${VOICECHECK_AGENT_NAME}"
```

### Mode: `token_server` (your backend mints LiveKit tokens)

Env vars:
```
TOKEN_SERVER_URL       # base URL of your token endpoint
FIREBASE_TEST_TOKEN    # bearer token your token server expects (example auth)
TEST_KID_ID            # whatever identifiers your token request needs
TEST_AGENT_ID
```

```yaml
transport:
  type: livekit
  mode: token_server
  config:
    token_url: "${TOKEN_SERVER_URL}/token"
    token_request:
      kid_id: "${TEST_KID_ID}"
      agent_id: "${TEST_AGENT_ID}"
    token_headers:
      Authorization: "Bearer ${FIREBASE_TEST_TOKEN}"
    response_mapping:
      url_field: "server_url"        # field in the token response holding the ws URL
      token_field: "participant_token"
```
The `token_request`, `token_headers`, and `response_mapping` keys are specific to
your backend's contract — adjust field names to match your token server.

---

## Daily / Pipecat

Env vars:
```
DAILY_API_KEY
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

---

## VAPI

Env vars:
```
VAPI_API_KEY
VAPI_ASSISTANT_ID
```

```yaml
transport:
  type: vapi
  mode: web_call
  config:
    api_key: "${VAPI_API_KEY}"
    assistant_id: "${VAPI_ASSISTANT_ID}"
    audio_format: "pcm_s16le"
```

---

## Retell

Env vars:
```
RETELL_API_KEY
RETELL_AGENT_ID
```

```yaml
transport:
  type: retell
  mode: web_call
  config:
    api_key: "${RETELL_API_KEY}"
    agent_id: "${RETELL_AGENT_ID}"
```

---

## Echo (built-in, zero setup)

No env vars, no real agent. Returns a canned reply after a delay — use it for the
smoke test and for validating scenario plumbing.

```yaml
transport:
  type: echo
  config:
    response_text: "Hello, I am the echo transport."
    first_byte_delay_ms: 500
```
