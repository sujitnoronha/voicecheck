"""Tests for scenario loading and validation."""

import os
import tempfile
from pathlib import Path

import pytest

from voicecheck.core.scenario import Scenario, load_scenario, validate_scenario

# Register evaluators for validation tests
import voicecheck.evaluators.keyword  # noqa: F401
import voicecheck.evaluators.latency  # noqa: F401
import voicecheck.evaluators.turn_count  # noqa: F401


BASIC_SCENARIO = """
name: "Test scenario"
description: "A simple test"

transport:
  type: livekit
  mode: direct
  config:
    url: "ws://localhost:7880"
    api_key: "test-key"
    api_secret: "test-secret"
    agent_name: "test-agent"

audio:
  tts_provider: edge
  stt_provider: whisper
  sample_rate: 16000

turns:
  - user: "Hello"
    expect:
      - type: latency
        max_first_byte_ms: 3000
      - type: keyword
        must_contain: ["hello", "hi"]

  - user: "Goodbye"
    expect:
      - type: turn_count
        min_words: 1

settings:
  turn_timeout: 10.0
  silence_threshold: 1.0
"""


def _write_yaml(content: str) -> Path:
    """Write YAML to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


class TestLoadScenario:
    def test_basic_load(self):
        path = _write_yaml(BASIC_SCENARIO)
        try:
            scenario = load_scenario(path)
            assert scenario.name == "Test scenario"
            assert scenario.transport.type == "livekit"
            assert scenario.transport.mode == "direct"
            assert len(scenario.turns) == 2
            assert scenario.turns[0].user == "Hello"
            assert len(scenario.turns[0].expect) == 2
            assert scenario.settings.turn_timeout == 10.0
        finally:
            os.unlink(path)

    def test_env_var_expansion(self):
        os.environ["TEST_LK_URL"] = "ws://my-server:7880"
        yaml_content = """
name: "env test"
transport:
  type: livekit
  config:
    url: "${TEST_LK_URL}"
turns:
  - user: "hi"
"""
        path = _write_yaml(yaml_content)
        try:
            scenario = load_scenario(path)
            assert scenario.transport.config["url"] == "ws://my-server:7880"
        finally:
            os.unlink(path)
            del os.environ["TEST_LK_URL"]

    def test_missing_env_var_preserved(self):
        yaml_content = """
name: "env test"
transport:
  type: livekit
  config:
    url: "${NONEXISTENT_VAR}"
turns:
  - user: "hi"
"""
        path = _write_yaml(yaml_content)
        try:
            scenario = load_scenario(path)
            assert scenario.transport.config["url"] == "${NONEXISTENT_VAR}"
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_scenario("/nonexistent/path.yaml")

    def test_empty_yaml(self):
        path = _write_yaml("")
        try:
            with pytest.raises(ValueError, match="YAML mapping"):
                load_scenario(path)
        finally:
            os.unlink(path)

    def test_defaults(self):
        yaml_content = """
name: "minimal"
turns:
  - user: "hello"
"""
        path = _write_yaml(yaml_content)
        try:
            scenario = load_scenario(path)
            assert scenario.transport.type == "livekit"
            assert scenario.audio.tts_provider == "edge"
            assert scenario.audio.stt_provider == "whisper"
            assert scenario.settings.turn_timeout == 15.0
        finally:
            os.unlink(path)


class TestValidateScenario:
    def test_valid_scenario(self):
        # Register livekit transport
        try:
            import voicecheck.transports.livekit  # noqa: F401
        except ImportError:
            pytest.skip("livekit not installed")

        path = _write_yaml(BASIC_SCENARIO)
        try:
            errors = validate_scenario(path)
            assert errors == []
        finally:
            os.unlink(path)

    def test_no_turns_no_persona(self):
        yaml_content = """
name: "empty"
turns: []
"""
        path = _write_yaml(yaml_content)
        try:
            errors = validate_scenario(path)
            assert any("turns" in e and "persona" in e for e in errors)
        finally:
            os.unlink(path)

    def test_persona_scenario_valid(self):
        """A scenario with persona (no turns) should be valid."""
        yaml_content = """
