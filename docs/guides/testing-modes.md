# Testing Modes

VoiceCheck supports four distinct testing modes, each suited to different testing needs. This guide covers all four modes in depth with complete YAML examples.

## Overview

| Mode | YAML key | User messages | Evaluators | Best for |
|---|---|---|---|---|
| **Scripted** | `turns` | Explicit per-turn text | Per-turn | Deterministic regression tests |
| **Questions** | `questions` | Fixed list of questions | Shared (`per_turn_expect`) | Quick smoke tests, reproducible Q&A |
| **Persona** | `persona` | LLM-generated dynamically | Shared (`per_turn_expect`) | Realistic conversation coverage |
| **Guided flow** | `persona` + `flow` | LLM-generated per goal | Per-step + shared | Structured user journeys |

The mode is determined automatically based on which top-level keys are present in your YAML file:

- `turns` present and no `persona` or `questions` --> **Scripted**
- `questions` present --> **Questions**
- `persona` present, no `flow` or `questions` --> **Persona**
- `persona` + `flow` present --> **Guided flow**

---

## Scripted Mode

Scripted mode gives you full control. You define each conversation turn with explicit user text and per-turn evaluators.

### When to use it

- Regression testing specific interactions
- Testing known edge cases (empty input, long input, specific topics)
- When you need fully deterministic, reproducible tests
- When you do not want to pay for LLM API calls (no OPENAI_API_KEY needed unless using `llm_judge`)

### How it works

1. For each turn in the `turns` list, VoiceCheck synthesizes the `user` text to audio via TTS.
2. The audio is sent to the agent through the configured transport.
3. The agent's audio response is captured and transcribed via STT.
4. Each evaluator in that turn's `expect` list is run against the agent's response.
5. The turn passes only if all evaluators pass.

### Complete example

```yaml
name: "Greeting and FAQ test"
description: "Scripted turns testing basic agent behavior"

transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "${VOICECHECK_AGENT_NAME}"

audio:
  tts_provider: edge
  stt_provider: whisper
  sample_rate: 16000

turns:
  - user: "Hello, who are you?"
    expect:
      - type: latency
        max_first_byte_ms: 3000
      - type: turn_count
        min_words: 3
      - type: keyword
        must_not_contain: ["error", "exception", "undefined"]

  - user: "What can you help me with?"
    expect:
      - type: latency
        max_first_byte_ms: 3000
      - type: turn_count
        min_words: 5
        max_words: 200
      - type: llm_judge
        criteria: "Agent clearly explains what it can help with in a friendly tone"
        min_score: 0.7

  - user: "Thank you, goodbye!"
    expect:
      - type: turn_count
        min_words: 2
      - type: keyword
        must_contain: ["bye"]

settings:
  turn_timeout: 20.0
  silence_threshold: 2.0
```

### Key points

- Each turn has its own `expect` list -- evaluators are specific to that turn.
- Turns execute sequentially within a single transport connection (the agent maintains conversation context).
- If a turn's audio exchange fails (timeout, transport error), the agent text is set to empty and evaluators run against that empty string.

---

## Questions Mode

Questions mode is a streamlined variant of scripted mode. You provide a flat list of user messages and a shared set of evaluators that apply to every turn.

### When to use it

- Quick smoke tests where every question gets the same checks
- Testing a batch of questions without repeating evaluator config for each one
- Reproducible tests with deterministic inputs
- Combined with `conversation_eval` for holistic conversation scoring

### How it works

1. Each string in the `questions` list becomes a user turn.
2. The same `per_turn_expect` evaluators run on every agent response.
3. After all questions are sent, an optional `conversation_eval` scores the full conversation with an LLM.

### Complete example

```yaml
name: "Customer service smoke test"
description: "Fixed questions with shared evaluators"

transport:
  type: vapi
  mode: web_call
  config:
    api_key: "${VAPI_API_KEY}"
    assistant_id: "${VAPI_ASSISTANT_ID}"

audio:
  tts_provider: edge
  stt_provider: whisper
  sample_rate: 16000

questions:
  - "Hi there, I need some help."
  - "What are your business hours?"
  - "How do I return an item?"
  - "Thanks for your help!"

per_turn_expect:
  - type: latency
    max_first_byte_ms: 5000
  - type: turn_count
    min_words: 3

conversation_eval:
  criteria:
    - "Agent was helpful and answered questions accurately"
    - "Agent maintained a professional and friendly tone"
    - "Responses were concise and suitable for voice"
  min_score: 0.7
  model: gpt-4o-mini

settings:
  turn_timeout: 15.0
  silence_threshold: 1.5
```

