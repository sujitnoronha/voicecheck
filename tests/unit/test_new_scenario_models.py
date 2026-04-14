"""Tests for new scenario models: InterruptConfig, SilenceConfig, DegradationConfig,
and updates to TurnConfig and AudioConfig."""

import os
import tempfile
from pathlib import Path

import pytest

from voicecheck.core.scenario import (
    AudioConfig,
    DegradationConfig,
    InterruptConfig,
    Scenario,
    SilenceConfig,
    TurnConfig,
    load_scenario,
    validate_scenario,
)

# Register evaluators for validation
import voicecheck.evaluators.keyword  # noqa: F401
import voicecheck.evaluators.latency  # noqa: F401
import voicecheck.evaluators.turn_count  # noqa: F401


def _write_yaml(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


class TestInterruptConfig:
    def test_basic_creation(self):
        cfg = InterruptConfig(after_ms=2000, with_text="Stop")
        assert cfg.after_ms == 2000
        assert cfg.with_text == "Stop"

    def test_alias_with(self):
        # "with" is the YAML key, mapped to with_text
        cfg = InterruptConfig(**{"after_ms": 1500, "with": "Wait"})
        assert cfg.with_text == "Wait"

    def test_from_yaml(self):
        path = _write_yaml("""
name: test
turns:
  - user: "Tell me a story"
    interrupt:
      after_ms: 2000
      with: "Stop talking"
    expect: []
""")
        try:
            scenario = load_scenario(path)
            assert scenario.turns[0].interrupt is not None
            assert scenario.turns[0].interrupt.after_ms == 2000
            assert scenario.turns[0].interrupt.with_text == "Stop talking"
        finally:
            os.unlink(path)


class TestSilenceConfig:
    def test_basic_creation(self):
        cfg = SilenceConfig(duration_s=10.0)
        assert cfg.duration_s == 10.0

    def test_from_yaml(self):
        path = _write_yaml("""
name: test
turns:
  - silence:
      duration_s: 5.0
    expect: []
""")
        try:
            scenario = load_scenario(path)
            assert scenario.turns[0].silence is not None
            assert scenario.turns[0].silence.duration_s == 5.0
            assert scenario.turns[0].user == ""  # no user text for silence turns
        finally:
            os.unlink(path)


class TestDegradationConfig:
    def test_all_fields(self):
        cfg = DegradationConfig(
            noise_snr_db=15.0,
            bandwidth="narrowband",
            packet_loss_pct=5.0,
            codec="mulaw",
        )
        assert cfg.noise_snr_db == 15.0
        assert cfg.bandwidth == "narrowband"
        assert cfg.packet_loss_pct == 5.0
        assert cfg.codec == "mulaw"

    def test_all_none(self):
        cfg = DegradationConfig()
        assert cfg.noise_snr_db is None
        assert cfg.bandwidth is None
        assert cfg.packet_loss_pct is None
        assert cfg.codec is None

    def test_partial_config(self):
        cfg = DegradationConfig(noise_snr_db=20.0)
        assert cfg.noise_snr_db == 20.0
        assert cfg.bandwidth is None


class TestTurnConfigExtensions:
    def test_default_user_empty(self):
        cfg = TurnConfig()
        assert cfg.user == ""

    def test_with_user_text(self):
        cfg = TurnConfig(user="Hello")
        assert cfg.user == "Hello"
        assert cfg.interrupt is None
        assert cfg.silence is None
        assert cfg.pause_before_ms == 0

    def test_with_interrupt(self):
        cfg = TurnConfig(
            user="Tell me a story",
            interrupt=InterruptConfig(after_ms=1000, with_text="Stop"),
        )
        assert cfg.interrupt.after_ms == 1000

    def test_with_silence(self):
        cfg = TurnConfig(silence=SilenceConfig(duration_s=5.0))
        assert cfg.silence.duration_s == 5.0

    def test_with_pause(self):
        cfg = TurnConfig(user="Hello", pause_before_ms=3000)
        assert cfg.pause_before_ms == 3000


class TestAudioConfigExtensions:
    def test_language_default_empty(self):
        cfg = AudioConfig()
        assert cfg.language == ""

    def test_language_set(self):
        cfg = AudioConfig(language="es")
        assert cfg.language == "es"

    def test_degradation_none_by_default(self):
        cfg = AudioConfig()
        assert cfg.degradation is None

    def test_degradation_set(self):
        cfg = AudioConfig(
            degradation=DegradationConfig(noise_snr_db=15.0, bandwidth="narrowband"),
        )
        assert cfg.degradation.noise_snr_db == 15.0

    def test_from_yaml(self):
        path = _write_yaml("""
name: test
audio:
  tts_provider: edge
  stt_provider: whisper
  language: fr
  degradation:
    noise_snr_db: 20
    bandwidth: narrowband
    codec: mulaw
turns:
  - user: "Bonjour"
""")
        try:
            scenario = load_scenario(path)
            assert scenario.audio.language == "fr"
            assert scenario.audio.degradation is not None
            assert scenario.audio.degradation.noise_snr_db == 20
            assert scenario.audio.degradation.bandwidth == "narrowband"
            assert scenario.audio.degradation.codec == "mulaw"
        finally:
            os.unlink(path)


class TestScenarioBackwardCompatibility:
    def test_existing_scenario_unchanged(self):
        """Existing YAML without new fields should load fine."""
        path = _write_yaml("""
name: legacy-test
turns:
  - user: "Hello"
    expect:
      - type: keyword
        must_contain: ["hi"]
settings:
  turn_timeout: 10.0
""")
        try:
            scenario = load_scenario(path)
            assert scenario.name == "legacy-test"
            assert len(scenario.turns) == 1
            assert scenario.turns[0].user == "Hello"
            assert scenario.turns[0].interrupt is None
            assert scenario.turns[0].silence is None
            assert scenario.turns[0].pause_before_ms == 0
            assert scenario.audio.language == ""
            assert scenario.audio.degradation is None
        finally:
            os.unlink(path)

    def test_full_featured_scenario(self):
        """Scenario using all new features loads correctly."""
        path = _write_yaml("""
name: full-featured
audio:
  language: ja
  degradation:
    noise_snr_db: 15
    bandwidth: narrowband
    packet_loss_pct: 5
    codec: mulaw
turns:
  - silence:
      duration_s: 3
    expect: []
  - user: "Hello"
    pause_before_ms: 1000
    expect:
      - type: latency
        max_first_byte_ms: 5000
  - user: "Tell me a story"
    interrupt:
      after_ms: 2000
      with: "Wait, stop"
    expect: []
""")
        try:
            scenario = load_scenario(path)
            assert scenario.audio.language == "ja"
            assert scenario.audio.degradation.noise_snr_db == 15
            assert len(scenario.turns) == 3
            assert scenario.turns[0].silence.duration_s == 3
            assert scenario.turns[1].pause_before_ms == 1000
            assert scenario.turns[2].interrupt.after_ms == 2000
            assert scenario.turns[2].interrupt.with_text == "Wait, stop"
        finally:
            os.unlink(path)


class TestScenarioRunnerHelpers:
    """Test the new helper methods on ScenarioRunner."""

    def test_create_providers_with_language(self):
        from voicecheck.core.scenario import ScenarioRunner
        path = _write_yaml("""
name: test
audio:
  language: es
turns:
  - user: "Hola"
""")
        try:
            scenario = load_scenario(path)
            runner = ScenarioRunner(scenario)
            tts, stt = runner._create_providers()
            # TTS should have Spanish voice
            assert hasattr(tts, "voice")
            assert "es" in tts.voice.lower()
        finally:
            os.unlink(path)

    def test_create_providers_no_language(self):
        from voicecheck.core.scenario import ScenarioRunner
        path = _write_yaml("""
name: test
turns:
  - user: "Hello"
""")
        try:
            scenario = load_scenario(path)
            runner = ScenarioRunner(scenario)
            tts, stt = runner._create_providers()
            assert hasattr(tts, "voice")
        finally:
            os.unlink(path)

    def test_apply_degradation_noop(self):
        from voicecheck.core.scenario import ScenarioRunner
        from voicecheck.core.types import AudioFrame
        path = _write_yaml("""
name: test
turns:
  - user: "Hello"
""")
        try:
            scenario = load_scenario(path)
            runner = ScenarioRunner(scenario)
            frames = [AudioFrame(data=b"\x01\x02" * 320, sample_rate=16000)]
            result = runner._apply_degradation(frames)
            assert result[0].data == frames[0].data  # unchanged
        finally:
            os.unlink(path)

    def test_apply_degradation_with_noise(self):
        from voicecheck.core.scenario import ScenarioRunner
        from voicecheck.core.types import AudioFrame
        import struct
        import math

        # Generate a non-silent tone
        samples = [int(10000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(320)]
        pcm = struct.pack(f"<{len(samples)}h", *samples)

        path = _write_yaml("""
name: test
audio:
  degradation:
    noise_snr_db: 10
turns:
  - user: "Hello"
""")
        try:
            scenario = load_scenario(path)
            runner = ScenarioRunner(scenario)
            frames = [AudioFrame(data=pcm, sample_rate=16000)]
            result = runner._apply_degradation(frames)
            assert result[0].data != frames[0].data  # modified by noise
        finally:
            os.unlink(path)
