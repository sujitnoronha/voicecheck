"""Tests for the built-in echo transport."""

from __future__ import annotations

import pytest

from voicecheck.core.transport import get_transport
import voicecheck.transports.echo  # noqa: F401  # ensure registration
from voicecheck.transports.echo import EchoTransport


class TestEchoTransport:
    def test_registered(self):
        assert get_transport("echo") is EchoTransport

    def test_validate_config_accepts_minimal(self):
        transport = EchoTransport()
        assert transport.validate_config({}) == []

    def test_validate_config_rejects_bad_first_byte_delay(self):
        transport = EchoTransport()
        errors = transport.validate_config({"first_byte_delay_ms": "not a number"})
        assert any("first_byte_delay_ms" in e for e in errors)

    def test_validate_config_rejects_non_string_response_text(self):
        transport = EchoTransport()
        errors = transport.validate_config({"response_text": ["list", "not", "str"]})
        assert any("response_text" in e for e in errors)

    @pytest.mark.asyncio
    async def test_receive_emits_silent_frames_when_not_synthesizing(self):
        transport = EchoTransport()
        await transport.connect({
            "response_text": "ignored",
            "synthesize": False,
            "first_byte_delay_ms": 0,
        })
        await transport.send_audio([])
        frames = await transport.receive_audio()
        assert len(frames) > 0
        # Metrics populated.
        assert transport.metrics.first_byte_ts > 0
        assert transport.metrics.last_byte_ts >= transport.metrics.first_byte_ts
        await transport.disconnect()

    @pytest.mark.asyncio
    async def test_empty_response_text_defaults_to_silence(self):
        transport = EchoTransport()
        await transport.connect({
            "response_text": "",
            "first_byte_delay_ms": 0,
        })
        await transport.send_audio([])
        frames = await transport.receive_audio()
        # Should fall back to silent PCM without crashing.
        assert len(frames) > 0
        await transport.disconnect()

    @pytest.mark.asyncio
    async def test_first_byte_delay_applied(self):
        import time
        transport = EchoTransport()
        await transport.connect({
            "response_text": "",
            "first_byte_delay_ms": 100,
        })
        t0 = time.monotonic()
        await transport.send_audio([])
        await transport.receive_audio()
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms >= 90  # allow for timing slop
        await transport.disconnect()