### CLI override

You can override questions from the command line, ignoring whatever is in the YAML:

```bash
voicecheck run scenario.yaml -q "Hello!" -q "What is your return policy?"
```

---

## Persona Mode

Persona mode uses an LLM to simulate a realistic user. Instead of scripted messages, VoiceCheck generates dynamic, contextually appropriate user messages based on a persona profile. This tests a much wider range of agent behaviors than scripted turns can.

### When to use it

- Exploratory testing of conversation flow
- Testing how the agent handles unexpected or off-topic responses
- Verifying agent persona consistency across varied interactions
- Getting closer to real-world usage patterns

### How it works

1. VoiceCheck builds a system prompt from the persona configuration (name, age, personality, goals, etc.).
2. The persona LLM generates an opening message (or uses the `opening` field if provided).
3. After each agent response, the persona LLM generates the next user message based on the full conversation history.
4. This continues for `max_turns` rounds.
5. `per_turn_expect` evaluators run on every agent response.
6. After the conversation ends, `conversation_eval` (if configured) evaluates the entire transcript.

### Persona configuration options

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | `"Test User"` | The persona's name |
| `description` | string | `""` | Background info about the persona |
| `age` | integer or null | `null` | Age (affects communication style in the LLM prompt) |
| `personality` | string | `"friendly and curious"` | Personality traits |
| `communication_style` | string | `"casual, short sentences"` | How the persona speaks |
| `goals` | list of strings | `[]` | What the persona wants to accomplish |
| `topics` | list of strings | `[]` | Subjects the persona is interested in |
| `instructions` | string | `""` | Freeform instructions for the persona LLM |
| `model` | string | `"gpt-4o-mini"` | OpenAI model for generating user messages |
| `max_turns` | integer | `5` | Number of conversation turns |
| `opening` | string | `""` | Fixed opening line (bypasses LLM generation for the first turn) |

### Complete example

```yaml
name: "Curious kid conversation"
description: "A 7-year-old chats with the agent"

transport:
  type: livekit
  mode: direct
  config:
    url: "${LIVEKIT_URL}"
    api_key: "${LIVEKIT_API_KEY}"
    api_secret: "${LIVEKIT_API_SECRET}"
    agent_name: "${VOICECHECK_AGENT_NAME}"

audio:
  tts_provider: edge
  stt_provider: whisper
  sample_rate: 16000

persona:
  name: "Emma"
  age: 7
  personality: "curious, excitable, loves space and animals, asks lots of questions"
  communication_style: >
    Short sentences. Uses words like 'cool' and 'wow' a lot.
    Sometimes goes off-topic with random kid tangents.
    Asks 'why?' follow-ups. Giggles at silly things.
  goals:
    - "Learn something fun about the stars or planets"
    - "Ask the agent to tell a story or adventure"
    - "See if the agent remembers her name"
  topics:
    - "stars and planets"
    - "space adventures"
    - "what it's like on the moon"
  instructions: >
    Start casual -- say hi and see how the agent introduces itself.
    If the agent says something about space, get excited and ask more.
    At some point, ask for a short adventure story. React to whatever
    the agent says like a real 7-year-old would.
  model: gpt-4o-mini
  max_turns: 5
  opening: "Hi! What's your name?"

per_turn_expect:
  - type: latency
    max_first_byte_ms: 4000
  - type: turn_count
    min_words: 3

conversation_eval:
  criteria:
    - "Agent introduced itself and stayed in character throughout"
    - "Agent kept responses short, fun, and age-appropriate for a 7-year-old"
    - "Agent was engaging -- asked questions back or offered fun facts"
    - "Agent responded naturally to topic changes and follow-up questions"
  min_score: 0.7
  model: gpt-4o-mini

settings:
  turn_timeout: 20.0
  silence_threshold: 2.0
```

### Tips for persona mode

- Use `opening` to ensure a consistent first message across runs.
- Keep `max_turns` between 3 and 8 for practical test durations.
- The `goals` and `instructions` fields are the most powerful levers for steering conversation direction.
- Persona mode requires `OPENAI_API_KEY` for message generation.

### Switching to persona mode from the CLI

If a scenario has both `questions` and `persona` sections, questions mode takes precedence by default. Use `--auto` to switch to persona mode:

```bash
voicecheck run scenario.yaml --auto
```

---

## Guided Flow Mode

