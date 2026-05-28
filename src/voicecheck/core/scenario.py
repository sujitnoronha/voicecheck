"""Scenario loading and execution — the core orchestrator of VoiceCheck."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from voicecheck.core.evaluator import get_evaluator
from voicecheck.core.transport import get_transport
from voicecheck.core.types import EvalContext, EvalResult, Timer, TurnResult
from voicecheck.observability import (
    ATTR_AGENT_TEXT,
    ATTR_EVAL_PASSED,
    ATTR_EVAL_REASON,
    ATTR_EVAL_SCORE,
    ATTR_EVAL_TYPE,
    ATTR_FIRST_BYTE_MS,
    ATTR_SCENARIO_MODE,
    ATTR_SCENARIO_NAME,
    ATTR_TOTAL_MS,
    ATTR_TRANSPORT_TYPE,
    ATTR_TURN_INDEX,
    ATTR_TURN_PASSED,
    ATTR_USER_TEXT,
    SPAN_CONVERSATION_EVAL,
    SPAN_EVALUATOR,
    SPAN_SCENARIO,
    SPAN_STT,
    SPAN_TRANSPORT_CONNECT,
    SPAN_TRANSPORT_DISCONNECT,
    SPAN_TRANSPORT_RECEIVE,
    SPAN_TRANSPORT_SEND,
    SPAN_TTS,
    SPAN_TURN,
    set_attrs,
    span,
)

logger = logging.getLogger("voicecheck.core.scenario")


# Evaluator types that issue LLM calls — all skipped by --skip-llm-judge.
_LLM_EVALUATOR_TYPES: frozenset[str] = frozenset(
    {
        "llm_judge",
        "rubric_judge",
        "emotional_tone",
        "fact_accuracy",
        "info_leakage",
        "memory_recall",
        "character_break",
        "personality_consistency",
    }
)


# ── Pydantic models for YAML schema ─────────────────────────────


class ExpectConfig(BaseModel):
    """A single evaluation expectation for a turn."""

    type: str
    # All other fields are passed as kwargs to the evaluator constructor
    model_config = {"extra": "allow"}


class InterruptConfig(BaseModel):
    """Mid-response interruption configuration.

    Send additional audio to the agent while it is responding,
    simulating a user barge-in.
    """

    after_ms: int
    with_text: str = Field(alias="with")
    model_config = {"populate_by_name": True}


class SilenceConfig(BaseModel):
    """Send silence instead of speech for a turn."""

    duration_s: float


class DegradationConfig(BaseModel):
    """Audio degradation settings for testing agent robustness.

    Applied to user audio after TTS synthesis, before sending to the agent.
    Effects are chained: noise → bandwidth → codec → packet loss.
    """

    noise_snr_db: float | None = None
    bandwidth: str | None = None  # "narrowband" (8kHz) or "wideband" (16kHz)
    packet_loss_pct: float | None = None
    codec: str | None = None  # "mulaw"


class TurnConfig(BaseModel):
    """A single conversation turn in a scenario.

    Specify either ``user`` text (synthesized via TTS) or ``silence``
    (sends silent frames). Use ``interrupt`` to barge in mid-response.
    """

    user: str = ""
    expect: list[ExpectConfig] = Field(default_factory=list)
    interrupt: InterruptConfig | None = None
    silence: SilenceConfig | None = None
    pause_before_ms: int = 0


class AudioConfig(BaseModel):
    """Audio provider configuration."""

    tts_provider: str = "edge"
    stt_provider: str = "whisper"
    sample_rate: int = 16000
    channels: int = 1
    language: str = ""
    degradation: DegradationConfig | None = None
    # Extra kwargs passed to the providers
    tts_kwargs: dict[str, Any] = Field(default_factory=dict)
    stt_kwargs: dict[str, Any] = Field(default_factory=dict)


class TransportConfig(BaseModel):
    """Transport configuration."""

    type: str = "livekit"
    mode: str = "direct"
    config: dict[str, Any] = Field(default_factory=dict)


class SettingsConfig(BaseModel):
    """Scenario-level settings."""

    turn_timeout: float = 15.0
    silence_threshold: float = 1.5


class ObservabilityYamlConfig(BaseModel):
    """OpenTelemetry tracing config (optional).

    When enabled, voicecheck emits OTel spans for every scenario, turn,
    audio phase, evaluator, and tool call. CLI flags
    (``--otel-endpoint``, ``--otel-console``, ``--otel-service``) take
    precedence over these YAML values.
    """

    enabled: bool = False
    service_name: str = "voicecheck"
    endpoint: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    console: bool = False
    resource_attrs: dict[str, str] = Field(default_factory=dict)


class PersonaConfig(BaseModel):
    """Persona for free-flowing conversations (alternative to scripted turns)."""

    name: str = "Test User"
    description: str = ""
    age: int | None = None
    personality: str = "friendly and curious"
    communication_style: str = "casual, short sentences"
    goals: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    instructions: str = ""
    model: str = "gpt-4o-mini"
    max_turns: int = 5
    opening: str = ""


class ConversationEvalConfig(BaseModel):
    """Post-conversation evaluation criteria."""

    criteria: list[str] = Field(default_factory=list)
    min_score: float = 0.7
    model: str = "gpt-4o-mini"


class FlowStepConfig(BaseModel):
    """A single step in a guided conversation flow.

    Each step gives the persona LLM a specific goal to achieve in that turn,
    and defines evaluators to check the agent's response for that step.
    """

    name: str = ""
    goal: str
    expect: list[ExpectConfig] = Field(default_factory=list)


class Scenario(BaseModel):
    """A complete test scenario loaded from YAML.

    Supports four modes:
    - Scripted: Define explicit `turns` with user text and per-turn evaluators
    - Questions: Define `questions` — fixed user messages with shared per_turn_expect
    - Persona: Define a `persona` for free-flowing conversation
    - Guided: Define a `persona` + `flow` for goal-driven structured conversation
    """

    name: str = "unnamed"
    description: str = ""
    transport: TransportConfig = Field(default_factory=TransportConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    # Scripted mode
    turns: list[TurnConfig] = Field(default_factory=list)
    # Questions mode — fixed user messages with per_turn_expect + conversation_eval
    questions: list[str] = Field(default_factory=list)
    # Persona mode (if persona is set, turns are generated dynamically)
    persona: PersonaConfig | None = None
    conversation_eval: ConversationEvalConfig | None = None
    # Per-turn evaluators applied to every turn in persona/questions mode
    per_turn_expect: list[ExpectConfig] = Field(default_factory=list)
    # Guided flow mode (persona + structured steps with per-step goals/evals)
    flow: list[FlowStepConfig] = Field(default_factory=list)
    settings: SettingsConfig = Field(default_factory=SettingsConfig)
    observability: ObservabilityYamlConfig = Field(default_factory=ObservabilityYamlConfig)

    @property
    def is_questions_mode(self) -> bool:
        return len(self.questions) > 0

    @property
    def is_persona_mode(self) -> bool:
        return self.persona is not None and not self.flow and not self.questions

    @property
    def is_guided_mode(self) -> bool:
        return self.persona is not None and len(self.flow) > 0


# ── YAML loading ─────────────────────────────────────────────────


class _MissingEnvVarError(ValueError):
    """Raised when a scenario references ``${VAR}`` but VAR is not set."""


def _expand_env_vars(value: Any, missing: set[str] | None = None) -> Any:
    """Recursively expand ``${VAR}`` references in strings.

    Missing env vars used to be silently preserved as the literal
    ``${VAR}`` text, which caused downstream failures with confusing
    "401 unauthorized" errors at transport-connect time. We now collect
    every missing var as we walk the tree and raise a single aggregated
    error at the end so users see the complete list.
    """
    # Root call owns the set; recursive calls share it.
    top_level = missing is None
    if top_level:
        missing = set()

    if isinstance(value, str):

        def _sub(m: re.Match[str]) -> str:
            var = m.group(1)
            env = os.environ.get(var)
            if env is None:
                missing.add(var)
                return m.group(0)  # preserve literal so we keep walking
            return env

        result: Any = re.sub(r"\$\{(\w+)\}", _sub, value)
    elif isinstance(value, dict):
        result = {k: _expand_env_vars(v, missing) for k, v in value.items()}
    elif isinstance(value, list):
        result = [_expand_env_vars(item, missing) for item in value]
    else:
        result = value

    if top_level and missing:
        names = ", ".join(sorted(missing))
        raise _MissingEnvVarError(
            f"Scenario references unset environment variables: {names}. "
            "Export them, put them in a .env loaded by your runner, or use "
            "dummy values for validation-only runs."
        )
    return result


def load_scenario(path: str | Path, *, strict_env: bool = True) -> Scenario:
    """Load a scenario from a YAML file.

    Supports ``${ENV_VAR}`` expansion in all string values.

    Args:
        path: Path to the YAML scenario file.
        strict_env: If True (default), raise when the YAML references env
            vars that aren't set. ``voicecheck run`` uses this.
            ``voicecheck validate`` sets this False — schema-only validation
            shouldn't need live credentials.

    Returns:
        Parsed Scenario object.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the YAML is invalid or references unset env vars.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Scenario file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Scenario must be a YAML mapping, got {type(raw).__name__}")

    try:
        expanded = _expand_env_vars(raw)
    except _MissingEnvVarError:
        if strict_env:
            raise
        # Permissive mode: re-expand and swallow missing vars (keep literal).
        expanded = _expand_env_vars_tolerant(raw)
    return Scenario(**expanded)