name: "persona test"
persona:
  name: "Emma"
  age: 7
  personality: "curious"
  max_turns: 3
"""
        path = _write_yaml(yaml_content)
        try:
            scenario = load_scenario(path)
            assert scenario.is_persona_mode
            assert scenario.persona.name == "Emma"
            assert scenario.persona.age == 7
            assert scenario.persona.max_turns == 3
            assert not scenario.turns
        finally:
            os.unlink(path)

    def test_persona_with_conversation_eval(self):
        yaml_content = """
name: "persona eval test"
persona:
  name: "Test Kid"
  max_turns: 2
conversation_eval:
  criteria:
    - "Agent was friendly"
    - "Agent stayed on topic"
  min_score: 0.8
  model: gpt-4o-mini
per_turn_expect:
  - type: latency
    max_first_byte_ms: 3000
"""
        path = _write_yaml(yaml_content)
        try:
            scenario = load_scenario(path)
            assert scenario.is_persona_mode
            assert scenario.conversation_eval is not None
            assert len(scenario.conversation_eval.criteria) == 2
            assert scenario.conversation_eval.min_score == 0.8
            assert len(scenario.per_turn_expect) == 1
            assert scenario.per_turn_expect[0].type == "latency"
        finally:
            os.unlink(path)

    def test_unknown_evaluator(self):
        yaml_content = """
name: "bad eval"
turns:
  - user: "hello"
    expect:
      - type: nonexistent_evaluator
"""
        path = _write_yaml(yaml_content)
        try:
            errors = validate_scenario(path)
            assert any("nonexistent_evaluator" in e for e in errors)
        finally:
            os.unlink(path)


class TestGuidedFlowScenario:
    """Tests for guided flow mode (persona + flow steps)."""

    GUIDED_YAML = """
name: "guided test"
persona:
  name: "Emma"
  age: 7
  personality: "curious"
  max_turns: 5
  opening: ""
flow:
  - name: "greeting"
    goal: "Say hello and introduce yourself"
    expect:
      - type: latency
        max_first_byte_ms: 3000
      - type: turn_count
        min_words: 3
  - name: "ask-question"
    goal: "Ask what the agent's favorite planet is"
    expect:
      - type: keyword
        must_contain: ["planet"]
  - name: "follow-up"
    goal: "Ask a follow-up question about space"
per_turn_expect:
  - type: turn_count
    min_words: 1
conversation_eval:
  criteria:
    - "Agent was friendly and engaging"
  min_score: 0.7
"""

    def test_load_guided_scenario(self):
        path = _write_yaml(self.GUIDED_YAML)
        try:
            scenario = load_scenario(path)
            assert scenario.is_guided_mode is True
            assert scenario.is_persona_mode is False
            assert scenario.persona is not None
            assert scenario.persona.name == "Emma"
            assert len(scenario.flow) == 3
            assert scenario.flow[0].name == "greeting"
            assert scenario.flow[0].goal == "Say hello and introduce yourself"
            assert len(scenario.flow[0].expect) == 2
            assert scenario.flow[1].name == "ask-question"
            assert scenario.flow[2].name == "follow-up"
            assert len(scenario.flow[2].expect) == 0
        finally:
            os.unlink(path)

    def test_guided_mode_properties(self):
        """Guided mode requires both persona and flow."""
        scenario = Scenario(
            name="test",
            persona={"name": "Emma", "age": 7},
            flow=[{"goal": "say hi"}, {"goal": "ask question"}],
        )
        assert scenario.is_guided_mode is True
        assert scenario.is_persona_mode is False

    def test_persona_only_is_not_guided(self):
        """Persona without flow is persona mode, not guided."""
        scenario = Scenario(
            name="test",
            persona={"name": "Emma"},
        )
        assert scenario.is_persona_mode is True
        assert scenario.is_guided_mode is False

    def test_flow_without_persona_validation_error(self):
        yaml_content = """
name: "bad flow"
flow:
  - goal: "say hi"
