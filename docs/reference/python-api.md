# Python API Reference

VoiceCheck can be used as a Python library for programmatic testing, custom integrations, and building tools on top of the test framework. This reference covers the public API, core types, and extension points.

## Quick Start

```python
import asyncio
from voicecheck.core.scenario import ScenarioRunner

async def main():
    runner = ScenarioRunner.from_yaml("scenario.yaml")
    report = await runner.run()

    if report.passed:
        print(f"All {report.total_turns} turns passed!")
    else:
        print(f"Failed: {report.passed_turns}/{report.total_turns} turns passed")

    for turn in report.turns:
        print(f"Turn {turn.turn_index + 1}: {turn.agent_text[:80]}")
        print(f"  Latency: {turn.metrics.first_byte_ms:.0f}ms")
        for result in turn.eval_results:
            status = "PASS" if result.passed else "FAIL"
            print(f"  [{status}] {result.evaluator_type}: {result.reason}")

asyncio.run(main())
```

## Public Imports

All public symbols are available from the top-level `voicecheck` package:

```python
from voicecheck import (
    # Core types
    AudioFrame,
    EvalContext,
    EvalResult,
    TranscriptSegment,
    TransportMetrics,
    TurnResult,

    # Abstract base classes
    Transport,
    Evaluator,

    # Registry functions
    register_transport,
    get_transport,
    register_evaluator,
    get_evaluator,

    # Scenario
    Scenario,
    ScenarioReport,
    ScenarioRunner,
    load_scenario,
    validate_scenario,

    # Storage
    ResultStore,
)
```

---

## ScenarioRunner

The main orchestrator class. Handles the full test lifecycle: TTS, transport, STT, and evaluation.

### Constructor

```python
ScenarioRunner(scenario: Scenario, skip_llm_judge: bool = False)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `scenario` | `Scenario` | **(required)** | Parsed scenario object. |
| `skip_llm_judge` | `bool` | `False` | Skip all `llm_judge` evaluators and `conversation_eval`. |

### Class methods

#### from_yaml

```python
ScenarioRunner.from_yaml(path: str | Path, **kwargs) -> ScenarioRunner
```

Create a runner from a YAML scenario file. Extra keyword arguments are passed to the constructor.

```python
runner = ScenarioRunner.from_yaml("scenario.yaml", skip_llm_judge=True)
```

### Instance methods

#### run

```python
async def run(self) -> ScenarioReport
```

Execute the full scenario and return a report. Automatically selects the appropriate mode (scripted, questions, persona, or guided) based on the scenario configuration.

Raises `RuntimeError` if preflight checks fail (missing API keys, invalid transport config).

```python
report = await runner.run()
```

---

## Scenario

Pydantic model representing a parsed YAML scenario. Supports all four testing modes.

### Loading from YAML

```python
from voicecheck.core.scenario import load_scenario

scenario = load_scenario("path/to/scenario.yaml")
print(scenario.name)
print(scenario.transport.type)
print(scenario.is_persona_mode)
```

### Constructing programmatically

```python
from voicecheck.core.scenario import (
    Scenario,
    TransportConfig,
    AudioConfig,
    TurnConfig,
    ExpectConfig,
    SettingsConfig,
)

scenario = Scenario(
    name="Programmatic test",
    transport=TransportConfig(
        type="livekit",
        mode="direct",
        config={
            "url": "wss://my-server.example.com",
            "api_key": "my-key",
            "api_secret": "my-secret",
            "agent_name": "my-agent",
        },
    ),
    audio=AudioConfig(
        tts_provider="edge",
        stt_provider="whisper",
    ),
    turns=[
        TurnConfig(
            user="Hello!",
            expect=[
                ExpectConfig(type="latency", max_first_byte_ms=3000),
                ExpectConfig(type="turn_count", min_words=3),
            ],
        ),
    ],
    settings=SettingsConfig(
        turn_timeout=20.0,
        silence_threshold=2.0,
    ),
)

runner = ScenarioRunner(scenario)
report = await runner.run()
```

### Properties

| Property | Type | Description |
|---|---|---|
| `is_questions_mode` | `bool` | True if `questions` is non-empty. |
| `is_persona_mode` | `bool` | True if `persona` is set and `flow`/`questions` are empty. |
| `is_guided_mode` | `bool` | True if `persona` is set and `flow` is non-empty. |

### Validation

```python
from voicecheck.core.scenario import validate_scenario

