"""STT providers — transcribe agent audio responses to text for evaluation."""

from __future__ import annotations

import io
import logging
import wave
from abc import ABC, abstractmethod

from voicecheck.core.types import AudioFrame, TranscriptSegment

logger = logging.getLogger("voicecheck.audio.stt")


class STTProvider(ABC):
    """Base class for speech-to-text providers."""

    @abstractmethod
    async def transcribe(self, frames: list[AudioFrame]) -> TranscriptSegment:
        """Transcribe audio frames to text.

        Args:
            frames: List of AudioFrame objects (16-bit PCM).

        Returns:
            TranscriptSegment with the transcribed text.
        """


class WhisperSTTProvider(STTProvider):
    """Local Whisper STT using faster-whisper for speed."""

    def __init__(
        self,
        model_size: str = "base",
        language: str = "en",
        device: str = "auto",
    ) -> None:
        self.model_size = model_size
        self.language = language
        self.device = device
        self._model = None

    def _get_model(self) -> object:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise ImportError("faster-whisper not installed. Run: pip install voicecheck[stt]")
            logger.info("Loading Whisper model: %s", self.model_size)
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type="int8",
            )
        return self._model

    async def transcribe(self, frames: list[AudioFrame]) -> TranscriptSegment:
        if not frames:
            return TranscriptSegment(text="")

        model = self._get_model()

        # Combine all frames into a single WAV in memory
        wav_bytes = _frames_to_wav(frames)

        # faster-whisper can read from a file-like object
        segments, info = model.transcribe(
            io.BytesIO(wav_bytes),
            language=self.language,
            vad_filter=True,
        )

        # Collect all segment text
        text_parts: list[str] = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        full_text = " ".join(text_parts)
        logger.info("Transcribed: %s", full_text[:100])

        return TranscriptSegment(
            text=full_text,
            confidence=getattr(info, "language_probability", 1.0),
        )


class OpenAISTTProvider(STTProvider):
    """OpenAI Whisper API — cloud-based STT, no local model needed.

    Requires OPENAI_API_KEY environment variable.
    """

    def __init__(
        self,
        model: str = "whisper-1",
        language: str = "en",
    ) -> None:
        self.model = model
        self.language = language
        self._client: object | None = None

    async def transcribe(self, frames: list[AudioFrame]) -> TranscriptSegment:
        if not frames:
            return TranscriptSegment(text="")

        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ImportError("openai not installed. Run: pip install voicecheck[llm]")
            self._client = AsyncOpenAI()

        client = self._client
        wav_bytes = _frames_to_wav(frames)

        transcript = await client.audio.transcriptions.create(
            model=self.model,
            file=("audio.wav", wav_bytes),
            language=self.language,
        )

        text = transcript.text.strip()
        logger.info("Transcribed (OpenAI): %s", text[:100])
        return TranscriptSegment(text=text)


# ── Provider registry ────────────────────────────────────────────

_STT_PROVIDERS: dict[str, type[STTProvider]] = {
    "whisper": WhisperSTTProvider,
    "openai": OpenAISTTProvider,
}


def get_stt_provider(name: str, **kwargs: object) -> STTProvider:
    """Get an STT provider by name."""
    if name not in _STT_PROVIDERS:
        available = ", ".join(_STT_PROVIDERS.keys())
        raise ValueError(f"Unknown STT provider: {name!r}. Available: {available}")
    return _STT_PROVIDERS[name](**kwargs)


# ── Audio helpers ────────────────────────────────────────────────


def _frames_to_wav(frames: list[AudioFrame]) -> bytes:
    """Combine AudioFrames into a WAV file in memory."""
    if not frames:
        return b""

    sample_rate = frames[0].sample_rate
    num_channels = frames[0].num_channels

    pcm_data = b"".join(f.data for f in frames)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)

    return buf.getvalue()
