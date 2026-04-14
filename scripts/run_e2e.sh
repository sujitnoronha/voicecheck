#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  run_e2e.sh — Full-stack E2E test via the token server
#
#  Tests the complete flow: Firebase auth → token server → LiveKit
#  agent dispatch → voice conversation → evaluation.
#
#  Two modes (single e2e.yaml has both questions and persona):
#    Questions (default) — deterministic, no OPENAI_API_KEY needed:
#      ./scripts/run_e2e.sh --firebase-token "..." --kid-id "..." --agent-id "..." --skip-llm-judge
#
#    Auto — LLM persona drives dynamic conversation, needs OPENAI_API_KEY:
#      ./scripts/run_e2e.sh --firebase-token "..." --kid-id "..." --agent-id "..." --auto
#
#  Options:
#    --firebase-token TOKEN   Firebase auth token
#    --kid-id ID              Kid UUID from database
#    --agent-id ID            Character/agent UUID from database
#    --scenario FILE          YAML scenario (default: e2e.yaml)
#    --save-audio DIR         Save WAV files + report to directory
#    --skip-llm-judge         Skip LLM evaluation (no OPENAI_API_KEY needed)
#    --auto                   Use LLM persona instead of fixed questions
#    --questions "Q1" "Q2"    Override scenario with custom questions
#    --verbose / -v           Verbose logging
#
#  Env var alternatives:
#    FIREBASE_TEST_TOKEN, TEST_KID_ID, TEST_AGENT_ID, OPENAI_API_KEY
#
#  Prerequisites:
#    - kubectl configured for kid-parent-dev cluster
#    - pip install voicecheck[all]
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Defaults ──────────────────────────────────────────────────────
NAMESPACE="livekit"
LK_SERVER_LOCAL_PORT=7880
TOKEN_SERVER_LOCAL_PORT=8090
SCENARIO="${PROJECT_DIR}/examples/e2e.yaml"
VERBOSE=""
SAVE_AUDIO=""
SKIP_LLM_JUDGE=""
AUTO_MODE=""
QUESTIONS=()
CLEANUP_PIDS=()

# ── Parse args ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --firebase-token) FIREBASE_TEST_TOKEN="$2"; shift 2 ;;
    --kid-id)         TEST_KID_ID="$2"; shift 2 ;;
    --agent-id)       TEST_AGENT_ID="$2"; shift 2 ;;
    --scenario)       SCENARIO="$2"; shift 2 ;;
    --save-audio)     SAVE_AUDIO="$2"; shift 2 ;;
    --skip-llm-judge) SKIP_LLM_JUDGE="--skip-llm-judge"; shift ;;
    --auto)           AUTO_MODE="--auto"; shift ;;
    --questions)      shift; while [[ $# -gt 0 && ! "$1" == --* ]]; do QUESTIONS+=("$1"); shift; done ;;
    --verbose|-v)     VERBOSE="-v"; shift ;;
    --help|-h)
      echo "Usage: ./scripts/run_e2e.sh --firebase-token TOKEN --kid-id ID --agent-id ID [OPTIONS]"
      echo ""
      echo "Modes:"
      echo "  (default)                Fixed questions from the YAML (no OPENAI_API_KEY needed with --skip-llm-judge)"
      echo "  --auto                   LLM persona drives a dynamic conversation (needs OPENAI_API_KEY)"
      echo ""
      echo "Options:"
      echo "  --scenario FILE          YAML scenario (default: e2e.yaml)"
      echo "  --save-audio DIR         Save WAV files and JSON report"
      echo "  --skip-llm-judge         Skip LLM-as-judge evaluation"
      echo "  --questions \"Q1\" \"Q2\"    Override with ad-hoc questions"
      echo "  --verbose / -v           Verbose logging"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# ── Validate required vars ────────────────────────────────────────
missing=()
[[ -z "${FIREBASE_TEST_TOKEN:-}" ]] && missing+=("FIREBASE_TEST_TOKEN (or --firebase-token)")
[[ -z "${TEST_KID_ID:-}" ]]        && missing+=("TEST_KID_ID (or --kid-id)")
[[ -z "${TEST_AGENT_ID:-}" ]]      && missing+=("TEST_AGENT_ID (or --agent-id)")
# Auto mode needs OPENAI_API_KEY for persona generation
if [[ -n "$AUTO_MODE" && -z "${OPENAI_API_KEY:-}" ]]; then
  missing+=("OPENAI_API_KEY (required for --auto mode)")
