"""Base transport for providers that stream audio over WebSocket.

VAPI, Retell, and Telephony all share the same pattern:
  1. Open a WebSocket connection
  2. Send/receive binary audio frames
  3. Detect silence to determine when agent stops speaking
  4. Collect timing metrics

Subclasses implement provider-specific hooks for URL construction,
authentication, and audio encoding/decoding.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import abstractmethod
from typing import Any

from voicecheck.audio.utils import is_silent
from voicecheck.core.transport import Transport
from voicecheck.core.types import AudioFrame, TransportMetrics

logger = logging.getLogger("voicecheck.transports.websocket_base")

# Number of consecutive non-silent frames before confirming speech.
# Filters codec warmup noise and isolated spikes (~60ms at 20ms/frame).
_SPEECH_CONFIRM_FRAMES = 3


class WebSocketTransport(Transport):
    """Base transport for providers that stream audio over WebSocket.

    Subclasses must implement the abstract hooks below to customize
    URL construction, auth, and audio format conversion.
    """

    def __init__(self) -> None:
        self._ws: Any = None
        self._metrics = TransportMetrics()
        self._agent_audio_frames: list[AudioFrame] = []
        self._config: dict = {}
        self._receive_task: asyncio.Task | None = None
        self._audio_buffer: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._connected = asyncio.Event()

    # ── Abstract hooks for subclasses ────────────────────────────────

    @abstractmethod
    async def _get_ws_url(self, config: dict) -> str:
        """Return the WebSocket URL to connect to.

        May involve REST API calls (e.g., creating a VAPI call).

        Args:
            config: Transport-specific config dict from YAML scenario.

        Returns:
            WebSocket URL string.
        """

    @abstractmethod
    async def _on_ws_connected(self, ws: Any, config: dict) -> None:
        """Called after WebSocket connection is established.

        Use for sending auth messages, config payloads, etc.

        Args:
            ws: The websocket connection object.
            config: Transport config dict.
        """

    @abstractmethod
    def _encode_outbound_frame(self, frame: AudioFrame) -> bytes:
        """Encode an AudioFrame for sending over the WebSocket.

        May involve PCM-to-mulaw conversion, base64 encoding,
        JSON wrapping, etc. depending on the provider.

        Args:
            frame: PCM audio frame to encode.

        Returns:
            Bytes to send over the WebSocket.
        """

    @abstractmethod
    def _decode_inbound_message(self, message: bytes | str) -> bytes | None:
        """Decode an inbound WebSocket message to raw PCM bytes.

        May involve JSON parsing, base64 decoding, mulaw-to-PCM
        conversion, resampling, etc. Return None to skip non-audio messages
        (e.g., control messages, metadata).

        Args:
            message: Raw message received from the WebSocket.

        Returns:
            PCM audio bytes, or None if the message is not audio.
        """

    @abstractmethod
    async def _on_ws_disconnecting(self, ws: Any, config: dict) -> None:
        """Called before the WebSocket connection is closed.

        Use for sending goodbye messages, ending calls via REST API, etc.

        Args:
            ws: The websocket connection object.
            config: Transport config dict.
        """

    # ── Optional hooks ───────────────────────────────────────────────

    def _get_sample_rate(self) -> int:
        """Return the sample rate for audio frames. Override if provider differs."""
        return self._config.get("sample_rate", 16000)

    def _get_num_channels(self) -> int:
        """Return the number of audio channels. Override if provider differs."""
        return self._config.get("num_channels", 1)

    def _get_silence_threshold(self) -> int:
        """Return the RMS silence threshold. Override for noisy transports."""
        return self._config.get("silence_rms_threshold", 500)

    # ── Transport interface implementation ───────────────────────────

    async def connect(self, config: dict) -> None:
        """Connect to the provider via WebSocket.

        Args:
            config: Transport-specific configuration dict from YAML scenario.
        """
        try:
            import websockets
        except ImportError as exc:
            raise ImportError(
                "websockets not installed. Run: pip install websockets"
            ) from exc

        self._config = config
        ws_url = await self._get_ws_url(config)

        logger.info("Connecting to WebSocket at %s", ws_url)
        self._ws = await websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        )
        logger.info("WebSocket connected")

        await self._on_ws_connected(self._ws, config)

        # Start background task to receive messages and buffer audio
        self._receive_task = asyncio.create_task(self._receive_loop())
        self._connected.set()

    async def send_audio(self, frames: list[AudioFrame]) -> None:
        """Send audio frames to the agent via WebSocket."""
        if not self._ws:
            raise RuntimeError("Not connected. Call connect() first.")

        self._metrics.send_start_ts = time.monotonic()

        for frame in frames:
            encoded = self._encode_outbound_frame(frame)
            await self._ws.send(encoded)

        self._metrics.send_end_ts = time.monotonic()
        logger.info(
            "Sent %d audio frames (%.1fs total)",
            len(frames),
            sum(f.duration_s for f in frames),
        )

    async def receive_audio(
        self, timeout: float = 10.0, silence_threshold: float = 1.5
    ) -> list[AudioFrame]:
        """Capture audio frames from the agent's response.

        Listens to the audio buffer filled by the background receive loop.
        Waits for the agent to start speaking, then captures until silence
        (no audio for silence_threshold seconds) or timeout.
        """
        self._agent_audio_frames = []
        sample_rate = self._get_sample_rate()
        num_channels = self._get_num_channels()
        rms_threshold = self._get_silence_threshold()

        last_audio_ts = time.monotonic()
        first_frame = True
        consecutive_speech = 0
        first_speech_ts = 0.0
        start_ts = time.monotonic()

        while True:
            now = time.monotonic()

            # Check overall timeout
            if now - start_ts > timeout:
                logger.info("Receive timeout reached (%.1fs)", timeout)
                break

            # Try to get audio from buffer with a short wait
            try:
                remaining = timeout - (now - start_ts)
                pcm_data = await asyncio.wait_for(
                    self._audio_buffer.get(), timeout=min(0.1, remaining)
                )
            except asyncio.TimeoutError:
                # No data available — check silence threshold
                if not first_frame and (time.monotonic() - last_audio_ts) > silence_threshold:
                    logger.info("Silence detected (%.1fs), ending capture", silence_threshold)
                    break
                continue

            if pcm_data is None:
                # Connection closed
                logger.info("WebSocket closed during receive")
                break

            # Check if this frame is silent
            if is_silent(pcm_data, threshold=rms_threshold):
                consecutive_speech = 0
                first_speech_ts = 0.0
                if not first_frame and (time.monotonic() - last_audio_ts) > silence_threshold:
                    logger.info("Silence detected (%.1fs), ending capture", silence_threshold)
                    break
                continue

            # Track consecutive non-silent frames for speech confirmation
            consecutive_speech += 1
            if consecutive_speech == 1:
                first_speech_ts = time.monotonic()

            if first_frame and consecutive_speech >= _SPEECH_CONFIRM_FRAMES:
                self._metrics.first_byte_ts = first_speech_ts
                first_frame = False
                logger.info(
                    "Agent speech confirmed (%.0fms after send, %d frames)",
                    (first_speech_ts - self._metrics.send_end_ts) * 1000,
                    consecutive_speech,
                )

            now = time.monotonic()
            last_audio_ts = now
            self._metrics.last_byte_ts = now

            # Calculate samples per channel from PCM data size
            samples_per_channel = len(pcm_data) // (2 * num_channels)

            vc_frame = AudioFrame(
                data=pcm_data,
                sample_rate=sample_rate,
                num_channels=num_channels,
                samples_per_channel=samples_per_channel,
            )
            self._agent_audio_frames.append(vc_frame)
            self._metrics.agent_audio_frames += 1
            self._metrics.agent_audio_duration_ms += vc_frame.duration_s * 1000

        if not self._agent_audio_frames:
            logger.warning(
                "No non-silent audio captured from agent (timeout=%.1fs). "
                "Agent may not have responded.",
                timeout,
            )

        logger.info(
            "Captured %d agent audio frames (first_byte=%.0fms, total=%.0fms, audio=%.1fs)",
            len(self._agent_audio_frames),
            self._metrics.first_byte_ms,
            self._metrics.total_ms,
            self._metrics.agent_audio_duration_ms / 1000,
        )
        return self._agent_audio_frames

    async def disconnect(self) -> None:
        """Disconnect the WebSocket and clean up."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._ws:
            try:
                await self._on_ws_disconnecting(self._ws, self._config)
            except Exception as e:
                logger.warning("Error during disconnect cleanup: %s", e)
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._connected.clear()
        logger.info("WebSocket disconnected")

    @property
    def metrics(self) -> TransportMetrics:
        return self._metrics

    def reset_metrics(self) -> None:
        """Reset metrics for a new turn."""
        self._metrics = TransportMetrics()
        self._agent_audio_frames = []
        # Drain any leftover audio from previous turn
        while not self._audio_buffer.empty():
            try:
                self._audio_buffer.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ── Internal ─────────────────────────────────────────────────────

    async def _receive_loop(self) -> None:
        """Background task that reads WebSocket messages and buffers audio."""
        try:
            async for message in self._ws:
                pcm_data = self._decode_inbound_message(message)
                if pcm_data is not None:
                    await self._audio_buffer.put(pcm_data)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning("WebSocket receive error: %s", e)
        finally:
            # Signal end of stream
            await self._audio_buffer.put(None)