"""
        path = _write_yaml(yaml_content)
        try:
            errors = validate_scenario(path)
            assert any("persona" in e.lower() and "flow" in e.lower() for e in errors)
        finally:
            os.unlink(path)

    def test_flow_step_missing_goal_validation(self):
        yaml_content = """
name: "bad step"
persona:
  name: "Emma"
flow:
  - name: "no-goal"
    goal: ""
"""
        path = _write_yaml(yaml_content)
        try:
            errors = validate_scenario(path)
            assert any("goal" in e.lower() for e in errors)
        finally:
            os.unlink(path)

    def test_flow_step_bad_evaluator_validation(self):
        yaml_content = """
name: "bad eval in flow"
persona:
  name: "Emma"
flow:
  - goal: "say hi"
    expect:
      - type: nonexistent_evaluator
"""
        path = _write_yaml(yaml_content)
        try:
            errors = validate_scenario(path)
            assert any("nonexistent_evaluator" in e for e in errors)
        finally:
            os.unlink(path)

    def test_validate_guided_valid(self):
        """Full guided scenario should pass validation."""
        try:
            import voicecheck.transports.livekit  # noqa: F401
        except ImportError:
            pytest.skip("livekit not installed")

        yaml_content = """
name: "valid guided"
transport:
  type: livekit
  mode: direct
  config:
    url: "ws://localhost:7880"
    api_key: "test-key"
    api_secret: "test-secret"
persona:
  name: "Emma"
  age: 7
flow:
  - name: "greeting"
    goal: "Say hello"
    expect:
      - type: latency
        max_first_byte_ms: 3000
  - name: "question"
    goal: "Ask about stars"
per_turn_expect:
  - type: turn_count
    min_words: 1
"""
        path = _write_yaml(yaml_content)
        try:
            errors = validate_scenario(path)
            assert errors == []
        finally:
            os.unlink(path)

    def test_per_turn_expect_combined_with_step_expect(self):
        """Flow steps merge step.expect + per_turn_expect."""
        scenario = Scenario(
            name="test",
            persona={"name": "Emma"},
            flow=[
                {"goal": "say hi", "expect": [{"type": "keyword", "must_contain": ["hi"]}]},
            ],
            per_turn_expect=[{"type": "latency", "max_first_byte_ms": 3000}],
        )
        step = scenario.flow[0]
        all_expects = list(step.expect) + list(scenario.per_turn_expect)
        assert len(all_expects) == 2
        assert all_expects[0].type == "keyword"
        assert all_expects[1].type == "latency"


class TestValidationEdgeCases:
    """Tests for validation edge cases."""

    def test_persona_max_turns_zero(self):
        yaml_content = """
name: "zero turns"
persona:
  name: "Emma"
  max_turns: 0
"""
        path = _write_yaml(yaml_content)
        try:
            errors = validate_scenario(path)
            assert any("max_turns" in e for e in errors)
        finally:
            os.unlink(path)

    def test_persona_max_turns_negative(self):
        yaml_content = """
name: "negative turns"
persona:
  name: "Emma"
  max_turns: -1
"""
        path = _write_yaml(yaml_content)
        try:
            errors = validate_scenario(path)
            assert any("max_turns" in e for e in errors)
        finally:
            os.unlink(path)

    def test_scenario_with_both_turns_and_persona(self):
        """When both turns and persona are set, persona mode takes priority."""
        yaml_content = """
name: "mixed"
persona:
  name: "Emma"
  max_turns: 3
turns:
  - user: "hello"
"""
        path = _write_yaml(yaml_content)
        try:
            scenario = load_scenario(path)
            # Persona takes priority (is_persona_mode checks persona != None and no flow)
            assert scenario.is_persona_mode
        finally:
            os.unlink(path)


class TestQuestionsMode:
    """Tests for questions mode — fixed user messages with shared evaluators."""

    def test_questions_mode_detected(self):
        scenario = Scenario(
            name="test",
            questions=["Hello!", "Tell me about butterflies"],
        )
        assert scenario.is_questions_mode is True
        assert scenario.is_persona_mode is False
        assert scenario.is_guided_mode is False

    def test_questions_with_persona_overrides_persona_mode(self):
        """When questions and persona are both set, questions mode takes priority."""
        scenario = Scenario(
            name="test",
            persona={"name": "Emma", "age": 7},
            questions=["Hello!", "What is a fraction?"],
        )
        assert scenario.is_questions_mode is True
        assert scenario.is_persona_mode is False

    def test_questions_yaml_load(self):
        yaml_content = """