errors = validate_scenario("path/to/scenario.yaml")
if errors:
    for e in errors:
        print(f"Error: {e}")
else:
    print("Valid!")
```

Returns a list of error strings (empty if valid).

---

## ScenarioReport

Dataclass containing the results of a completed scenario run.

### Fields

| Field | Type | Description |
|---|---|---|
| `scenario_name` | `str` | Name of the scenario. |
| `turns` | `list[TurnResult]` | Results for each turn. |
| `conversation_eval` | `dict | None` | Post-conversation evaluation results. |

### Properties

| Property | Type | Description |
|---|---|---|
| `passed` | `bool` | True if all turns passed and conversation_eval passed (if present). |
| `total_turns` | `int` | Total number of turns. |
| `passed_turns` | `int` | Number of turns where all evaluators passed. |

### Example

```python
report = await runner.run()

print(f"Passed: {report.passed}")
print(f"Turns: {report.passed_turns}/{report.total_turns}")

if report.conversation_eval:
    score = report.conversation_eval.get("overall_score", 0)
    print(f"Conversation score: {score:.2f}")
```

---

## TurnResult

Dataclass containing the result of a single conversation turn.

### Fields

| Field | Type | Description |
|---|---|---|
| `turn_index` | `int` | Zero-based index of this turn. |
| `user_text` | `str` | What the simulated user said. |
| `agent_text` | `str` | Transcribed agent response. |
| `user_audio` | `list[AudioFrame]` | Synthesized user audio frames. |
| `agent_audio` | `list[AudioFrame]` | Captured agent audio frames. |
| `metrics` | `TransportMetrics` | Timing metrics for this turn. |
| `eval_results` | `list[EvalResult]` | Results from all evaluators. |

### Properties

| Property | Type | Description |
|---|---|---|
| `passed` | `bool` | True if all evaluators passed. |

---

## TransportMetrics

Dataclass containing timing metrics collected during a transport session.

### Fields

| Field | Type | Description |
|---|---|---|
| `send_start_ts` | `float` | Timestamp when audio sending started. |
| `send_end_ts` | `float` | Timestamp when audio sending finished. |
| `first_byte_ts` | `float` | Timestamp of first sustained agent speech. |
| `last_byte_ts` | `float` | Timestamp of last agent audio frame. |
| `tts_duration_ms` | `float` | Time spent on TTS synthesis. |
| `stt_duration_ms` | `float` | Time spent on STT transcription. |
| `user_audio_duration_ms` | `float` | Duration of user audio. |
| `agent_audio_duration_ms` | `float` | Duration of agent audio. |
| `agent_audio_frames` | `int` | Number of agent audio frames captured. |

### Properties

| Property | Type | Description |
|---|---|---|
| `first_byte_ms` | `float` | Time from end of user speech to first agent audio (ms). |
| `total_ms` | `float` | Time from end of user speech to last agent audio (ms). |
| `send_duration_ms` | `float` | Time spent sending user audio frames (ms). |

---

## EvalResult

Dataclass containing the result of a single evaluator.

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `evaluator_type` | `str` | | Name of the evaluator (e.g., `"latency"`). |
| `passed` | `bool` | | Whether this evaluator passed. |
| `score` | `float` | `1.0` | Score from 0.0 to 1.0. |
| `reason` | `str` | `""` | Human-readable explanation. |
| `details` | `dict` | `{}` | Evaluator-specific data. |

---

## EvalContext

Dataclass passed to evaluators with full context about the current turn.

### Fields

| Field | Type | Description |
|---|---|---|
| `user_text` | `str` | What the user said. |
| `agent_text` | `str` | Transcribed agent response. |
| `agent_audio` | `list[AudioFrame]` | Raw agent audio frames. |
| `metrics` | `TransportMetrics` | Timing metrics. |
| `turn_index` | `int` | Zero-based turn index. |
| `scenario_name` | `str` | Scenario name. |
| `conversation` | `list[dict]` | Full conversation history (list of `{"role": "user"|"agent", "text": "..."}` dicts). |

---

## AudioFrame

Dataclass representing a single audio frame (PCM data).

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `data` | `bytes` | | Raw 16-bit PCM audio data. |
| `sample_rate` | `int` | `16000` | Sample rate in Hz. |
| `num_channels` | `int` | `1` | Number of audio channels. |
| `samples_per_channel` | `int` | `0` | Number of samples (auto-calculated from `data` if 0). |

### Properties

| Property | Type | Description |
|---|---|---|
| `duration_s` | `float` | Duration of this frame in seconds. |

---

## Transport ABC

Abstract base class for transport implementations. Subclass this to add support for a new voice agent platform.

### Abstract methods

```python
class Transport(ABC):
    async def connect(self, config: dict) -> None: ...
    async def send_audio(self, frames: list[AudioFrame]) -> None: ...
    async def receive_audio(self, timeout: float = 10.0, silence_threshold: float = 1.5) -> list[AudioFrame]: ...
    async def disconnect(self) -> None: ...

    @property
    def metrics(self) -> TransportMetrics: ...
