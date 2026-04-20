"""Tests for the rubric_judge evaluator and metrics_library."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import voicecheck.evaluators.rubric_judge  # noqa: F401  # ensures registration
from voicecheck.core.types import EvalContext, TransportMetrics
from voicecheck.evaluators.metrics_library import (
    COMMERCIAL_METRICS,
    effective_min_score,
    effective_weight,
    resolve_dimension,
)
from voicecheck.evaluators.rubric_judge import RubricJudgeEvaluator

# ── Fixtures ────────────────────────────────────────────────────────


def _ctx(
    *,
    user_text: str = "hi",
    agent_text: str = "hello",
    conversation: list[dict] | None = None,
) -> EvalContext:
    return EvalContext(
        user_text=user_text,
        agent_text=agent_text,
        agent_audio=[],
        metrics=TransportMetrics(),
        turn_index=0,
        scenario_name="test",
        conversation=conversation or [],
    )


def _patch_judge(return_value):
    """Patch call_llm_judge to return the given dict."""
    return patch(
        "voicecheck.evaluators.rubric_judge.call_llm_judge",
        new=AsyncMock(return_value=return_value),
    )


# ── metrics_library.resolve_dimension ───────────────────────────────


class TestResolveDimension:
    def test_string_spec_returns_preset(self):
        dim, overrides = resolve_dimension("task_completion")
        assert dim.name == "task_completion"
        assert overrides == {}

    def test_unknown_string_raises(self):
        with pytest.raises(ValueError, match="Unknown metric preset"):
            resolve_dimension("does_not_exist")

    def test_dict_override_on_preset(self):
        dim, overrides = resolve_dimension(
            {
                "name": "pii_handling",
                "min_score": 1.0,
                "weight": 2.0,
            }
        )
        assert dim.name == "pii_handling"
        assert overrides == {"min_score": 1.0, "weight": 2.0}
        assert effective_min_score(dim, overrides) == 1.0
        assert effective_weight(dim, overrides) == 2.0

    def test_dict_preset_with_description_override(self):
        dim, overrides = resolve_dimension(
            {
                "name": "task_completion",
                "description": "custom description",
            }
        )
        assert dim.description == "custom description"
        # Shouldn't mutate the preset.
        assert COMMERCIAL_METRICS["task_completion"].description != "custom description"

    def test_adhoc_dimension(self):
        dim, overrides = resolve_dimension(
            {
                "name": "allergy_confirmed",
                "description": "Agent read the allergy back.",
                "prompt_guidance": "Fail if not repeated.",
                "min_score": 1.0,
                "weight": 1.5,
            }
        )
        assert dim.name == "allergy_confirmed"
        assert dim.description == "Agent read the allergy back."
        assert overrides["min_score"] == 1.0
        assert overrides["weight"] == 1.5

    def test_adhoc_requires_description(self):
        with pytest.raises(ValueError, match="description"):
            resolve_dimension({"name": "no_desc"})

    def test_dict_requires_name(self):
        with pytest.raises(ValueError, match="name"):
            resolve_dimension({"description": "x"})

    def test_preset_count(self):
        # Keep us honest — plan promised 12.
        assert len(COMMERCIAL_METRICS) == 12


# ── RubricJudgeEvaluator.__init__ ───────────────────────────────────


class TestRubricInit:
    def test_requires_dimensions(self):
        with pytest.raises(ValueError, match="at least one dimension"):
            RubricJudgeEvaluator(dimensions=[])

    def test_invalid_weighting(self):
        with pytest.raises(ValueError, match="weighting must be"):
            RubricJudgeEvaluator(dimensions=["task_completion"], weighting="geometric")

    def test_accepts_mixed_specs(self):
        ev = RubricJudgeEvaluator(
            dimensions=[
                "task_completion",
                {"name": "pii_handling", "min_score": 1.0},
                {"name": "custom", "description": "c"},
            ],
        )
        assert len(ev._dims) == 3

    def test_requires_kwargs_enforced_for_factual_accuracy(self):
        with pytest.raises(ValueError, match="known_facts"):
            RubricJudgeEvaluator(dimensions=["factual_accuracy"])

    def test_factual_accuracy_accepts_known_facts_list(self):
        ev = RubricJudgeEvaluator(
            dimensions=["factual_accuracy"],
            known_facts=["Pier 9 is open 5-10pm."],
        )
        assert ev.known_facts == ["Pier 9 is open 5-10pm."]

    def test_factual_accuracy_accepts_ground_truth_alias(self):
        """Backward-compat: old YAMLs using `ground_truth` string still work."""
        ev = RubricJudgeEvaluator(
            dimensions=["factual_accuracy"],
            ground_truth="Pier 9 is open 5-10pm.",
        )
        assert ev.ground_truth == "Pier 9 is open 5-10pm."

    def test_requires_kwargs_enforced_for_policy_compliance(self):
        with pytest.raises(ValueError, match="policy"):
            RubricJudgeEvaluator(dimensions=["policy_compliance"])

    def test_requires_kwargs_satisfied_with_known_facts(self):
        ev = RubricJudgeEvaluator(
            dimensions=["factual_accuracy"],
            known_facts=["The sky is blue."],
        )
        assert len(ev._dims) == 1

    def test_unknown_kwargs_rejected(self):
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            RubricJudgeEvaluator(
                dimensions=["task_completion"],
                brand_voices="playful",  # typo — plural
            )


# ── Evaluate: happy paths and aggregation modes ────────────────────


@pytest.mark.asyncio
class TestRubricEvaluate:
    async def test_all_dimensions_pass(self):
        response = {
            "dimensions": [
                {"name": "task_completion", "score": 0.95, "passed": True, "reason": "done"},
                {"name": "conciseness_for_voice", "score": 0.9, "passed": True, "reason": "short"},
            ],
            "overall_score": 0.92,
            "overall_reason": "great",
        }
        ev = RubricJudgeEvaluator(
            dimensions=["task_completion", "conciseness_for_voice"],
            min_overall_score=0.7,
        )
        with _patch_judge(response):
            result = await ev.evaluate(_ctx())

        assert result.passed is True
        assert result.score > 0.9
        assert result.evaluator_type == "rubric_judge"
        assert len(result.details["dimensions"]) == 2
        assert all(d["passed"] for d in result.details["dimensions"])

    async def test_one_dimension_fails_forces_overall_fail(self):
        response = {
            "dimensions": [
                {"name": "task_completion", "score": 0.95, "passed": True, "reason": "ok"},
                {"name": "pii_handling", "score": 0.5, "passed": True, "reason": "leak"},
            ],
            "overall_score": 0.72,
        }
        ev = RubricJudgeEvaluator(
            dimensions=[
                "task_completion",
                {"name": "pii_handling", "min_score": 0.9},
            ],
            min_overall_score=0.5,
        )
        with _patch_judge(response):
            result = await ev.evaluate(_ctx())

        # Judge said passed=True but score < min_score → we override.
        assert result.passed is False
        pii = next(d for d in result.details["dimensions"] if d["name"] == "pii_handling")
        assert pii["passed"] is False

    async def test_missing_dimension_in_response_fills_zero(self):
        response = {
            "dimensions": [
                {"name": "task_completion", "score": 1.0, "passed": True, "reason": "ok"},
                # pii_handling omitted by the judge
            ],
            "overall_score": 0.9,
        }
        ev = RubricJudgeEvaluator(
            dimensions=["task_completion", "pii_handling"],
        )
        with _patch_judge(response):
            result = await ev.evaluate(_ctx())

        assert result.passed is False
        pii = next(d for d in result.details["dimensions"] if d["name"] == "pii_handling")
        assert pii["score"] == 0.0
        assert "missing" in pii["reason"]

    async def test_weighting_mean(self):
        response = {
            "dimensions": [
                {"name": "a", "score": 0.6, "passed": True, "reason": "x"},
                {"name": "b", "score": 1.0, "passed": True, "reason": "x"},
            ],
        }
        ev = RubricJudgeEvaluator(
            dimensions=[
                {"name": "a", "description": "d", "min_score": 0.5, "weight": 1.0},
                {"name": "b", "description": "d", "min_score": 0.5, "weight": 3.0},
            ],
            weighting="mean",
            min_overall_score=0.0,
        )
        with _patch_judge(response):
            result = await ev.evaluate(_ctx())
        assert result.score == pytest.approx(0.8)

    async def test_weighting_weighted(self):
        response = {
            "dimensions": [
                {"name": "a", "score": 0.6, "passed": True, "reason": "x"},
                {"name": "b", "score": 1.0, "passed": True, "reason": "x"},
            ],
        }
        ev = RubricJudgeEvaluator(
            dimensions=[
                {"name": "a", "description": "d", "min_score": 0.5, "weight": 1.0},
                {"name": "b", "description": "d", "min_score": 0.5, "weight": 3.0},
            ],
            weighting="weighted",
            min_overall_score=0.0,
        )
        with _patch_judge(response):
            result = await ev.evaluate(_ctx())
        # (0.6*1 + 1.0*3) / 4 = 0.9
        assert result.score == pytest.approx(0.9)

    async def test_weighting_min(self):
        response = {
            "dimensions": [
                {"name": "a", "score": 0.6, "passed": True, "reason": "x"},
                {"name": "b", "score": 1.0, "passed": True, "reason": "x"},
            ],
        }
        ev = RubricJudgeEvaluator(
            dimensions=[
                {"name": "a", "description": "d", "min_score": 0.0, "weight": 1.0},
                {"name": "b", "description": "d", "min_score": 0.0, "weight": 1.0},
            ],
            weighting="min",
            min_overall_score=0.0,
        )
        with _patch_judge(response):
            result = await ev.evaluate(_ctx())
        assert result.score == pytest.approx(0.6)

    async def test_out_of_range_score_clamped(self):
        response = {
            "dimensions": [
                {"name": "task_completion", "score": 1.7, "passed": True, "reason": "ok"},
            ],
        }
        ev = RubricJudgeEvaluator(dimensions=["task_completion"], min_overall_score=0.0)
        with _patch_judge(response):
            result = await ev.evaluate(_ctx())
        dim = result.details["dimensions"][0]
        assert dim["score"] == 1.0

    async def test_llm_error_returns_failed_result(self):
        from voicecheck.evaluators._llm_service import LLMServiceError

        ev = RubricJudgeEvaluator(dimensions=["task_completion"])
        with patch(
            "voicecheck.evaluators.rubric_judge.call_llm_judge",
            new=AsyncMock(side_effect=LLMServiceError("boom")),
        ):
            result = await ev.evaluate(_ctx())
        assert result.passed is False
        assert result.score == 0.0
        assert "LLM error" in result.reason