Guided flow combines the dynamic nature of persona mode with the structured assertions of scripted mode. You define a sequence of steps, each with a goal for the persona and evaluators for the agent's response. The persona LLM generates contextually appropriate messages aimed at each step's goal.

### When to use it

- Testing structured user journeys (onboarding, checkout, support ticket flow)
- Verifying that the agent handles a specific sequence of topics correctly
- When you want dynamic, realistic messages but need per-step quality gates
- Testing narrative arcs (introduction, exploration, conclusion)

### How it works

1. VoiceCheck initializes the persona LLM with the persona configuration.
2. For each step in the `flow` list, the persona LLM generates a user message steered toward the step's `goal`.
3. The message is synthesized, sent, and the agent's response is captured.
4. The step's `expect` evaluators are combined with any global `per_turn_expect` evaluators and all run on the response.
5. After all steps, `conversation_eval` (if configured) evaluates the full transcript.

### Flow step configuration

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | `""` | Human-readable label for the step (used in logs) |
| `goal` | string | **(required)** | What the persona should try to accomplish in this turn |
| `expect` | list of ExpectConfig | `[]` | Step-specific evaluators |

### Complete example

```yaml
name: "Support ticket guided flow"
description: "Walk through a support ticket creation flow"

transport:
  type: retell
  mode: web_call
  config:
    api_key: "${RETELL_API_KEY}"
    agent_id: "${RETELL_AGENT_ID}"

audio:
  tts_provider: edge
  stt_provider: whisper
  sample_rate: 16000

persona:
  name: "Alex"
  personality: "polite but slightly frustrated, has a real problem to solve"
  communication_style: "clear, direct sentences"
  model: gpt-4o-mini

flow:
  - name: "greeting"
    goal: "Say hello and explain that you need help with an issue"
    expect:
      - type: turn_count
        min_words: 5
      - type: keyword
        must_not_contain: ["error", "undefined"]

  - name: "describe-problem"
    goal: "Describe a billing problem -- you were charged twice for your subscription"
    expect:
      - type: turn_count
        min_words: 10
      - type: llm_judge
        criteria: "Agent acknowledges the billing issue and shows empathy"
        min_score: 0.7

  - name: "provide-details"
    goal: "When asked, provide your account email: alex@example.com"
    expect:
      - type: turn_count
        min_words: 5

  - name: "resolution"
    goal: "Ask what happens next and when you can expect the refund"
    expect:
      - type: turn_count
        min_words: 10
      - type: llm_judge
        criteria: "Agent provides clear next steps and a timeline for resolution"
        min_score: 0.7

  - name: "farewell"
    goal: "Thank the agent and say goodbye"
    expect:
      - type: turn_count
        min_words: 2

per_turn_expect:
  - type: latency
    max_first_byte_ms: 3000

conversation_eval:
  criteria:
    - "Agent handled the billing complaint professionally and empathetically"
    - "Agent gathered necessary information before proposing a solution"
    - "Agent provided clear next steps for resolution"
    - "Conversation felt natural and not robotic"
  min_score: 0.7
  model: gpt-4o-mini

settings:
  turn_timeout: 15.0
  silence_threshold: 2.0
```

### Key differences from persona mode

- The number of turns is determined by the number of `flow` steps (not `persona.max_turns`).
- Each step has its own `expect` evaluators in addition to global `per_turn_expect`.
- The persona LLM receives the step `goal` as a steering instruction, producing more targeted messages.

---

## Conversation Evaluation

All modes except scripted support `conversation_eval`, which runs an LLM evaluation on the complete conversation transcript after all turns are finished.

```yaml
conversation_eval:
  criteria:
    - "Agent maintained a consistent personality throughout"
    - "Agent provided accurate information"
    - "Responses were appropriate length for voice"
  min_score: 0.7     # minimum score to pass (0.0 to 1.0)
  model: gpt-4o-mini  # LLM model for evaluation
```

The evaluator scores each criterion individually (0.0 to 1.0) and computes an overall score. The scenario's `conversation_eval` passes if the overall score meets or exceeds `min_score`.

Conversation evaluation requires an LLM API key. Use `--skip-llm-judge` to skip it when you want to save costs or run without API keys.

---

## Choosing a Mode

| Scenario | Recommended mode |
|---|---|
| CI regression tests | Scripted or Questions |
| Smoke tests after deploy | Questions |
| Exploratory testing | Persona |
| User journey testing | Guided flow |
| Latency benchmarking | Scripted (most deterministic) |
| Soak/stress testing | Questions or Scripted (lowest per-run cost) |
| Agent persona validation | Persona with conversation_eval |
