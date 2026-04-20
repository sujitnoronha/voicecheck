"""VAPI transport -- connects to a voice agent via VAPI web call.

Creates a VAPI web call through the REST API, then streams audio
bidirectionally over the resulting WebSocket connection. Supports
both raw PCM (pcm_s16le) and G.711 mu-law audio formats.

Config shape (YAML):
    transport:
      type: vapi
      mode: web_call
      config:
        api_key: "${VAPI_API_KEY}"
        assistant_id: "${VAPI_ASSISTANT_ID}"
        audio_format: "pcm_s16le"   # or "mulaw"
        sample_rate: 16000
        assistant_config: {}        # optional: full assistant override
"""

from __future__ import annotations

import json
import logging
from typing import Any

from voicecheck.audio.utils import mulaw_to_pcm, pcm_to_mulaw
from voicecheck.core.transport import register_transport
from voicecheck.core.types import AudioFrame
from voicecheck.transports.websocket_base import WebSocketTransport

logger = logging.getLogger("voicecheck.transports.vapi")

# VAPI REST API base URL.
_VAPI_API_BASE = "https://api.vapi.ai"


def _require_httpx():
    """Lazily import httpx with a clear error message if missing."""
    try:
        import httpx
    except ImportError:
        raise ImportError(
            "httpx is required for the VAPI transport but is not installed. "
            "Install it with:  pip install httpx"
        ) from None
    return httpx


