"""Abstract transport base class for voice agent connections."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from voicecheck.core.types import AudioFrame, ToolCallEvent, TransportMetrics


class Transport(ABC):
    """Pluggable transport for connecting to a voice agent.

    Implementations handle the specifics of each platform (LiveKit, Pipecat, VAPI)
    while exposing a uniform interface for sending/receiving audio.

    Subclasses that observe tool/function-call events on their control
    channel should call :meth:`emit_tool_call` so the runner can attach
    them to the current turn (and the OTel span tree).
    """

    def __init__(self) -> None:
        # Tool calls observed since the last reset_metrics() call. The
        # runner reads + clears this each turn.
        self._tool_calls: list[ToolCallEvent] = []

    @abstractmethod
    async def connect(self, config: dict) -> None:
        """Connect to the voice agent.

        Args:
            config: Transport-specific configuration dict from YAML scenario.
        """

    @abstractmethod
    async def send_audio(self, frames: list[AudioFrame]) -> None:
        """Send audio frames to the agent (simulated user speech).

        Args:
            frames: List of PCM audio frames to publish.
        """

    @abstractmethod
    async def receive_audio(
        self, timeout: float = 10.0, silence_threshold: float = 1.5
    ) -> list[AudioFrame]:
        """Receive audio frames from the agent's response.

        Captures audio until silence is detected (no audio for `silence_threshold` seconds)
        or `timeout` is reached.

        Args:
            timeout: Maximum seconds to wait for agent audio.
            silence_threshold: Seconds of silence before considering agent done speaking.

        Returns:
            List of captured audio frames from the agent.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the voice agent and clean up resources."""

    @property
    @abstractmethod
    def metrics(self) -> TransportMetrics:
        """Timing metrics from the current/last turn."""

    def reset_metrics(self) -> None:
        """Reset metrics for a new turn. Override if needed.

        Subclasses overriding this method should ``super().reset_metrics()``
        (or clear ``self._tool_calls``) to keep tool-call buffering correct.
        """
        self._tool_calls = []

    def emit_tool_call(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        result: Any = None,
        error: str = "",
        call_id: str = "",
    ) -> None:
        """Record a tool/function call observed on the control channel.

        Buffers the event for inclusion on the current TurnResult and
        emits it as a span event under the active OTel span. Safe to
        call whether or not observability is configured.

        ``call_id`` is the provider's invocation id (e.g. VAPI's
        ``toolCalls[].id`` or Retell's ``tool_call_id``). Pass it when
        you need a later result event to find this row.
        """
        event = ToolCallEvent(
            name=name,
            args=args or {},
            result=result,
            error=error,
            call_id=call_id,
        )
        self._tool_calls.append(event)
        try:
            from voicecheck.observability import record_tool_call_event

            record_tool_call_event(
                name=name,
                args=event.args,
                result=result,
                error=error or None,
            )
        except Exception:
            # Observability is best-effort. A telemetry failure must
            # never break a transport.
            pass

    def take_tool_calls(self) -> list[ToolCallEvent]:
        """Drain and return tool calls observed since the last drain."""
        events = self._tool_calls
        self._tool_calls = []
        return events

    def validate_config(self, config: dict) -> list[str]:
        """Validate transport-specific config before running.

        Each transport subclass should override this to check for required
        keys (API keys, URLs, etc.) and return a list of error strings.
        An empty list means config is valid.

        Args:
            config: The merged transport config dict (includes mode, sample_rate, etc.).

        Returns:
            List of validation error strings (empty if valid).
        """
        return []


# Transport registry for dynamic lookup from YAML config
_TRANSPORT_REGISTRY: dict[str, type[Transport]] = {}


def register_transport(name: str, transport_cls: type[Transport]) -> None:
    """Register a transport class by name."""
    _TRANSPORT_REGISTRY[name] = transport_cls


def get_transport(name: str) -> type[Transport]:
    """Get a registered transport class by name.

    Raises:
        ValueError: If transport name is not registered.
    """
    if name not in _TRANSPORT_REGISTRY:
        available = ", ".join(_TRANSPORT_REGISTRY.keys()) or "(none)"
        raise ValueError(
            f"Unknown transport: {name!r}. Available: {available}. "
            f"Install the corresponding extra (e.g., pip install voicecheck[livekit])."
        )
    return _TRANSPORT_REGISTRY[name]
