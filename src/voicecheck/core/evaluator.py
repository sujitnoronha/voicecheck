"""Abstract evaluator base class and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod

from voicecheck.core.types import EvalContext, EvalResult


class Evaluator(ABC):
    """Scores an agent turn or full conversation.

    Each evaluator gets an EvalContext with the user text, agent text,
    audio, latency metrics, and conversation history, and returns a pass/fail
    result with a score and explanation.
    """

    @abstractmethod
    async def evaluate(self, context: EvalContext) -> EvalResult:
        """Evaluate the agent's response.

        Args:
            context: Full context of the turn including text, audio, metrics.

        Returns:
            EvalResult with passed, score, reason, and optional details.
        """


# Evaluator registry for dynamic lookup from YAML config
_EVALUATOR_REGISTRY: dict[str, type[Evaluator]] = {}


def register_evaluator(name: str, evaluator_cls: type[Evaluator]) -> None:
    """Register an evaluator class by name."""
    _EVALUATOR_REGISTRY[name] = evaluator_cls


def get_evaluator(name: str) -> type[Evaluator]:
    """Get a registered evaluator class by name.

    Raises:
        ValueError: If evaluator name is not registered.
    """
    if name not in _EVALUATOR_REGISTRY:
        available = ", ".join(_EVALUATOR_REGISTRY.keys()) or "(none)"
        raise ValueError(f"Unknown evaluator: {name!r}. Available: {available}.")
    return _EVALUATOR_REGISTRY[name]