```

### Optional methods

```python
    def reset_metrics(self) -> None: ...
    def validate_config(self, config: dict) -> list[str]: ...
```

### Creating a custom transport

```python
from voicecheck.core.transport import Transport, register_transport
from voicecheck.core.types import AudioFrame, TransportMetrics


class MyCustomTransport(Transport):
    """Custom transport for my voice platform."""

    def __init__(self) -> None:
        self._metrics = TransportMetrics()

    async def connect(self, config: dict) -> None:
        """Connect to the voice agent.

        Args:
            config: Contains keys from the YAML transport.config section,
                    plus 'mode', 'sample_rate', and 'num_channels'.
        """
        api_key = config["api_key"]
        endpoint = config["endpoint"]
        # ... establish connection

    async def send_audio(self, frames: list[AudioFrame]) -> None:
        """Send audio frames to the agent."""
        import time
        self._metrics.send_start_ts = time.monotonic()
        for frame in frames:
            # ... send frame.data to the platform
            pass
        self._metrics.send_end_ts = time.monotonic()

    async def receive_audio(
        self, timeout: float = 10.0, silence_threshold: float = 1.5
    ) -> list[AudioFrame]:
        """Capture agent audio.

        Must set self._metrics.first_byte_ts when first speech is detected
        and self._metrics.last_byte_ts on each received frame.
        """
        captured: list[AudioFrame] = []
        # ... capture audio from platform, detect silence, build frames
        return captured

    async def disconnect(self) -> None:
        """Clean up connection."""
        pass

    @property
    def metrics(self) -> TransportMetrics:
        return self._metrics

    def reset_metrics(self) -> None:
        self._metrics = TransportMetrics()

    def validate_config(self, config: dict) -> list[str]:
        """Return a list of config errors (empty if valid)."""
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required")
        if not config.get("endpoint"):
            errors.append("'endpoint' is required")
        return errors


# Register so YAML scenarios can reference it as type: my_platform
register_transport("my_platform", MyCustomTransport)
```

Usage in YAML:

```yaml
transport:
  type: my_platform
  config:
    api_key: "${MY_API_KEY}"
    endpoint: "wss://my-platform.example.com/ws"
```

---

## Evaluator ABC

Abstract base class for evaluator implementations.

### Abstract methods

```python
class Evaluator(ABC):
    async def evaluate(self, context: EvalContext) -> EvalResult: ...
```

### Creating a custom evaluator

```python
from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult


class ResponseTimeEvaluator(Evaluator):
    """Check that agent responds within a time budget proportional to input length."""

    def __init__(self, ms_per_word: float = 500) -> None:
        self.ms_per_word = ms_per_word

    async def evaluate(self, context: EvalContext) -> EvalResult:
        user_words = len(context.user_text.split())
        budget_ms = user_words * self.ms_per_word
        actual_ms = context.metrics.first_byte_ms

        passed = actual_ms <= budget_ms

        return EvalResult(
            evaluator_type="response_time",
            passed=passed,
            score=min(1.0, budget_ms / actual_ms) if actual_ms > 0 else 1.0,
            reason=(
                f"Response in {actual_ms:.0f}ms "
                f"(budget: {budget_ms:.0f}ms for {user_words} words)"
            ),
            details={
                "budget_ms": budget_ms,
                "actual_ms": actual_ms,
                "user_words": user_words,
            },
        )


register_evaluator("response_time", ResponseTimeEvaluator)
```

Usage in YAML:

```yaml
expect:
  - type: response_time
    ms_per_word: 300
```

---

## ResultStore

SQLite-backed storage for test results. Used by the CLI and dashboard.

### Constructor

```python
ResultStore(db_path: str | Path | None = None)
```

Default path: `~/.voicecheck/results.db`.

### Methods

```python
# Save a scenario report, returns the run ID (UUID string)
run_id = store.save_report(report, transport_type="livekit", tags=["ci"])

