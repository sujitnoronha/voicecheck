# VoiceCheck Scripts

## run_e2e.sh

Full-stack E2E test for the kid-parent voice agent. Tests the complete flow:

```
Firebase auth → Token server → LiveKit agent dispatch → Voice conversation → Evaluation
```

Handles kubectl port-forwards, credential fetching, and cleanup automatically.

### Prerequisites

- `kubectl` configured for the `kid-parent-dev` cluster
- `pip install voicecheck[all]`
- A valid Firebase test token, kid ID, and agent ID

```bash
# If kubectl isn't configured:
gcloud container clusters get-credentials dev-cluster --region us-central1 --project kid-parent-dev
```

### Quick Start

```bash
# Minimal run — fixed questions, no LLM eval, no OPENAI_API_KEY needed
./scripts/run_e2e.sh \
  --firebase-token "eyJhb..." \
  --kid-id "uuid-of-kid" \
  --agent-id "uuid-of-character" \
  --skip-llm-judge
```

### Two Modes

#### 1. Questions Mode (default)

Sends a fixed set of questions to the agent. Deterministic and reproducible — same questions every run. Does not need `OPENAI_API_KEY` when used with `--skip-llm-judge`.

**Scenario:** [`examples/e2e_questions.yaml`](../examples/e2e_questions.yaml)

```bash
# Default — uses e2e_questions.yaml
./scripts/run_e2e.sh \
  --firebase-token "..." --kid-id "..." --agent-id "..." \
  --skip-llm-judge \
  --save-audio ./artifacts

# With LLM conversation eval (needs OPENAI_API_KEY)
./scripts/run_e2e.sh \
  --firebase-token "..." --kid-id "..." --agent-id "..."

# Override questions from the command line
./scripts/run_e2e.sh \
  --firebase-token "..." --kid-id "..." --agent-id "..." \
  --questions "Hello!" "What is 2 plus 2?" "Tell me a story" \
  --skip-llm-judge
```

#### 2. Persona Mode

An LLM-driven child persona has a dynamic conversation with the agent. Each run is unique — the persona adapts to agent responses. Requires `OPENAI_API_KEY`.

**Scenario:** [`examples/e2e_persona.yaml`](../examples/e2e_persona.yaml)

```bash
./scripts/run_e2e.sh \
  --firebase-token "..." --kid-id "..." --agent-id "..." \
  --scenario examples/e2e_persona.yaml \
  --save-audio ./artifacts
```

### Options

| Flag | Description |
|------|-------------|
| `--firebase-token TOKEN` | Firebase auth token (or set `FIREBASE_TEST_TOKEN`) |
| `--kid-id ID` | Kid UUID from database (or set `TEST_KID_ID`) |
| `--agent-id ID` | Character/agent UUID from database (or set `TEST_AGENT_ID`) |
| `--scenario FILE` | YAML scenario file (default: `examples/e2e_questions.yaml`) |
| `--save-audio DIR` | Save WAV files + JSON report to directory |
| `--skip-llm-judge` | Skip LLM evaluation — no `OPENAI_API_KEY` needed |
| `--questions "Q1" "Q2"` | Override scenario with ad-hoc questions |
| `--verbose` / `-v` | Verbose logging |

### What It Does

1. Validates required env vars / flags
2. Sets up kubectl port-forwards:
   - LiveKit server → `localhost:7880`
   - Token server → `localhost:8090`
3. Verifies token server health
4. Fetches LiveKit API credentials from cluster secrets (if not already set)
5. Exports all env vars needed for YAML `${VAR}` expansion
6. Validates the scenario YAML
7. Runs `voicecheck run` with all flags
8. Cleans up port-forwards on exit (even on error/Ctrl+C)

### Output

When `--save-audio` is used, artifacts are saved to the specified directory:

```
./artifacts/
  report.json          # Full JSON report with metrics and eval results
  turn_1_user.wav      # TTS audio sent to the agent
  turn_1_agent.wav     # Agent's audio response
  turn_2_user.wav
  turn_2_agent.wav
  ...
```

### Example Scenarios

| File | Mode | Description |
|------|------|-------------|
| [`e2e_questions.yaml`](../examples/e2e_questions.yaml) | Questions | Fixed questions via token server (default) |
| [`e2e_persona.yaml`](../examples/e2e_persona.yaml) | Persona | LLM child persona via token server |
| [`livekit_token_server.yaml`](../examples/livekit_token_server.yaml) | Scripted | Minimal smoke test (single "Hello!" turn) |
| [`soak_personas/`](../examples/soak_personas/) | Persona | Kid personas for soak testing (direct mode) |

### Environment Variables

These can be set instead of passing flags:

| Variable | Flag equivalent |
|----------|----------------|
| `FIREBASE_TEST_TOKEN` | `--firebase-token` |
| `TEST_KID_ID` | `--kid-id` |
| `TEST_AGENT_ID` | `--agent-id` |
| `OPENAI_API_KEY` | Required for persona mode and LLM eval |

These are set automatically by the script:

| Variable | Value | Purpose |
|----------|-------|---------|
| `TOKEN_SERVER_URL` | `http://localhost:8090` | Token server endpoint |
| `LIVEKIT_URL` | `ws://localhost:7880` | LiveKit WebSocket URL |
| `LIVEKIT_API_KEY` | From cluster secrets | LiveKit API key |
| `LIVEKIT_API_SECRET` | From cluster secrets | LiveKit API secret |
| `VOICECHECK_AGENT_NAME` | `kidco-agent` | Deployed agent name |