fi
# Without --auto, --skip-llm-judge, or --questions, conversation_eval needs OPENAI_API_KEY
if [[ -z "$AUTO_MODE" && -z "$SKIP_LLM_JUDGE" && ${#QUESTIONS[@]} -eq 0 && -z "${OPENAI_API_KEY:-}" ]]; then
  missing+=("OPENAI_API_KEY (or use --skip-llm-judge / --questions)")
fi

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: Missing required variables:"
  for m in "${missing[@]}"; do echo "  - $m"; done
  echo ""
  echo "Set them as env vars or pass as flags. Run with --help for usage."
  exit 1
fi

# ── Cleanup function ──────────────────────────────────────────────
cleanup() {
  echo ""
  echo "Cleaning up port-forwards..."
  for pid in "${CLEANUP_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT

# ── Check kubectl access ──────────────────────────────────────────
echo "Checking cluster access..."
if ! kubectl get ns "$NAMESPACE" &>/dev/null; then
  echo "ERROR: Cannot access namespace '$NAMESPACE'. Is kubectl configured?"
  echo "  gcloud container clusters get-credentials dev-cluster --region us-central1 --project kid-parent-dev"
  exit 1
fi

# ── Start port-forwards ──────────────────────────────────────────
echo "Starting port-forwards..."

# LiveKit server (needed for the token's server_url to resolve)
kubectl port-forward -n "$NAMESPACE" svc/livekit-server "$LK_SERVER_LOCAL_PORT:80" &>/dev/null &
CLEANUP_PIDS+=($!)
echo "  LiveKit server → localhost:$LK_SERVER_LOCAL_PORT"

# Token server
kubectl port-forward -n "$NAMESPACE" svc/livekit-token-server "$TOKEN_SERVER_LOCAL_PORT:80" &>/dev/null &
CLEANUP_PIDS+=($!)
echo "  Token server   → localhost:$TOKEN_SERVER_LOCAL_PORT"

# Wait for port-forwards to be ready
sleep 3

# ── Verify token server is reachable ─────────────────────────────
echo "Verifying token server health..."
if ! curl -sf "http://localhost:$TOKEN_SERVER_LOCAL_PORT/health" &>/dev/null; then
  echo "ERROR: Token server not reachable at localhost:$TOKEN_SERVER_LOCAL_PORT"
  echo "  Check: kubectl get pods -n $NAMESPACE -l app=livekit-token-server"
  exit 1
fi
echo "  Token server is healthy."

# ── Export env vars for voicecheck YAML expansion ─────────────────
export FIREBASE_TEST_TOKEN
export TEST_KID_ID
export TEST_AGENT_ID
export TOKEN_SERVER_URL="http://localhost:$TOKEN_SERVER_LOCAL_PORT"
export LIVEKIT_URL="ws://localhost:$LK_SERVER_LOCAL_PORT"

# Also export LiveKit creds (needed if running direct-mode scenarios too)
if [[ -z "${LIVEKIT_API_KEY:-}" ]]; then
  echo "Fetching LiveKit credentials from cluster..."
  export LIVEKIT_API_KEY=$(kubectl get secret livekit-secrets -n "$NAMESPACE" -o jsonpath='{.data.LIVEKIT_API_KEY}' | base64 -d)
  export LIVEKIT_API_SECRET=$(kubectl get secret livekit-secrets -n "$NAMESPACE" -o jsonpath='{.data.LIVEKIT_API_SECRET}' | base64 -d)
  echo "  LiveKit credentials loaded from cluster secrets."
fi

# Agent name for direct-mode scenarios (deployed agent registers as kidco-agent)
export VOICECHECK_AGENT_NAME="kidco-agent"

# ── Print config ──────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════"
echo "  E2E Test Configuration"
echo "═══════════════════════════════════════════════"
echo "  Token server: $TOKEN_SERVER_URL"
echo "  LiveKit URL:  $LIVEKIT_URL"
echo "  Kid ID:       $TEST_KID_ID"
echo "  Agent ID:     $TEST_AGENT_ID"
echo "  Agent name:   $VOICECHECK_AGENT_NAME"
echo "  Scenario:     $(basename "$SCENARIO")"
[[ -n "$AUTO_MODE" ]]           && echo "  Mode:         AUTO (LLM persona)" || echo "  Mode:         QUESTIONS (fixed)"
[[ -n "$SAVE_AUDIO" ]]          && echo "  Save audio:   $SAVE_AUDIO"
[[ -n "$SKIP_LLM_JUDGE" ]]     && echo "  LLM judge:    SKIPPED"
[[ ${#QUESTIONS[@]} -gt 0 ]]    && echo "  Questions:    ${#QUESTIONS[@]} custom questions"
echo "═══════════════════════════════════════════════"
echo ""

# ── Validate scenario ────────────────────────────────────────────
echo "Validating scenario..."
voicecheck validate "$SCENARIO"
echo ""

# ── Build voicecheck command ─────────────────────────────────────
VC_CMD=(voicecheck run "$SCENARIO")
[[ -n "$VERBOSE" ]]        && VC_CMD+=("$VERBOSE")
[[ -n "$SAVE_AUDIO" ]]     && VC_CMD+=(--save-audio "$SAVE_AUDIO")
[[ -n "$SKIP_LLM_JUDGE" ]] && VC_CMD+=(--skip-llm-judge)
[[ -n "$AUTO_MODE" ]]      && VC_CMD+=(--auto)
if [[ ${#QUESTIONS[@]} -gt 0 ]]; then
  for q in "${QUESTIONS[@]}"; do VC_CMD+=(-q "$q"); done
fi

echo "Running E2E test..."
echo ""
"${VC_CMD[@]}"

echo ""
echo "Done."
