"""Echo transport — a built-in dev transport with no external dependencies.

The echo transport lets you exercise the scenario pipeline without a live
voice agent. It ignores the audio you send and returns a configurable
agent response after a configurable delay.

Use cases:
- Validating YAML schema end-to-end without needing a real agent deployed.
- Testing custom evaluators against predictable agent output.
- Smoke-testing CI changes without burning transport minutes or tokens.

YAML::

    transport:
      type: echo
      config:
        # Text the "agent" will say back. Synthesized via the scenario's
        # configured TTS provider, then transcribed by the configured STT.
        # If empty, the agent produces silence (useful for timing-only tests).
        response_text: "I am the echo bot. You said something."

        # Simulated latency before the first agent byte arrives (ms).
        first_byte_delay_ms: 1500

        # Whether to use the configured TTS to synthesize real audio for
        # `response_text`. If False, emit silent PCM of matching duration.
        # Default True when response_text is set.
        synthesize: true

Echo transport is intentionally minimal. It won't catch transport bugs
because there is no transport layer — but it will catch YAML schema issues,
evaluator bugs, and CI plumbing problems before you spend a dollar on
OpenAI tokens or LiveKit minutes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from voicecheck.core.transport import Transport, register_transport
from voicecheck.core.types import AudioFrame, TransportMetrics

logger = logging.getLogger("voicecheck.transports.echo")

_DEFAULT_SAMPLE_RATE = 16000
_DEFAULT_FIRST_BYTE_DELAY_MS = 1500
_DEFAULT_RESPONSE_TEXT = "I am the echo transport. Agent response goes here."


class EchoTransport(Transport):
    """Built-in dev transport — no network, no dependencies.

    Produces a configurable agent response (either TTS-synthesized real
    audio or silent PCM) after a configurable delay. Lets developers
    exercise the scenario runner end-to-end before wiring up a real agent.
    """

    def __init__(self) -> None:
        self._metrics = TransportMetrics()
        self._config: dict[str, Any] = {}
        self._response_text: str = _DEFAULT_RESPONSE_TEXT
        self._first_byte_delay_s: float = _DEFAULT_FIRST_BYTE_DELAY_MS / 1000.0
        self._synthesize: bool = True
        self._sample_rate: int = _DEFAULT_SAMPLE_RATE

    # ── Transport interface ──────────────────────────────────────────

    async def connect(self, config: dict) -> None:
        self._config = config
        self._response_text = str(config.get("response_text", _DEFAULT_RESPONSE_TEXT))
        self._first_byte_delay_s = (
            float(config.get("first_byte_delay_ms", _DEFAULT_FIRST_BYTE_DELAY_MS)) / 1000.0
        )
        self._synthesize = bool(config.get("synthesize", bool(self._response_text)))
        self._sample_rate = int(config.get("sample_rate", _DEFAULT_SAMPLE_RATE))
        logger.info(
            "echo transport ready (text=%r, first_byte=%.1fs, synthesize=%s)",
            self._response_text[:40],
            self._first_byte_delay_s,
            self._synthesize,
        )

    async def send_audio(self, frames: list[AudioFrame]) -> None:
        # The echo transport ignores what you send — it just records when
        # sending finished so latency math works out.
        if self._metrics.send_start_ts == 0.0:
            self._metrics.send_start_ts = time.monotonic()
        self._metrics.send_end_ts = time.monotonic()

    async def receive_audio(
        self,
        timeout: float = 10.0,
        silence_threshold: float = 1.5,
    ) -> list[AudioFrame]:
        # Simulate first-byte latency.
        await asyncio.sleep(self._first_byte_delay_s)
        self._metrics.first_byte_ts = time.monotonic()

        frames = await self._build_response_frames()
        self._metrics.last_byte_ts = time.monotonic()
        self._metrics.agent_audio_duration_ms = sum(f.duration_s for f in frames) * 1000
        self._metrics.agent_audio_frames = len(frames)
        return frames

    async def disconnect(self) -> None:
        # Nothing to clean up.
        pass

    @property
    def metrics(self) -> TransportMetrics:
        return self._metrics

    def reset_metrics(self) -> None:
        self._metrics = TransportMetrics()

    def validate_config(self, config: dict) -> list[str]:
        errors: list[str] = []
        first_byte = config.get("first_byte_delay_ms", _DEFAULT_FIRST_BYTE_DELAY_MS)
        try:
            float(first_byte)
        except (TypeError, ValueError):
            errors.append(f"first_byte_delay_ms must be a number, got {first_byte!r}")
        if "response_text" in config and not isinstance(config["response_text"], str):
            errors.append("response_text must be a string")
        return errors

    # ── Internals ──────────────────────────────────────────────────

    async def _build_response_frames(self) -> list[AudioFrame]:
        """Produce the agent audio. Either TTS-synthesized or silence."""
        if self._synthesize and self._response_text:
            try:
                return await self._synthesize_response()
            except Exception as e:
                # Fall back to silence so a missing TTS extra doesn't break the
                # dev loop — the whole point of echo is to Just Work.
                logger.warning(
                    "echo: TTS synthesis failed (%s), falling back to silence",
                    e,
                )
        return self._silent_response()

    async def _synthesize_response(self) -> list[AudioFrame]:
        """Use voicecheck's TTS layer to synthesize ``response_text``."""
        from voicecheck.audio.tts import get_tts_provider

        tts = get_tts_provider("edge", sample_rate=self._sample_rate)
        return await tts.synthesize(self._response_text)

    def _silent_response(self, duration_s: float = 2.0) -> list[AudioFrame]:
        """Fallback: emit ``duration_s`` seconds of silent PCM frames."""
        from voicecheck.audio.utils import generate_silence

        return generate_silence(
            duration_s=duration_s,
            sample_rate=self._sample_rate,
            num_channels=1,
        )


register_transport("echo", EchoTransport)
