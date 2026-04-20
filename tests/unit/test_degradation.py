"""Tests for audio degradation effects."""

import struct

from voicecheck.audio.degradation import (
    add_gaussian_noise,
    apply_degradation,
    codec_roundtrip,
    lowpass_filter,
    simulate_packet_loss,
)
from voicecheck.audio.utils import compute_rms
from voicecheck.core.types import AudioFrame


def _make_tone(
    frequency: float = 440,
    duration_s: float = 0.1,
    sample_rate: int = 16000,
    amplitude: int = 10000,
) -> bytes:
    """Generate a sine wave tone as 16-bit PCM."""
    import math

    num_samples = int(sample_rate * duration_s)
    samples = []
    for i in range(num_samples):
        val = int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
        samples.append(max(-32768, min(32767, val)))
    return struct.pack(f"<{num_samples}h", *samples)


def _make_frames(
    pcm_data: bytes, sample_rate: int = 16000, frame_duration_ms: int = 20
) -> list[AudioFrame]:
    """Split PCM data into AudioFrames."""
    bytes_per_frame = int(sample_rate * frame_duration_ms / 1000) * 2
    frames = []
    for i in range(0, len(pcm_data), bytes_per_frame):
        chunk = pcm_data[i : i + bytes_per_frame]
        if len(chunk) < bytes_per_frame:
            chunk += b"\x00" * (bytes_per_frame - len(chunk))
        frames.append(
            AudioFrame(
                data=chunk,
                sample_rate=sample_rate,
                num_channels=1,
            )
        )
    return frames


class TestAddGaussianNoise:
    def test_output_same_length(self):
        pcm = _make_tone()
        noisy = add_gaussian_noise(pcm, snr_db=20)
        assert len(noisy) == len(pcm)

    def test_modifies_audio(self):
        pcm = _make_tone()
        noisy = add_gaussian_noise(pcm, snr_db=10)
        assert noisy != pcm

    def test_low_snr_is_noisier(self):
        pcm = _make_tone(duration_s=0.5)
        noise_20 = add_gaussian_noise(pcm, snr_db=20)
        noise_5 = add_gaussian_noise(pcm, snr_db=5)
        # Compute difference RMS between original and noisy
        diff_20 = _rms_diff(pcm, noise_20)
        diff_5 = _rms_diff(pcm, noise_5)
        assert diff_5 > diff_20  # lower SNR = more noise

    def test_empty_input(self):
        assert add_gaussian_noise(b"", snr_db=20) == b""

    def test_silence_input(self):
        silence = b"\x00" * 640
        result = add_gaussian_noise(silence, snr_db=20)
        assert len(result) == 640  # passthrough for zero signal

    def test_clips_to_16bit_range(self):
        # Extreme amplitude + low SNR
        pcm = _make_tone(amplitude=30000)
        noisy = add_gaussian_noise(pcm, snr_db=1)
        num_samples = len(noisy) // 2
        samples = struct.unpack(f"<{num_samples}h", noisy)
        for s in samples:
            assert -32768 <= s <= 32767


class TestLowpassFilter:
    def test_output_same_length(self):
        pcm = _make_tone()
        filtered = lowpass_filter(pcm, cutoff_hz=3400, sample_rate=16000)
        assert len(filtered) == len(pcm)

    def test_attenuates_high_frequency(self):
        # High frequency tone should be quieter after filtering
        pcm = _make_tone(frequency=7000, duration_s=0.1, sample_rate=16000)
        filtered = lowpass_filter(pcm, cutoff_hz=3400, sample_rate=16000)
        assert compute_rms(filtered) < compute_rms(pcm)

    def test_preserves_low_frequency(self):
        # Low frequency should pass through mostly unchanged
        pcm = _make_tone(frequency=200, duration_s=0.1)
        filtered = lowpass_filter(pcm, cutoff_hz=3400, sample_rate=16000)
        # Allow 20% RMS loss for single-pole filter
        assert compute_rms(filtered) > compute_rms(pcm) * 0.8

    def test_empty_input(self):
        assert lowpass_filter(b"", cutoff_hz=3400, sample_rate=16000) == b""

    def test_narrowband_cutoff(self):
        # Narrowband should attenuate more than wideband at 5kHz
        pcm = _make_tone(frequency=5000, duration_s=0.1)
        narrow = lowpass_filter(pcm, cutoff_hz=3400, sample_rate=16000)
        wide = lowpass_filter(pcm, cutoff_hz=7000, sample_rate=16000)
        assert compute_rms(narrow) < compute_rms(wide)


