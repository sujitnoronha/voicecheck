# CLI Reference

VoiceCheck provides a command-line interface for running tests, validating scenarios, viewing history, and generating dashboards. The CLI entry point is `voicecheck` (installed via `pip install voicecheck`).

```
voicecheck [OPTIONS] COMMAND [ARGS]...
```

**Global options:**

| Option | Description |
|---|---|
| `--version` | Show the installed VoiceCheck version and exit. |
| `--help` | Show help message and exit. |

---

## voicecheck run

Run one or more voice agent test scenarios.

```
voicecheck run [OPTIONS] PATH
```

**PATH** can be a single YAML file or a directory containing YAML files (`*.yaml` and `*.yml`).

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `-v`, `--verbose` | flag | off | Enable verbose (DEBUG) logging. Shows full audio pipeline details. |
| `-o`, `--output PATH` | path | none | Write JSON report(s) to this path. Automatically timestamped. For directories, each scenario gets its own file. |
| `--parallel N` | int | `1` | Number of scenarios to run concurrently. Only applies when running a directory of scenarios. |
| `--no-save` | flag | off | Do not save results to the local SQLite database. |
| `--tag TAG` | string | *(repeatable)* | Tag this run. Can be specified multiple times (e.g., `--tag ci --tag v2`). |
| `--db PATH` | path | `~/.voicecheck/results.db` | Custom database file path. |
| `--duration DURATION` | string | none | Run as a soak test for the given duration (e.g., `20m`, `1h`, `90s`). See [Soak Testing](../guides/soak-testing.md). |
| `--save-audio DIR` | path | none | Save audio artifacts (WAV files + JSON report) to this directory. |
| `--skip-llm-judge` | flag | off | Skip all `llm_judge` evaluators and `conversation_eval`. Saves API cost. |
| `-q`, `--questions TEXT` | string | *(repeatable)* | Override scenario questions from the CLI. Can be specified multiple times. Overrides `persona` and `turns` in the YAML. |
| `--auto` | flag | off | Use LLM persona mode instead of questions mode. Requires `OPENAI_API_KEY`. |
| `--concurrent N` | int | `1` | Run N simultaneous sessions of the same scenario (load testing). Reports aggregate pass rate, latency percentiles (p50/p95/p99), and throughput. |

### Examples

```bash
# Run a single scenario
voicecheck run scenario.yaml

# Run with verbose output
voicecheck run scenario.yaml -v

# Run all scenarios in a directory
voicecheck run examples/

# Run in parallel
voicecheck run examples/ --parallel 3

# Export JSON results
voicecheck run scenario.yaml -o results.json

# Save audio for debugging
voicecheck run scenario.yaml --save-audio ./artifacts

# Soak test for 20 minutes
voicecheck run scenario.yaml --duration 20m

# Tag a CI run
voicecheck run scenario.yaml --tag ci --tag "commit:abc123"

# Override questions from CLI
voicecheck run scenario.yaml -q "Hello!" -q "What are your hours?"

# Switch to persona mode
voicecheck run scenario.yaml --auto

# Cost-optimized run (no LLM calls)
voicecheck run scenario.yaml --skip-llm-judge

# Load test: 10 concurrent sessions
voicecheck run scenario.yaml --concurrent 10

# Sustained load: 20 concurrent sessions for 5 minutes
voicecheck run scenario.yaml --concurrent 20 --duration 5m
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | All scenarios passed |
| `1` | One or more scenarios failed, errored, or (for soak) pass rate < 100% |

### JSON output format

When using `-o`, each scenario produces a timestamped JSON file (e.g., `2026-03-24_143022_results.json`) containing:

- `scenario_name`: string
- `passed`: boolean
- `total_turns`: integer
- `passed_turns`: integer
- `turns`: array of turn objects with `user_text`, `agent_text`, `metrics`, and `evaluations`
- `conversation_eval`: object (if configured and not skipped)

For soak mode, the JSON contains a `soak_summary` object with aggregate statistics.

### Audio artifacts

When using `--save-audio`, VoiceCheck creates:

```
<output_dir>/
  report.json            # Full JSON report
  turn_1_user.wav        # Synthesized user speech for turn 1
  turn_1_agent.wav       # Captured agent audio for turn 1
  turn_2_user.wav
  turn_2_agent.wav
  ...
  full_conversation.wav  # All audio concatenated
```

---

## voicecheck validate

Validate one or more scenario YAML files without running them. Checks YAML syntax, Pydantic model validation, transport type registration, TTS/STT provider names, and evaluator type registration.

```
voicecheck validate PATH
```

**PATH** can be a single YAML file or a directory.

### Examples

```bash
# Validate a single file
voicecheck validate scenario.yaml

# Validate all YAML files in a directory
voicecheck validate examples/
```

### Output

```
scenario.yaml: OK
broken.yaml: INVALID
  - Unknown transport: 'websocket'. Available: livekit, daily, vapi, retell, telephony.
  - Turn 0: Unknown evaluator: 'spelling'. Available: latency, keyword, turn_count, llm_judge.
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | All files are valid |
| `1` | One or more files have validation errors |

---

## voicecheck history

Show recent test run history from the local database.

```
voicecheck history [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `-n`, `--limit N` | int | `20` | Number of runs to display. |
| `-s`, `--scenario NAME` | string | none | Filter by scenario name. |
| `--db PATH` | path | `~/.voicecheck/results.db` | Custom database file path. |

### Examples

```bash
# Show last 20 runs
voicecheck history

