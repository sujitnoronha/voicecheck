"""Audio degradation effects for testing voice agent robustness.

Simulates real-world audio conditions: background noise, narrow bandwidth
(telephony), codec artifacts, and packet loss. All functions operate on
raw 16-bit PCM (little-endian, signed) — pure Python, no numpy/scipy.
"""

from __future__ import annotations

import math
import random
import struct

from voicecheck.audio.utils import mulaw_to_pcm, pcm_to_mulaw
from voicecheck.core.types import AudioFrame


def add_gaussian_noise(pcm_data: bytes, snr_db: float) -> bytes:
    """Add Gaussian noise at the specified signal-to-noise ratio.

    Uses the Box-Muller transform for Gaussian random number generation.

    Args:
        pcm_data: Raw 16-bit PCM bytes.
        snr_db: Signal-to-noise ratio in decibels. Lower = noisier.
                 20 dB = light noise, 10 dB = noisy, 5 dB = very noisy.

    Returns:
        PCM bytes with added noise (same length as input).
    """
    num_samples = len(pcm_data) // 2
    if num_samples == 0:
        return pcm_data

    # Truncate to even byte boundary to prevent struct.unpack errors
    pcm_data = pcm_data[: num_samples * 2]
    samples = struct.unpack(f"<{num_samples}h", pcm_data)

    # Compute signal RMS
    sum_sq = sum(s * s for s in samples)
    signal_rms = math.sqrt(sum_sq / num_samples) if num_samples else 0.0

    if signal_rms == 0:
        return pcm_data

    # Derive noise amplitude from SNR: noise_rms = signal_rms / 10^(snr_db/20)
    noise_rms = signal_rms / (10 ** (snr_db / 20))

    # Box-Muller Gaussian noise generation
    noisy: list[int] = []
    for i in range(num_samples):
        # Generate Gaussian noise using Box-Muller
        u1 = max(random.random(), 1e-10)
        u2 = random.random()
        z = math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)
        noise = noise_rms * z
        val = int(samples[i] + noise)
        noisy.append(max(-32768, min(32767, val)))

    return struct.pack(f"<{num_samples}h", *noisy)


def lowpass_filter(pcm_data: bytes, cutoff_hz: int, sample_rate: int) -> bytes:
    """Apply a single-pole IIR low-pass filter.

    Simulates narrowband telephony (cutoff ~3400 Hz for G.711) or
    wideband conditions. First-order filter is adequate for this purpose.

    Args:
        pcm_data: Raw 16-bit PCM bytes.
        cutoff_hz: Cutoff frequency in Hz.
        sample_rate: Sample rate of the audio.

    Returns:
        Filtered PCM bytes.
    """
    num_samples = len(pcm_data) // 2
    if num_samples == 0:
        return pcm_data

    pcm_data = pcm_data[: num_samples * 2]
    samples = struct.unpack(f"<{num_samples}h", pcm_data)

    # Single-pole IIR: y[n] = alpha * x[n] + (1 - alpha) * y[n-1]
    dt = 1.0 / sample_rate
    rc = 1.0 / (2 * math.pi * cutoff_hz)
    alpha = dt / (rc + dt)

    filtered: list[int] = []
    prev = float(samples[0])
    for s in samples:
        prev = alpha * s + (1 - alpha) * prev
        filtered.append(max(-32768, min(32767, int(prev))))

    return struct.pack(f"<{num_samples}h", *filtered)


def simulate_packet_loss(frames: list[AudioFrame], loss_pct: float) -> list[AudioFrame]:
    """Randomly zero out frames to simulate network packet loss.

    Args:
        frames: List of AudioFrame objects.
        loss_pct: Percentage of frames to drop (0-100).

    Returns:
        New list of AudioFrame objects with some frames replaced by silence.
    """
    result = []
    for frame in frames:
        if random.random() * 100 < loss_pct:
            # Replace with silence (same dimensions)
            result.append(
                AudioFrame(
                    data=b"\x00" * len(frame.data),
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                    samples_per_channel=frame.samples_per_channel,
                )
            )
        else:
            result.append(frame)
    return result


def codec_roundtrip(pcm_data: bytes) -> bytes:
    """PCM → mu-law → PCM round-trip to simulate telephony codec artifacts.

    G.711 mu-law is a lossy compression used in telephony networks.
    The round-trip introduces quantization noise similar to a real phone call.

    Args:
        pcm_data: Raw 16-bit PCM bytes.

    Returns:
        PCM bytes after codec degradation.
    """
    return mulaw_to_pcm(pcm_to_mulaw(pcm_data))


def apply_degradation(
    frames: list[AudioFrame],
    noise_snr_db: float | None = None,
    bandwidth: str | None = None,
    packet_loss_pct: float | None = None,
    codec: str | None = None,
) -> list[AudioFrame]:
    """Apply a chain of degradation effects to audio frames.

    Effects are applied in order: noise → bandwidth → codec → packet loss.
    Each effect is optional — only applied when the corresponding parameter
    is not None.

    Args:
        frames: Input audio frames.
        noise_snr_db: Signal-to-noise ratio for Gaussian noise (dB).
        bandwidth: "narrowband" (3400 Hz cutoff) or "wideband" (7000 Hz).
        packet_loss_pct: Percentage of frames to zero out (0-100).
        codec: "mulaw" for G.711 round-trip degradation.

    Returns:
        New list of degraded AudioFrame objects.
    """
    if not frames:
        return frames

    result = list(frames)
    sample_rate = frames[0].sample_rate

    # Step 1: Add noise
    if noise_snr_db is not None:
        result = [
            AudioFrame(
                data=add_gaussian_noise(f.data, noise_snr_db),
                sample_rate=f.sample_rate,
                num_channels=f.num_channels,
                samples_per_channel=f.samples_per_channel,
            )
            for f in result
        ]

    # Step 2: Bandwidth reduction (low-pass filter)
    if bandwidth:
        cutoff = 3400 if bandwidth == "narrowband" else 7000
        result = [
            AudioFrame(
                data=lowpass_filter(f.data, cutoff, sample_rate),
                sample_rate=f.sample_rate,
                num_channels=f.num_channels,
                samples_per_channel=f.samples_per_channel,
            )
            for f in result
        ]

    # Step 3: Codec degradation
    if codec == "mulaw":
        result = [
            AudioFrame(
                data=codec_roundtrip(f.data),
                sample_rate=f.sample_rate,
                num_channels=f.num_channels,
                samples_per_channel=f.samples_per_channel,
            )
            for f in result
        ]

    # Step 4: Packet loss (operates on whole frames)
    if packet_loss_pct is not None and packet_loss_pct > 0:
        result = simulate_packet_loss(result, packet_loss_pct)

    return result
