"""Tests for silence generation and multi-language support."""

import pytest

from voicecheck.audio.utils import generate_silence, compute_rms, is_silent
from voicecheck.audio.tts import EDGE_TTS_VOICES, EdgeTTSProvider
from voicecheck.core.types import AudioFrame


class TestGenerateSilence:
    def test_correct_frame_count(self):
        frames = generate_silence(duration_s=1.0, sample_rate=16000, frame_duration_ms=20)
        assert len(frames) == 50  # 1000ms / 20ms

    def test_frames_are_silent(self):
        frames = generate_silence(duration_s=0.5)
        for f in frames:
            assert is_silent(f.data, threshold=1)
            assert compute_rms(f.data) == 0.0

    def test_frame_metadata(self):
        frames = generate_silence(duration_s=0.1, sample_rate=24000, num_channels=1)
        for f in frames:
            assert f.sample_rate == 24000
            assert f.num_channels == 1
            assert f.samples_per_channel == 480  # 24000 * 20ms

    def test_correct_byte_length(self):
        frames = generate_silence(duration_s=0.1, sample_rate=16000)
        for f in frames:
            expected_bytes = 320 * 2  # 320 samples * 2 bytes (16-bit)
            assert len(f.data) == expected_bytes

    def test_total_duration(self):
        frames = generate_silence(duration_s=2.0)
        total = sum(f.duration_s for f in frames)
        assert abs(total - 2.0) < 0.01

    def test_zero_duration(self):
        frames = generate_silence(duration_s=0.0)
        assert frames == []

    def test_short_duration(self):
        frames = generate_silence(duration_s=0.02)  # exactly 1 frame
        assert len(frames) == 1

    def test_custom_frame_duration(self):
        frames = generate_silence(duration_s=0.1, frame_duration_ms=10)
        assert len(frames) == 10  # 100ms / 10ms

    def test_stereo_silence(self):
        frames = generate_silence(duration_s=0.1, num_channels=2)
        for f in frames:
            assert f.num_channels == 2
            # 2 channels * 320 samples * 2 bytes
            assert len(f.data) == 320 * 2 * 2


class TestEdgeTTSVoices:
    def test_all_languages_have_voices(self):
        expected = ["en", "es", "fr", "de", "pt", "ja", "ko", "zh",
                    "it", "hi", "ar", "ru", "nl", "pl", "sv", "tr", "th", "vi"]
        for lang in expected:
            assert lang in EDGE_TTS_VOICES, f"Missing voice for {lang}"

    def test_voice_format(self):
        for lang, voice in EDGE_TTS_VOICES.items():
            assert "Neural" in voice, f"{lang}: {voice} should end with Neural"
            assert "-" in voice, f"{lang}: {voice} should have locale format"


class TestEdgeTTSLanguageSelection:
    def test_language_selects_voice(self):
        provider = EdgeTTSProvider(language="es")
        assert provider.voice == "es-ES-ElviraNeural"

    def test_language_selects_french(self):
        provider = EdgeTTSProvider(language="fr")
        assert provider.voice == "fr-FR-DeniseNeural"

    def test_voice_overrides_language(self):
        provider = EdgeTTSProvider(voice="custom-voice", language="es")
        assert provider.voice == "custom-voice"

    def test_no_language_uses_default(self):
        provider = EdgeTTSProvider()
        assert provider.voice == "en-US-JennyNeural"

    def test_unknown_language_uses_default(self):
        provider = EdgeTTSProvider(language="xx")
        assert provider.voice == "en-US-JennyNeural"

    def test_empty_voice_with_language(self):
        provider = EdgeTTSProvider(voice="", language="ja")
        assert provider.voice == "ja-JP-NanamiNeural"

    def test_sample_rate_preserved(self):
        provider = EdgeTTSProvider(language="ko", sample_rate=24000)
        assert provider.sample_rate == 24000
        assert provider.voice == "ko-KR-SunHiNeural"
