# Soak Testing

Soak testing (also called stress testing or endurance testing) runs your voice agent test scenarios repeatedly over an extended period. This helps surface intermittent failures, latency degradation, memory leaks, rate limiting issues, and other problems that only appear under sustained load.

## What Soak Testing Does

When you run VoiceCheck with `--duration`, it enters soak mode:

1. Loads one or more scenario YAML files
2. Runs them in a loop for the specified duration
3. Collects timing metrics and pass/fail results from every iteration
4. Prints an aggregate summary when the duration expires (or when you press Ctrl+C)

Each iteration runs all scenarios sequentially (or in parallel with `--parallel`). The loop continues until the clock runs out.

## CLI Usage

### Basic soak test

```bash
# Run for 20 minutes
voicecheck run scenario.yaml --duration 20m

# Run for 1 hour
voicecheck run scenario.yaml --duration 1h

# Run for 90 seconds
voicecheck run scenario.yaml --duration 90s
```

### Duration format

| Format | Example | Duration |
|---|---|---|
| `Ns` | `90s` | 90 seconds |
| `Nm` | `20m` | 20 minutes |
| `Nh` | `1h` | 1 hour |
| `N` | `120` | 120 seconds (default unit) |

### Multiple scenarios

Run all YAML files in a directory:

```bash
voicecheck run examples/soak_personas/ --duration 30m
```

### Parallel execution

Run multiple scenarios concurrently within each iteration:

```bash
voicecheck run examples/ --duration 20m --parallel 3
```

With `--parallel 3`, up to 3 scenarios run simultaneously. This is useful for stress-testing your agent under concurrent load.

### Saving results

```bash
# Save aggregate summary to JSON
voicecheck run scenario.yaml --duration 20m -o soak_results.json

# Save individual runs to the database (default behavior)
voicecheck run scenario.yaml --duration 20m

# Skip database storage
voicecheck run scenario.yaml --duration 20m --no-save
```

### Tagging soak runs

Tag runs for filtering in the dashboard:

```bash
voicecheck run scenario.yaml --duration 20m --tag soak --tag v2.1
```

Each iteration is automatically tagged with `soak` and `iteration:N`.

## Understanding the Summary Output

After the soak test completes (or when you press Ctrl+C), VoiceCheck prints an aggregate summary:

```
============================================================
  SOAK TEST SUMMARY
============================================================

  Duration:      20.0 minutes
  Iterations:    12
  Total runs:    24
  Passed:        22
  Failed:        1
  Errors:        1
  Pass rate:     91.7%

  Turns:         88/96 passed
  Avg latency:   1847ms (first byte)
  P95 latency:   3102ms (first byte)
  Avg total:     5230ms

  Per-scenario breakdown:
  --------------------------------------------------
  greeting-test                  12/12 passed (100%) avg=1650ms
  faq-test                       10/12 passed (83%) avg=2044ms

============================================================
```

### Summary fields

| Field | Description |
|---|---|
| **Duration** | Total wall-clock time of the soak test |
| **Iterations** | Number of complete loops through all scenarios |
| **Total runs** | Total number of individual scenario executions |
| **Passed** | Number of runs where all turns and evaluators passed |
| **Failed** | Number of runs where at least one evaluator failed |
| **Errors** | Number of runs that crashed (transport error, timeout, etc.) |
| **Pass rate** | `passed / total_runs * 100` |
| **Turns** | Aggregate turn pass count across all runs |
| **Avg latency** | Mean first-byte latency across all turns |
| **P95 latency** | 95th percentile first-byte latency (95% of turns were faster than this) |
| **Avg total** | Mean total response time across all turns |

### Per-scenario breakdown

Shows per-scenario pass rate and average first-byte latency. This helps identify which scenarios are problematic.

## JSON Export

When you use `-o`, the soak summary is written as JSON:

```json
{
  "type": "soak_summary",
  "duration_seconds": 1200.5,
  "total_iterations": 12,
  "total_runs": 24,
  "passed_runs": 22,
  "failed_runs": 1,
  "error_runs": 1,
  "pass_rate": 91.67,
  "avg_first_byte_ms": 1847.3,
  "p95_first_byte_ms": 3102.1,
  "avg_total_ms": 5230.4,
  "per_scenario": {
    "greeting-test": {
      "runs": 12,
      "passed": 12,
      "failed": 0,
      "errors": 0,
      "first_byte_ms": [1523.0, 1650.0, ...]
    },
    "faq-test": {
      "runs": 12,
      "passed": 10,
      "failed": 1,
      "errors": 1,
      "first_byte_ms": [2044.0, 1891.0, ...]
    }
  }
}
```

The output file is automatically timestamped (e.g., `2026-03-24_143022_soak_results.json`).

## Exit Code

The soak test exits with code `1` if the pass rate is below 100%. This makes it easy to use in CI pipelines where any failure should fail the build.

## Early Termination

Press **Ctrl+C** at any time to stop the soak test early. VoiceCheck will immediately generate the summary from all results collected so far. This is useful when you spot a pattern and want to investigate without waiting for the full duration.

## Tips for Effective Soak Testing

### 1. Start short, then extend

Begin with 5-minute soak tests to validate your setup, then extend to 20-60 minutes for real endurance testing.

```bash
# Quick validation
voicecheck run scenario.yaml --duration 5m

# Real soak test
voicecheck run scenario.yaml --duration 1h
```

### 2. Use simple, fast scenarios

Soak tests run many iterations, so favor scripted or questions mode over persona mode to minimize per-run cost and time:

```yaml
questions:
  - "Hello, how are you?"
  - "What can you help me with?"

per_turn_expect:
  - type: latency
    max_first_byte_ms: 3000
  - type: turn_count
    min_words: 3
```

### 3. Skip LLM judge for soak tests

LLM evaluator calls add cost and latency. Use `--skip-llm-judge` during soak tests:

```bash
voicecheck run scenario.yaml --duration 30m --skip-llm-judge
```

### 4. Monitor latency trends

The P95 latency in the summary reveals tail latency issues. If P95 is much higher than the average, your agent has inconsistent response times under load.

### 5. Test concurrent load

Use `--parallel` with multiple scenarios to simulate multiple users:

```bash
voicecheck run scenarios/ --duration 20m --parallel 5
```

### 6. Use multiple persona variations

Create a directory of persona YAML files with different personalities and topics to test diverse conversation patterns:

```
soak_personas/
  curious_kid.yaml
  impatient_adult.yaml
  confused_elderly.yaml
  talkative_teen.yaml
```

```bash
voicecheck run soak_personas/ --duration 30m --parallel 2
```

### 7. Set latency thresholds appropriately

For soak tests, set slightly relaxed latency thresholds to account for variance. If your normal test uses 3000ms, consider 4000-5000ms for soak:

```yaml
per_turn_expect:
  - type: latency
    max_first_byte_ms: 5000  # more forgiving for sustained load
```

### 8. Review results in the dashboard

After a soak test, use the dashboard to visualize latency trends over time:

```bash
voicecheck dashboard --open
```

Or launch the live dashboard:

```bash
voicecheck serve
```

The dashboard shows pass/fail trends, latency charts, and per-scenario breakdowns from all stored runs.
