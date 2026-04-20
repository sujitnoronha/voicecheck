"""LiveKit transport — connects to a voice agent via LiveKit room.

Supports three connection modes:
- direct: Generate token with RoomAgentDispatch using api_key/api_secret
- token_server: Call an external token endpoint to get a token
- token: Use a pre-made token
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from voicecheck.audio.utils import is_silent
from voicecheck.core.transport import Transport, register_transport
from voicecheck.core.types import AudioFrame, TransportMetrics

logger = logging.getLogger("voicecheck.transports.livekit")


class LiveKitTransport(Transport):
    """LiveKit transport using the livekit.rtc SDK."""

    def __init__(self) -> None:
        self._room: Any = None
        self._audio_source: Any = None
        self._audio_track: Any = None
        self._agent_audio_frames: list[AudioFrame] = []
        self._metrics = TransportMetrics()
        self._agent_track: Any = None
        self._agent_participant_ready = asyncio.Event()
        self._config: dict = {}

    async def connect(self, config: dict) -> None:
        """Connect to a LiveKit room.

        Config keys depend on mode:
          direct (default): url, api_key, api_secret, agent_name, agent_metadata
          token_server: token_url, token_request, token_headers, response_mapping
          token: url, token
        """
        try:
            from livekit import rtc
        except ImportError:
            raise ImportError("LiveKit SDK not installed. Run: pip install voicecheck[livekit]")

        self._config = config
        self._rtc = rtc
        mode = config.get("mode", "direct")

        url, token = await self._resolve_connection(mode, config)

        self._room = rtc.Room()

        # Listen for agent participant joining the room
        @self._room.on("participant_connected")
        def _on_participant_connected(participant: Any) -> None:
            if participant.identity != "voicecheck-tester":
                logger.info("Agent participant joined: %s", participant.identity)
                self._agent_participant_ready.set()

        # Listen for agent audio track subscriptions
        @self._room.on("track_subscribed")
        def _on_track_subscribed(track: Any, publication: Any, participant: Any) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                logger.info("Agent audio track subscribed from %s", participant.identity)
                self._agent_track = track

        logger.info("Connecting to LiveKit room at %s (mode=%s)", url, mode)
        await self._room.connect(url, token)
        logger.info("Connected to room: %s", self._room.name)

        # Check if agent is already in the room (race-condition-safe)
        for p in self._room.remote_participants.values():
            logger.info("Found existing participant: %s", p.identity)
            self._agent_participant_ready.set()
            for pub in p.track_publications.values():
                if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                    self._agent_track = pub.track
            break

        # Publish our audio track (simulated user mic)
        sample_rate = config.get("sample_rate", 16000)
        num_channels = config.get("num_channels", 1)
        self._audio_source = rtc.AudioSource(sample_rate, num_channels)
        self._audio_track = rtc.LocalAudioTrack.create_audio_track(
            "voicecheck-mic", self._audio_source
        )
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        await self._room.local_participant.publish_track(self._audio_track, options)
        logger.info("Published audio track (sample_rate=%d)", sample_rate)

        # Wait for agent participant to be present before sending audio
        agent_timeout = config.get("agent_connect_timeout", 15.0)
        try:
            await asyncio.wait_for(self._agent_participant_ready.wait(), timeout=agent_timeout)
            logger.info("Agent is present in the room")
        except asyncio.TimeoutError:
            logger.warning("Agent did not join within %.0fs — proceeding anyway", agent_timeout)

    async def send_audio(self, frames: list[AudioFrame]) -> None:
        """Send audio frames to the room (simulated user speech)."""
        if not self._audio_source:
            raise RuntimeError("Not connected. Call connect() first.")

        rtc = self._rtc
        self._metrics.send_start_ts = time.monotonic()

        for frame in frames:
            lk_frame = rtc.AudioFrame(
                data=frame.data,
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
                samples_per_channel=frame.samples_per_channel,
            )
            await self._audio_source.capture_frame(lk_frame)

        await self._audio_source.wait_for_playout()
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

        Waits for the agent to start speaking, then captures until silence
        (no audio for silence_threshold seconds) or timeout.

        Raises RuntimeError if no agent audio track is available after timeout.
        """
        rtc = self._rtc
        self._agent_audio_frames = []

        # Get agent audio track — may already be available or wait for it
        agent_track = await self._get_agent_track(timeout)
        if agent_track is None:
            raise RuntimeError(
                f"Agent did not publish an audio track within {timeout}s. "
                "Check that the agent is running and subscribed to the room."
            )

        audio_stream = rtc.AudioStream(
            agent_track,
            sample_rate=self._config.get("sample_rate", 16000),
            num_channels=self._config.get("num_channels", 1),
        )

        last_audio_ts = time.monotonic()
        first_frame = True
        consecutive_speech = 0
        first_speech_ts = 0.0  # timestamp of the first frame in a speech run
        start_ts = time.monotonic()

        # Require multiple consecutive non-silent frames before counting as
        # real speech.  This filters codec warmup noise and isolated spikes
        # that would otherwise produce a misleadingly low first_byte_ms.
        speech_confirm_frames = 3  # ~60ms at 20ms/frame

        try:
            async for frame_event in audio_stream:
                now = time.monotonic()

                # Check overall timeout
                if now - start_ts > timeout:
                    logger.info("Receive timeout reached (%.1fs)", timeout)
                    break

                frame = frame_event.frame
                # Skip silent frames using RMS energy detection
                if is_silent(frame.data):
                    consecutive_speech = 0
                    first_speech_ts = 0.0
                    if not first_frame and (now - last_audio_ts) > silence_threshold:
                        logger.info(
                            "Silence detected (%.1fs), ending capture",
                            silence_threshold,
                        )
                        break
                    continue

                # Track consecutive non-silent frames for speech confirmation
                consecutive_speech += 1
                if consecutive_speech == 1:
                    first_speech_ts = now

                if first_frame and consecutive_speech >= speech_confirm_frames:
                    # Use the timestamp of the first frame in this speech run
                    self._metrics.first_byte_ts = first_speech_ts
                    first_frame = False
                    logger.info(
                        "Agent speech confirmed (%.0fms after send, %d frames)",
                        (first_speech_ts - self._metrics.send_end_ts) * 1000,
                        consecutive_speech,
                    )

                last_audio_ts = now
                self._metrics.last_byte_ts = now

                vc_frame = AudioFrame(
                    data=bytes(frame.data),
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                    samples_per_channel=frame.samples_per_channel,
                )
                self._agent_audio_frames.append(vc_frame)
                self._metrics.agent_audio_frames += 1
                self._metrics.agent_audio_duration_ms += vc_frame.duration_s * 1000

        finally:
            await audio_stream.aclose()

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
        """Disconnect from the LiveKit room."""
        if self._audio_source:
            await self._audio_source.aclose()
            self._audio_source = None
        if self._room:
            await self._room.disconnect()
            self._room = None
        logger.info("Disconnected from LiveKit room")

    @property
    def metrics(self) -> TransportMetrics:
        return self._metrics

    def reset_metrics(self) -> None:
        """Reset metrics for a new turn.

        NOTE: We do NOT clear _agent_track — the track persists across turns
        in the same room connection. Only timing metrics and captured frames
        are reset.
        """
        self._metrics = TransportMetrics()
        self._agent_audio_frames = []

    def validate_config(self, config: dict) -> list[str]:
        """Validate LiveKit transport config."""
        errors: list[str] = []
        mode = config.get("mode", "direct")

        if mode == "direct":
            for key in ("url", "api_key", "api_secret"):
                val = config.get(key, "")
                if not val or val.startswith("${"):
                    errors.append(
                        f"'{key}' is missing or has unexpanded env var: {val!r}. "
                        f"Set the corresponding environment variable."
                    )
            agent_name = config.get("agent_name", "")
            if agent_name.startswith("${"):
                errors.append(
                    f"'agent_name' has unexpanded env var: {agent_name!r}. "
                    f"Set the corresponding environment variable, or remove it "
                    f"to use automatic agent dispatch."
                )
        elif mode == "token_server":
            if not config.get("token_url"):
                errors.append("'token_url' is required for token_server mode.")
        elif mode == "token":
            for key in ("url", "token"):
                if not config.get(key):
                    errors.append(f"'{key}' is required for token mode.")

        return errors

    # ── Private helpers ──────────────────────────────────────────────

    async def _get_agent_track(self, timeout: float) -> Any:
        """Get the agent's audio track, waiting if necessary.

        The track persists across turns, so after the first turn we
        already have it and return immediately.
        """
        if self._agent_track is not None:
            return self._agent_track

        # Wait for a new track subscription
        track_ready = asyncio.Event()

        def _on_new_track(track: Any, pub: Any, participant: Any) -> None:
            rtc = self._rtc
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                self._agent_track = track
                track_ready.set()

        self._room.on("track_subscribed", _on_new_track)

        try:
            await asyncio.wait_for(track_ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            try:
                self._room.off("track_subscribed", _on_new_track)
            except Exception:
                pass

        return self._agent_track

    async def _resolve_connection(self, mode: str, config: dict) -> tuple[str, str]:
        """Resolve URL and token based on connection mode."""
        if mode == "direct":
            return self._direct_token(config)
        elif mode == "token_server":
            return await self._token_server(config)
        elif mode == "token":
            return config["url"], config["token"]
        else:
            raise ValueError(f"Unknown LiveKit mode: {mode!r}. Use direct|token_server|token")

    def _direct_token(self, config: dict) -> tuple[str, str]:
        """Generate token internally using api_key/api_secret."""
        try:
            from livekit.api import (
                AccessToken,
                RoomAgentDispatch,
                RoomConfiguration,
                VideoGrants,
            )
        except ImportError:
            raise ImportError("livekit-api not installed. Run: pip install voicecheck[livekit]")

        url = config["url"]
        api_key = config["api_key"]
        api_secret = config["api_secret"]
        agent_name = config.get("agent_name", "")
        room_name = config.get("room_name", f"voicecheck-{int(time.time())}")

        # Build agent metadata from dict
        agent_metadata = config.get("agent_metadata", {})
        metadata_str = json.dumps(agent_metadata) if agent_metadata else ""

        token_builder = (
            AccessToken(api_key, api_secret)
            .with_identity("voicecheck-tester")
            .with_name("VoiceCheck Tester")
            .with_grants(
                VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                )
            )
        )

        if agent_name:
            token_builder = token_builder.with_room_config(
                RoomConfiguration(
                    agents=[
                        RoomAgentDispatch(
                            agent_name=agent_name,
                            metadata=metadata_str,
                        )
                    ]
                )
            )
            logger.info(
                "Generated token (room=%s, agent=%s, metadata_keys=%s)",
                room_name,
                agent_name,
                list(agent_metadata.keys()),
            )
        else:
            logger.info(
                "Generated token (room=%s, auto-dispatch, metadata_keys=%s)",
                room_name,
                list(agent_metadata.keys()),
            )

        token = token_builder.to_jwt()
        return url, token

    async def _token_server(self, config: dict) -> tuple[str, str]:
        """Call an external token server to get a token."""
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx not installed. Run: pip install httpx")

        token_url = config["token_url"]
        request_body = config.get("token_request", {})
        headers = config.get("token_headers", {})
        mapping = config.get("response_mapping", {})
        request_timeout = config.get("token_timeout", 15.0)

        url_field = mapping.get("url_field", "server_url")
        token_field = mapping.get("token_field", "participant_token")

        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                resp = await client.post(token_url, json=request_body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            raise ConnectionError(f"Token server timed out after {request_timeout}s: {token_url}")
        except httpx.HTTPStatusError as e:
            raise ConnectionError(
                f"Token server returned HTTP {e.response.status_code}: {token_url}"
            )

        url = data[url_field]
        token = data[token_field]
        logger.info("Got token from server %s (room=%s)", token_url, data.get("room_name", "?"))
        return url, token


# Register this transport
register_transport("livekit", LiveKitTransport)