class TestSimulatePacketLoss:
    def test_zero_loss(self):
        frames = _make_frames(_make_tone())
        result = simulate_packet_loss(frames, loss_pct=0)
        assert len(result) == len(frames)
        for orig, res in zip(frames, result, strict=False):
            assert orig.data == res.data

    def test_full_loss(self):
        frames = _make_frames(_make_tone())
        result = simulate_packet_loss(frames, loss_pct=100)
        for f in result:
            assert f.data == b"\x00" * len(f.data)

    def test_partial_loss_preserves_dimensions(self):
        frames = _make_frames(_make_tone())
        result = simulate_packet_loss(frames, loss_pct=50)
        assert len(result) == len(frames)
        for orig, res in zip(frames, result, strict=False):
            assert len(res.data) == len(orig.data)
            assert res.sample_rate == orig.sample_rate

    def test_approximate_loss_rate(self):
        frames = _make_frames(_make_tone(duration_s=1.0))  # 50 frames at 20ms
        result = simulate_packet_loss(frames, loss_pct=50)
        silent = sum(1 for f in result if f.data == b"\x00" * len(f.data))
        # Allow wide margin for randomness
        assert 10 < silent < 40

    def test_empty_frames(self):
        assert simulate_packet_loss([], loss_pct=50) == []


class TestCodecRoundtrip:
    def test_output_same_length(self):
        pcm = _make_tone()
        result = codec_roundtrip(pcm)
        assert len(result) == len(pcm)

    def test_introduces_quantization_noise(self):
        pcm = _make_tone()
        result = codec_roundtrip(pcm)
        assert result != pcm  # lossy codec changes the signal

    def test_preserves_approximate_signal(self):
        pcm = _make_tone()
        result = codec_roundtrip(pcm)
        # Mu-law preserves the general shape — RMS should be close
        orig_rms = compute_rms(pcm)
        result_rms = compute_rms(result)
        assert abs(orig_rms - result_rms) / orig_rms < 0.3  # within 30%

    def test_empty_input(self):
        assert codec_roundtrip(b"") == b""

    def test_silence_stays_silent(self):
        silence = b"\x00" * 640
        result = codec_roundtrip(silence)
        assert compute_rms(result) < 100  # near-silent after roundtrip


class TestApplyDegradation:
    def test_no_effects(self):
        frames = _make_frames(_make_tone())
        result = apply_degradation(frames)
        assert len(result) == len(frames)
        for orig, res in zip(frames, result, strict=False):
            assert orig.data == res.data

    def test_noise_only(self):
        frames = _make_frames(_make_tone())
        result = apply_degradation(frames, noise_snr_db=10)
        assert len(result) == len(frames)
        assert any(r.data != o.data for r, o in zip(result, frames, strict=False))

    def test_bandwidth_narrowband(self):
        frames = _make_frames(_make_tone(frequency=6000))
        result = apply_degradation(frames, bandwidth="narrowband")
        # High freq attenuated
        orig_rms = sum(compute_rms(f.data) for f in frames)
        result_rms = sum(compute_rms(f.data) for f in result)
        assert result_rms < orig_rms

    def test_bandwidth_wideband(self):
        frames = _make_frames(_make_tone(frequency=6000))
        result = apply_degradation(frames, bandwidth="wideband")
        assert len(result) == len(frames)

    def test_codec_mulaw(self):
        frames = _make_frames(_make_tone())
        result = apply_degradation(frames, codec="mulaw")
        assert any(r.data != o.data for r, o in zip(result, frames, strict=False))

    def test_packet_loss(self):
        frames = _make_frames(_make_tone(duration_s=1.0))
        result = apply_degradation(frames, packet_loss_pct=50)
        silent = sum(1 for f in result if f.data == b"\x00" * len(f.data))
        assert silent > 0

    def test_all_effects_chained(self):
        frames = _make_frames(_make_tone(duration_s=0.5))
        result = apply_degradation(
            frames,
            noise_snr_db=15,
            bandwidth="narrowband",
            packet_loss_pct=10,
            codec="mulaw",
        )
        assert len(result) == len(frames)

    def test_empty_frames(self):
        assert apply_degradation([]) == []

    def test_preserves_frame_metadata(self):
        frames = _make_frames(_make_tone(), sample_rate=24000)
        result = apply_degradation(frames, noise_snr_db=20)
        for f in result:
            assert f.sample_rate == 24000


def _rms_diff(a: bytes, b: bytes) -> float:
    """Compute RMS of the difference between two PCM signals."""
    n = len(a) // 2
    sa = struct.unpack(f"<{n}h", a[: n * 2])
    sb = struct.unpack(f"<{n}h", b[: n * 2])
    diff_sq = sum((x - y) ** 2 for x, y in zip(sa, sb, strict=False))
    return (diff_sq / n) ** 0.5 if n else 0.0