class VAPITransport(WebSocketTransport):
    """VAPI transport that streams audio over a WebSocket web call.

    Lifecycle:
      1. ``_get_ws_url`` creates a call via the VAPI REST API and returns
         the WebSocket transport URL from the response.
      2. ``_on_ws_connected`` sends a session configuration message that
         tells VAPI which audio format and sample rate to use.
      3. Audio flows bidirectionally as raw binary frames (PCM or mu-law).
      4. ``_on_ws_disconnecting`` terminates the call via the REST API so
         the server-side resources are cleaned up immediately.

    Supported audio formats:
      - ``pcm_s16le`` (default): 16-bit signed little-endian PCM. Zero
        conversion overhead -- frames pass through as-is.
      - ``mulaw``: G.711 mu-law, typically used for telephony. Frames are
        converted between PCM and mu-law on the fly.
    """

    def __init__(self) -> None:
        super().__init__()
        self._call_id: str | None = None
        self._api_key: str | None = None
        self._audio_format: str = "pcm_s16le"

    # ── Abstract hook implementations ────────────────────────────────

    async def _get_ws_url(self, config: dict) -> str:
        """Create a VAPI web call and return the WebSocket URL.

        Calls ``POST /call/web`` on the VAPI REST API. The response
        contains a ``transport`` object with the WebSocket URL to stream
        audio over, plus a ``id`` field used later to end the call.

        Args:
            config: Transport config dict from the YAML scenario. Must
                contain ``api_key`` and either ``assistant_id`` or
                ``assistant_config``.

        Returns:
            WebSocket URL for the newly created call.

        Raises:
            ValueError: If ``api_key`` is missing from config.
            ConnectionError: If the VAPI API returns an error or the
                response does not contain the expected fields.
        """
        httpx = _require_httpx()

        api_key = config.get("api_key")
        if not api_key:
            raise ValueError(
                "VAPI transport requires 'api_key' in config. "
                "Set it directly or use ${VAPI_API_KEY} env-var substitution."
            )

        self._api_key = api_key
        self._audio_format = config.get("audio_format", "pcm_s16le")

        # Build the request body -- either a full assistant config override
        # or a simple assistant_id reference.
        assistant_config = config.get("assistant_config")
        if assistant_config:
            body: dict[str, Any] = {"assistant": assistant_config}
            logger.info("Creating VAPI web call with inline assistant config")
        else:
            assistant_id = config.get("assistant_id")
            if not assistant_id:
                raise ValueError(
                    "VAPI transport requires either 'assistant_id' or 'assistant_config' in config."
                )
            body = {"assistantId": assistant_id}
            logger.info("Creating VAPI web call for assistant %s", assistant_id)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        request_timeout = config.get("api_timeout", 15.0)

        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                resp = await client.post(
                    f"{_VAPI_API_BASE}/call/web",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            raise ConnectionError(
                f"VAPI API timed out after {request_timeout}s when creating "
                "web call. Check your network connection and API key."
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = exc.response.text[:500]
            raise ConnectionError(
                f"VAPI API returned HTTP {status} when creating web call: {detail}"
            )

        # Extract call ID and transport URL from the response.
        call_id = data.get("id")
        if not call_id:
            raise ConnectionError(
                f"VAPI API response missing 'id' field. Response keys: {list(data.keys())}"
            )
        self._call_id = call_id

        # The transport URL lives under data["transport"]["websocket"]["url"]
        # or directly under data["webCallUrl"] depending on API version.
        ws_url = _nested_get(data, "transport", "websocket", "url") or data.get("webCallUrl")
        if not ws_url:
            raise ConnectionError(
                f"VAPI API response for call {call_id} does not contain a "
                "WebSocket URL. Check your VAPI plan and assistant "
                f"configuration. Response keys: {list(data.keys())}"
            )

        logger.info(
            "VAPI call created (call_id=%s, ws_url=%s...)",
            call_id,
            ws_url[:80],
        )
        return ws_url

    async def _on_ws_connected(self, ws: Any, config: dict) -> None:
        """Send the initial session configuration to VAPI.

        Tells the server which audio format and sample rate to use for
        both input and output streams. This must be sent before any
        audio frames are exchanged.

        Args:
            ws: The active websocket connection.
            config: Transport config dict.
        """
        sample_rate = config.get("sample_rate", 16000)
        audio_format = config.get("audio_format", "pcm_s16le")

        session_update = {
            "type": "session.update",
            "session": {
                "inputAudioFormat": audio_format,
                "outputAudioFormat": audio_format,
                "inputAudioSampleRate": sample_rate,
                "outputAudioSampleRate": sample_rate,
            },
        }

        await ws.send(json.dumps(session_update))
        logger.info(
            "Sent session config (format=%s, sample_rate=%d)",
            audio_format,
            sample_rate,
        )

    def _encode_outbound_frame(self, frame: AudioFrame) -> bytes:
        """Encode a PCM AudioFrame for sending to VAPI.

        For ``pcm_s16le`` format the raw bytes are sent directly with no
        conversion. For ``mulaw`` format the PCM data is compressed to
        G.711 mu-law first.

        Args:
            frame: PCM audio frame to encode.

        Returns:
            Bytes ready to send over the WebSocket.
        """
        if self._audio_format == "mulaw":
            return pcm_to_mulaw(frame.data)
        # pcm_s16le (default) -- pass through as-is.
        return frame.data

    def _decode_inbound_message(self, message: bytes | str) -> bytes | None:
        """Decode an inbound WebSocket message from VAPI.

        VAPI sends two types of messages over the WebSocket:

        - **Binary**: Raw audio data (PCM or mu-law depending on the
          session config). Returned as PCM bytes for the base class to
          buffer.
        - **Text (JSON)**: Control / event messages such as speech
          updates, transcripts, errors, and hang-up signals. These are
          logged and ``None`` is returned so the base class skips them.

        Args:
            message: Raw WebSocket message (bytes or str).

        Returns:
            PCM audio bytes, or ``None`` for non-audio messages.
        """
        # Binary message -- audio data.
        if isinstance(message, bytes):
            if self._audio_format == "mulaw":
                return mulaw_to_pcm(message)
            # pcm_s16le -- already in the right format.
            return message

        # Text message -- JSON control / event.
        try:
            event = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Non-JSON text message from VAPI: %s", message[:200])
            return None

        msg_type = event.get("type", "unknown")

        if msg_type == "speech-update":
            status = event.get("status", "")
            role = event.get("role", "")
            logger.debug("VAPI speech-update: role=%s status=%s", role, status)

        elif msg_type == "transcript":
            role = event.get("role", "")
            text = event.get("transcript", "")
            is_final = event.get("transcriptType") == "final"
            logger.info(
                "VAPI transcript (%s, final=%s): %s",
                role,
                is_final,
                text[:120],
            )

        elif msg_type == "hang":
            logger.info("VAPI hang-up received (call_id=%s)", self._call_id)

        elif msg_type == "error":
            error_msg = event.get("message", event.get("error", "unknown"))
            logger.error("VAPI error: %s", error_msg)

        else:
            logger.debug("VAPI event (type=%s): %s", msg_type, message[:200])

        return None

    async def _on_ws_disconnecting(self, ws: Any, config: dict) -> None:
        """End the VAPI call via the REST API before closing the WebSocket.

        This is a best-effort cleanup. If the API call fails we log a
        warning but do not raise, since the WebSocket disconnect will
        happen regardless.

        Args:
            ws: The active websocket connection.
            config: Transport config dict.
        """
        if not self._call_id or not self._api_key:
            logger.debug("Skipping VAPI call teardown (no call_id or api_key)")
            return

        httpx = _require_httpx()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(
                    f"{_VAPI_API_BASE}/call/{self._call_id}",
                    headers=headers,
                )
                if resp.is_success:
                    logger.info("VAPI call ended via API (call_id=%s)", self._call_id)
                else:
                    logger.warning(
                        "VAPI call teardown returned HTTP %d for call_id=%s: %s",
                        resp.status_code,
                        self._call_id,
                        resp.text[:200],
                    )
        except Exception as exc:
            logger.warning(
                "Failed to end VAPI call %s via API: %s",
                self._call_id,
                exc,
            )
        finally:
            self._call_id = None


# ── Helpers ──────────────────────────────────────────────────────────


def _nested_get(d: dict, *keys: str) -> Any | None:
    """Safely traverse nested dicts, returning None if any key is missing."""
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


# ── Registration ─────────────────────────────────────────────────────

register_transport("vapi", VAPITransport)
