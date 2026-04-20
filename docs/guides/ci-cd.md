# CI/CD Integration

VoiceCheck is designed to run in automated pipelines. This guide covers pytest integration, programmatic usage, JSON output for downstream tools, cost optimization, and a complete GitHub Actions workflow.

## pytest Integration

VoiceCheck ships with a pytest plugin that is auto-discovered via the `pytest11` entry point. No configuration is needed beyond installing VoiceCheck.

### Using the marker

Decorate any test function with `@pytest.mark.voicecheck("path/to/scenario.yaml")`:

```python
import pytest


@pytest.mark.voicecheck("examples/livekit_basic.yaml")
def test_basic_greeting():
    """Test that the agent greets the user properly."""
    pass


@pytest.mark.voicecheck("examples/e2e_questions.yaml")
def test_faq_flow():
    """Test FAQ question handling."""
    pass
```

The test body is ignored -- the plugin intercepts execution and runs the scenario. If the scenario fails, `pytest.fail()` is called with a detailed failure message listing which turns and evaluators failed:

```
FAILED test_voice.py::test_basic_greeting - VoiceCheck scenario 'greeting test' failed:
  - Turn 1 [latency]: First byte 4523ms exceeds max 3000ms
  - Turn 2 [keyword]: Missing required keywords: ['hello']
```

### Programmatic testing

For more control, use `ScenarioRunner` directly in async tests:

```python
import pytest
from voicecheck.core.scenario import ScenarioRunner


@pytest.mark.asyncio
async def test_custom_scenario():
    runner = ScenarioRunner.from_yaml("examples/livekit_basic.yaml")
    report = await runner.run()

    assert report.passed, f"Scenario failed: {report.passed_turns}/{report.total_turns} turns passed"
    assert report.total_turns == 2

    # Check specific turn metrics
    first_turn = report.turns[0]
    assert first_turn.metrics.first_byte_ms < 3000
    assert len(first_turn.agent_text) > 0


@pytest.mark.asyncio
async def test_with_skip_llm():
    runner = ScenarioRunner.from_yaml(
        "examples/persona_frustrated_customer.yaml",
        skip_llm_judge=True,
    )
    report = await runner.run()
    assert report.passed
```

### conftest.py setup

If you use custom evaluators, import them in your conftest:

```python
# conftest.py
import my_custom_evaluators  # noqa: F401 -- registers custom evaluators
```

## Exit Codes

VoiceCheck CLI uses exit codes that integrate with CI systems:

| Code | Meaning | CI behavior |
|---|---|---|
| `0` | All scenarios passed | Build passes |
| `1` | One or more scenarios failed or errored | Build fails |

For soak tests, exit code `1` means the pass rate was below 100%.

## JSON Output

Use `-o` / `--output` to write machine-readable JSON reports:

```bash
voicecheck run scenario.yaml -o results.json
```

The output file is automatically timestamped (e.g., `2026-03-24_143022_results.json`). For multiple scenarios, each gets its own timestamped file in the output directory.

### JSON report structure

```json
{
  "scenario_name": "My test",
  "passed": true,
  "total_turns": 3,
  "passed_turns": 3,
  "turns": [
    {
      "turn_index": 0,
      "passed": true,
      "user_text": "Hello!",
      "agent_text": "Hi there! How can I help?",
      "metrics": {
        "first_byte_ms": 1523.4,
        "total_ms": 3200.1,
        "send_duration_ms": 45.2,
        "tts_duration_ms": 340.5,
        "stt_duration_ms": 210.3,
        "agent_audio_duration_ms": 2800.0,
        "agent_audio_frames": 140,
        "user_audio_duration_ms": 1200.0,
        "agent_words_per_second": 4.3
      },
      "evaluations": [
        {
          "type": "latency",
          "passed": true,
          "score": 1.0,
          "reason": "Latency OK (first_byte=1523ms, total=3200ms)",
          "details": { ... }
        }
      ]
    }
  ],
  "conversation_eval": {
    "overall_score": 0.85,
    "overall_passed": true,
    "overall_reason": "Agent performed well across all criteria",
    "criteria_scores": [ ... ]
  }
}
```

You can parse this JSON in subsequent CI steps to extract metrics, post comments on PRs, or feed data to monitoring dashboards.

## Tagging Runs

Tag runs to identify them in the dashboard:

```bash
voicecheck run scenario.yaml --tag ci --tag "pr-42" --tag "commit:abc123"
```

Tags are stored in the database and visible in `voicecheck history` and the web dashboard. This is useful for correlating test results with deployments.

## Cost Optimization with --skip-llm-judge

The `llm_judge` evaluator and `conversation_eval` make LLM API calls that cost money and add latency. In CI, you often want fast, cheap tests:

