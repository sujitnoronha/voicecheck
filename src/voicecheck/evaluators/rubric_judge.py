"""Rubric-based LLM judge — score a turn across multiple named dimensions.

One LLM call returns a score per dimension plus an aggregated overall score.
Dimensions are either preset names from :mod:`metrics_library` or ad-hoc dicts
defined inline in YAML.
"""

from __future__ import annotations

import logging
from typing import Any

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult
from voicecheck.evaluators._llm_service import (
    LLMServiceError,
    build_conversation_context,
    call_llm_judge,
    default_model,
)
from voicecheck.evaluators.metrics_library import (
    Dimension,
    effective_min_score,
    effective_weight,
    resolve_dimension,
)

logger = logging.getLogger("voicecheck.evaluators.rubric_judge")


_SYSTEM_TEMPLATE = """You are a strict evaluator of a voice-agent turn. Score each dimension on a 0.0-1.0 scale where 0 is complete failure and 1 is perfect. Return ONLY the JSON object described below — no prose, no markdown fences.

Schema:
{{
  "dimensions": [
    {{"name": "<name>", "score": <0.0-1.0>, "passed": <bool>, "reason": "<short>"}}
  ],
  "overall_score": <0.0-1.0>,
  "overall_reason": "<one-line summary>"
}}

Dimensions to score (apply each definition independently):
{dimensions_block}
{context_block}"""


_USER_TEMPLATE = """Conversation so far:
{conversation_transcript}

The LAST agent turn to evaluate:
User said: "{user_text}"
Agent responded: "{agent_text}"

Score every dimension listed in the system prompt. Return JSON only."""


def _format_dimension_block(
    dims_with_overrides: list[tuple[Dimension, dict[str, float]]],
) -> str:
    lines: list[str] = []
    for dim, overrides in dims_with_overrides:
        lines.append(f"- name: {dim.name}")
        lines.append(f"  what to look for: {dim.description}")
        lines.append(f"  guidance: {dim.prompt_guidance}")
        lines.append(f"  pass threshold: {effective_min_score(dim, overrides)}")
    return "\n".join(lines)


def _format_context_block(
    *,
    policy: str,
    brand_voice: str,
    ground_truth: str,
    known_facts: list[str],
    false_facts: list[str],
) -> str:
    parts: list[str] = []
    if policy:
        parts.append(f"Business policy the agent must follow: {policy}")
    if brand_voice:
        parts.append(f"Required brand voice: {brand_voice}")
    if known_facts:
        bullets = "\n".join(f"  - {f}" for f in known_facts)
        parts.append(f"Known facts (agent should not contradict):\n{bullets}")
    if false_facts:
        bullets = "\n".join(f"  - {f}" for f in false_facts)
        parts.append(f"False facts (agent must not assert any of these):\n{bullets}")
    if ground_truth:
        # Kept for backward compat — new YAMLs should prefer `known_facts`.
        parts.append(f"Additional ground-truth context: {ground_truth}")
    if not parts:
        return ""
    return "\n\n" + "\n".join(parts)


