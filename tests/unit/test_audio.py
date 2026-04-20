"""Tests for audio utilities."""

import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voicecheck.audio.tts import _pcm_to_frames
from voicecheck.audio.utils import resample_pcm as _resample_pcm
from voicecheck.core.types import AudioFrame


class TestPcmToFrames:
    def test_basic_split(self):
        # 16000 Hz, 20ms frame = 320 samples = 640 bytes
        sample_rate = 16000
        frame_duration_ms = 20
        samples_per_frame = int(sample_rate * frame_duration_ms / 1000)
        bytes_per_frame = samples_per_frame * 2  # 16-bit

        # Create 3 frames worth of data
        pcm_data = b"\x00" * (bytes_per_frame * 3)
        frames = _pcm_to_frames(pcm_data, sample_rate, 1, frame_duration_ms)

        assert len(frames) == 3
        assert all(f.sample_rate == 16000 for f in frames)
        assert all(f.num_channels == 1 for f in frames)
        assert all(f.samples_per_channel == samples_per_frame for f in frames)

    def test_padding_last_frame(self):
        sample_rate = 16000
        bytes_per_frame = 640  # 320 samples * 2 bytes

        # 1.5 frames worth of data
        pcm_data = b"\x01" * (bytes_per_frame + bytes_per_frame // 2)
        frames = _pcm_to_frames(pcm_data, sample_rate)

        assert len(frames) == 2
        # Last frame should be padded to full size
        assert len(frames[-1].data) == bytes_per_frame

    def test_empty_data(self):
        frames = _pcm_to_frames(b"", 16000)
        assert len(frames) == 0


class TestAudioFrame:
    def test_duration(self):
        frame = AudioFrame(
            data=b"\x00" * 640,
            sample_rate=16000,
            num_channels=1,
        )
        assert frame.samples_per_channel == 320
        assert abs(frame.duration_s - 0.02) < 0.001  # 20ms

    def test_empty_frame(self):
        frame = AudioFrame(data=b"", sample_rate=16000, num_channels=1)
        assert frame.samples_per_channel == 0
        assert frame.duration_s == 0.0


# ── Resampling ─────────────────────────────────────────────────


class TestResamplePcm:
    def test_same_rate_returns_unchanged(self):
        pcm = struct.pack("<4h", 100, 200, 300, 400)
        result = _resample_pcm(pcm, 16000, 16000)
        assert result == pcm

    def test_downsample_24k_to_16k(self):
        # 24kHz → 16kHz is a 3:2 ratio, so 6 samples → 4 samples
        samples_24k = [1000, 2000, 3000, 4000, 5000, 6000]
        pcm_24k = struct.pack(f"<{len(samples_24k)}h", *samples_24k)
        result = _resample_pcm(pcm_24k, 24000, 16000)
        out_samples = struct.unpack(f"<{len(result) // 2}h", result)
        assert len(out_samples) == 4  # 6 * (16000/24000) = 4

    def test_preserves_amplitude_range(self):
        # Ensure no clipping beyond 16-bit range
        samples = [32767, -32768, 0, 16000, -16000, 32767]
        pcm = struct.pack(f"<{len(samples)}h", *samples)
        result = _resample_pcm(pcm, 24000, 16000)
        out_samples = struct.unpack(f"<{len(result) // 2}h", result)
        assert all(-32768 <= s <= 32767 for s in out_samples)

    def test_empty_data(self):
        result = _resample_pcm(b"", 24000, 16000)
        assert result == struct.pack("<0h")


# ── OpenAI TTS Provider ───────────────────────────────────────


class TestOpenAITTS:
    @pytest.mark.asyncio
    async def test_synthesize(self):
        from voicecheck.audio.tts import OpenAITTSProvider

        provider = OpenAITTSProvider(voice="nova", model="tts-1", sample_rate=16000)

        # Create fake 24kHz PCM (1 second = 24000 samples = 48000 bytes)
        num_samples = 2400  # 100ms at 24kHz
        fake_pcm_24k = struct.pack(f"<{num_samples}h", *([1000] * num_samples))

        mock_response = MagicMock()
        mock_response.read.return_value = fake_pcm_24k

        mock_create = AsyncMock(return_value=mock_response)

        with patch("openai.AsyncOpenAI") as MockClient:
            mock_client = MagicMock()
            mock_client.audio.speech.create = mock_create
            MockClient.return_value = mock_client

            frames = await provider.synthesize("Hello world")

        assert len(frames) > 0
        assert all(f.sample_rate == 16000 for f in frames)
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "tts-1"
        assert call_kwargs["voice"] == "nova"
        assert call_kwargs["response_format"] == "pcm"

    @pytest.mark.asyncio
    async def test_synthesize_empty_response(self):
        from voicecheck.audio.tts import OpenAITTSProvider

        provider = OpenAITTSProvider()

        mock_response = MagicMock()
        mock_response.read.return_value = b""

        with patch("openai.AsyncOpenAI") as MockClient:
            mock_client = MagicMock()
            mock_client.audio.speech.create = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            frames = await provider.synthesize("Hello")

        assert frames == []


# ── OpenAI STT Provider ───────────────────────────────────────


class TestOpenAISTT:
    @pytest.mark.asyncio
    async def test_transcribe(self):
        from voicecheck.audio.stt import OpenAISTTProvider

        provider = OpenAISTTProvider(model="whisper-1", language="en")

        # Create a valid AudioFrame
        test_frames = [
            AudioFrame(
                data=b"\x00" * 640,
                sample_rate=16000,
                num_channels=1,
                samples_per_channel=320,
            )
        ]

        mock_transcript = MagicMock()
        mock_transcript.text = "  Hello world  "

        with patch("openai.AsyncOpenAI") as MockClient:
            mock_client = MagicMock()
            mock_client.audio.transcriptions.create = AsyncMock(return_value=mock_transcript)
            MockClient.return_value = mock_client

            result = await provider.transcribe(test_frames)

        assert result.text == "Hello world"  # stripped
        mock_client.audio.transcriptions.create.assert_called_once()
        call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
        assert call_kwargs["model"] == "whisper-1"
        assert call_kwargs["language"] == "en"

    @pytest.mark.asyncio
    async def test_transcribe_empty_frames(self):
        from voicecheck.audio.stt import OpenAISTTProvider

        provider = OpenAISTTProvider()
        result = await provider.transcribe([])
        assert result.text == ""