```bash
# Skip all LLM-based evaluators
voicecheck run scenario.yaml --skip-llm-judge
```

When `--skip-llm-judge` is used:
- All `type: llm_judge` evaluators are silently skipped (logged as `[SKIP]`)
- `conversation_eval` is not executed
- Latency, keyword, and turn_count evaluators still run normally

### Recommended CI strategy

Run two tiers of tests:

```yaml
# Tier 1: fast, cheap, runs on every PR
- name: "Quick voice tests"
  run: voicecheck run scenarios/ --skip-llm-judge

# Tier 2: comprehensive, runs on merge to main
- name: "Full voice tests"
  run: voicecheck run scenarios/
```

## Database Storage

By default, every run is saved to `~/.voicecheck/results.db`. In CI, you may want to:

```bash
# Skip database storage (ephemeral CI environment)
voicecheck run scenario.yaml --no-save

# Use a custom database path (for artifact collection)
voicecheck run scenario.yaml --db ./ci-results.db
```

## GitHub Actions Workflow

Here is a complete GitHub Actions workflow that runs VoiceCheck tests:

```yaml
name: Voice Agent Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  voice-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install system dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y ffmpeg

      - name: Install VoiceCheck
        run: |
          pip install voicecheck[livekit,tts,stt,llm]

      - name: Validate scenarios
        run: |
          voicecheck validate scenarios/

      - name: Run voice agent tests (fast)
        env:
          LIVEKIT_URL: ${{ secrets.LIVEKIT_URL }}
          LIVEKIT_API_KEY: ${{ secrets.LIVEKIT_API_KEY }}
          LIVEKIT_API_SECRET: ${{ secrets.LIVEKIT_API_SECRET }}
          VOICECHECK_AGENT_NAME: ${{ secrets.VOICECHECK_AGENT_NAME }}
        run: |
          voicecheck run scenarios/ \
            --skip-llm-judge \
            --tag ci \
            --tag "commit:${{ github.sha }}" \
            -o test-results/

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: voicecheck-results
          path: test-results/

  voice-tests-full:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    if: github.ref == 'refs/heads/main'

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install system dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y ffmpeg

      - name: Install VoiceCheck
        run: |
          pip install voicecheck[livekit,tts,stt,llm]

      - name: Run full voice agent tests
        env:
          LIVEKIT_URL: ${{ secrets.LIVEKIT_URL }}
          LIVEKIT_API_KEY: ${{ secrets.LIVEKIT_API_KEY }}
          LIVEKIT_API_SECRET: ${{ secrets.LIVEKIT_API_SECRET }}
          VOICECHECK_AGENT_NAME: ${{ secrets.VOICECHECK_AGENT_NAME }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          voicecheck run scenarios/ \
            --tag ci \
            --tag "main" \
            --tag "commit:${{ github.sha }}" \
            -o test-results/

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: voicecheck-results-full
          path: test-results/
```

## Tips for Reliable CI Testing

### 1. Set generous timeouts

Network latency in CI is often higher than local development. Use relaxed `turn_timeout` and `silence_threshold`:

```yaml
settings:
  turn_timeout: 30.0      # generous for CI
  silence_threshold: 3.0   # handle variable network
```

### 2. Use deterministic test modes

Scripted and questions modes produce the most predictable results in CI. Persona mode introduces LLM-generated variance that may cause intermittent failures:

```yaml
# Deterministic: good for CI
questions:
  - "Hello"
  - "What can you do?"
```

### 3. Validate before running

Always validate YAML files as a separate step. This catches syntax errors without spending time on transport connections:

```bash
voicecheck validate scenarios/
```

### 4. Use environment variables for secrets

Never hardcode API keys in YAML. Always use `${ENV_VAR}` syntax:

```yaml
config:
  api_key: "${LIVEKIT_API_KEY}"    # expanded from environment
```

### 5. Collect audio artifacts for debugging failures

When a test fails in CI, audio artifacts help diagnose the issue:

```bash
voicecheck run scenario.yaml --save-audio ./artifacts
```

Upload the `artifacts/` directory as a CI artifact for post-mortem analysis.

### 6. Use pytest for integration with test frameworks

If your project already uses pytest, the marker-based approach integrates voice tests with your existing test suite:

```bash
pytest tests/ -v --timeout=120
```

### 7. Run soak tests on a schedule

Use a scheduled GitHub Actions workflow for longer soak tests:

```yaml
on:
  schedule:
    - cron: "0 2 * * *"  # 2 AM daily

jobs:
  soak-test:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      # ...
      - name: Soak test
        run: |
          voicecheck run scenarios/ \
            --duration 30m \
            --parallel 2 \
            --skip-llm-judge \
            --tag soak \
            -o soak-results.json
```