# Show last 50 runs
voicecheck history -n 50

# Filter by scenario
voicecheck history -s "My test scenario"
```

### Output

```
ID         Scenario                       Status   Turns      Latency      Date
------------------------------------------------------------------------------------------
a1b2c3d4   greeting-test                  PASS     2/2        1523ms       2026-03-24 14:30:22
e5f6g7h8   faq-test                       FAIL     1/3        2044ms       2026-03-24 14:28:15
...

20 runs shown. Use --limit to see more.
```

---

## voicecheck show

Show detailed results of a specific test run.

```
voicecheck show [OPTIONS] RUN_ID
```

**RUN_ID** supports partial matching -- you only need to type the first few characters.

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `--db PATH` | path | `~/.voicecheck/results.db` | Custom database file path. |

### Examples

```bash
# Show a run by partial ID
voicecheck show a1b2c3d4

# Even shorter partial IDs work
voicecheck show a1b2
```

### Output

```
Run: a1b2c3d4
Scenario: greeting-test
Status: PASSED
Turns: 2/2
Avg Latency: 1523ms
Tags: ci, v2.1
Date: 2026-03-24T14:30:22.000000+00:00

Turn 1: [PASS]
  User:  Hello, who are you?
  Agent: Hi there! I'm Luna, your space explorer friend!
  Latency: 1523ms first byte, 3200ms total
  [+] latency: Latency OK (first_byte=1523ms, total=3200ms) (score=1.00)
  [+] turn_count: Response length OK (10 words) (score=1.00)

Turn 2: [PASS]
  User:  Tell me something interesting.
  Agent: Did you know that the moon has no atmosphere?
  Latency: 1891ms first byte, 4100ms total
  [+] latency: Latency OK (first_byte=1891ms, total=4100ms) (score=1.00)
  [+] turn_count: Response length OK (9 words) (score=1.00)
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Run found and displayed |
| `1` | Run not found |

---

## voicecheck dashboard

Generate a static HTML dashboard with charts and history.

```
voicecheck dashboard [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `-o`, `--output PATH` | path | `voicecheck_dashboard.html` | Output HTML file path. |
| `-s`, `--scenario NAME` | string | none | Filter by scenario name. |
| `-n`, `--limit N` | int | `100` | Maximum number of runs per scenario. |
| `--db PATH` | path | `~/.voicecheck/results.db` | Custom database file path. |
| `--open` | flag | off | Open the dashboard in the default browser after generating. |

### Examples

```bash
# Generate and open dashboard
voicecheck dashboard --open

# Custom output path
voicecheck dashboard -o reports/dashboard.html

# Filter to one scenario
voicecheck dashboard -s "greeting-test" --open
```

### Dashboard contents

The generated HTML dashboard includes:
- Pass/fail trend charts per scenario
- Latency trend charts (first byte and total)
- Conversation transcripts
- Per-scenario quality metrics

---

## voicecheck serve

Launch a live, interactive web dashboard.

```
voicecheck serve [OPTIONS]
```

Requires the `dashboard` extra: `pip install voicecheck[dashboard]`.

### Options

| Option | Type | Default | Description |
|---|---|---|---|
| `-p`, `--port N` | int | `8989` | Port to listen on. |
| `--host HOST` | string | `127.0.0.1` | Host to bind to. |
| `--db PATH` | path | `~/.voicecheck/results.db` | Custom database file path. |

### Examples

```bash
# Start on default port
voicecheck serve

# Custom port and host
voicecheck serve -p 3000 --host 0.0.0.0

# Use a custom database
voicecheck serve --db ./project-results.db
```

### Dashboard features

The live dashboard provides:

- **Scenario overview**: All scenarios with pass rates, run counts, and latency percentiles (P50, P95, P99)
- **Run history**: Paginated list of all runs, filterable by scenario
- **Run detail**: Full conversation transcripts with per-turn timing and evaluator results
- **Comparison**: Side-by-side scenario comparison

### API endpoints

The dashboard also exposes JSON API endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/api/scenarios` | GET | Summary stats per scenario |
| `/api/scenarios/{name}/history` | GET | Historical runs for a scenario |
| `/api/scenarios/{name}/percentiles` | GET | Latency percentiles (P50, P95, P99) |
| `/api/runs` | GET | Paginated run list (query params: `scenario`, `limit`, `offset`) |
| `/api/runs/{run_id}` | GET | Full run details with turns |
| `/api/runs/{run_id}` | DELETE | Delete a run |

---

## Environment Variables

VoiceCheck does not require any environment variables itself, but your scenarios typically need them for transport authentication:

| Variable | Used by |
|---|---|
| `LIVEKIT_URL` | LiveKit transport |
| `LIVEKIT_API_KEY` | LiveKit transport (direct mode) |
| `LIVEKIT_API_SECRET` | LiveKit transport (direct mode) |
| `DAILY_API_KEY` | Daily transport (api_key mode) |
| `VAPI_API_KEY` | VAPI transport |
| `VAPI_ASSISTANT_ID` | VAPI transport |
| `RETELL_API_KEY` | Retell transport |
| `RETELL_AGENT_ID` | Retell transport |
| `TWILIO_ACCOUNT_SID` | Telephony transport |
| `TWILIO_AUTH_TOKEN` | Telephony transport |
| `OPENAI_API_KEY` | Persona mode, llm_judge evaluator, OpenAI TTS, OpenAI STT |
| `ANTHROPIC_API_KEY` | llm_judge evaluator (Anthropic provider) |
