"""Core data types for VoiceCheck."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AudioFrame:
    """A single audio frame (PCM data)."""

    data: bytes
    sample_rate: int = 16000
    num_channels: int = 1
    samples_per_channel: int = 0

    def __post_init__(self) -> None:
        if self.samples_per_channel == 0 and self.data:
            # 16-bit PCM: 2 bytes per sample
            self.samples_per_channel = len(self.data) // (2 * self.num_channels)

    @property
    def duration_s(self) -> float:
        """Duration of this frame in seconds."""
        if self.sample_rate == 0:
            return 0.0
        return self.samples_per_channel / self.sample_rate


@dataclass
class TranscriptSegment:
    """A transcribed segment of audio."""

    text: str
    start_time: float = 0.0
    end_time: float = 0.0
    confidence: float = 1.0


@dataclass
class TransportMetrics:
    """Timing metrics collected during a transport session.

    Timeline per turn:
      TTS synth → send audio → [first_byte] agent speaks → [last_byte] → STT transcribe
    """

    # When audio was first sent to the agent
    send_start_ts: float = 0.0
    # When we finished sending audio
    send_end_ts: float = 0.0
    # When we received the first sustained speech from the agent
    first_byte_ts: float = 0.0
    # When we received the last audio frame from the agent
    last_byte_ts: float = 0.0

    # Set by scenario runner (wraps TTS/STT with Timer)
    tts_duration_ms: float = 0.0
    stt_duration_ms: float = 0.0
    user_audio_duration_ms: float = 0.0

    # Set by transport receive loop
    agent_audio_duration_ms: float = 0.0
    agent_audio_frames: int = 0

    @property
    def first_byte_ms(self) -> float:
        """Time from end of user speech to first agent audio (ms)."""
        if not self.send_end_ts or not self.first_byte_ts:
            return 0.0
        return (self.first_byte_ts - self.send_end_ts) * 1000

    @property
    def total_ms(self) -> float:
        """Time from end of user speech to last agent audio (ms)."""
        if not self.send_end_ts or not self.last_byte_ts:
            return 0.0
        return (self.last_byte_ts - self.send_end_ts) * 1000

    @property
    def send_duration_ms(self) -> float:
        """Time spent sending user audio frames to transport."""
        if not self.send_start_ts or not self.send_end_ts:
            return 0.0
        return (self.send_end_ts - self.send_start_ts) * 1000


@dataclass
class ToolCallEvent:
    """A tool/function call observed during a turn.

    Populated by transports that surface agent tool calls over their
    control channel (VAPI ``function-call`` messages, Retell
    ``tool_call_invocation``, custom websocket events, etc.). Used by
    tool-aware evaluators (e.g. assert tool X was called with args Y)
    and emitted as span events so traces show the tool timeline.

    ``call_id`` is the provider's invocation id (when present) — used
    internally by transports to match a result event to the matching
    invocation. Evaluators should ignore it.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str = ""
    timestamp: float = field(default_factory=time.monotonic)
    call_id: str = ""


@dataclass
class TurnResult:
    """Result of a single conversation turn."""

    turn_index: int
    user_text: str
    agent_text: str = ""
    user_audio: list[AudioFrame] = field(default_factory=list)
    agent_audio: list[AudioFrame] = field(default_factory=list)
    metrics: TransportMetrics = field(default_factory=TransportMetrics)
    eval_results: list[EvalResult] = field(default_factory=list)
    tool_calls: list[ToolCallEvent] = field(default_factory=list)
    # Populated when the turn crashed (persona LLM failure, transport error,
    # STT timeout, etc.). Non-empty means `passed` returns False regardless
    # of what individual evaluators reported — a crashed turn cannot pass.
    error: str = ""

    @property
    def passed(self) -> bool:
        """True if all evaluators passed for this turn.

        Hard fails (cannot pass regardless of evaluator results):
          - The turn crashed during execution (``error`` populated).
          - The agent returned no text AND no audio — every evaluator would
            be scoring against nothing, so this prevents silent false passes.
        """
        if self.error:
            return False
        if not self.agent_text and not self.agent_audio:
            return False
        return all(r.passed for r in self.eval_results)


@dataclass
class EvalResult:
    """Result from a single evaluator."""

    evaluator_type: str
    passed: bool
    score: float = 1.0
    reason: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class EvalContext:
    """Context passed to evaluators for scoring."""

    user_text: str
    agent_text: str
    agent_audio: list[AudioFrame]
    metrics: TransportMetrics
    turn_index: int
    scenario_name: str = ""
    # Full conversation history up to this point
    conversation: list[dict] = field(default_factory=list)
    # Transport/runner metadata for this turn (e.g., interrupt info, silence)
    turn_metadata: dict = field(default_factory=dict)
    # Tool/function calls surfaced by the transport during this turn
    tool_calls: list[ToolCallEvent] = field(default_factory=list)


class Timer:
    """Simple context-manager timer for measuring durations."""

    def __init__(self) -> None:
        self.start: float = 0.0
        self.end: float = 0.0

    def __enter__(self) -> Timer:
        self.start = time.monotonic()
        return self

    def __exit__(self, *args: object) -> None:
        self.end = time.monotonic()

    @property
    def elapsed_ms(self) -> float:
        return (self.end - self.start) * 1000
