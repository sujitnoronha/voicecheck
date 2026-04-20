"""Shared audio utilities used across transports and audio providers.

Provides silence detection, RMS computation, G.711 mu-law codec, and PCM resampling.
"""

from __future__ import annotations

import struct

# Default RMS threshold for silence detection (16-bit PCM).
# Typical background noise / codec artifacts sit around 100-300 RMS.
# A threshold of 500 cleanly separates speech from silence in practice.
DEFAULT_SILENCE_RMS = 500

# G.711 mu-law constants
_MULAW_BIAS = 0x84
_MULAW_MAX = 0x7FFF
_MULAW_CLIP = 32635

# Pre-computed mu-law decode table (256 entries)
_MULAW_DECODE_TABLE: list[int] = []


def _build_mulaw_decode_table() -> list[int]:
    """Build the mu-law to linear PCM decode table (ITU-T G.711)."""
    table: list[int] = []
    for i in range(256):
        val = ~i
        sign = val & 0x80
        exponent = (val >> 4) & 0x07
        mantissa = val & 0x0F
        sample = ((mantissa << 3) + _MULAW_BIAS) << exponent
        sample -= _MULAW_BIAS
        if sign:
            sample = -sample
        table.append(sample)
    return table


_MULAW_DECODE_TABLE = _build_mulaw_decode_table()


def compute_rms(data: bytes | bytearray) -> float:
    """Compute root-mean-square energy of 16-bit PCM audio.

    Args:
        data: Raw 16-bit little-endian signed PCM bytes.

    Returns:
        RMS energy value. Silent audio is ~0, loud speech is ~3000-10000+.
    """
    if len(data) < 2:
        return 0.0

    num_samples = len(data) // 2
    sum_sq = 0
    for i in range(0, num_samples * 2, 2):
        sample = int.from_bytes(data[i : i + 2], byteorder="little", signed=True)
        sum_sq += sample * sample

    return (sum_sq / num_samples) ** 0.5


def is_silent(data: bytes | bytearray, threshold: int = DEFAULT_SILENCE_RMS) -> bool:
    """Check if audio frame is silent using RMS energy.

    Computes root-mean-square of 16-bit PCM samples and compares against
    the threshold. RMS is more robust than max-amplitude because it handles
    background noise and codec artifacts that produce isolated spikes.

    Args:
        data: Raw 16-bit little-endian signed PCM bytes.
        threshold: RMS threshold below which audio is considered silent.

    Returns:
        True if the audio frame is silent.
    """
    if len(data) < 2:
        return True
    return compute_rms(data) < threshold


def pcm_to_mulaw(pcm_data: bytes) -> bytes:
    """Encode 16-bit PCM to G.711 mu-law (ITU-T G.711).

    Each 16-bit PCM sample (2 bytes) is compressed to a single mu-law byte.
    Used for telephony transport (Twilio Media Streams send/receive mu-law at 8kHz).

    Args:
        pcm_data: Raw 16-bit little-endian signed PCM bytes.

    Returns:
        Mu-law encoded bytes (half the size of input).
    """
    num_samples = len(pcm_data) // 2
    result = bytearray(num_samples)

    for i in range(num_samples):
        sample = int.from_bytes(pcm_data[i * 2 : i * 2 + 2], byteorder="little", signed=True)

        # Get sign bit
        sign = 0
        if sample < 0:
            sign = 0x80
            sample = -sample

        # Clip to max
        if sample > _MULAW_CLIP:
            sample = _MULAW_CLIP

        sample += _MULAW_BIAS

        # Find exponent and mantissa
        exponent = 7
        mask = 0x4000
        for exp in range(7, 0, -1):
            if sample & mask:
                exponent = exp
                break
            mask >>= 1
        else:
            exponent = 0

        mantissa = (sample >> (exponent + 3)) & 0x0F
        mulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
        result[i] = mulaw_byte

    return bytes(result)


def mulaw_to_pcm(mulaw_data: bytes) -> bytes:
    """Decode G.711 mu-law to 16-bit PCM (ITU-T G.711).

    Each mu-law byte is expanded to a 16-bit PCM sample (2 bytes).
    Used for telephony transport (Twilio Media Streams send mu-law at 8kHz).

    Args:
        mulaw_data: Mu-law encoded bytes.

    Returns:
        Raw 16-bit little-endian signed PCM bytes (double the size of input).
    """
    num_samples = len(mulaw_data)
    result = bytearray(num_samples * 2)

    for i in range(num_samples):
        sample = _MULAW_DECODE_TABLE[mulaw_data[i]]
        result[i * 2] = sample & 0xFF
        result[i * 2 + 1] = (sample >> 8) & 0xFF

    return bytes(result)


def generate_silence(
    duration_s: float,
    sample_rate: int = 16000,
    num_channels: int = 1,
    frame_duration_ms: int = 20,
) -> list:
    """Generate silent AudioFrames (PCM zeros) for the specified duration.

    Args:
        duration_s: Duration of silence in seconds.
        sample_rate: Sample rate in Hz.
        num_channels: Number of audio channels.
        frame_duration_ms: Duration of each frame in milliseconds.

    Returns:
        List of AudioFrame objects containing silence.
    """
    from voicecheck.core.types import AudioFrame

    samples_per_frame = int(sample_rate * frame_duration_ms / 1000)
    bytes_per_frame = samples_per_frame * num_channels * 2  # 16-bit PCM
    total_frames = int(duration_s * 1000 / frame_duration_ms)

    frames = []
    for _ in range(total_frames):
        frames.append(
            AudioFrame(
                data=b"\x00" * bytes_per_frame,
                sample_rate=sample_rate,
                num_channels=num_channels,
                samples_per_channel=samples_per_frame,
            )
        )
    return frames


def resample_pcm(
    pcm_data: bytes,
    source_rate: int,
    target_rate: int,
) -> bytes:
    """Resample 16-bit PCM from source_rate to target_rate using linear interpolation.

    This avoids heavy dependencies like scipy or librosa for a simple resample.
    Suitable for speech audio where perfect quality isn't critical (testing context).

    Args:
        pcm_data: Raw 16-bit little-endian signed PCM bytes.
        source_rate: Source sample rate in Hz (e.g., 24000).
        target_rate: Target sample rate in Hz (e.g., 16000).

    Returns:
        Resampled PCM bytes at the target rate.
    """
    if source_rate == target_rate:
        return pcm_data

    num_samples = len(pcm_data) // 2
    if num_samples == 0:
        return pcm_data

    samples = struct.unpack(f"<{num_samples}h", pcm_data)

    ratio = source_rate / target_rate
    out_len = int(num_samples / ratio)
    resampled: list[int] = []

    for i in range(out_len):
        src_pos = i * ratio
        idx = int(src_pos)
        frac = src_pos - idx

        if idx + 1 < num_samples:
            val = samples[idx] * (1 - frac) + samples[idx + 1] * frac
        else:
            val = samples[idx] if idx < num_samples else 0

        resampled.append(int(max(-32768, min(32767, val))))

    return struct.pack(f"<{len(resampled)}h", *resampled)
