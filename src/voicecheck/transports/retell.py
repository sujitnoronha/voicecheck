"""Retell transport — connects to a voice agent via Retell's WebSocket API.

Retell streams raw PCM audio over a WebSocket connection established after
creating a web call via their REST API.  The flow is:

  1. POST /v2/create-web-call  → obtain ``access_token`` + ``call_id``
  2. Connect to ``wss://api.retellai.com/audio-websocket/{call_id}?access_token=…``
  3. Send a one-shot config frame, then stream raw PCM in both directions
  4. Disconnect when the WebSocket closes (Retell ends the call automatically)

Config shape (YAML):

.. code-block:: yaml

   transport:
     type: retell
     mode: web_call
     config:
       api_key: "${RETELL_API_KEY}"
       agent_id: "${RETELL_AGENT_ID}"
       retell_sample_rate: 24000   # Retell's native PCM rate (default 24000)
       sample_rate: 16000          # VoiceCheck's internal rate
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any
from urllib.parse import urlencode

from voicecheck.audio.utils import resample_pcm
from voicecheck.core.transport import register_transport
from voicecheck.core.types import AudioFrame
from voicecheck.transports.websocket_base import WebSocketTransport

logger = logging.getLogger("voicecheck.transports.retell")

# Retell REST API base URL.
_RETELL_API_BASE = "https://api.retellai.com"

# Default PCM sample rate that Retell sends/expects when not overridden.
_DEFAULT_RETELL_SAMPLE_RATE = 24000

# VoiceCheck's canonical internal sample rate.
_VOICECHECK_SAMPLE_RATE = 16000


class RetellTransport(WebSocketTransport):
    """Retell transport that extends the WebSocket base.

    Creates a web call via the Retell REST API, then streams raw PCM audio
    over a WebSocket.  Audio is automatically resampled between Retell's
    native rate (commonly 24 kHz) and VoiceCheck's internal 16 kHz rate.

    Required config keys:
        api_key:   Retell API key (``Bearer`` token).
        agent_id:  The Retell agent to connect to.

    Optional config keys:
        retell_sample_rate:            Retell PCM rate (default 24000).
        sample_rate:                   VoiceCheck target rate (default 16000).
        metadata:                      Dict passed through to the web-call request.
        retell_llm_dynamic_variables:  Dict of LLM template variables.
    """

    def __init__(self) -> None:
        super().__init__()
        self._api_key: str = ""
        self._access_token: str = ""
        self._call_id: str = ""
        self._retell_sample_rate: int = _DEFAULT_RETELL_SAMPLE_RATE

    # ── Abstract hook implementations ────────────────────────────────

    async def _get_ws_url(self, config: dict) -> str:
        """Create a Retell web call and return the authenticated WebSocket URL.

        Calls ``POST /v2/create-web-call`` to obtain an ``access_token`` and
        ``call_id``, then constructs the WebSocket URL with the token as a
        query parameter so Retell authenticates the connection on open.

        Raises:
            ImportError:  If ``httpx`` is not installed.
            ConnectionError: If the REST call fails.
        """
        try:
            import httpx
        except ImportError:
            raise ImportError(
                "httpx is required for the Retell transport but is not installed. "
                "Run:  pip install httpx"
            )

        self._api_key = config["api_key"]
        self._retell_sample_rate = config.get("retell_sample_rate", _DEFAULT_RETELL_SAMPLE_RATE)

        # Build request body.
        body: dict[str, Any] = {"agent_id": config["agent_id"]}
        if "metadata" in config:
            body["metadata"] = config["metadata"]
        if "retell_llm_dynamic_variables" in config:
            body["retell_llm_dynamic_variables"] = config["retell_llm_dynamic_variables"]

        url = f"{_RETELL_API_BASE}/v2/create-web-call"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        request_timeout = config.get("request_timeout", 15.0)

        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            raise ConnectionError(f"Retell create-web-call timed out after {request_timeout}s")
        except httpx.HTTPStatusError as exc:
            raise ConnectionError(
                f"Retell create-web-call returned HTTP {exc.response.status_code}: "
                f"{exc.response.text}"
            )

        self._access_token = data["access_token"]
        self._call_id = data["call_id"]

        logger.info(
            "Created Retell web call (call_id=%s, agent_id=%s)",
            self._call_id,
            config["agent_id"],
        )

        # Build authenticated WebSocket URL.
        query = urlencode({"access_token": self._access_token})
        ws_url = f"wss://api.retellai.com/audio-websocket/{self._call_id}?{query}"
        return ws_url

    async def _on_ws_connected(self, ws: Any, config: dict) -> None:
        """Send the initial config frame after the WebSocket opens.

        Retell expects a one-shot JSON message that tells the server not to
        auto-reconnect and to send call detail events.
        """
        config_message = json.dumps(
            {
                "response_type": "config",
                "config": {
                    "auto_reconnect": False,
                    "call_details": True,
                },
            }
        )
        await ws.send(config_message)
        logger.info("Sent config frame to Retell (call_id=%s)", self._call_id)

    def _encode_outbound_frame(self, frame: AudioFrame) -> bytes:
        """Encode an outbound audio frame for Retell.

        Retell accepts raw PCM bytes.  If VoiceCheck's internal sample rate
        differs from Retell's configured rate we resample before sending.

        Args:
            frame: PCM audio frame at VoiceCheck's sample rate.

        Returns:
            Raw PCM bytes at Retell's expected sample rate.
        """
        voicecheck_rate = self._config.get("sample_rate", _VOICECHECK_SAMPLE_RATE)
        retell_rate = self._retell_sample_rate

        pcm_data = frame.data

        if voicecheck_rate != retell_rate:
            pcm_data = resample_pcm(pcm_data, voicecheck_rate, retell_rate)

        return pcm_data

    def _decode_inbound_message(self, message: bytes | str) -> bytes | None:
        """Decode an inbound Retell WebSocket message.

        Retell sends two kinds of messages:

        * **Binary** — raw PCM audio at Retell's native sample rate.
        * **Text (JSON)** — control / metadata frames:
            - ``"audio"``         : base64-encoded audio payload.
            - ``"ping"``          : keep-alive (ignored).
            - ``"call_details"``  : logged, not audio.
            - ``"transcript"``    : logged, not audio.
            - ``"metadata"``      : logged, not audio.
            - ``"error"``         : logged as warning.

        Returns:
            PCM bytes resampled to VoiceCheck's rate, or ``None`` for
            non-audio messages.
        """
        voicecheck_rate = self._config.get("sample_rate", _VOICECHECK_SAMPLE_RATE)
        retell_rate = self._retell_sample_rate

        # ── Binary frame: raw PCM audio ─────────────────────────────
        if isinstance(message, (bytes, bytearray)):
            pcm_data = bytes(message)
            if retell_rate != voicecheck_rate:
                pcm_data = resample_pcm(pcm_data, retell_rate, voicecheck_rate)
            return pcm_data

        # ── Text frame: JSON control message ─────────────────────────
        try:
            payload = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Retell: could not parse text message: %s", message[:200])
            return None

        # Retell may send the event type under different keys depending on
        # API version; handle the common shapes defensively.
        event_type: str = ""
        if isinstance(payload, dict):
            event_type = (
                payload.get("response_type", "")
                or payload.get("type", "")
                or payload.get("event", "")
            )

        if event_type == "audio":
            # Base64-encoded audio data.
            audio_b64 = payload.get("data") or payload.get("audio", "")
            if not audio_b64:
                return None
            pcm_data = base64.b64decode(audio_b64)
            if retell_rate != voicecheck_rate:
                pcm_data = resample_pcm(pcm_data, retell_rate, voicecheck_rate)
            return pcm_data

        if event_type == "ping":
            # Retell keep-alive — safe to ignore (the websockets library
            # handles protocol-level pings automatically).
            logger.debug("Retell: received ping")
            return None

        if event_type in ("call_details", "call_detail"):
            logger.info(
                "Retell call details: %s",
                json.dumps(payload, default=str)[:500],
            )
            return None

        if event_type == "transcript":
            logger.info(
                "Retell transcript: %s",
                payload.get("transcript", payload.get("text", "")),
            )
            return None

        if event_type == "metadata":
            logger.info("Retell metadata: %s", json.dumps(payload, default=str)[:500])
            return None

        if event_type == "error":
            logger.warning(
                "Retell error: %s",
                payload.get("message", payload.get("error", json.dumps(payload))),
            )
            return None

        # Unknown event — log at debug level so it does not spam in prod.
        logger.debug("Retell: unhandled event type %r: %s", event_type, message[:300])
        return None

    async def _on_ws_disconnecting(self, ws: Any, config: dict) -> None:
        """Handle pre-disconnect cleanup.

        Retell ends the call automatically when the WebSocket closes, so no
        explicit REST teardown is required.  We simply log the disconnect.
        """
        logger.info("Disconnecting from Retell (call_id=%s)", self._call_id)


# ---------------------------------------------------------------------------
# Transport registration
# ---------------------------------------------------------------------------
register_transport("retell", RetellTransport)
