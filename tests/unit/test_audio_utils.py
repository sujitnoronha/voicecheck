"""Tests for voicecheck.audio.utils — shared audio utilities."""

from __future__ import annotations

import struct

import pytest

from voicecheck.audio.utils import (
    compute_rms,
    is_silent,
    mulaw_to_pcm,
    pcm_to_mulaw,
    resample_pcm,
)


# ── is_silent / compute_rms ──────────────────────────────────────


class TestIsSilent:
    def test_empty_data_is_silent(self) -> None:
        assert is_silent(b"") is True

    def test_single_byte_is_silent(self) -> None:
        assert is_silent(b"\x00") is True

    def test_zero_pcm_is_silent(self) -> None:
        data = b"\x00" * 640  # 320 zero samples
        assert is_silent(data) is True

    def test_loud_pcm_is_not_silent(self) -> None:
        data = struct.pack("<320h", *([10000] * 320))
        assert is_silent(data) is False

    def test_quiet_pcm_below_threshold_is_silent(self) -> None:
        # Very quiet signal — RMS should be below 500
        data = struct.pack("<320h", *([100] * 320))
        assert is_silent(data) is True

    def test_custom_threshold(self) -> None:
        data = struct.pack("<320h", *([300] * 320))
        # RMS is 300, which is below default 500
        assert is_silent(data, threshold=500) is True
        # But above a lower threshold
        assert is_silent(data, threshold=200) is False

    def test_bytearray_input(self) -> None:
        data = bytearray(b"\x00" * 640)
        assert is_silent(data) is True


class TestComputeRms:
    def test_silence_is_zero(self) -> None:
        data = b"\x00" * 640
        assert compute_rms(data) == 0.0

    def test_empty_data(self) -> None:
        assert compute_rms(b"") == 0.0

    def test_constant_signal(self) -> None:
        # A constant signal of 1000 should have RMS of 1000
        data = struct.pack("<100h", *([1000] * 100))
        rms = compute_rms(data)
        assert abs(rms - 1000.0) < 1.0

    def test_positive_values(self) -> None:
        data = struct.pack("<10h", *([5000] * 10))
        rms = compute_rms(data)
        assert abs(rms - 5000.0) < 1.0

    def test_negative_values(self) -> None:
        data = struct.pack("<10h", *([-5000] * 10))
        rms = compute_rms(data)
        assert abs(rms - 5000.0) < 1.0


# ── G.711 mu-law codec ──────────────────────────────────────────


class TestMulawCodec:
    def test_roundtrip_preserves_shape(self) -> None:
        """Mu-law is lossy, but the roundtrip should preserve the general signal shape."""
        original = struct.pack("<10h", 0, 1000, -1000, 5000, -5000, 10000, -10000, 20000, -20000, 32000)
        mulaw = pcm_to_mulaw(original)
        assert len(mulaw) == 10  # 10 PCM samples -> 10 mulaw bytes
        back = mulaw_to_pcm(mulaw)
        assert len(back) == 20  # 10 samples * 2 bytes each

        # Decode back and check values are in the right ballpark
        decoded = struct.unpack("<10h", back)
        # Zero stays near zero
        assert abs(decoded[0]) < 100
        # Signs are preserved
        assert decoded[1] > 0
        assert decoded[2] < 0
        # Large values stay large
        assert decoded[7] > 10000
        assert decoded[8] < -10000

    def test_output_sizes(self) -> None:
        pcm = struct.pack("<100h", *range(100))
        mulaw = pcm_to_mulaw(pcm)
        assert len(mulaw) == 100

        back = mulaw_to_pcm(mulaw)
        assert len(back) == 200

    def test_empty_input(self) -> None:
        assert pcm_to_mulaw(b"") == b""
        assert mulaw_to_pcm(b"") == b""

    def test_silence_roundtrip(self) -> None:
        """Silent PCM should encode to mu-law and decode back to near-silence."""
        silent = b"\x00" * 20  # 10 zero samples
        mulaw = pcm_to_mulaw(silent)
        back = mulaw_to_pcm(mulaw)
        # Decoded values should be very small
        decoded = struct.unpack("<10h", back)
        for val in decoded:
            assert abs(val) < 200  # mu-law has bias so silence isn't exactly 0


# ── Resampling ───────────────────────────────────────────────────


class TestResamplePcm:
    def test_same_rate_passthrough(self) -> None:
        data = struct.pack("<100h", *range(100))
        result = resample_pcm(data, 16000, 16000)
        assert result == data

    def test_downsample_24k_to_16k(self) -> None:
        # 2400 samples at 24kHz = 100ms
        data = struct.pack("<2400h", *range(2400))
        result = resample_pcm(data, 24000, 16000)
        expected_samples = int(2400 * 16000 / 24000)
        assert len(result) == expected_samples * 2

    def test_upsample_8k_to_16k(self) -> None:
        # 800 samples at 8kHz = 100ms
        data = struct.pack("<800h", *range(800))
        result = resample_pcm(data, 8000, 16000)
        expected_samples = int(800 * 16000 / 8000)
        assert len(result) == expected_samples * 2

    def test_downsample_16k_to_8k(self) -> None:
        # For telephony: 16kHz -> 8kHz
        data = struct.pack("<1600h", *([1000] * 1600))
        result = resample_pcm(data, 16000, 8000)
        expected_samples = int(1600 * 8000 / 16000)
        assert len(result) == expected_samples * 2

    def test_empty_data(self) -> None:
        result = resample_pcm(b"", 16000, 8000)
        assert result == b""

    def test_preserves_constant_signal(self) -> None:
        """Resampling a constant signal should produce a constant signal."""
        val = 5000
        data = struct.pack("<1000h", *([val] * 1000))
        result = resample_pcm(data, 16000, 8000)
        samples = struct.unpack(f"<{len(result) // 2}h", result)
        for s in samples:
            assert abs(s - val) < 2  # Linear interpolation of constant = constant


# ── Transport registration ───────────────────────────────────────


class TestTransportRegistration:
    def test_all_transports_registered(self) -> None:
        from voicecheck.core.transport import _TRANSPORT_REGISTRY

        # Import all transports
        import voicecheck.transports.livekit  # noqa: F401
        import voicecheck.transports.daily  # noqa: F401
        import voicecheck.transports.vapi  # noqa: F401
        import voicecheck.transports.retell  # noqa: F401
        import voicecheck.transports.telephony  # noqa: F401

        assert "livekit" in _TRANSPORT_REGISTRY
        assert "daily" in _TRANSPORT_REGISTRY
        assert "vapi" in _TRANSPORT_REGISTRY
        assert "retell" in _TRANSPORT_REGISTRY
        assert "telephony" in _TRANSPORT_REGISTRY

    def test_transport_subclass(self) -> None:
        from voicecheck.core.transport import Transport, get_transport

        import voicecheck.transports.livekit  # noqa: F401
        import voicecheck.transports.daily  # noqa: F401
        import voicecheck.transports.vapi  # noqa: F401
        import voicecheck.transports.retell  # noqa: F401
        import voicecheck.transports.telephony  # noqa: F401

        for name in ("livekit", "daily", "vapi", "retell", "telephony"):
            cls = get_transport(name)
            assert issubclass(cls, Transport), f"{name} is not a Transport subclass"
