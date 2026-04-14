# Evaluators

Evaluators are the assertion layer of VoiceCheck. After each conversation turn, evaluators score the agent's response and determine whether it passes. VoiceCheck ships with four built-in evaluators and supports custom evaluators via a simple plugin interface.

## How Evaluators Work

Every evaluator receives an `EvalContext` containing:

- `user_text` -- what the simulated user said
- `agent_text` -- the transcribed agent response
- `agent_audio` -- raw audio frames from the agent
- `metrics` -- timing data (first_byte_ms, total_ms, TTS/STT durations, etc.)
- `turn_index` -- which turn this is (0-based)
- `scenario_name` -- name of the running scenario
- `conversation` -- full conversation history up to this point

Every evaluator returns an `EvalResult`:

```python
@dataclass
class EvalResult:
    evaluator_type: str   # e.g. "latency", "keyword"
    passed: bool          # did this evaluator pass?
    score: float          # 0.0 to 1.0
    reason: str           # human-readable explanation
    details: dict         # evaluator-specific extra data
```

A turn passes only if **all** of its evaluators pass. A scenario passes only if **all** turns pass (and conversation_eval, if configured).

---

## Built-in Evaluators

### latency

Checks that the agent's response time is within acceptable bounds. Measures two timestamps:

- **first_byte_ms**: Time from when VoiceCheck finishes sending user audio to when the first sustained agent speech is detected. This is the "time to first word" -- the most important latency metric for voice agents.
- **total_ms**: Time from end of user audio to the last agent audio frame. This captures the full response duration.

Speech detection uses RMS energy analysis with a confirmation window of 3 consecutive non-silent frames (~60ms) to filter codec warmup noise and produce accurate measurements.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_first_byte_ms` | float | `0` | Maximum allowed first-byte latency in milliseconds. `0` means no limit. |
| `max_total_ms` | float | `0` | Maximum allowed total response time in milliseconds. `0` means no limit. |

#### YAML example

```yaml
expect:
  - type: latency
    max_first_byte_ms: 3000   # agent must start speaking within 3 seconds
    max_total_ms: 15000       # entire response must complete within 15 seconds
```

#### Scoring

- **score**: `1.0` if passed, `0.0` if failed.
- **details** include: `first_byte_ms`, `total_ms`, `max_first_byte_ms`, `max_total_ms`, `send_duration_ms`, `tts_duration_ms`, `stt_duration_ms`, `agent_audio_duration_ms`, `agent_audio_frames`.

#### Pass/fail logic

The evaluator fails if either measured value exceeds its corresponding threshold. If a threshold is set to `0`, that check is skipped. You can use just one threshold:

```yaml
# Only check first-byte latency, ignore total time
- type: latency
  max_first_byte_ms: 2000
```

---

### keyword

Checks that the agent's response contains required keywords and does not contain forbidden keywords. Uses regex-escaped substring matching.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `must_contain` | list of strings | `[]` | Keywords that must appear in the response |
| `must_not_contain` | list of strings | `[]` | Keywords that must not appear in the response |
| `case_sensitive` | bool | `false` | Whether matching is case-sensitive |

#### YAML example

```yaml
expect:
  - type: keyword
    must_contain: ["hello", "welcome"]
    must_not_contain: ["error", "undefined", "null", "exception"]

  # Case-sensitive matching
  - type: keyword
    must_contain: ["OpenAI"]
    case_sensitive: true
```

#### Scoring

- **score**: Proportion of checks that passed. For example, if 3 out of 4 keyword checks pass, score is `0.75`.
- **passed**: `true` only if score is `1.0` (all checks pass).
- **details** include: `missing` (list of required keywords not found), `found_forbidden` (list of forbidden keywords found), `agent_text_preview` (first 200 characters of agent response).

#### How matching works

Keywords are matched as substrings using `re.search(re.escape(pattern), text)`. This means:

- `"hello"` matches "Hello there!" (case-insensitive by default)
- `"error"` matches "There was an error" and "No errors found"
- Special regex characters in keywords are escaped, so `"price: $10"` matches literally

---

### turn_count

Validates the length of the agent's response by counting words. Despite the name, this evaluator checks word count within a single turn, not the number of turns in the conversation.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `min_words` | int | `1` | Minimum number of words required |
| `max_words` | int | `0` | Maximum number of words allowed. `0` means no upper limit. |

#### YAML example

```yaml
expect:
  # Agent must say something (at least 1 word, the default)
  - type: turn_count

  # Agent must give a substantive response
  - type: turn_count
    min_words: 10
    max_words: 100

  # Agent must be concise (for voice, shorter is usually better)
  - type: turn_count
    max_words: 50
```

#### Scoring

- **score**: `1.0` if passed, `0.0` if failed.
- **details** include: `word_count`.

#### Word counting

Words are counted by splitting the agent text on whitespace: `len(text.split())`. An empty or missing agent response has 0 words and will fail if `min_words >= 1` (the default).

---

### llm_judge

Uses an LLM (OpenAI or Anthropic) to evaluate the quality of the agent's response against freeform criteria. This is the most flexible evaluator -- you describe what "good" looks like in natural language.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `criteria` | string | `""` | Natural language description of what to evaluate |
| `min_score` | float | `0.7` | Minimum score (0.0 to 1.0) to pass |
| `provider` | string | `"openai"` | LLM provider: `"openai"` or `"anthropic"` |
| `model` | string | *(provider default)* | Model to use. Defaults: `gpt-4o-mini` (OpenAI), `claude-sonnet-4-5-20250929` (Anthropic) |

#### YAML example

```yaml
expect:
  - type: llm_judge
    criteria: "Agent responds warmly and uses the child's name"
    min_score: 0.8

  - type: llm_judge
    criteria: >
      Agent provides accurate information about the topic.
      The response is age-appropriate and engaging.
      No hallucinated facts or made-up information.
    min_score: 0.7
    provider: anthropic
    model: claude-sonnet-4-5-20250929
