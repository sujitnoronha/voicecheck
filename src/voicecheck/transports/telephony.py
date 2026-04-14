"""Telephony transport — connects to a voice agent via Twilio Media Streams.

Unlike other transports that act as WebSocket *clients*, TelephonyTransport
runs a local HTTP + WebSocket *server*. The flow is:

1. Start a local aiohttp web server on a configurable port (default 8765).
2. Place an outbound call via Twilio REST API, pointing TwiML at our server.
3. Twilio answers and opens a Media Stream WebSocket back to our /stream endpoint.
4. Audio flows bidirectionally over that WebSocket as base64-encoded G.711 mu-law
   at 8kHz mono.

Config shape (from YAML scenario):

    transport:
      type: telephony
      mode: twilio
      config:
        account_sid: "${TWILIO_ACCOUNT_SID}"
        auth_token: "${TWILIO_AUTH_TOKEN}"
        from_number: "${TWILIO_FROM_NUMBER}"
        to_number: "${AGENT_PHONE_NUMBER}"
        public_url: "${VOICECHECK_PUBLIC_URL}"   # e.g. https://abc123.ngrok.io
        server_port: 8765                        # local port, default 8765
        call_connect_timeout: 30.0               # seconds, default 30
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

from voicecheck.audio.utils import is_silent, mulaw_to_pcm, pcm_to_mulaw, resample_pcm
from voicecheck.core.transport import Transport, register_transport
from voicecheck.core.types import AudioFrame, TransportMetrics

logger = logging.getLogger("voicecheck.transports.telephony")


class TelephonyTransport(Transport):
    """Twilio Media Streams transport.

    Acts as a WebSocket server that Twilio connects to after an outbound call
    is placed.  Audio is exchanged as base64-encoded G.711 mu-law at 8kHz and
    transcoded to/from 16-bit PCM at 16kHz internally so the rest of VoiceCheck
    works with a uniform format.
    """

    # ── Internal PCM parameters ──────────────────────────────────────
    _INTERNAL_RATE = 16000  # VoiceCheck standard sample rate
    _TWILIO_RATE = 8000     # Twilio Media Streams sample rate

    def __init__(self) -> None:
        self._metrics = TransportMetrics()
        self._agent_audio_frames: list[AudioFrame] = []

        # aiohttp web server and runner
        self._app: Any = None
        self._runner: Any = None
        self._site: Any = None

        # Twilio state
        self._twilio_client: Any = None
        self._call_sid: str | None = None
        self._stream_sid: str | None = None

        # WebSocket connection from Twilio
        self._ws: Any = None

        # Signalling events
        self._stream_connected = asyncio.Event()
        self._stream_stopped = asyncio.Event()

        # Inbound audio queue (PCM 16kHz frames from the WebSocket handler)
        self._audio_queue: asyncio.Queue[AudioFrame | None] = asyncio.Queue()

        self._config: dict = {}

    # ── Transport interface ──────────────────────────────────────────

    async def connect(self, config: dict) -> None:  # noqa: C901
        """Start the local server, place an outbound call, and wait for the media stream.

        Args:
            config: Transport-specific configuration. Required keys:
                account_sid, auth_token, from_number, to_number, public_url.
                Optional: server_port (default 8765), call_connect_timeout (default 30).
        """
        # --- Lazy imports with clear error messages ---------------------------
        try:
            import aiohttp.web as aio_web  # noqa: F811
        except ImportError:
            raise ImportError(
                "aiohttp is not installed. Run: pip install aiohttp"
            )

        try:
            from twilio.rest import Client as TwilioClient  # noqa: F811
        except ImportError:
            raise ImportError(
                "Twilio SDK is not installed. Run: pip install twilio"
            )

        self._config = config
        self._aio_web = aio_web

        # --- Validate required config ----------------------------------------
        for key in ("account_sid", "auth_token", "from_number", "to_number", "public_url"):
            if key not in config:
                raise ValueError(
                    f"Telephony transport requires config key {key!r}. "
                    "See TelephonyTransport docstring for the full config shape."
                )

        public_url: str = config["public_url"].rstrip("/")
        server_port: int = int(config.get("server_port", 8765))
        call_timeout: float = float(config.get("call_connect_timeout", 30.0))

        # --- Start local HTTP + WebSocket server ------------------------------
        self._app = aio_web.Application()
        self._app.router.add_get("/twiml", self._handle_twiml)
        self._app.router.add_get("/stream", self._handle_stream)

        self._runner = aio_web.AppRunner(self._app)
        await self._runner.setup()
        self._site = aio_web.TCPSite(self._runner, "0.0.0.0", server_port)
        await self._site.start()
        logger.info("Local server listening on 0.0.0.0:%d", server_port)

        # --- Create Twilio client (sync SDK) ----------------------------------
        self._twilio_client = TwilioClient(config["account_sid"], config["auth_token"])

        # --- Place outbound call via Twilio REST API --------------------------
        twiml_url = f"{public_url}/twiml"
        logger.info(
            "Placing outbound call: %s -> %s (TwiML: %s)",
            config["from_number"],
            config["to_number"],
            twiml_url,
        )

        try:
            call = await asyncio.to_thread(
                self._twilio_client.calls.create,
                to=config["to_number"],
                from_=config["from_number"],
                url=twiml_url,
            )
        except Exception as exc:
            await self._cleanup_server()
            raise ConnectionError(
                f"Failed to place Twilio call: {exc}"
            ) from exc

        self._call_sid = call.sid
        logger.info("Call placed — SID: %s", self._call_sid)

        # --- Wait for Twilio Media Stream WebSocket to connect ----------------
        try:
            await asyncio.wait_for(
                self._stream_connected.wait(), timeout=call_timeout
            )
            logger.info(
                "Media Stream connected (streamSid=%s)", self._stream_sid
            )
        except asyncio.TimeoutError:
            logger.error(
                "Twilio did not open a Media Stream within %.0fs — "
                "check that public_url (%s) is reachable from the internet "
                "and the call was answered.",
                call_timeout,
                public_url,
            )
            await self.disconnect()
            raise ConnectionError(
                f"Timed out waiting for Twilio Media Stream after {call_timeout}s. "
                f"Ensure {public_url} is publicly reachable (e.g., via ngrok)."
            )

    async def send_audio(self, frames: list[AudioFrame]) -> None:
        """Send PCM audio frames to the call as G.711 mu-law over the media stream.

        Each 16kHz PCM frame is resampled to 8kHz, mu-law encoded, base64-wrapped,
        and sent as a Twilio ``media`` event.

        Args:
            frames: List of 16kHz 16-bit PCM AudioFrame objects.
        """
        if self._ws is None or self._ws.closed:
            raise RuntimeError(
                "Media Stream WebSocket is not connected. Call connect() first."
            )
        if not self._stream_sid:
            raise RuntimeError(
                "No streamSid available — the media stream has not started."
            )

        self._metrics.send_start_ts = time.monotonic()

        for frame in frames:
            # Resample 16kHz -> 8kHz
            pcm_8k = resample_pcm(frame.data, self._INTERNAL_RATE, self._TWILIO_RATE)
            # Encode PCM -> mu-law
            mulaw_bytes = pcm_to_mulaw(pcm_8k)
            # Base64 encode
            payload = base64.b64encode(mulaw_bytes).decode("ascii")
            # Build Twilio media message
            message = json.dumps({
                "event": "media",
                "streamSid": self._stream_sid,
                "media": {
                    "payload": payload,
                },
            })
            await self._ws.send_str(message)

        self._metrics.send_end_ts = time.monotonic()
        logger.info(
            "Sent %d audio frames (%.1fs total, send_duration=%.0fms)",
            len(frames),
            sum(f.duration_s for f in frames),
            self._metrics.send_duration_ms,
        )

    async def receive_audio(
        self, timeout: float = 10.0, silence_threshold: float = 1.5
    ) -> list[AudioFrame]:
        """Receive audio from the agent via the Twilio Media Stream.

        Listens on the internal audio queue (populated by the WebSocket handler)
        until silence is detected or the timeout is reached.

        Args:
            timeout: Maximum seconds to wait for agent audio.
            silence_threshold: Seconds of silence before considering the agent
                done speaking.

        Returns:
            List of captured 16kHz PCM AudioFrame objects.
        """
        self._agent_audio_frames = []
        last_audio_ts = time.monotonic()
        first_frame = True
        consecutive_speech = 0
        first_speech_ts = 0.0
        start_ts = time.monotonic()

        # Require several consecutive non-silent frames to confirm real speech
        # and filter codec warm-up artefacts.
        speech_confirm_frames = 3  # ~60ms at 20ms/frame

        while True:
            now = time.monotonic()

            # Overall timeout
            if now - start_ts > timeout:
                logger.info("Receive timeout reached (%.1fs)", timeout)
                break

            # Calculate how long to wait for the next frame
            remaining = timeout - (now - start_ts)
            try:
                frame = await asyncio.wait_for(
                    self._audio_queue.get(), timeout=min(remaining, 0.1)
                )
            except asyncio.TimeoutError:
                # No frame available — check silence
                if not first_frame and (time.monotonic() - last_audio_ts) > silence_threshold:
                    logger.info(
                        "Silence detected (%.1fs), ending capture",
                        silence_threshold,
                    )
                    break
                continue

            # Sentinel value signals stream ended
            if frame is None:
                logger.info("Media stream ended, finishing capture")
                break

            now = time.monotonic()

            # Silence detection on the PCM data
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

            # Speech detected
            consecutive_speech += 1
            if consecutive_speech == 1:
                first_speech_ts = now

            if first_frame and consecutive_speech >= speech_confirm_frames:
                self._metrics.first_byte_ts = first_speech_ts
                first_frame = False
                logger.info(
                    "Agent speech confirmed (%.0fms after send, %d frames)",
                    (first_speech_ts - self._metrics.send_end_ts) * 1000
                    if self._metrics.send_end_ts
                    else 0,
                    consecutive_speech,
                )

            last_audio_ts = now
            self._metrics.last_byte_ts = now

            self._agent_audio_frames.append(frame)
            self._metrics.agent_audio_frames += 1
            self._metrics.agent_audio_duration_ms += frame.duration_s * 1000

        if not self._agent_audio_frames:
            logger.warning(
                "No non-silent audio captured from agent (timeout=%.1fs). "
                "Agent may not have responded or the call may have dropped.",
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
        """Hang up the call, close the media stream, and stop the local server."""
        # Hang up the Twilio call
        if self._call_sid and self._twilio_client:
            try:
                logger.info("Hanging up call %s", self._call_sid)
                await asyncio.to_thread(
                    self._twilio_client.calls(self._call_sid).update,
                    status="completed",
                )
            except Exception as exc:
                logger.warning("Failed to hang up call %s: %s", self._call_sid, exc)
            finally:
                self._call_sid = None

        # Close the WebSocket (if still open)
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
            self._ws = None

        # Stop the local server
        await self._cleanup_server()

        self._stream_sid = None
        self._twilio_client = None
        logger.info("Telephony transport disconnected and cleaned up")

    @property
    def metrics(self) -> TransportMetrics:
        """Timing metrics from the current/last turn."""
        return self._metrics

    def reset_metrics(self) -> None:
        """Reset metrics for a new turn."""
        self._metrics = TransportMetrics()
        self._agent_audio_frames = []
        # Drain the audio queue of any leftover frames
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ── HTTP / WebSocket handlers ────────────────────────────────────

    async def _handle_twiml(self, request: Any) -> Any:
        """Serve TwiML that instructs Twilio to open a Media Stream back to us.

        GET /twiml
        Returns XML with <Connect><Stream> pointing at our /stream WebSocket.
        """
        aio_web = self._aio_web
        public_url: str = self._config["public_url"].rstrip("/")

        # Extract the host from public_url for the wss:// stream URL
        parsed = urlparse(public_url)
        public_host = parsed.netloc  # e.g. "abc123.ngrok.io"

        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            "  <Connect>\n"
            f'    <Stream url="wss://{public_host}/stream" />\n'
            "  </Connect>\n"
            "</Response>"
        )

        logger.info("Serving TwiML (stream URL: wss://%s/stream)", public_host)
        return aio_web.Response(text=twiml, content_type="application/xml")

    async def _handle_stream(self, request: Any) -> Any:
        """Handle the Twilio Media Stream WebSocket connection.

        Twilio sends JSON messages with an ``event`` field:
        - ``connected`` : Stream connected
        - ``start``     : Stream started — contains ``streamSid``
        - ``media``     : Audio data — base64-encoded G.711 mu-law payload
        - ``stop``      : Stream stopped

        Inbound audio is decoded (base64 -> mu-law -> PCM -> resample 8kHz->16kHz)
        and placed on ``self._audio_queue`` for ``receive_audio`` to consume.
        """
        aio_web = self._aio_web
        ws = aio_web.WebSocketResponse()
        await ws.prepare(request)

        self._ws = ws
        logger.info("Twilio Media Stream WebSocket connected")

        try:
            async for msg in ws:
                if msg.type == aio_web.WSMsgType.TEXT:
                    await self._process_stream_message(msg.data)
                elif msg.type == aio_web.WSMsgType.ERROR:
                    logger.error(
                        "WebSocket error: %s", ws.exception()
                    )
                    break
                elif msg.type in (
                    aio_web.WSMsgType.CLOSE,
                    aio_web.WSMsgType.CLOSING,
                    aio_web.WSMsgType.CLOSED,
                ):
                    logger.info("WebSocket closed by Twilio")
                    break
        except Exception:
            logger.exception("Unexpected error in Media Stream handler")
        finally:
            # Signal that the stream has stopped so receive_audio can finish
            self._stream_stopped.set()
            await self._audio_queue.put(None)  # sentinel
            logger.info("Media Stream WebSocket handler finished")

        return ws

    async def _process_stream_message(self, raw: str) -> None:
        """Parse and handle a single Twilio Media Stream JSON message.

        Args:
            raw: Raw JSON string from the WebSocket.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Received non-JSON message on stream, ignoring")
            return

        event = data.get("event")

        if event == "connected":
            logger.info("Media Stream event: connected (protocol=%s)", data.get("protocol"))

        elif event == "start":
            self._stream_sid = data.get("streamSid") or data.get("start", {}).get("streamSid")
            logger.info(
                "Media Stream event: start (streamSid=%s, tracks=%s)",
                self._stream_sid,
                data.get("start", {}).get("tracks"),
            )
            self._stream_connected.set()

        elif event == "media":
            media = data.get("media", {})
            payload_b64 = media.get("payload", "")
            if not payload_b64:
                return

            # Decode: base64 -> mu-law bytes -> PCM 8kHz -> PCM 16kHz
            mulaw_bytes = base64.b64decode(payload_b64)
            pcm_8k = mulaw_to_pcm(mulaw_bytes)
            pcm_16k = resample_pcm(pcm_8k, self._TWILIO_RATE, self._INTERNAL_RATE)

            frame = AudioFrame(
                data=pcm_16k,
                sample_rate=self._INTERNAL_RATE,
                num_channels=1,
            )
            await self._audio_queue.put(frame)

        elif event == "stop":
            logger.info(
                "Media Stream event: stop (streamSid=%s)",
                data.get("streamSid"),
            )
            self._stream_stopped.set()
            await self._audio_queue.put(None)  # sentinel for receive_audio

        elif event == "mark":
            # Twilio sends mark events as acknowledgements; safe to ignore.
            logger.debug("Media Stream event: mark (name=%s)", data.get("mark", {}).get("name"))

        else:
            logger.debug("Unknown Media Stream event: %s", event)

    # ── Private helpers ──────────────────────────────────────────────

    async def _cleanup_server(self) -> None:
        """Gracefully shut down the local aiohttp server."""
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        logger.info("Local HTTP/WebSocket server stopped")


# Register this transport
register_transport("telephony", TelephonyTransport)
