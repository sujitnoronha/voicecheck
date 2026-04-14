"""pytest plugin for VoiceCheck — run scenarios as pytest tests.

Usage:
    @pytest.mark.voicecheck("examples/livekit_basic.yaml")
    def test_greeting():
        pass

Or programmatic:
    async def test_custom():
        runner = ScenarioRunner.from_yaml("examples/livekit_basic.yaml")
        report = await runner.run()
        assert report.passed
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "voicecheck(path): run a VoiceCheck scenario YAML file as a test",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        marker = item.get_closest_marker("voicecheck")
        if marker is not None:
            # Wrap the test to run the scenario
            scenario_path = marker.args[0] if marker.args else None
            if scenario_path:
                item.add_marker(pytest.mark.asyncio)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> None:
    marker = item.get_closest_marker("voicecheck")
    if marker is None:
        return

    scenario_path = marker.args[0] if marker.args else None
    if not scenario_path:
        pytest.fail("@pytest.mark.voicecheck requires a scenario file path")

    # Ensure transport registrations — each is optional
    for _transport_mod in (
        "voicecheck.transports.livekit",
        "voicecheck.transports.daily",
        "voicecheck.transports.vapi",
        "voicecheck.transports.retell",
        "voicecheck.transports.telephony",
    ):
        try:
            __import__(_transport_mod)
        except ImportError:
            pass

    # Ensure evaluator registrations
    import voicecheck.evaluators.latency  # noqa: F401
    import voicecheck.evaluators.keyword  # noqa: F401
    import voicecheck.evaluators.turn_count  # noqa: F401
    try:
        import voicecheck.evaluators.llm_judge  # noqa: F401
    except ImportError:
        pass

    from voicecheck.core.scenario import ScenarioRunner

    runner = ScenarioRunner.from_yaml(scenario_path)

    # Avoid asyncio.run() which fails if an event loop is already running
    # (common with pytest-asyncio). Run in a separate thread if needed.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            report = pool.submit(asyncio.run, runner.run()).result()
    else:
        report = asyncio.run(runner.run())

    if not report.passed:
        failures: list[str] = []
        for turn in report.turns:
            for result in turn.eval_results:
                if not result.passed:
                    failures.append(
                        f"Turn {turn.turn_index + 1} [{result.evaluator_type}]: "
                        f"{result.reason}"
                    )
        pytest.fail(
            f"VoiceCheck scenario {report.scenario_name!r} failed:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
