"""Report generation — console, JSON, and audio artifact output."""

from __future__ import annotations

import json
import logging
import struct
import wave
from pathlib import Path
from typing import TextIO

from voicecheck.core.scenario import ScenarioReport
from voicecheck.core.types import AudioFrame

logger = logging.getLogger("voicecheck.core.report")


def print_console_report(report: ScenarioReport, file: TextIO | None = None) -> None:
    """Print a human-readable report to the console."""
    import sys

    out = file or sys.stdout
    status = "PASSED" if report.passed else "FAILED"

    out.write(f"\n{'='*60}\n")
    out.write(f"  VoiceCheck Report: {report.scenario_name}\n")
    out.write(f"  Status: {status}\n")
    out.write(f"  Turns: {report.passed_turns}/{report.total_turns} passed\n")
    out.write(f"{'='*60}\n\n")

    for turn in report.turns:
        m = turn.metrics
        turn_status = "PASS" if turn.passed else "FAIL"
        out.write(f"Turn {turn.turn_index + 1}: [{turn_status}]\n")
        out.write(f"  User: {turn.user_text}\n")
        out.write(f"  Agent: {turn.agent_text[:200]}\n")

        # Timing breakdown
        timing_parts = [f"first_byte={m.first_byte_ms:.0f}ms", f"total={m.total_ms:.0f}ms"]
        if m.tts_duration_ms:
            timing_parts.append(f"tts={m.tts_duration_ms:.0f}ms")
        if m.stt_duration_ms:
            timing_parts.append(f"stt={m.stt_duration_ms:.0f}ms")
        out.write(f"  Timing: {' | '.join(timing_parts)}\n")

        # Audio metrics
        if m.agent_audio_duration_ms or m.user_audio_duration_ms:
            audio_parts = []
            if m.agent_audio_duration_ms:
                agent_dur = m.agent_audio_duration_ms / 1000
                word_count = len(turn.agent_text.split()) if turn.agent_text else 0
                wps = word_count / agent_dur if agent_dur > 0 else 0
                audio_parts.append(f"agent={agent_dur:.1f}s ({word_count} words, {wps:.1f} wps)")
            if m.user_audio_duration_ms:
                audio_parts.append(f"user={m.user_audio_duration_ms / 1000:.1f}s")
            out.write(f"  Audio:  {' | '.join(audio_parts)}\n")

        for result in turn.eval_results:
            icon = "+" if result.passed else "x"
            out.write(
                f"  [{icon}] {result.evaluator_type}: "
                f"{result.reason} (score={result.score:.2f})\n"
            )
        out.write("\n")

    if report.conversation_eval:
        ce = report.conversation_eval
        ce_status = "PASS" if ce.get("overall_passed") else "FAIL"
        out.write(f"Conversation Eval: [{ce_status}] score={ce.get('overall_score', 0):.2f}\n")
        out.write(f"  {ce.get('overall_reason', '')}\n")
        for cs in ce.get("criteria_scores", []):
            criterion = cs.get("criterion", "unknown")
            score = cs.get("score", 0)
            reason = cs.get("reason", "")
            out.write(f"  - {criterion}: {score:.2f} — {reason}\n")
        out.write("\n")

    out.write(f"{'='*60}\n")
    out.write(f"Result: {status}\n")
    out.write(f"{'='*60}\n")


def write_json_report(report: ScenarioReport, path: str | Path) -> None:
    """Write a machine-readable JSON report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "scenario_name": report.scenario_name,
        "passed": report.passed,
        "total_turns": report.total_turns,
        "passed_turns": report.passed_turns,
        "turns": [],
    }

    for turn in report.turns:
        m = turn.metrics
        word_count = len(turn.agent_text.split()) if turn.agent_text else 0
        agent_dur_s = m.agent_audio_duration_ms / 1000 if m.agent_audio_duration_ms else 0
        turn_data = {
            "turn_index": turn.turn_index,
            "passed": turn.passed,
            "user_text": turn.user_text,
            "agent_text": turn.agent_text,
            "metrics": {
                "first_byte_ms": m.first_byte_ms,
                "total_ms": m.total_ms,
                "send_duration_ms": m.send_duration_ms,
                "tts_duration_ms": m.tts_duration_ms,
                "stt_duration_ms": m.stt_duration_ms,
                "agent_audio_duration_ms": m.agent_audio_duration_ms,
                "agent_audio_frames": m.agent_audio_frames,
                "user_audio_duration_ms": m.user_audio_duration_ms,
                "agent_words_per_second": word_count / agent_dur_s if agent_dur_s > 0 else 0,
            },
            "evaluations": [
                {
                    "type": r.evaluator_type,
                    "passed": r.passed,
                    "score": r.score,
                    "reason": r.reason,
                    "details": r.details,
                }
                for r in turn.eval_results
            ],
        }
        data["turns"].append(turn_data)

    if report.conversation_eval:
        data["conversation_eval"] = report.conversation_eval

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info("JSON report written to %s", path)


def _frames_to_wav(frames: list[AudioFrame], path: Path) -> None:
    """Write a list of AudioFrames to a WAV file."""
    if not frames:
        return
    sample_rate = frames[0].sample_rate
    num_channels = frames[0].num_channels
    pcm_data = b"".join(f.data for f in frames)
    if not pcm_data:
        return

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)


def save_run_artifacts(report: ScenarioReport, output_dir: str | Path) -> Path:
    """Save all run artifacts to a directory.

    Creates a directory structure:
        <output_dir>/
            report.json
            turn_1_user.wav
            turn_1_agent.wav
            turn_2_user.wav
            turn_2_agent.wav
            ...

    Returns the output directory path.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Write JSON report
    write_json_report(report, out / "report.json")

    # Write audio files per turn + collect frames for full conversation
    all_frames: list[AudioFrame] = []

    for turn in report.turns:
        n = turn.turn_index + 1

        if turn.user_audio:
            wav_path = out / f"turn_{n}_user.wav"
            _frames_to_wav(turn.user_audio, wav_path)
            logger.info("Saved user audio: %s", wav_path)
            all_frames.extend(turn.user_audio)

        if turn.agent_audio:
            wav_path = out / f"turn_{n}_agent.wav"
            _frames_to_wav(turn.agent_audio, wav_path)
            logger.info("Saved agent audio: %s", wav_path)
            all_frames.extend(turn.agent_audio)

    # Write full conversation as a single WAV
    if all_frames:
        _frames_to_wav(all_frames, out / "full_conversation.wav")
        logger.info("Saved full conversation audio: %s", out / "full_conversation.wav")

    logger.info("Run artifacts saved to %s", out)
    return out