```

#### How it works

1. The evaluator builds a prompt containing:
   - The full conversation history up to this point
   - The current user message and agent response
   - Your evaluation criteria
   - The minimum passing score
2. The LLM scores the response from 0.0 to 1.0 and returns a JSON object with `score`, `passed`, and `reason`.
3. The evaluator marks the turn as passed if `score >= min_score`.

#### Scoring

- **score**: The LLM's score (0.0 to 1.0).
- **passed**: `true` if `score >= min_score`.
- **reason**: The LLM's explanation of the score.
- **details** include: `criteria`, `min_score`, `model`, `provider`.

#### Cost considerations

Each `llm_judge` evaluation makes one LLM API call. For cost optimization:

- Use `gpt-4o-mini` (default) -- fast and cheap
- Use `--skip-llm-judge` to skip all LLM evaluators during development
- Place `llm_judge` evaluators only on turns that need semantic evaluation
- Use keyword and turn_count evaluators for structural checks that do not need an LLM

#### Skipping LLM judge

```bash
# Skip all llm_judge evaluators (and conversation_eval)
voicecheck run scenario.yaml --skip-llm-judge
```

---

## Evaluator Placement

### Scripted mode: per-turn evaluators

In scripted mode, each turn has its own `expect` list:

```yaml
turns:
  - user: "Hello"
    expect:
      - type: latency
        max_first_byte_ms: 3000
      - type: turn_count
        min_words: 3
```

### Questions, persona, and guided modes: shared evaluators

Use `per_turn_expect` for evaluators that apply to every turn:

```yaml
per_turn_expect:
  - type: latency
    max_first_byte_ms: 5000
  - type: turn_count
    min_words: 3
```

### Guided mode: per-step + shared

In guided mode, each flow step can have its own `expect` list in addition to the global `per_turn_expect`. Both sets run on each step's agent response:

```yaml
per_turn_expect:
  - type: latency
    max_first_byte_ms: 4000

flow:
  - name: "greeting"
    goal: "Say hello"
    expect:
      - type: turn_count
        min_words: 5
      # latency evaluator from per_turn_expect also runs here
```

---

## Creating Custom Evaluators

You can create custom evaluators by subclassing the `Evaluator` ABC and registering them.

### Step 1: Create the evaluator class

```python
# my_evaluators.py
from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult


class SentimentEvaluator(Evaluator):
    """Check that the agent's response has a positive sentiment."""

    def __init__(self, min_positivity: float = 0.5) -> None:
        self.min_positivity = min_positivity

    async def evaluate(self, context: EvalContext) -> EvalResult:
        # Your custom evaluation logic here.
        # This example uses a simple heuristic; you could use an NLP
        # library, an LLM call, or any other method.
        positive_words = {"great", "happy", "love", "wonderful", "awesome",
                          "fantastic", "excellent", "amazing", "fun", "enjoy"}
        negative_words = {"bad", "terrible", "awful", "hate", "horrible",
                          "wrong", "error", "fail", "broken"}

        words = set(context.agent_text.lower().split())
        pos_count = len(words & positive_words)
        neg_count = len(words & negative_words)
        total = pos_count + neg_count

        if total == 0:
            score = 0.5  # neutral
        else:
            score = pos_count / total

        passed = score >= self.min_positivity

        return EvalResult(
            evaluator_type="sentiment",
            passed=passed,
            score=score,
            reason=f"Sentiment score: {score:.2f} (min: {self.min_positivity})",
            details={
                "positive_count": pos_count,
                "negative_count": neg_count,
                "min_positivity": self.min_positivity,
            },
        )


# Register the evaluator so it can be referenced in YAML
register_evaluator("sentiment", SentimentEvaluator)
```

### Step 2: Import the evaluator before running

Make sure your module is imported before VoiceCheck runs. You can do this by:

**Option A: Import in your test script**

```python
import my_evaluators  # registers the evaluator

from voicecheck.core.scenario import ScenarioRunner

runner = ScenarioRunner.from_yaml("scenario.yaml")
report = await runner.run()
```

**Option B: Import via a conftest.py for pytest**

```python
# conftest.py
import my_evaluators  # noqa: F401
```

### Step 3: Use in YAML

```yaml
expect:
  - type: sentiment
    min_positivity: 0.6
```

Any additional parameters you pass in the YAML `expect` block (beyond `type`) are forwarded as keyword arguments to your evaluator's `__init__` method.

### Evaluator contract

Your custom evaluator must:

1. Subclass `voicecheck.core.evaluator.Evaluator`
2. Implement `async def evaluate(self, context: EvalContext) -> EvalResult`
3. Be registered with `register_evaluator("name", YourClass)`

The `EvalContext` gives you access to everything about the current turn. The `EvalResult` you return must include at minimum `evaluator_type`, `passed`, and `score`. The `reason` and `details` fields are optional but strongly recommended for debugging.

### Access to audio data

Custom evaluators have access to the raw agent audio via `context.agent_audio`, a list of `AudioFrame` objects. Each frame contains:

- `data` (bytes): raw 16-bit PCM audio
- `sample_rate` (int): sample rate in Hz
- `num_channels` (int): number of audio channels
- `samples_per_channel` (int): number of samples
- `duration_s` (float): duration in seconds

This enables evaluators that analyze audio characteristics (volume, speech rate, pauses, etc.) directly.
