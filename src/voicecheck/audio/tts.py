"""TTS providers — synthesize text to audio frames for sending to agents."""

from __future__ import annotations

import io
import logging
import wave
from abc import ABC, abstractmethod
from pathlib import Path

from voicecheck.audio.utils import resample_pcm
from voicecheck.core.types import AudioFrame

logger = logging.getLogger("voicecheck.audio.tts")


class TTSProvider(ABC):
    """Base class for text-to-speech providers."""

    @abstractmethod
    async def synthesize(self, text: str) -> list[AudioFrame]:
        """Convert text to a list of AudioFrames (16-bit PCM)."""


# Language code → default Edge TTS voice mapping
EDGE_TTS_VOICES: dict[str, str] = {
    "en": "en-US-JennyNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "it": "it-IT-ElsaNeural",
    "hi": "hi-IN-SwaraNeural",
    "ar": "ar-SA-ZariyahNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "nl": "nl-NL-ColetteNeural",
    "pl": "pl-PL-AgnieszkaNeural",
    "sv": "sv-SE-SofieNeural",
    "tr": "tr-TR-EmelNeural",
    "th": "th-TH-PremwadeeNeural",
    "vi": "vi-VN-HoaiMyNeural",
}


class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge TTS — free, no API key required.

    Supports automatic language-to-voice mapping via the ``language``
    parameter. If both ``voice`` and ``language`` are given, ``voice``
    takes precedence.
    """

    def __init__(
        self,
        voice: str = "",
        sample_rate: int = 16000,
        language: str = "",
    ) -> None:
        if voice:
            self.voice = voice
        elif language and language in EDGE_TTS_VOICES:
            self.voice = EDGE_TTS_VOICES[language]
        else:
            self.voice = "en-US-JennyNeural"
        self.sample_rate = sample_rate

    async def synthesize(self, text: str) -> list[AudioFrame]:
        try:
            import edge_tts
        except ImportError:
            raise ImportError("edge-tts not installed. Run: pip install voicecheck[tts]")

        communicate = edge_tts.Communicate(text, self.voice)

        # Edge TTS returns MP3 chunks — collect them all
        mp3_data = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_data.extend(chunk["data"])

        if not mp3_data:
            logger.warning("Edge TTS returned no audio for text: %s", text[:50])
            return []

        # Convert MP3 to PCM using pydub (commonly available) or raw fallback
        pcm_data = _mp3_to_pcm(bytes(mp3_data), self.sample_rate)
        frames = _pcm_to_frames(pcm_data, self.sample_rate)
        logger.info(
            "Synthesized %d frames (%.1fs) for: %s",
            len(frames),
            sum(f.duration_s for f in frames),
            text[:60],
        )
        return frames


class OpenAITTSProvider(TTSProvider):
    """OpenAI TTS — high-quality voices via the API.

    Requires OPENAI_API_KEY environment variable.
    Available voices: alloy, echo, fable, onyx, nova, shimmer.
    """

    def __init__(
        self,
        voice: str = "alloy",
        model: str = "tts-1",
        sample_rate: int = 16000,
    ) -> None:
        self.voice = voice
        self.model = model
        self.sample_rate = sample_rate
        self._client: object | None = None

    async def synthesize(self, text: str) -> list[AudioFrame]:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("openai not installed. Run: pip install voicecheck[llm]")
            self._client = AsyncOpenAI()

        client = self._client
        response = await client.audio.speech.create(
            model=self.model,
            voice=self.voice,
            input=text,
            response_format="pcm",  # Raw 24kHz 16-bit mono PCM
        )

        pcm_24k = response.read()
        if not pcm_24k:
            logger.warning("OpenAI TTS returned no audio for text: %s", text[:50])
            return []

        # OpenAI TTS pcm format is always 24kHz — resample to target
        if self.sample_rate != 24000:
            pcm_data = resample_pcm(pcm_24k, 24000, self.sample_rate)
        else:
            pcm_data = pcm_24k

        frames = _pcm_to_frames(pcm_data, self.sample_rate)
        logger.info(
            "Synthesized %d frames (%.1fs) via OpenAI TTS for: %s",
            len(frames),
            sum(f.duration_s for f in frames),
            text[:60],
        )
        return frames


class FileAudioProvider(TTSProvider):
    """Load audio from a local WAV file instead of synthesizing.

    Useful for testing with pre-recorded audio clips.
    """

    def __init__(self, file_path: str | Path) -> None:
        self.file_path = Path(file_path)

    async def synthesize(self, text: str) -> list[AudioFrame]:
        """Load audio from file. `text` is ignored — the file is the audio."""
        if not self.file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {self.file_path}")

        with wave.open(str(self.file_path), "rb") as wf:
            sample_rate = wf.getframerate()
            num_channels = wf.getnchannels()
            pcm_data = wf.readframes(wf.getnframes())

        frames = _pcm_to_frames(pcm_data, sample_rate, num_channels)
        logger.info(
            "Loaded %d frames from %s (%.1fs)",
            len(frames),
            self.file_path.name,
            sum(f.duration_s for f in frames),
        )
        return frames


# ── Provider registry ────────────────────────────────────────────

_TTS_PROVIDERS: dict[str, type[TTSProvider]] = {
    "edge": EdgeTTSProvider,
    "openai": OpenAITTSProvider,
    "file": FileAudioProvider,
}


def get_tts_provider(name: str, **kwargs: object) -> TTSProvider:
    """Get a TTS provider by name."""
    if name not in _TTS_PROVIDERS:
        available = ", ".join(_TTS_PROVIDERS.keys())
        raise ValueError(f"Unknown TTS provider: {name!r}. Available: {available}")
    return _TTS_PROVIDERS[name](**kwargs)


# ── Audio conversion helpers ─────────────────────────────────────


def _mp3_to_pcm(mp3_data: bytes, target_sample_rate: int = 16000) -> bytes:
    """Convert MP3 bytes to 16-bit PCM at the target sample rate."""
    try:
        from pydub import AudioSegment
    except ImportError as e:
        # pydub itself imports `audioop`, which Python 3.13 removed (PEP 594).
        # The [tts] extra pulls in audioop-lts automatically on 3.13+, but if
        # the user installed pydub manually they may hit this.
        detail = f"Original error: {e}"
        raise ImportError(
            f"pydub could not be imported. {detail}\n"
            "Fix: pip install 'voicecheck[tts]' (or on Python 3.13+: pip install audioop-lts)\n"
            "Also ensure ffmpeg is installed on your system."
        ) from e

    audio = AudioSegment.from_mp3(io.BytesIO(mp3_data))
    audio = audio.set_frame_rate(target_sample_rate).set_channels(1).set_sample_width(2)
    return audio.raw_data


def _pcm_to_frames(
    pcm_data: bytes,
    sample_rate: int = 16000,
    num_channels: int = 1,
    frame_duration_ms: int = 20,
) -> list[AudioFrame]:
    """Split raw PCM bytes into fixed-duration AudioFrame chunks.

    Args:
        pcm_data: Raw 16-bit PCM bytes.
        sample_rate: Sample rate in Hz.
        num_channels: Number of audio channels.
        frame_duration_ms: Duration of each frame in milliseconds.

    Returns:
        List of AudioFrame objects.
    """
    samples_per_frame = int(sample_rate * frame_duration_ms / 1000)
    bytes_per_frame = samples_per_frame * num_channels * 2  # 16-bit = 2 bytes

    frames: list[AudioFrame] = []
    for i in range(0, len(pcm_data), bytes_per_frame):
        chunk = pcm_data[i : i + bytes_per_frame]
        # Pad last chunk if needed
        if len(chunk) < bytes_per_frame:
            chunk = chunk + b"\x00" * (bytes_per_frame - len(chunk))

        frames.append(
            AudioFrame(
                data=chunk,
                sample_rate=sample_rate,
                num_channels=num_channels,
                samples_per_channel=samples_per_frame,
            )
        )

    return frames