def _expand_env_vars_tolerant(value: Any) -> Any:
    """Like ``_expand_env_vars`` but leaves missing ``${VAR}`` as literals.

    Only used by ``validate_scenario`` for schema-only checks.
    """
    if isinstance(value, str):
        return re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
    elif isinstance(value, dict):
        return {k: _expand_env_vars_tolerant(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env_vars_tolerant(item) for item in value]
    return value


def validate_scenario(path: str | Path) -> list[str]:
    """Validate a scenario YAML file without running it.

    Validates the schema and every evaluator's constructor. Does NOT require
    referenced env vars to be set — schema-only checks should work in CI
    without live credentials. ``voicecheck run`` is strict about env.

    Returns:
        List of validation errors (empty if valid).
    """
    errors: list[str] = []
    try:
        scenario = load_scenario(path, strict_env=False)
    except Exception as e:
        return [str(e)]

    if not scenario.turns and not scenario.persona and not scenario.questions:
        errors.append(
            "Scenario must define 'turns' (scripted), 'questions' (fixed Q&A), 'persona' (dynamic), or 'persona' + 'flow' (guided)"
        )

    if scenario.flow and not scenario.persona:
        errors.append(
            "'flow' requires a 'persona' section — the persona LLM generates messages for each flow step"
        )

    if scenario.persona and not scenario.flow and scenario.persona.max_turns < 1:
        errors.append("persona.max_turns must be at least 1")

    # Check transport type is registered
    try:
        get_transport(scenario.transport.type)
    except ValueError as e:
        errors.append(str(e))

    # Check TTS provider is valid
    from voicecheck.audio.tts import _TTS_PROVIDERS

    if scenario.audio.tts_provider not in _TTS_PROVIDERS:
        available = ", ".join(_TTS_PROVIDERS.keys())
        errors.append(
            f"Unknown TTS provider: {scenario.audio.tts_provider!r}. Available: {available}"
        )

    # Check STT provider is valid
    from voicecheck.audio.stt import _STT_PROVIDERS

    if scenario.audio.stt_provider not in _STT_PROVIDERS:
        available = ", ".join(_STT_PROVIDERS.keys())
        errors.append(
            f"Unknown STT provider: {scenario.audio.stt_provider!r}. Available: {available}"
        )

    # Check evaluator types + instantiation for scripted turns
    for i, turn in enumerate(scenario.turns):
        for expect in turn.expect:
            errors.extend(_check_expect(expect, where=f"Turn {i}"))

    # Check per-turn evaluators (persona/questions mode)
    for expect in scenario.per_turn_expect:
        errors.extend(_check_expect(expect, where="per_turn_expect"))

    # Check flow step evaluators (guided mode)
    for j, step in enumerate(scenario.flow):
        if not step.goal:
            errors.append(f"Flow step {j}: 'goal' is required")
        label = step.name or "unnamed"
        for expect in step.expect:
            errors.extend(_check_expect(expect, where=f"Flow step {j} ({label})"))

    return errors


def _check_expect(expect: ExpectConfig, *, where: str) -> list[str]:
    """Validate one evaluator config: registered AND instantiable.

    Running the constructor is the cheapest way to catch kwarg typos,
    missing-required kwargs (like ``rubric_judge``'s ``ground_truth``
    enforcement), and plain type errors. Without this, YAML validation
    lies: ``voicecheck validate`` returns OK, then the scenario crashes
    inside ``_run_evaluators`` at runtime.
    """
    try:
        evaluator_cls = get_evaluator(expect.type)
    except ValueError as e:
        return [f"{where}: {e}"]

    kwargs = expect.model_dump(exclude={"type"})
    try:
        evaluator_cls(**kwargs)
    except Exception as e:  # TypeError, ValueError, etc.
        return [f"{where} [{expect.type}]: {type(e).__name__}: {e}"]
    return []


# ── ScenarioRunner ───────────────────────────────────────────────


@dataclass
class ScenarioReport:
    """Results from running a complete scenario."""

    scenario_name: str
    turns: list[TurnResult] = field(default_factory=list)
    conversation_eval: dict[str, Any] | None = None

    @property
    def passed(self) -> bool:
        turns_ok = all(t.passed for t in self.turns)
        if self.conversation_eval:
            return turns_ok and self.conversation_eval.get("overall_passed", False)
        return turns_ok

    @property
    def total_turns(self) -> int:
        return len(self.turns)

    @property
    def passed_turns(self) -> int:
        return sum(1 for t in self.turns if t.passed)


class ScenarioRunner:
    """Orchestrates end-to-end voice agent testing.

    Flow per turn:
    1. TTS: Synthesize user text → audio frames
    2. Transport: Send audio → agent processes → receive agent audio
    3. STT: Transcribe agent audio → text
    4. Evaluate: Run each evaluator on the turn results
    """

    def __init__(
        self,
        scenario: Scenario,
        skip_llm_judge: bool = False,
        turn_callback: Any | None = None,
    ) -> None:
        self.scenario = scenario
        self.skip_llm_judge = skip_llm_judge
        # Optional async callback(turn_result, turn_index, total_turns | None)
        # called after every turn. Used by the dashboard for live SSE streaming.
        self.turn_callback = turn_callback

    @classmethod
    def from_yaml(cls, path: str | Path, **kwargs: Any) -> ScenarioRunner:
        """Create a runner from a YAML scenario file."""
        return cls(load_scenario(path), **kwargs)

    async def run(self) -> ScenarioReport:
        """Execute the full scenario and return a report."""
        self._preflight_checks()
        scenario = self.scenario
        with span(
            SPAN_SCENARIO,
            attrs={
                ATTR_SCENARIO_NAME: scenario.name,
                ATTR_SCENARIO_MODE: self._scenario_mode(),
                ATTR_TRANSPORT_TYPE: scenario.transport.type,
                "voicecheck.transport.mode": scenario.transport.mode,
                "voicecheck.audio.tts_provider": scenario.audio.tts_provider,
                "voicecheck.audio.stt_provider": scenario.audio.stt_provider,
                "voicecheck.audio.language": scenario.audio.language or "auto",
            },
        ) as scenario_span:
            if scenario.is_guided_mode:
                report = await self._run_guided()
            elif scenario.is_questions_mode:
                report = await self._run_questions()
            elif scenario.is_persona_mode:
                report = await self._run_persona()
            else:
                report = await self._run_scripted()
            set_attrs(
                scenario_span,
                {
                    "voicecheck.scenario.passed": report.passed,
                    "voicecheck.scenario.passed_turns": report.passed_turns,
                    "voicecheck.scenario.total_turns": report.total_turns,
                },
            )
            return report

    def _scenario_mode(self) -> str:
        s = self.scenario
        if s.is_guided_mode:
            return "guided"
        if s.is_questions_mode:
            return "questions"
        if s.is_persona_mode:
            return "persona"
        return "scripted"

    def _preflight_checks(self) -> None:
        """Verify required API keys and config before running."""
        s = self.scenario

        # OpenAI key needed for: openai TTS, openai STT, persona/guided mode, or LLM judge
        needs_openai = (
            s.audio.tts_provider == "openai"
            or s.audio.stt_provider == "openai"
            or s.is_persona_mode
            or s.is_guided_mode
        )
        # Questions mode doesn't need OpenAI for generation (no persona LLM)
        # Check if any turn uses an LLM evaluator (skip if --skip-llm-judge).
        # Guided mode already forces needs_openai=True for the persona engine,
        # so we only walk scripted turns and per_turn_expect here.
        if not self.skip_llm_judge:
            for turn in s.turns:
                for expect in turn.expect:
                    if expect.type in _LLM_EVALUATOR_TYPES:
                        needs_openai = True
            for expect in s.per_turn_expect:
                if expect.type in _LLM_EVALUATOR_TYPES:
                    needs_openai = True
            if s.conversation_eval and s.conversation_eval.criteria:
                needs_openai = True

        if needs_openai and not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is required for this scenario "
                f"(tts={s.audio.tts_provider}, stt={s.audio.stt_provider}, "
                f"persona={s.is_persona_mode}). "
                "Set it with: export OPENAI_API_KEY=sk-..."
            )

        # Transport-specific config validation
        transport_cls = get_transport(s.transport.type)
        transport_config = {
            **s.transport.config,
            "mode": s.transport.mode,
            "sample_rate": s.audio.sample_rate,
            "num_channels": s.audio.channels,
        }
        config_errors = transport_cls().validate_config(transport_config)
        if config_errors:
            raise RuntimeError(
                f"Transport config errors for {s.transport.type!r}:\n"
                + "\n".join(f"  - {e}" for e in config_errors)
            )

    # ── Shared helpers ─────────────────────────────────────────────

    def _create_providers(self) -> tuple:
        """Create TTS and STT providers with language wiring.

        Returns:
            (tts_provider, stt_provider)
        """
        from voicecheck.audio.stt import get_stt_provider
        from voicecheck.audio.tts import get_tts_provider

        s = self.scenario
        tts_kwargs = {**s.audio.tts_kwargs}
        stt_kwargs = {**s.audio.stt_kwargs}

        if s.audio.language:
            tts_kwargs.setdefault("language", s.audio.language)
            stt_kwargs.setdefault("language", s.audio.language)

        tts = get_tts_provider(
            s.audio.tts_provider,
            sample_rate=s.audio.sample_rate,
            **tts_kwargs,
        )
        stt = get_stt_provider(s.audio.stt_provider, **stt_kwargs)
        return tts, stt

    def _create_transport(self) -> tuple:
        """Create transport instance and config dict.

        Returns:
            (transport, transport_config)
        """
        s = self.scenario
        transport_cls = get_transport(s.transport.type)
        transport = transport_cls()
        transport_config = {
            **s.transport.config,
            "mode": s.transport.mode,
            "sample_rate": s.audio.sample_rate,
            "num_channels": s.audio.channels,
        }
        return transport, transport_config

    async def _tts_synthesize(self, tts: Any, text: str) -> list:
        """TTS-synthesize ``text`` inside a span."""
        with span(
            SPAN_TTS,
            attrs={
                "voicecheck.tts.provider": self.scenario.audio.tts_provider,
                "voicecheck.tts.text": text,
                "voicecheck.tts.text_length": len(text),
            },
        ) as s:
            with Timer() as timer:
                frames = await tts.synthesize(text)
            set_attrs(
                s,
                {
                    "voicecheck.tts.duration_ms": timer.elapsed_ms,
                    "voicecheck.tts.frames": len(frames),
                },
            )
            return frames

    async def _transport_send(self, transport: Any, frames: list) -> None:
        with span(
            SPAN_TRANSPORT_SEND,
            attrs={
                ATTR_TRANSPORT_TYPE: self.scenario.transport.type,
                "voicecheck.transport.send.frames": len(frames),
            },
        ):
            await transport.send_audio(frames)

    async def _transport_receive(self, transport: Any) -> list:
        with span(
            SPAN_TRANSPORT_RECEIVE,
            attrs={ATTR_TRANSPORT_TYPE: self.scenario.transport.type},
        ) as s:
            frames = await transport.receive_audio(
                timeout=self.scenario.settings.turn_timeout,
                silence_threshold=self.scenario.settings.silence_threshold,
            )
            set_attrs(
                s,
                {
                    ATTR_FIRST_BYTE_MS: transport.metrics.first_byte_ms,
                    ATTR_TOTAL_MS: transport.metrics.total_ms,
                    "voicecheck.transport.receive.frames": len(frames),
                },
            )
            return frames

    async def _stt_transcribe(self, stt: Any, frames: list) -> Any:
        with span(
            SPAN_STT,
            attrs={
                "voicecheck.stt.provider": self.scenario.audio.stt_provider,
                "voicecheck.stt.input_frames": len(frames),
            },
        ) as s:
            with Timer() as timer:
                transcript = await stt.transcribe(frames)
            set_attrs(
                s,
                {
                    "voicecheck.stt.duration_ms": timer.elapsed_ms,
                    "voicecheck.stt.text": transcript.text,
                },
            )
            return transcript

    def _apply_degradation(self, frames: list) -> list:
        """Apply audio degradation if configured. Returns frames unchanged if not."""
        deg = self.scenario.audio.degradation
        if not deg:
            return frames
        from voicecheck.audio.degradation import apply_degradation

        return apply_degradation(
            frames,
            noise_snr_db=deg.noise_snr_db,
            bandwidth=deg.bandwidth,
            packet_loss_pct=deg.packet_loss_pct,
            codec=deg.codec,
        )

    # ── Scripted mode ────────────────────────────────────────────

    async def _run_scripted(self) -> ScenarioReport:
        """Execute scripted turns with explicit user text.

        Supports silence turns, pre-turn pause, mid-response interruption,
        and audio degradation.
        """
        import asyncio

        scenario = self.scenario
        report = ScenarioReport(scenario_name=scenario.name)
        tts, stt = self._create_providers()
        transport, transport_config = self._create_transport()

        conversation: list[dict] = []

        try:
            with span(SPAN_TRANSPORT_CONNECT, attrs={ATTR_TRANSPORT_TYPE: scenario.transport.type}):
                await transport.connect(transport_config)

            for i, turn_config in enumerate(scenario.turns):
                user_text = turn_config.user or f"[silence: {turn_config.silence.duration_s}s]"
                logger.info("── Turn %d/%d: %s", i + 1, len(scenario.turns), user_text[:60])
                transport.reset_metrics()

                user_frames: list = []
                turn_metadata: dict = {}
                with span(
                    SPAN_TURN,
                    attrs={
                        ATTR_TURN_INDEX: i,
                        ATTR_USER_TEXT: turn_config.user or "[silence]",
                    },
                ) as turn_span:
                    try:
                        # Optional pre-turn pause
                        if turn_config.pause_before_ms > 0:
                            await asyncio.sleep(turn_config.pause_before_ms / 1000.0)

                        # Prepare user audio: silence or TTS
                        if turn_config.silence:
                            from voicecheck.audio.utils import generate_silence

                            user_frames = generate_silence(
                                duration_s=turn_config.silence.duration_s,
                                sample_rate=scenario.audio.sample_rate,
                                num_channels=scenario.audio.channels,
                            )
                            turn_metadata["silence"] = True
                            turn_metadata["silence_duration_s"] = turn_config.silence.duration_s
                        else:
                            user_frames = await self._tts_synthesize(tts, turn_config.user)
                            transport.metrics.tts_duration_ms = (
                                sum(f.duration_s for f in user_frames) * 1000
                            )

                        # Apply audio degradation if configured
                        user_frames = self._apply_degradation(user_frames)
                        transport.metrics.user_audio_duration_ms = (
                            sum(f.duration_s for f in user_frames) * 1000
                        )

                        # Send and receive (with optional interruption)
                        await self._transport_send(transport, user_frames)

                        if turn_config.interrupt:
                            # Start receiving in background, then interrupt
                            receive_task = asyncio.create_task(
                                transport.receive_audio(
                                    timeout=scenario.settings.turn_timeout,
                                    silence_threshold=scenario.settings.silence_threshold,
                                )
                            )
                            try:
                                await asyncio.sleep(turn_config.interrupt.after_ms / 1000.0)
                                interrupt_frames = await self._tts_synthesize(
                                    tts, turn_config.interrupt.with_text
                                )
                                interrupt_frames = self._apply_degradation(interrupt_frames)
                                await self._transport_send(transport, interrupt_frames)
                                agent_frames = await receive_task
                            except Exception:
                                receive_task.cancel()
                                try:
                                    await receive_task
                                except (asyncio.CancelledError, Exception):
                                    pass
                                raise
                            turn_metadata["interrupted"] = True
                            turn_metadata["interrupt_after_ms"] = turn_config.interrupt.after_ms
                            turn_metadata["interrupt_text"] = turn_config.interrupt.with_text
                        else:
                            agent_frames = await self._transport_receive(transport)

                        transcript = await self._stt_transcribe(stt, agent_frames)
                        agent_text = transcript.text
                        logger.info("Agent said: %s", agent_text[:100])
                        turn_error = ""
                    except Exception as e:
                        logger.error("Turn %d failed: %s", i, e)
                        agent_text = ""
                        agent_frames = []
                        turn_error = str(e)[:300]

                    conversation.append({"role": "user", "text": turn_config.user or "[silence]"})
                    conversation.append({"role": "agent", "text": agent_text})

                    # Drain tool calls before evaluators so tool-aware
                    # evaluators (tool_called, tool_sequence) can read them
                    # off the EvalContext. The same list is attached to the
                    # TurnResult for the report.
                    tool_calls = transport.take_tool_calls()

                    eval_context = EvalContext(
                        user_text=turn_config.user,
                        agent_text=agent_text,
                        agent_audio=agent_frames,
                        metrics=transport.metrics,
                        turn_index=i,
                        scenario_name=scenario.name,
                        conversation=list(conversation),
                        turn_metadata=turn_metadata,
                        tool_calls=tool_calls,
                    )

                    eval_results = await self._run_evaluators(turn_config.expect, eval_context)

                    turn_passed = (
                        not turn_error
                        and (bool(agent_text) or bool(agent_frames))
                        and all(r.passed for r in eval_results)
                    )
                    set_attrs(
                        turn_span,
                        {
                            ATTR_AGENT_TEXT: agent_text,
                            ATTR_TURN_PASSED: turn_passed,
                            "voicecheck.turn.error": turn_error,
                            "voicecheck.turn.tool_call_count": len(tool_calls),
                        },
                    )

                    turn_result = TurnResult(
                        turn_index=i,
                        user_text=turn_config.user,
                        agent_text=agent_text,
                        user_audio=user_frames,
                        agent_audio=agent_frames,
                        metrics=transport.metrics,
                        eval_results=eval_results,
                        error=turn_error,
                        tool_calls=tool_calls,
                    )
                    report.turns.append(turn_result)
                    if self.turn_callback:
                        await self.turn_callback(turn_result, i, len(scenario.turns))
        finally:
            with span(
                SPAN_TRANSPORT_DISCONNECT, attrs={ATTR_TRANSPORT_TYPE: scenario.transport.type}
            ):
                await transport.disconnect()

        logger.info(
            "Scenario %r complete: %d/%d turns passed",
            scenario.name,
            report.passed_turns,
            report.total_turns,
        )
        return report

    async def _run_questions(self) -> ScenarioReport:
        """Execute fixed questions with per_turn_expect and conversation_eval.

        Like scripted mode but uses a flat list of questions with shared
        evaluators. Supports conversation_eval for post-conversation LLM scoring.
        """
        scenario = self.scenario
        questions = scenario.questions
        report = ScenarioReport(scenario_name=scenario.name)

        tts, stt = self._create_providers()
        transport, transport_config = self._create_transport()

        conversation: list[dict] = []

        try:
            with span(SPAN_TRANSPORT_CONNECT, attrs={ATTR_TRANSPORT_TYPE: scenario.transport.type}):
                await transport.connect(transport_config)

            for i, user_text in enumerate(questions):
                logger.info("── Question %d/%d: %s", i + 1, len(questions), user_text[:60])
                transport.reset_metrics()

                user_frames: list = []
                with span(
                    SPAN_TURN,
                    attrs={ATTR_TURN_INDEX: i, ATTR_USER_TEXT: user_text},
                ) as turn_span:
                    try:
                        user_frames = await self._tts_synthesize(tts, user_text)
                        transport.metrics.tts_duration_ms = (
                            sum(f.duration_s for f in user_frames) * 1000
                        )
                        user_frames = self._apply_degradation(user_frames)
                        transport.metrics.user_audio_duration_ms = (
                            sum(f.duration_s for f in user_frames) * 1000
                        )
                        await self._transport_send(transport, user_frames)
                        agent_frames = await self._transport_receive(transport)
                        transcript = await self._stt_transcribe(stt, agent_frames)
                        agent_text = transcript.text
                        logger.info("Agent said: %s", agent_text[:100])
                        turn_error = ""
                    except Exception as e:
                        logger.error("Question %d failed: %s", i, e)
                        agent_text = ""
                        agent_frames = []
                        turn_error = str(e)[:300]

                    conversation.append({"role": "user", "text": user_text})
                    conversation.append({"role": "agent", "text": agent_text})

                    tool_calls = transport.take_tool_calls()
                    eval_context = EvalContext(
                        user_text=user_text,
                        agent_text=agent_text,
                        agent_audio=agent_frames,
                        metrics=transport.metrics,
                        turn_index=i,
                        scenario_name=scenario.name,
                        conversation=list(conversation),
                        tool_calls=tool_calls,
                    )
                    eval_results = await self._run_evaluators(
                        scenario.per_turn_expect, eval_context
                    )

                    turn_passed = (
                        not turn_error
                        and (bool(agent_text) or bool(agent_frames))
                        and all(r.passed for r in eval_results)
                    )
                    set_attrs(
                        turn_span,
                        {
                            ATTR_AGENT_TEXT: agent_text,
                            ATTR_TURN_PASSED: turn_passed,
                            "voicecheck.turn.error": turn_error,
                            "voicecheck.turn.tool_call_count": len(tool_calls),
                        },
                    )

                    turn_result = TurnResult(
                        turn_index=i,
                        user_text=user_text,
                        agent_text=agent_text,
                        user_audio=user_frames,
                        agent_audio=agent_frames,
                        metrics=transport.metrics,
                        eval_results=eval_results,
                        error=turn_error,
                        tool_calls=tool_calls,
                    )
                    report.turns.append(turn_result)
                    if self.turn_callback:
                        await self.turn_callback(turn_result, i, len(questions))
        finally:
            with span(
                SPAN_TRANSPORT_DISCONNECT, attrs={ATTR_TRANSPORT_TYPE: scenario.transport.type}
            ):
                await transport.disconnect()

        # Post-conversation evaluation
        if (
            scenario.conversation_eval
            and scenario.conversation_eval.criteria
            and not self.skip_llm_judge
        ):
            from voicecheck.conversation.engine import (
                ConversationEngine,
            )
            from voicecheck.conversation.engine import (
                ConversationEvalConfig as EngineEvalConfig,
            )
            from voicecheck.conversation.engine import (
                PersonaConfig as EnginePersonaConfig,
            )

            logger.info("Running post-conversation evaluation...")
            # Build a minimal persona for the eval engine (only needs model config)
            if scenario.persona:
                engine_persona = EnginePersonaConfig(**scenario.persona.model_dump())
            else:
                engine_persona = EnginePersonaConfig(name="Tester")
            engine = ConversationEngine(engine_persona)
            engine_eval = EngineEvalConfig(
                criteria=scenario.conversation_eval.criteria,
                min_score=scenario.conversation_eval.min_score,
                model=scenario.conversation_eval.model,
            )
            with span(
                SPAN_CONVERSATION_EVAL,
                attrs={
                    "voicecheck.conversation_eval.criteria_count": len(engine_eval.criteria),
                    "voicecheck.conversation_eval.model": engine_eval.model,
                    "voicecheck.conversation_eval.min_score": engine_eval.min_score,
                },
            ) as conv_span:
                conv_result = await engine.evaluate_conversation(conversation, engine_eval)
                set_attrs(
                    conv_span,
                    {
                        "voicecheck.conversation_eval.score": conv_result.get("overall_score", 0.0),
                        "voicecheck.conversation_eval.passed": conv_result.get(
                            "overall_passed", False
                        ),
                        "voicecheck.conversation_eval.reason": conv_result.get(
                            "overall_reason", ""
                        ),
                    },
                )
            report.conversation_eval = conv_result
            logger.info(
                "Conversation eval: score=%.2f passed=%s reason=%s",
                conv_result.get("overall_score", 0),
                conv_result.get("overall_passed", False),
                conv_result.get("overall_reason", "")[:80],
            )

        logger.info(
            "Questions scenario %r complete: %d/%d turns passed",
            scenario.name,
            report.passed_turns,
            report.total_turns,
        )
        return report

    async def _run_persona(self) -> ScenarioReport:
        """Execute persona-driven dynamic conversation."""
        from voicecheck.conversation.engine import (
            ConversationEngine,
        )
        from voicecheck.conversation.engine import (
            ConversationEvalConfig as EngineEvalConfig,
        )
        from voicecheck.conversation.engine import (
            PersonaConfig as EnginePersonaConfig,
        )

        scenario = self.scenario
        persona_cfg = scenario.persona
        report = ScenarioReport(scenario_name=scenario.name)

        tts, stt = self._create_providers()
        transport, transport_config = self._create_transport()

        # Build engine persona from scenario config
        engine_persona = EnginePersonaConfig(**persona_cfg.model_dump())
        engine = ConversationEngine(engine_persona)

        conversation: list[dict] = []

        try:
            with span(SPAN_TRANSPORT_CONNECT, attrs={ATTR_TRANSPORT_TYPE: scenario.transport.type}):
                await transport.connect(transport_config)

            # Generate opening message
            user_text = await engine.generate_opening()

            for i in range(persona_cfg.max_turns):
                logger.info(
                    "── Persona turn %d/%d: %s", i + 1, persona_cfg.max_turns, user_text[:60]
                )
                transport.reset_metrics()

                user_frames: list = []
                with span(
                    SPAN_TURN,
                    attrs={ATTR_TURN_INDEX: i, ATTR_USER_TEXT: user_text},
                ) as turn_span:
                    try:
                        user_frames = await self._tts_synthesize(tts, user_text)
                        transport.metrics.tts_duration_ms = (
                            sum(f.duration_s for f in user_frames) * 1000
                        )
                        user_frames = self._apply_degradation(user_frames)
                        transport.metrics.user_audio_duration_ms = (
                            sum(f.duration_s for f in user_frames) * 1000
                        )
                        await self._transport_send(transport, user_frames)
                        agent_frames = await self._transport_receive(transport)
                        transcript = await self._stt_transcribe(stt, agent_frames)
                        agent_text = transcript.text
                        logger.info("Agent said: %s", agent_text[:100])
                        turn_error = ""
                    except Exception as e:
                        logger.error("Persona turn %d failed: %s", i, e)
                        agent_text = ""
                        agent_frames = []
                        turn_error = str(e)[:300]

                    conversation.append({"role": "user", "text": user_text})
                    conversation.append({"role": "agent", "text": agent_text})

                    # Per-turn evaluators (latency, turn_count, etc.)
                    tool_calls = transport.take_tool_calls()
                    eval_context = EvalContext(
                        user_text=user_text,
                        agent_text=agent_text,
                        agent_audio=agent_frames,
                        metrics=transport.metrics,
                        turn_index=i,
                        scenario_name=scenario.name,
                        conversation=list(conversation),
                        tool_calls=tool_calls,
                    )
                    eval_results = await self._run_evaluators(
                        scenario.per_turn_expect, eval_context
                    )

                    turn_passed = (
                        not turn_error
                        and (bool(agent_text) or bool(agent_frames))
                        and all(r.passed for r in eval_results)
                    )
                    set_attrs(
                        turn_span,
                        {
                            ATTR_AGENT_TEXT: agent_text,
                            ATTR_TURN_PASSED: turn_passed,
                            "voicecheck.turn.error": turn_error,
                            "voicecheck.turn.tool_call_count": len(tool_calls),
                        },
                    )

                    turn_result = TurnResult(
                        turn_index=i,
                        user_text=user_text,
                        agent_text=agent_text,
                        user_audio=user_frames,
                        agent_audio=agent_frames,
                        metrics=transport.metrics,
                        eval_results=eval_results,
                        error=turn_error,
                        tool_calls=tool_calls,
                    )
                    report.turns.append(turn_result)
                    if self.turn_callback:
                        await self.turn_callback(turn_result, i, None)

                # Generate next user message (unless this is the last turn)
                if i < persona_cfg.max_turns - 1:
                    user_text = await engine.generate_next(agent_text)

        finally:
            with span(
                SPAN_TRANSPORT_DISCONNECT, attrs={ATTR_TRANSPORT_TYPE: scenario.transport.type}
            ):
                await transport.disconnect()

        # Post-conversation evaluation (uses LLM, skip if --skip-llm-judge)
        if (
            scenario.conversation_eval
            and scenario.conversation_eval.criteria
            and not self.skip_llm_judge
        ):
            logger.info("Running post-conversation evaluation...")
            engine_eval = EngineEvalConfig(
                criteria=scenario.conversation_eval.criteria,
                min_score=scenario.conversation_eval.min_score,
                model=scenario.conversation_eval.model,
            )
            conv_result = await engine.evaluate_conversation(conversation, engine_eval)

            # Add as an eval result on a synthetic "summary" turn
            report.conversation_eval = conv_result
            logger.info(
                "Conversation eval: score=%.2f passed=%s reason=%s",
                conv_result.get("overall_score", 0),
                conv_result.get("overall_passed", False),
                conv_result.get("overall_reason", "")[:80],
            )

        logger.info(
            "Persona scenario %r complete: %d turns, %d/%d per-turn passed",
            scenario.name,
            len(report.turns),
            report.passed_turns,
            report.total_turns,
        )
        return report

    async def _run_guided(self) -> ScenarioReport:
        """Execute guided persona conversation — LLM follows a flow of steps."""
        from voicecheck.conversation.engine import (
            ConversationEngine,
        )
        from voicecheck.conversation.engine import (
            ConversationEvalConfig as EngineEvalConfig,
        )
        from voicecheck.conversation.engine import (
            PersonaConfig as EnginePersonaConfig,
        )

        scenario = self.scenario
        persona_cfg = scenario.persona
        flow_steps = scenario.flow
        report = ScenarioReport(scenario_name=scenario.name)

        tts, stt = self._create_providers()
        transport, transport_config = self._create_transport()

        engine_persona = EnginePersonaConfig(**persona_cfg.model_dump())
        engine = ConversationEngine(engine_persona)

        conversation: list[dict] = []

        try:
            with span(SPAN_TRANSPORT_CONNECT, attrs={ATTR_TRANSPORT_TYPE: scenario.transport.type}):
                await transport.connect(transport_config)

            for i, step in enumerate(flow_steps):
                step_label = step.name or f"step-{i + 1}"
                logger.info(
                    "── Guided step %d/%d [%s]: %s",
                    i + 1,
                    len(flow_steps),
                    step_label,
                    step.goal[:60],
                )
                transport.reset_metrics()

                user_frames: list = []
                with span(
                    SPAN_TURN,
                    attrs={
                        ATTR_TURN_INDEX: i,
                        "voicecheck.flow.step": step_label,
                        "voicecheck.flow.goal": step.goal,
                    },
                ) as turn_span:
                    try:
                        # Generate user message for this step
                        if i == 0:
                            user_text = await engine.generate_opening_guided(step.goal)
                        else:
                            last_agent_text = conversation[-1]["text"] if conversation else ""
                            user_text = await engine.generate_next_guided(
                                last_agent_text, step.goal
                            )

                        logger.info("Persona said: %s", user_text[:100])
                        set_attrs(turn_span, {ATTR_USER_TEXT: user_text})

                        user_frames = await self._tts_synthesize(tts, user_text)
                        transport.metrics.tts_duration_ms = (
                            sum(f.duration_s for f in user_frames) * 1000
                        )
                        user_frames = self._apply_degradation(user_frames)
                        transport.metrics.user_audio_duration_ms = (
                            sum(f.duration_s for f in user_frames) * 1000
                        )
                        await self._transport_send(transport, user_frames)
                        agent_frames = await self._transport_receive(transport)
                        transcript = await self._stt_transcribe(stt, agent_frames)
                        agent_text = transcript.text
                        logger.info("Agent said: %s", agent_text[:100])
                        turn_error = ""
                    except Exception as e:
                        logger.error("Step %s failed: %s", step_label, e)
                        user_text = getattr(e, "_user_text", "") or f"[step {step_label} failed]"
                        agent_text = ""
                        agent_frames = []
                        turn_error = str(e)[:300]

                    conversation.append({"role": "user", "text": user_text})
                    conversation.append({"role": "agent", "text": agent_text})

                    tool_calls = transport.take_tool_calls()
                    eval_context = EvalContext(
                        user_text=user_text,
                        agent_text=agent_text,
                        agent_audio=agent_frames,
                        metrics=transport.metrics,
                        turn_index=i,
                        scenario_name=scenario.name,
                        conversation=list(conversation),
                        tool_calls=tool_calls,
                    )

                    # Run step-specific evaluators + per-turn evaluators
                    all_expects = list(step.expect) + list(scenario.per_turn_expect)
                    eval_results = await self._run_evaluators(all_expects, eval_context)

                    turn_passed = (
                        not turn_error
                        and (bool(agent_text) or bool(agent_frames))
                        and all(r.passed for r in eval_results)
                    )
                    set_attrs(
                        turn_span,
                        {
                            ATTR_AGENT_TEXT: agent_text,
                            ATTR_TURN_PASSED: turn_passed,
                            "voicecheck.turn.error": turn_error,
                            "voicecheck.turn.tool_call_count": len(tool_calls),
                        },
                    )

                    turn_result = TurnResult(
                        turn_index=i,
                        user_text=user_text,
                        agent_text=agent_text,
                        user_audio=user_frames,
                        agent_audio=agent_frames,
                        metrics=transport.metrics,
                        eval_results=eval_results,
                        error=turn_error,
                        tool_calls=tool_calls,
                    )
                    report.turns.append(turn_result)
                    if self.turn_callback:
                        await self.turn_callback(turn_result, i, len(flow_steps))

        finally:
            with span(
                SPAN_TRANSPORT_DISCONNECT, attrs={ATTR_TRANSPORT_TYPE: scenario.transport.type}
            ):
                await transport.disconnect()

        # Post-conversation evaluation (uses LLM, skip if --skip-llm-judge)
        if (
            scenario.conversation_eval
            and scenario.conversation_eval.criteria
            and not self.skip_llm_judge
        ):
            logger.info("Running post-conversation evaluation...")
            engine_eval = EngineEvalConfig(
                criteria=scenario.conversation_eval.criteria,
                min_score=scenario.conversation_eval.min_score,
                model=scenario.conversation_eval.model,
            )
            with span(
                SPAN_CONVERSATION_EVAL,
                attrs={
                    "voicecheck.conversation_eval.criteria_count": len(engine_eval.criteria),
                    "voicecheck.conversation_eval.model": engine_eval.model,
                    "voicecheck.conversation_eval.min_score": engine_eval.min_score,
                },
            ) as conv_span:
                conv_result = await engine.evaluate_conversation(conversation, engine_eval)
                set_attrs(
                    conv_span,
                    {
                        "voicecheck.conversation_eval.score": conv_result.get("overall_score", 0.0),
                        "voicecheck.conversation_eval.passed": conv_result.get(
                            "overall_passed", False
                        ),
                        "voicecheck.conversation_eval.reason": conv_result.get(
                            "overall_reason", ""
                        ),
                    },
                )
            report.conversation_eval = conv_result
            logger.info(
                "Conversation eval: score=%.2f passed=%s reason=%s",
                conv_result.get("overall_score", 0),
                conv_result.get("overall_passed", False),
                conv_result.get("overall_reason", "")[:80],
            )

        logger.info(
            "Guided scenario %r complete: %d steps, %d/%d passed",
            scenario.name,
            len(report.turns),
            report.passed_turns,
            report.total_turns,
        )
        return report

    async def _run_evaluators(
        self, expects: list[ExpectConfig], context: EvalContext
    ) -> list[EvalResult]:
        """Run a list of evaluators and return results."""
        results: list[EvalResult] = []
        for expect in expects:
            if self.skip_llm_judge and expect.type in _LLM_EVALUATOR_TYPES:
                logger.info("  [SKIP] %s: skipped (--skip-llm-judge)", expect.type)
                continue
            with span(
                SPAN_EVALUATOR,
                attrs={ATTR_EVAL_TYPE: expect.type},
            ) as eval_span:
                try:
                    evaluator_cls = get_evaluator(expect.type)
                    kwargs = expect.model_dump(exclude={"type"})
                    evaluator = evaluator_cls(**kwargs)
                    result = await evaluator.evaluate(context)
                except Exception as e:
                    logger.error("Evaluator %r crashed: %s", expect.type, e)
                    result = EvalResult(
                        evaluator_type=expect.type,
                        passed=False,
                        score=0.0,
                        reason=f"Evaluator error: {e}",
                    )
                set_attrs(
                    eval_span,
                    {
                        ATTR_EVAL_PASSED: result.passed,
                        ATTR_EVAL_SCORE: result.score,
                        ATTR_EVAL_REASON: result.reason,
                    },
                )
            results.append(result)

            status = "PASS" if result.passed else "FAIL"
            logger.info(
                "  [%s] %s: %s (score=%.2f)",
                status,
                result.evaluator_type,
                result.reason[:80],
                result.score,
            )
        return results
