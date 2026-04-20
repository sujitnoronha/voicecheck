"""Tests for persona-driven conversation engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from voicecheck.conversation.engine import (
    ConversationEngine,
    ConversationEvalConfig,
    PersonaConfig,
    _build_persona_system_prompt,
)

# ── PersonaConfig tests ─────────────────────────────────────────


class TestPersonaConfig:
    def test_defaults(self):
        cfg = PersonaConfig()
        assert cfg.name == "Test User"
        assert cfg.personality == "friendly and curious"
        assert cfg.model == "gpt-4o-mini"
        assert cfg.max_turns == 5
        assert cfg.opening == ""

    def test_custom_values(self):
        cfg = PersonaConfig(
            name="Emma",
            age=7,
            personality="curious, loves animals",
            communication_style="short sentences",
            goals=["learn about dolphins"],
            topics=["ocean animals"],
            model="gpt-4o",
            max_turns=3,
            opening="Hi! Tell me about dolphins!",
        )
        assert cfg.name == "Emma"
        assert cfg.age == 7
        assert cfg.max_turns == 3
        assert cfg.opening == "Hi! Tell me about dolphins!"
        assert len(cfg.goals) == 1
        assert len(cfg.topics) == 1


# ── System prompt tests ──────────────────────────────────────────


class TestBuildPersonaSystemPrompt:
    def test_basic_prompt(self):
        cfg = PersonaConfig(name="Alex", personality="shy")
        prompt = _build_persona_system_prompt(cfg)
        assert "Alex" in prompt
        assert "shy" in prompt
        assert "Stay in character" in prompt

    def test_includes_age(self):
        cfg = PersonaConfig(name="Emma", age=7)
        prompt = _build_persona_system_prompt(cfg)
        assert "7 years old" in prompt

    def test_no_age_when_none(self):
        cfg = PersonaConfig(name="User")
        prompt = _build_persona_system_prompt(cfg)
        assert "years old" not in prompt

    def test_includes_goals(self):
        cfg = PersonaConfig(goals=["learn about space", "ask for a story"])
        prompt = _build_persona_system_prompt(cfg)
        assert "learn about space" in prompt
        assert "ask for a story" in prompt

    def test_includes_topics(self):
        cfg = PersonaConfig(topics=["dinosaurs", "planets"])
        prompt = _build_persona_system_prompt(cfg)
        assert "dinosaurs" in prompt
        assert "planets" in prompt

    def test_includes_description(self):
        cfg = PersonaConfig(description="A homeschooled kid from Ohio")
        prompt = _build_persona_system_prompt(cfg)
        assert "homeschooled kid from Ohio" in prompt

    def test_includes_instructions(self):
        cfg = PersonaConfig(instructions="Always ask follow-up questions")
        prompt = _build_persona_system_prompt(cfg)
        assert "Always ask follow-up questions" in prompt


# ── ConversationEngine tests ─────────────────────────────────────


def _mock_openai_response(content: str):
    """Create a mock OpenAI chat completion response."""
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


class TestConversationEngine:
    def test_init(self):
        cfg = PersonaConfig(name="Emma", age=7)
        engine = ConversationEngine(cfg)
        assert engine.persona.name == "Emma"
        assert engine._messages == []

    @pytest.mark.asyncio
    async def test_generate_opening_with_preset(self):
        cfg = PersonaConfig(opening="Hello there!")
        engine = ConversationEngine(cfg)
        result = await engine.generate_opening()
        assert result == "Hello there!"
        assert len(engine._messages) == 1
        assert engine._messages[0]["content"] == "Hello there!"

    @pytest.mark.asyncio
    async def test_generate_opening_with_llm(self):
        cfg = PersonaConfig(name="Emma")
        engine = ConversationEngine(cfg)

        mock_create = AsyncMock(return_value=_mock_openai_response("Hi! I'm Emma!"))

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            result = await engine.generate_opening()

        assert result == "Hi! I'm Emma!"
        assert len(engine._messages) == 1
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_next(self):
        cfg = PersonaConfig(name="Emma")
        engine = ConversationEngine(cfg)
        engine._messages = [{"role": "assistant", "content": "Hi!"}]

        mock_create = AsyncMock(return_value=_mock_openai_response("That's so cool! Tell me more!"))

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            result = await engine.generate_next("Dolphins can sleep with one eye open!")

        assert result == "That's so cool! Tell me more!"
        # Should have: original message + agent response + new response
        assert len(engine._messages) == 3
        assert "[Agent says]" in engine._messages[1]["content"]

    @pytest.mark.asyncio
    async def test_evaluate_conversation_success(self):
        cfg = PersonaConfig()
        engine = ConversationEngine(cfg)

        eval_response = (
            '{"overall_score": 0.85, "overall_passed": true, '
            '"overall_reason": "Good conversation", '
            '"criteria_scores": [{"criterion": "Friendly tone", "score": 0.9, "reason": "Very warm"}]}'
        )
        mock_create = AsyncMock(return_value=_mock_openai_response(eval_response))

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            conversation = [
                {"role": "user", "text": "Hi!"},
                {"role": "agent", "text": "Hello! How are you?"},
            ]
            eval_config = ConversationEvalConfig(
                criteria=["Friendly tone"],
                min_score=0.7,
            )
            result = await engine.evaluate_conversation(conversation, eval_config)

        assert result["overall_score"] == 0.85
        assert result["overall_passed"] is True
        assert len(result["criteria_scores"]) == 1

    @pytest.mark.asyncio
    async def test_evaluate_conversation_failure(self):
        cfg = PersonaConfig()
        engine = ConversationEngine(cfg)

        eval_response = (
            '{"overall_score": 0.3, "overall_passed": false, '
            '"overall_reason": "Agent was dismissive", "criteria_scores": []}'
        )
        mock_create = AsyncMock(return_value=_mock_openai_response(eval_response))

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            conversation = [
                {"role": "user", "text": "Hi!"},
                {"role": "agent", "text": "What do you want."},
            ]
            eval_config = ConversationEvalConfig(
                criteria=["Friendly tone"],
                min_score=0.7,
            )
            result = await engine.evaluate_conversation(conversation, eval_config)

        assert result["overall_score"] == 0.3
        assert result["overall_passed"] is False

    @pytest.mark.asyncio
    async def test_evaluate_conversation_error_handling(self):
        cfg = PersonaConfig()
        engine = ConversationEngine(cfg)

        mock_create = AsyncMock(side_effect=Exception("API error"))

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            conversation = [{"role": "user", "text": "Hi!"}]
            eval_config = ConversationEvalConfig(criteria=["test"])
            result = await engine.evaluate_conversation(conversation, eval_config)

        assert result["overall_score"] == 0.0
        assert result["overall_passed"] is False
        assert "error" in result["overall_reason"].lower()


# ── ScenarioReport with conversation_eval ────────────────────────


class TestScenarioReportConversationEval:
    def test_passed_without_conversation_eval(self):
        from voicecheck.core.scenario import ScenarioReport

        report = ScenarioReport(scenario_name="test")
        assert report.passed is True  # no turns, no eval = vacuously true

    def test_passed_with_passing_conversation_eval(self):
        from voicecheck.core.scenario import ScenarioReport

        report = ScenarioReport(
            scenario_name="test",
            conversation_eval={"overall_passed": True, "overall_score": 0.9},
        )
        assert report.passed is True

    def test_failed_with_failing_conversation_eval(self):
        from voicecheck.core.scenario import ScenarioReport

        report = ScenarioReport(
            scenario_name="test",
            conversation_eval={"overall_passed": False, "overall_score": 0.3},
        )
        assert report.passed is False


# ── Guided flow engine tests ─────────────────────────────────────


class TestConversationEngineGuided:
    """Tests for guided-flow engine methods (generate_opening_guided, generate_next_guided)."""

    @pytest.mark.asyncio
    async def test_generate_opening_guided_with_preset(self):
        """If persona has an opening, use it regardless of step goal."""
        cfg = PersonaConfig(opening="Are you a real pirate?!")
        engine = ConversationEngine(cfg)
        result = await engine.generate_opening_guided("Greet the pirate captain")
        assert result == "Are you a real pirate?!"
        assert len(engine._messages) == 1

    @pytest.mark.asyncio
    async def test_generate_opening_guided_with_llm(self):
        """Without a preset opening, LLM generates one steered by the goal."""
        cfg = PersonaConfig(name="Emma", age=7)
        engine = ConversationEngine(cfg)

        mock_create = AsyncMock(return_value=_mock_openai_response("Hi! Do you know about stars?"))

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            result = await engine.generate_opening_guided("Ask about stars and planets")

        assert result == "Hi! Do you know about stars?"
        assert len(engine._messages) == 1

        # Verify the goal was injected into the prompt
        call_kwargs = mock_create.call_args[1]
        messages = call_kwargs["messages"]
        user_msg = messages[-1]["content"]
        assert "Ask about stars and planets" in user_msg

    @pytest.mark.asyncio
    async def test_generate_next_guided(self):
        """Next guided message includes goal in the prompt."""
        cfg = PersonaConfig(name="Emma", age=7)
        engine = ConversationEngine(cfg)
        engine._messages = [{"role": "assistant", "content": "Hi!"}]

        mock_create = AsyncMock(
            return_value=_mock_openai_response("Can I do an experiment at home?")
        )

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            result = await engine.generate_next_guided(
                "Volcanoes erupt because of magma pressure!",
                "Ask about a home experiment",
            )

        assert result == "Can I do an experiment at home?"
        # Messages: original + agent response + new response = 3
        assert len(engine._messages) == 3
        assert "[Agent says]" in engine._messages[1]["content"]

        # Verify goal was in the prompt
        call_kwargs = mock_create.call_args[1]
        messages = call_kwargs["messages"]
        goal_msg = messages[-1]["content"]
        assert "Ask about a home experiment" in goal_msg

    @pytest.mark.asyncio
    async def test_guided_preserves_conversation_history(self):
        """Guided messages accumulate history across steps."""
        cfg = PersonaConfig(name="Liam")
        engine = ConversationEngine(cfg)

        mock_create = AsyncMock(
            side_effect=[
                _mock_openai_response("Hey! Why do volcanoes erupt?"),
                _mock_openai_response("That's so cool! Can I try an experiment?"),
                _mock_openai_response("Wait, really? Tell me more!"),
            ]
        )

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            # Step 1: opening
            msg1 = await engine.generate_opening_guided("Ask about volcanoes")
            assert msg1 == "Hey! Why do volcanoes erupt?"

            # Step 2: next guided
            msg2 = await engine.generate_next_guided(
                "Volcanoes erupt because of magma!",
                "Ask about a home experiment",
            )
            assert msg2 == "That's so cool! Can I try an experiment?"

            # Step 3: another next guided
            msg3 = await engine.generate_next_guided(
                "You can make a baking soda volcano!",
                "Ask for more details",
            )
            assert msg3 == "Wait, really? Tell me more!"

        # 5 messages: opening + agent1 + response2 + agent2 + response3
        assert len(engine._messages) == 5
        assert mock_create.call_count == 3


# ── LLM retry tests ──────────────────────────────────────────────


class TestLLMRetry:
    """Tests that _call_llm retries once on failure."""

    @pytest.mark.asyncio
    async def test_retry_on_first_failure(self):
        """If first call fails but second succeeds, we get the result."""
        cfg = PersonaConfig(name="Emma")
        engine = ConversationEngine(cfg)

        mock_create = AsyncMock(
            side_effect=[
                Exception("API error"),
                _mock_openai_response("Hi there!"),
            ]
        )

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            result = await engine.generate_opening()

        assert result == "Hi there!"
        assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_both_retries_fail(self):
        """If both attempts fail, the exception is raised."""
        cfg = PersonaConfig(name="Emma")
        engine = ConversationEngine(cfg)

        mock_create = AsyncMock(side_effect=Exception("API down"))

        with patch("openai.AsyncOpenAI", create=True) as MockClient:
            mock_client = MagicMock()
            mock_client.chat.completions.create = mock_create
            MockClient.return_value = mock_client

            with pytest.raises(Exception, match="API down"):
                await engine.generate_opening()

        assert mock_create.call_count == 2
