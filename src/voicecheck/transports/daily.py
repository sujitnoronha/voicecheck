"""Daily/Pipecat transport — connects to a voice agent via Daily WebRTC room.

Supports three connection modes:
- api_key: Create a room via Daily REST API, then join it with a generated token
- room_url: Join an existing room URL with an optional meeting token
- token: Use a pre-made meeting token to join a room
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from voicecheck.audio.utils import is_silent
from voicecheck.core.transport import Transport, register_transport
from voicecheck.core.types import AudioFrame, TransportMetrics

logger = logging.getLogger("voicecheck.transports.daily")


class DailyTransport(Transport):
    """Daily transport using the daily-python SDK.

    Connects to a Daily room where a Pipecat (or other Daily-based) voice
    agent is running.  Publishes user audio via a virtual microphone track
    and subscribes to the agent's audio track for capture and analysis.

    The daily-python SDK is lazily imported in connect() so this module can
    be loaded without the SDK installed (for registry discovery etc.).
    """

    def __init__(self) -> None:
        self._call_client: Any = None
        self._daily: Any = None
        self._agent_audio_frames: list[AudioFrame] = []
        self._metrics = TransportMetrics()
        self._agent_track: Any = None
        self._agent_participant_ready = asyncio.Event()
        self._config: dict = {}
        self._mic_device: Any = None
        self._speaker_device: Any = None
        self._room_url: str = ""
        self._meeting_token: str | None = None
        # Reference kept alive to prevent GC of the event handler
        self._event_handler: Any = None

    async def connect(self, config: dict) -> None:
        """Connect to a Daily room.

        Config keys depend on mode:
          api_key: api_key, room_name (creates room via REST API, then joins)
          room_url: room_url, meeting_token (optional)
          token: room_url, meeting_token (required)

        Common optional keys:
          agent_connect_timeout (float): Seconds to wait for agent. Default 15.
          sample_rate (int): Audio sample rate in Hz. Default 16000.
          num_channels (int): Number of audio channels. Default 1.
        """
        try:
            import daily
        except ImportError:
            raise ImportError(
                "Daily SDK not installed. Run: pip install voicecheck[daily]  "
                "(requires the 'daily-python' package)"
            )

        self._daily = daily
        self._config = config
        mode = config.get("mode", "room_url")

        room_url, meeting_token = await self._resolve_connection(mode, config)
        self._room_url = room_url
        self._meeting_token = meeting_token

        sample_rate = config.get("sample_rate", 16000)
        num_channels = config.get("num_channels", 1)

        # Initialize the Daily library (must be called before creating clients)
        await asyncio.to_thread(daily.Daily.init)

        # Create virtual microphone device for publishing user audio
        self._mic_device = daily.Daily.create_microphone_device(
            "voicecheck-mic",
            sample_rate=sample_rate,
            channels=num_channels,
        )

        # Create virtual speaker device for capturing agent audio
        self._speaker_device = daily.Daily.create_speaker_device(
            "voicecheck-speaker",
            sample_rate=sample_rate,
            channels=num_channels,
        )

        # Select the virtual speaker so CallClient routes received audio to it
        daily.Daily.select_speaker_device("voicecheck-speaker")

        # Build an event handler to track remote participants and their tracks
        event_handler = self._build_event_handler(daily)
        self._event_handler = event_handler  # prevent GC

        # Create the call client with our event handler
        self._call_client = daily.CallClient(event_handler=event_handler)

        # Subscribe to remote microphone tracks, ignore camera
        self._call_client.update_subscription_profiles({
            "base": {
                "camera": "unsubscribed",
                "microphone": "subscribed",
            }
        })

        # Join the room
        logger.info("Connecting to Daily room at %s (mode=%s)", room_url, mode)

        join_kwargs: dict[str, Any] = {
            "url": room_url,
            "client_settings": {
                "inputs": {
                    "camera": False,
                    "microphone": {
                        "isEnabled": True,
                        "settings": {
                            "deviceId": "voicecheck-mic",
                        },
                    },
                },
            },
        }
        if meeting_token:
            join_kwargs["meeting_token"] = meeting_token

        await asyncio.to_thread(self._call_client.join, **join_kwargs)
        logger.info("Connected to Daily room: %s", room_url)

        # Check if agent is already in the room (race-condition-safe).
        # participants() returns a dict keyed by participant ID.
        remote_participants = await asyncio.to_thread(
            self._call_client.participants
        )
        for pid, info in remote_participants.items():
            if pid == "local":
                continue
            user_name = info.get("info", {}).get("userName", pid)
            logger.info("Found existing participant: %s", user_name)
            self._agent_participant_ready.set()
            if info.get("info", {}).get("isPublishingMicrophone", False):
                self._agent_track = pid
            break

        # Wait for agent participant to be present before sending audio
        agent_timeout = config.get("agent_connect_timeout", 15.0)
        try:
            await asyncio.wait_for(
                self._agent_participant_ready.wait(), timeout=agent_timeout
            )
            logger.info("Agent is present in the room")
        except asyncio.TimeoutError:
            logger.warning(
                "Agent did not join within %.0fs — proceeding anyway", agent_timeout
            )

        logger.info("Published audio track (sample_rate=%d)", sample_rate)

    async def send_audio(self, frames: list[AudioFrame]) -> None:
        """Send audio frames to the room (simulated user speech).

        Writes PCM audio data to the virtual microphone device, which the
        Daily SDK publishes to the room as the local participant's mic track.

        Args:
            frames: List of PCM audio frames to send.

        Raises:
            RuntimeError: If not connected (connect() not called).
        """
        if not self._mic_device:
            raise RuntimeError("Not connected. Call connect() first.")

        self._metrics.send_start_ts = time.monotonic()

        for frame in frames:
            # VirtualMicrophoneDevice.write_frames() expects raw bytes of
            # 16-bit signed little-endian PCM audio.
            await asyncio.to_thread(
                self._mic_device.write_frames, frame.data
            )

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

        Uses RMS-based silence detection (is_silent) and requires multiple
        consecutive non-silent frames before confirming speech has started.
        This filters codec warmup noise and isolated spikes that would
        produce a misleadingly low first_byte_ms.

        Args:
            timeout: Maximum seconds to wait for agent audio.
            silence_threshold: Seconds of silence before considering agent
                done speaking.

        Returns:
            List of captured audio frames from the agent.

        Raises:
            RuntimeError: If no speaker device is available.
        """
        if not self._speaker_device:
            raise RuntimeError("Not connected. Call connect() first.")

        self._agent_audio_frames = []
        sample_rate = self._config.get("sample_rate", 16000)
        num_channels = self._config.get("num_channels", 1)

        last_audio_ts = time.monotonic()
        first_frame = True
        consecutive_speech = 0
        first_speech_ts = 0.0  # timestamp of the first frame in a speech run
        start_ts = time.monotonic()

        # Require multiple consecutive non-silent frames before counting as
        # real speech.  This filters codec warmup noise and isolated spikes
        # that would otherwise produce a misleadingly low first_byte_ms.
        speech_confirm_frames = 3  # ~60ms at 20ms/frame

        # Read frames from the virtual speaker device in a polling loop.
        # Daily's speaker device provides audio from all subscribed remote
        # participants mixed together.  We read fixed-size chunks (~20ms).
        frame_duration_s = 0.02  # 20ms frames
        samples_per_frame = int(sample_rate * frame_duration_s)

        try:
            while True:
                now = time.monotonic()

                # Check overall timeout
                if now - start_ts > timeout:
                    logger.info("Receive timeout reached (%.1fs)", timeout)
                    break

                # Read audio from the virtual speaker device (blocking call).
                # read_frames() returns raw PCM bytes for the requested number
                # of samples, or an empty/short buffer if nothing is available.
                raw_data = await asyncio.to_thread(
                    self._speaker_device.read_frames, samples_per_frame
                )

                if not raw_data or len(raw_data) == 0:
                    # No data available yet — short sleep to avoid busy-wait
                    await asyncio.sleep(frame_duration_s / 2)
                    continue

                now = time.monotonic()

                # Skip silent frames using RMS energy detection
                if is_silent(raw_data):
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
                    data=bytes(raw_data),
                    sample_rate=sample_rate,
                    num_channels=num_channels,
                    samples_per_channel=samples_per_frame,
                )
                self._agent_audio_frames.append(vc_frame)
                self._metrics.agent_audio_frames += 1
                self._metrics.agent_audio_duration_ms += vc_frame.duration_s * 1000

        except Exception:
            logger.exception("Error during audio capture")
            raise

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
        """Disconnect from the Daily room and release resources.

        Leaves the room, destroys the call client, and cleans up virtual
        audio devices.  Also calls Daily.deinit() to release native resources.
        """
        if self._call_client:
            try:
                await asyncio.to_thread(self._call_client.leave)
            except Exception:
                logger.debug("Error leaving room (may already be disconnected)")
            try:
                await asyncio.to_thread(self._call_client.release)
            except Exception:
                logger.debug("Error releasing call client")
            self._call_client = None

        self._mic_device = None
        self._speaker_device = None
        self._agent_track = None
        self._event_handler = None

        # Deinitialize Daily native library
        if self._daily:
            try:
                await asyncio.to_thread(self._daily.Daily.deinit)
            except Exception:
                logger.debug("Error during Daily deinit")

        logger.info("Disconnected from Daily room")

    @property
    def metrics(self) -> TransportMetrics:
        """Timing metrics from the current/last turn."""
        return self._metrics

    def reset_metrics(self) -> None:
        """Reset metrics for a new turn.

        NOTE: We do NOT clear _agent_track — the track persists across turns
        in the same room connection. Only timing metrics and captured frames
        are reset.
        """
        self._metrics = TransportMetrics()
        self._agent_audio_frames = []

    # ── Private helpers ──────────────────────────────────────────────

    def _build_event_handler(self, daily: Any) -> Any:
        """Create a Daily EventHandler subclass wired to our state.

        The daily-python SDK dispatches events to an EventHandler instance.
        We subclass it and override the participant lifecycle methods so we
        can detect when the agent joins and starts publishing audio.

        Args:
            daily: The imported daily module.

        Returns:
            An EventHandler instance ready to pass to CallClient().
        """
        transport = self  # capture for closure

        class _Handler(daily.EventHandler):
            """Handles Daily room events for VoiceCheck."""

            def on_participant_joined(self, participant: dict[str, Any]) -> None:
                """Called when a new participant joins the room."""
                pid = participant.get("id", "")
                info = participant.get("info", {})
                user_name = info.get("userName", pid)
                is_local = info.get("isLocal", False)

                if not is_local:
                    logger.info("Agent participant joined: %s", user_name)
                    transport._agent_participant_ready.set()
                    transport._agent_track = pid

            def on_participant_updated(self, participant: dict[str, Any]) -> None:
                """Called when a participant's state changes."""
                pid = participant.get("id", "")
                info = participant.get("info", {})
                is_local = info.get("isLocal", False)

                if not is_local:
                    is_publishing = info.get("isPublishingMicrophone", False)
                    if is_publishing and transport._agent_track is None:
                        user_name = info.get("userName", pid)
                        logger.info(
                            "Agent audio track available from %s", user_name
                        )
                        transport._agent_track = pid

            def on_participant_left(self, participant: dict[str, Any], reason: str) -> None:
                """Called when a participant leaves the room."""
                pid = participant.get("id", "")
                info = participant.get("info", {})
                is_local = info.get("isLocal", False)

                if not is_local:
                    user_name = info.get("userName", pid)
                    logger.info(
                        "Participant left: %s (reason=%s)", user_name, reason
                    )
                    if transport._agent_track == pid:
                        transport._agent_track = None

        return _Handler()

    async def _resolve_connection(
        self, mode: str, config: dict
    ) -> tuple[str, str | None]:
        """Resolve room URL and meeting token based on connection mode.

        Args:
            mode: Connection mode -- 'api_key', 'room_url', or 'token'.
            config: Transport configuration dict from YAML.

        Returns:
            Tuple of (room_url, meeting_token). Token may be None for
            room_url mode without authentication.

        Raises:
            ValueError: If mode is unknown.
            KeyError: If required config keys are missing.
        """
        if mode == "api_key":
            return await self._create_room_with_api_key(config)
        elif mode == "room_url":
            room_url = config["room_url"]
            meeting_token = config.get("meeting_token")
            logger.info(
                "Using room URL: %s (token=%s)",
                room_url,
                "yes" if meeting_token else "no",
            )
            return room_url, meeting_token
        elif mode == "token":
            room_url = config["room_url"]
            meeting_token = config["meeting_token"]
            logger.info("Using pre-made token for room: %s", room_url)
            return room_url, meeting_token
        else:
            raise ValueError(
                f"Unknown Daily mode: {mode!r}. Use api_key|room_url|token"
            )

    async def _create_room_with_api_key(
        self, config: dict
    ) -> tuple[str, str | None]:
        """Create a Daily room via the REST API and return (room_url, token).

        Uses the Daily REST API (https://api.daily.co/v1/rooms) to create
        a new room, then creates a meeting token for it.

        Args:
            config: Must contain 'api_key'. Optionally 'room_name' (auto-
                generated if missing).

        Returns:
            Tuple of (room_url, meeting_token).

        Raises:
            ImportError: If httpx is not installed.
            ConnectionError: If the API request fails or times out.
        """
        try:
            import httpx
        except ImportError:
            raise ImportError(
                "httpx not installed. Run: pip install httpx"
            )

        api_key = config["api_key"]
        room_name = config.get("room_name", f"voicecheck-{int(time.time())}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Step 1: Create the room
        room_payload: dict[str, Any] = {
            "name": room_name,
            "properties": {
                "enable_chat": False,
                "enable_screenshare": False,
                "exp": int(time.time()) + 3600,  # 1-hour expiry
                "eject_at_room_exp": True,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Create room (or reuse existing one)
                resp = await client.post(
                    "https://api.daily.co/v1/rooms",
                    json=room_payload,
                    headers=headers,
                )
                if resp.status_code == 400 and "already exists" in resp.text.lower():
                    # Room already exists — fetch it instead
                    logger.info("Room '%s' already exists, reusing", room_name)
                    resp = await client.get(
                        f"https://api.daily.co/v1/rooms/{room_name}",
                        headers=headers,
                    )
                resp.raise_for_status()
                room_data = resp.json()
                room_url = room_data["url"]
                logger.info("Created/found Daily room: %s", room_url)

                # Step 2: Create a meeting token for the room
                token_payload = {
                    "properties": {
                        "room_name": room_name,
                        "user_name": "voicecheck-tester",
                        "enable_screenshare": False,
                        "start_audio_off": False,
                        "start_video_off": True,
                    },
                }
                token_resp = await client.post(
                    "https://api.daily.co/v1/meeting-tokens",
                    json=token_payload,
                    headers=headers,
                )
                token_resp.raise_for_status()
                meeting_token = token_resp.json()["token"]
                logger.info(
                    "Generated meeting token for room %s", room_name
                )

        except httpx.TimeoutException:
            raise ConnectionError(
                f"Daily API request timed out while creating room '{room_name}'"
            )
        except httpx.HTTPStatusError as e:
            raise ConnectionError(
                f"Daily API returned HTTP {e.response.status_code}: "
                f"{e.response.text[:200]}"
            )

        return room_url, meeting_token


# Register this transport
register_transport("daily", DailyTransport)