name: "questions test"
questions:
  - "Hello!"
  - "Tell me about butterflies"
  - "What colors are they?"
per_turn_expect:
  - type: latency
    max_first_byte_ms: 5000
  - type: turn_count
    min_words: 3
conversation_eval:
  criteria:
    - "Agent was friendly"
  min_score: 0.7
"""
        path = _write_yaml(yaml_content)
        try:
            scenario = load_scenario(path)
            assert scenario.is_questions_mode
            assert len(scenario.questions) == 3
            assert scenario.questions[0] == "Hello!"
            assert len(scenario.per_turn_expect) == 2
            assert scenario.conversation_eval is not None
        finally:
            os.unlink(path)

    def test_questions_validates_ok(self):
        """Questions mode should pass validation."""
        yaml_content = """
name: "questions valid"
questions:
  - "Hello!"
per_turn_expect:
  - type: latency
    max_first_byte_ms: 3000
"""
        path = _write_yaml(yaml_content)
        try:
            errors = validate_scenario(path)
            assert errors == []
        finally:
            os.unlink(path)

    def test_empty_questions_not_questions_mode(self):
        """Empty questions list should not trigger questions mode."""
        scenario = Scenario(
            name="test",
            questions=[],
            turns=[{"user": "hi"}],
        )
        assert scenario.is_questions_mode is False


class TestPreflightChecks:
    """Tests for _preflight_checks catching bad config."""

    def test_unexpanded_agent_name_raises(self):
        """Preflight should catch literal ${VOICECHECK_AGENT_NAME} in agent_name."""
        from voicecheck.core.scenario import ScenarioRunner

        scenario = Scenario(
            name="test",
            transport={"type": "livekit", "mode": "direct", "config": {
                "url": "ws://localhost:7880",
                "api_key": "test-key",
                "api_secret": "test-secret",
                "agent_name": "${VOICECHECK_AGENT_NAME}",
            }},
            turns=[{"user": "hi"}],
        )
        runner = ScenarioRunner(scenario)
        with pytest.raises(RuntimeError, match="agent_name.*unexpanded"):
            runner._preflight_checks()

    def test_no_agent_name_passes(self):
        """Preflight should pass when agent_name is omitted (auto-dispatch)."""
        from voicecheck.core.scenario import ScenarioRunner

        scenario = Scenario(
            name="test",
            transport={"type": "livekit", "mode": "direct", "config": {
                "url": "ws://localhost:7880",
                "api_key": "test-key",
                "api_secret": "test-secret",
            }},
            turns=[{"user": "hi"}],
        )
        runner = ScenarioRunner(scenario)
        # Should not raise
        runner._preflight_checks()

    def test_valid_agent_name_passes(self):
        """Preflight should pass when agent_name is a real value."""
        from voicecheck.core.scenario import ScenarioRunner

        scenario = Scenario(
            name="test",
            transport={"type": "livekit", "mode": "direct", "config": {
                "url": "ws://localhost:7880",
                "api_key": "test-key",
                "api_secret": "test-secret",
                "agent_name": "kidco-agent",
            }},
            turns=[{"user": "hi"}],
        )
        runner = ScenarioRunner(scenario)
        # Should not raise
        runner._preflight_checks()


class TestRunEvaluatorsErrorHandling:
    """Test that _run_evaluators catches evaluator exceptions."""

    @pytest.mark.asyncio
    async def test_bad_evaluator_does_not_crash(self):
        from voicecheck.core.scenario import ScenarioRunner, ExpectConfig
        from voicecheck.core.types import EvalContext, TransportMetrics

        scenario = Scenario(
            name="test",
            turns=[{"user": "hi", "expect": [{"type": "latency", "max_first_byte_ms": 3000}]}],
        )
        runner = ScenarioRunner(scenario)

        ctx = EvalContext(
            user_text="hi",
            agent_text="hello",
            agent_audio=[],
            metrics=TransportMetrics(),
            turn_index=0,
        )

        # Pass a bad kwarg that will make the evaluator constructor crash
        bad_expects = [ExpectConfig(type="latency", max_first_byte_ms="not_a_number")]
        results = await runner._run_evaluators(bad_expects, ctx)

        assert len(results) == 1
        assert not results[0].passed
        assert "error" in results[0].reason.lower()


# ── Skip LLM judge tests ──────────────────────────────────────────


class TestSkipLLMJudge:
    """Tests for --skip-llm-judge flag."""

    @pytest.mark.asyncio
    async def test_skip_llm_judge_filters_evaluators(self):
        from voicecheck.core.scenario import ScenarioRunner, ExpectConfig
        from voicecheck.core.types import EvalContext, TransportMetrics

        scenario = Scenario(
            name="test",
            turns=[{"user": "hi", "expect": [
                {"type": "latency", "max_first_byte_ms": 3000},
                {"type": "llm_judge", "criteria": "Agent is friendly", "min_score": 0.7},
            ]}],
        )
        runner = ScenarioRunner(scenario, skip_llm_judge=True)

        ctx = EvalContext(
            user_text="hi",
            agent_text="hello there",
            agent_audio=[],
            metrics=TransportMetrics(),
            turn_index=0,
        )

        expects = [
            ExpectConfig(type="latency", max_first_byte_ms=3000),
            ExpectConfig(type="llm_judge", criteria="Agent is friendly", min_score=0.7),
        ]
        results = await runner._run_evaluators(expects, ctx)

        # Only latency should run, llm_judge should be skipped
        assert len(results) == 1
        assert results[0].evaluator_type == "latency"

    def test_skip_llm_judge_skips_openai_check(self):
        """With --skip-llm-judge, scenarios with only llm_judge don't need OPENAI_API_KEY."""
        from voicecheck.core.scenario import ScenarioRunner
        import os

        # Remove OPENAI_API_KEY if set
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            scenario = Scenario(
                name="test",
                transport={"type": "livekit", "mode": "direct", "config": {
                    "url": "ws://localhost:7880",
                    "api_key": "test-key",
                    "api_secret": "test-secret",
                }},
                turns=[{"user": "hi", "expect": [
                    {"type": "llm_judge", "criteria": "test", "min_score": 0.5},
                ]}],
            )
            runner = ScenarioRunner(scenario, skip_llm_judge=True)
            # Should not raise even without OPENAI_API_KEY
            runner._preflight_checks()
        finally:
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key