class RubricJudgeEvaluator(Evaluator):
    """Score a turn across a list of named dimensions in a single LLM call.

    YAML example::

        - type: rubric_judge
          min_overall_score: 0.8
          weighting: weighted
          policy: "No medical advice. No diagnoses."
          dimensions:
            - task_completion
            - pii_handling
            - {name: escalation_appropriateness, min_score: 0.9, weight: 1.5}
            - name: urgency_triage
              description: "Agent escalated pain signals."
              prompt_guidance: "Fail if agent ignored 'severe pain' signal."
              min_score: 1.0
    """

    #: Aggregation strategy for the per-dim scores → overall.
    WEIGHTING_MODES = ("mean", "weighted", "min")

    def __init__(
        self,
        dimensions: list[str | dict[str, Any]],
        min_overall_score: float = 0.7,
        weighting: str = "weighted",
        provider: str = "openai",
        model: str = "",
        policy: str = "",
        brand_voice: str = "",
        ground_truth: str = "",
        known_facts: list[str] | None = None,
        false_facts: list[str] | None = None,
    ) -> None:
        if not dimensions:
            raise ValueError("rubric_judge requires at least one dimension")
        if weighting not in self.WEIGHTING_MODES:
            raise ValueError(
                f"weighting must be one of {self.WEIGHTING_MODES}, got {weighting!r}"
            )

        self._dims: list[tuple[Dimension, dict[str, float]]] = [
            resolve_dimension(spec) for spec in dimensions
        ]
        self.min_overall_score = float(min_overall_score)
        self.weighting = weighting
        self.provider = provider
        self.model = model or default_model(provider)
        self.policy = policy
        self.brand_voice = brand_voice
        self.ground_truth = ground_truth
        self.known_facts = list(known_facts or [])
        self.false_facts = list(false_facts or [])

        # Enforce per-dimension required kwargs. A preset like `factual_accuracy`
        # declares `requires_kwargs=("known_facts",)`; fail loudly if the YAML
        # references it without supplying the context the judge needs.
        # `ground_truth` is accepted as a backward-compat alias for `known_facts`
        # so scenarios written before the unification still work.
        has_fact_context = bool(self.known_facts or self.ground_truth)
        supplied = {
            "policy": bool(policy),
            "brand_voice": bool(brand_voice),
            "known_facts": has_fact_context,
            "ground_truth": has_fact_context,
        }
        missing: list[str] = []
        for dim, _ in self._dims:
            for required in dim.requires_kwargs:
                if not supplied.get(required, False):
                    missing.append(f"{dim.name} requires {required!r}")
        if missing:
            raise ValueError(
                "rubric_judge: " + "; ".join(missing)
            )

    # ── Public Evaluator API ────────────────────────────────────

    async def evaluate(self, context: EvalContext) -> EvalResult:
        system_prompt = _SYSTEM_TEMPLATE.format(
            dimensions_block=_format_dimension_block(self._dims),
            context_block=_format_context_block(
                policy=self.policy,
                brand_voice=self.brand_voice,
                ground_truth=self.ground_truth,
                known_facts=self.known_facts,
                false_facts=self.false_facts,
            ),
        )
        user_prompt = _USER_TEMPLATE.format(
            conversation_transcript=build_conversation_context(context.conversation),
            user_text=context.user_text,
            agent_text=context.agent_text,
        )

        try:
            raw = await call_llm_judge(
                system_prompt,
                user_prompt,
                model=self.model,
                provider=self.provider,
            )
        except LLMServiceError as e:
            logger.error("rubric_judge failed: %s", e)
            return EvalResult(
                evaluator_type="rubric_judge",
                passed=False,
                score=0.0,
                reason=f"LLM error: {str(e)[:200]}",
                details={"dimensions": [d.name for d, _ in self._dims]},
            )

        return self._parse_and_aggregate(raw)

    # ── Internals ──────────────────────────────────────────────

    def _parse_and_aggregate(self, raw: dict[str, Any]) -> EvalResult:
        response_dims = {
            (d.get("name") or "").strip(): d
            for d in raw.get("dimensions", [])
            if isinstance(d, dict)
        }

        per_dim_details: list[dict[str, Any]] = []
        scores: list[float] = []
        weights: list[float] = []
        all_passed = True
        failure_reasons: list[str] = []

        for dim, overrides in self._dims:
            min_score = effective_min_score(dim, overrides)
            weight = effective_weight(dim, overrides)

            entry = response_dims.get(dim.name)
            if entry is None:
                per_dim_details.append({
                    "name": dim.name,
                    "score": 0.0,
                    "passed": False,
                    "reason": "missing from judge response",
                    "min_score": min_score,
                    "weight": weight,
                })
                scores.append(0.0)
                weights.append(weight)
                all_passed = False
                failure_reasons.append(f"{dim.name}: missing")
                continue

            raw_score = entry.get("score", 0.0)
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 0.0
            if score < 0.0 or score > 1.0:
                logger.warning(
                    "rubric_judge: %s score %s out of range, clamping",
                    dim.name, score,
                )
                score = max(0.0, min(1.0, score))

            # Trust judge's 'passed' flag if present; else derive from threshold.
            passed_flag = entry.get("passed")
            dim_passed = (
                bool(passed_flag)
                if isinstance(passed_flag, bool)
                else score >= min_score
            )
            # Always enforce the threshold — even if the judge claims pass.
            if score < min_score:
                dim_passed = False

            reason = str(entry.get("reason", ""))[:300]

            per_dim_details.append({
                "name": dim.name,
                "score": score,
                "passed": dim_passed,
                "reason": reason,
                "min_score": min_score,
                "weight": weight,
            })
            scores.append(score)
            weights.append(weight)
            if not dim_passed:
                all_passed = False
                failure_reasons.append(f"{dim.name}={score:.2f} (<{min_score})")

        aggregated = self._aggregate(scores, weights)
        overall_passed = all_passed and aggregated >= self.min_overall_score
        overall_reason = (
            raw.get("overall_reason")
            or ("; ".join(failure_reasons) if failure_reasons else "all dimensions passed")
        )

        return EvalResult(
            evaluator_type="rubric_judge",
            passed=overall_passed,
            score=aggregated,
            reason=str(overall_reason)[:400],
            details={
                "dimensions": per_dim_details,
                "weighting": self.weighting,
                "min_overall_score": self.min_overall_score,
                "model": self.model,
                "provider": self.provider,
            },
        )

    def _aggregate(self, scores: list[float], weights: list[float]) -> float:
        if not scores:
            return 0.0
        if self.weighting == "min":
            return min(scores)
        if self.weighting == "weighted":
            total_weight = sum(weights)
            if total_weight == 0:
                return sum(scores) / len(scores)
            return sum(s * w for s, w in zip(scores, weights)) / total_weight
        # mean
        return sum(scores) / len(scores)


register_evaluator("rubric_judge", RubricJudgeEvaluator)
