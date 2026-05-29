"""VoiceCheck — E2E testing framework for voice agents.

Test voice agents through the full audio loop:
TTS -> transport -> agent -> capture -> STT -> evaluate.

Quick start::

    from voicecheck.core.scenario import ScenarioRunner

    runner = ScenarioRunner.from_yaml("scenario.yaml")
    report = await runner.run()
    assert report.passed
"""

__version__ = "0.2.1"

# Public API
from voicecheck.core.evaluator import Evaluator, get_evaluator, register_evaluator
from voicecheck.core.scenario import (
    Scenario,
    ScenarioReport,
    ScenarioRunner,
    load_scenario,
    validate_scenario,
)
from voicecheck.core.transport import Transport, get_transport, register_transport
from voicecheck.core.types import (
    AudioFrame,
    EvalContext,
    EvalResult,
    TranscriptSegment,
    TransportMetrics,
    TurnResult,
)
from voicecheck.storage.store import ResultStore

__all__ = [
    # Types
    "AudioFrame",
    "EvalContext",
    "EvalResult",
    "TranscriptSegment",
    "TransportMetrics",
    "TurnResult",
    # ABCs
    "Transport",
    "Evaluator",
    # Registry
    "register_transport",
    "get_transport",
    "register_evaluator",
    "get_evaluator",
    # Scenario
    "Scenario",
    "ScenarioReport",
    "ScenarioRunner",
    "load_scenario",
    "validate_scenario",
    # Storage
    "ResultStore",
]