# ── Audio artifact saving tests ────────────────────────────────────


class TestSaveRunArtifacts:
    """Tests for save_run_artifacts and WAV file writing."""

    def test_save_creates_directory_and_files(self, tmp_path):
        from voicecheck.core.report import save_run_artifacts
        from voicecheck.core.scenario import ScenarioReport
        from voicecheck.core.types import AudioFrame, TurnResult, TransportMetrics

        # Create a report with audio data
        user_frame = AudioFrame(data=b"\x00\x01" * 800, sample_rate=16000)
        agent_frame = AudioFrame(data=b"\x01\x00" * 1600, sample_rate=16000)

        report = ScenarioReport(
            scenario_name="test",
            turns=[
                TurnResult(
                    turn_index=0,
                    user_text="Hello",
                    agent_text="Hi there",
                    user_audio=[user_frame],
                    agent_audio=[agent_frame],
                    metrics=TransportMetrics(),
                ),
            ],
        )

        out_dir = tmp_path / "artifacts"
        result = save_run_artifacts(report, out_dir)

        assert result == out_dir
        assert (out_dir / "report.json").exists()
        assert (out_dir / "turn_1_user.wav").exists()
        assert (out_dir / "turn_1_agent.wav").exists()

        # Verify WAV files are valid
        import wave
        with wave.open(str(out_dir / "turn_1_agent.wav"), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000

    def test_save_skips_empty_audio(self, tmp_path):
        from voicecheck.core.report import save_run_artifacts
        from voicecheck.core.scenario import ScenarioReport
        from voicecheck.core.types import TurnResult, TransportMetrics

        report = ScenarioReport(
            scenario_name="test",
            turns=[
                TurnResult(
                    turn_index=0,
                    user_text="Hello",
                    agent_text="",
                    metrics=TransportMetrics(),
                ),
            ],
        )

        out_dir = tmp_path / "artifacts"
        save_run_artifacts(report, out_dir)

        assert (out_dir / "report.json").exists()
        assert not (out_dir / "turn_1_user.wav").exists()
        assert not (out_dir / "turn_1_agent.wav").exists()


# ── Transport metrics tests ──────────────────────────────────────


class TestTransportMetrics:
    """Tests for TransportMetrics computed properties."""

    def test_first_byte_ms(self):
        from voicecheck.core.types import TransportMetrics

        m = TransportMetrics(send_end_ts=100.0, first_byte_ts=101.5)
        assert m.first_byte_ms == 1500.0

    def test_total_ms(self):
        from voicecheck.core.types import TransportMetrics

        m = TransportMetrics(send_end_ts=100.0, last_byte_ts=103.0)
        assert m.total_ms == 3000.0

    def test_send_duration_ms(self):
        from voicecheck.core.types import TransportMetrics

        m = TransportMetrics(send_start_ts=100.0, send_end_ts=100.5)
        assert m.send_duration_ms == 500.0

    def test_zero_when_timestamps_missing(self):
        from voicecheck.core.types import TransportMetrics

        m = TransportMetrics()
        assert m.first_byte_ms == 0.0
        assert m.total_ms == 0.0
        assert m.send_duration_ms == 0.0

    def test_new_fields_default_zero(self):
        from voicecheck.core.types import TransportMetrics

        m = TransportMetrics()
        assert m.tts_duration_ms == 0.0
        assert m.stt_duration_ms == 0.0
        assert m.user_audio_duration_ms == 0.0
        assert m.agent_audio_duration_ms == 0.0
        assert m.agent_audio_frames == 0

    def test_timer_context_manager(self):
        import time
        from voicecheck.core.types import Timer

        with Timer() as t:
            time.sleep(0.01)  # 10ms
        assert t.elapsed_ms >= 5  # allow some slack


class TestJsonReportMetrics:
    """Tests that JSON report includes all new metrics."""

    def test_report_includes_new_metrics(self, tmp_path):
        import json
        from voicecheck.core.report import write_json_report
        from voicecheck.core.scenario import ScenarioReport
        from voicecheck.core.types import TransportMetrics, TurnResult

        m = TransportMetrics(
            send_start_ts=100.0,
            send_end_ts=100.5,
            first_byte_ts=101.2,
            last_byte_ts=103.5,
            tts_duration_ms=85.0,
            stt_duration_ms=320.0,
            user_audio_duration_ms=450.0,
            agent_audio_duration_ms=2800.0,
            agent_audio_frames=140,
        )
        report = ScenarioReport(
            scenario_name="test",
            turns=[
                TurnResult(
                    turn_index=0,
                    user_text="Hello",
                    agent_text="Hi there how are you",
                    metrics=m,
                ),
            ],
        )

        out = tmp_path / "report.json"
        write_json_report(report, out)

        data = json.loads(out.read_text())
        metrics = data["turns"][0]["metrics"]

        assert metrics["first_byte_ms"] == pytest.approx(700.0)
        assert metrics["total_ms"] == pytest.approx(3000.0)
        assert metrics["send_duration_ms"] == pytest.approx(500.0)
        assert metrics["tts_duration_ms"] == 85.0
        assert metrics["stt_duration_ms"] == 320.0
        assert metrics["agent_audio_duration_ms"] == 2800.0
        assert metrics["agent_audio_frames"] == 140
        assert metrics["user_audio_duration_ms"] == 450.0
        # "Hi there how are you" = 5 words, 2.8s audio → ~1.79 wps
        assert metrics["agent_words_per_second"] == pytest.approx(5 / 2.8, rel=0.01)