# List recent runs
runs = store.list_runs(scenario_name="my-test", limit=50, offset=0)
# Returns: list[dict] with keys: id, scenario_name, passed, total_turns,
#          passed_turns, avg_first_byte_ms, avg_total_ms, transport_type,
#          tags, conversation_eval, created_at

# Get full run details with turns
run = store.get_run(run_id)
# Returns: dict with all run fields plus "turns" list

# Get per-scenario summary stats
scenarios = store.get_scenarios()
# Returns: list[dict] with: scenario_name, run_count, pass_count,
#          avg_latency, last_run

# Get latency percentiles
percentiles = store.get_scenario_percentiles("my-test", "first_byte_ms")
# Returns: {"p50": 1500.0, "p95": 3000.0, "p99": 4500.0}

# Get scenarios with full percentile stats
all_stats = store.get_all_scenario_stats()
# Returns: list[dict] with p50/p95/p99 for both first_byte and total

# Count runs
count = store.count_runs(scenario_name="my-test")

# Delete a run
deleted = store.delete_run(run_id)  # returns bool

# Get scenario history for trend charts
history = store.get_scenario_history("my-test", limit=100)

# Close the database connection
store.close()
```

### Example: custom reporting

```python
from voicecheck import ResultStore

store = ResultStore()

# Get all scenarios and their stats
for s in store.get_all_scenario_stats():
    print(
        f"{s['scenario_name']}: "
        f"{s['pass_count']}/{s['run_count']} passed, "
        f"P95 latency: {s['p95_first_byte_ms']:.0f}ms"
    )

# Get the last 10 runs for a scenario
for run in store.list_runs(scenario_name="greeting-test", limit=10):
    status = "PASS" if run["passed"] else "FAIL"
    print(f"  {run['id'][:8]} [{status}] {run['created_at'][:19]}")

store.close()
```

---

## Common Patterns

### Load and run a scenario with custom overrides

```python
from voicecheck.core.scenario import load_scenario, ScenarioRunner

scenario = load_scenario("scenario.yaml")

# Override questions from code
scenario.questions = ["Custom question 1", "Custom question 2"]

runner = ScenarioRunner(scenario, skip_llm_judge=True)
report = await runner.run()
```

### Run multiple scenarios and collect results

```python
import asyncio
from pathlib import Path
from voicecheck.core.scenario import ScenarioRunner

async def run_all():
    files = sorted(Path("scenarios").glob("*.yaml"))
    results = {}

    for f in files:
        try:
            runner = ScenarioRunner.from_yaml(f)
            report = await runner.run()
            results[f.name] = {
                "passed": report.passed,
                "turns": f"{report.passed_turns}/{report.total_turns}",
            }
        except Exception as e:
            results[f.name] = {"passed": False, "error": str(e)}

    return results

results = asyncio.run(run_all())
for name, r in results.items():
    print(f"{name}: {'PASS' if r['passed'] else 'FAIL'}")
```

### Ensure registrations in library usage

When using VoiceCheck as a library (outside the CLI), you must ensure transports and evaluators are registered by importing their modules:

```python
# Import transports (each is optional)
try:
    import voicecheck.transports.livekit
except ImportError:
    pass

try:
    import voicecheck.transports.daily
except ImportError:
    pass

# Import evaluators (core ones are always available)
import voicecheck.evaluators.latency
import voicecheck.evaluators.keyword
import voicecheck.evaluators.turn_count

try:
    import voicecheck.evaluators.llm_judge
except ImportError:
    pass
```

The CLI does this automatically via `_ensure_registrations()`. When using the library directly, you need to handle it yourself.

### Save and retrieve results programmatically

```python
from voicecheck import ResultStore, ScenarioRunner

async def test_and_store():
    runner = ScenarioRunner.from_yaml("scenario.yaml")
    report = await runner.run()

    store = ResultStore()
    run_id = store.save_report(report, transport_type="livekit", tags=["api", "v2"])

    # Later: retrieve
    run_data = store.get_run(run_id)
    for turn in run_data["turns"]:
        print(f"Turn {turn['turn_index']}: {turn['agent_text'][:60]}")

    store.close()
```

---

## TranscriptSegment

Dataclass representing a transcribed segment of audio (returned by STT providers).

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `text` | `str` | | Transcribed text. |
| `start_time` | `float` | `0.0` | Start time in seconds. |
| `end_time` | `float` | `0.0` | End time in seconds. |
| `confidence` | `float` | `1.0` | Transcription confidence (0.0 to 1.0). |
