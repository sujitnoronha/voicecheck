"""Latency evaluator — checks response time thresholds."""

from __future__ import annotations

from voicecheck.core.evaluator import Evaluator, register_evaluator
from voicecheck.core.types import EvalContext, EvalResult


class LatencyEvaluator(Evaluator):
    """Check that agent response latency is within acceptable bounds."""

    def __init__(
        self,
        max_first_byte_ms: float = 0,
        max_total_ms: float = 0,
    ) -> None:
        self.max_first_byte_ms = max_first_byte_ms
        self.max_total_ms = max_total_ms

    async def evaluate(self, context: EvalContext) -> EvalResult:
        first_byte = context.metrics.first_byte_ms
        total = context.metrics.total_ms
        reasons: list[str] = []
        passed = True

        if self.max_first_byte_ms > 0 and first_byte > self.max_first_byte_ms:
            passed = False
            reasons.append(
                f"First byte {first_byte:.0f}ms exceeds max {self.max_first_byte_ms:.0f}ms"
            )

        if self.max_total_ms > 0 and total > self.max_total_ms:
            passed = False
            reasons.append(
                f"Total {total:.0f}ms exceeds max {self.max_total_ms:.0f}ms"
            )

        if not reasons:
            reasons.append(f"Latency OK (first_byte={first_byte:.0f}ms, total={total:.0f}ms)")

        return EvalResult(
            evaluator_type="latency",
            passed=passed,
            score=1.0 if passed else 0.0,
            reason="; ".join(reasons),
            details={
                "first_byte_ms": first_byte,
                "total_ms": total,
                "max_first_byte_ms": self.max_first_byte_ms,
                "max_total_ms": self.max_total_ms,
                "send_duration_ms": context.metrics.send_duration_ms,
                "tts_duration_ms": context.metrics.tts_duration_ms,
                "stt_duration_ms": context.metrics.stt_duration_ms,
                "agent_audio_duration_ms": context.metrics.agent_audio_duration_ms,
                "agent_audio_frames": context.metrics.agent_audio_frames,
            },
        )


register_evaluator("latency", LatencyEvaluator)
