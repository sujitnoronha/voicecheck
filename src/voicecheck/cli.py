"""VoiceCheck CLI — run, validate, and visualize voice agent test results."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import click


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    # Suppress noisy LiveKit SDK logs (text stream "ignoring" messages come
    # from the root logger at INFO level when no callbacks are attached for
    # lk.transcription / lk.agent.events topics).
    if not verbose:
        logging.getLogger("livekit").setLevel(logging.WARNING)
        # The SDK also logs some messages via the root logger; add a filter
        # to drop the "ignoring text stream" noise.
        class _IgnoreTextStreamFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                return "ignoring text stream" not in record.getMessage()

        logging.getLogger().addFilter(_IgnoreTextStreamFilter())


def _timestamped_path(original: str | Path) -> Path:
    """Prefix a filename with a datetime stamp, e.g. results.json -> 2026-03-10_143022_results.json."""
    p = Path(original)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return p.parent / f"{stamp}_{p.name}"


def _parse_duration(value: str) -> int:
    """Parse a duration string like '20m', '1h', '90s' into seconds."""
    match = re.fullmatch(r"(\d+)\s*([smh])?", value.strip().lower())
    if not match:
        raise click.BadParameter(
            f"Invalid duration: {value!r}. Use e.g. '20m', '1h', '90s'."
        )
    num = int(match.group(1))
    unit = match.group(2) or "s"
    multiplier = {"s": 1, "m": 60, "h": 3600}
    return num * multiplier[unit]


def _ensure_registrations() -> None:
    """Import modules that register transports and evaluators."""
    # Transports — each is optional (depends on installed extras)
    for _transport_mod in (
        "voicecheck.transports.livekit",
        "voicecheck.transports.daily",
        "voicecheck.transports.vapi",
        "voicecheck.transports.retell",
    ):
        try:
            __import__(_transport_mod)
        except ImportError:
            pass

    # Evaluators — core ones are always available
    import voicecheck.evaluators.latency  # noqa: F401
    import voicecheck.evaluators.keyword  # noqa: F401
    import voicecheck.evaluators.turn_count  # noqa: F401

    try:
        import voicecheck.evaluators.llm_judge  # noqa: F401
    except ImportError:
        pass

    try:
        import voicecheck.evaluators.emotional_tone  # noqa: F401
    except ImportError:
        pass


@click.group()
@click.version_option(package_name="voicecheck")
def main() -> None:
    """VoiceCheck — E2E testing for voice agents."""


# ── run ──────────────────────────────────────────────────────────


@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging")
@click.option("-o", "--output", type=click.Path(), default=None, help="Write JSON report to path")
@click.option("--parallel", type=int, default=1, help="Parallel scenario execution count")
@click.option("--no-save", is_flag=True, help="Don't save results to local database")
@click.option("--tag", multiple=True, help="Tag this run (repeatable, e.g. --tag ci --tag v2)")
@click.option("--db", type=click.Path(), default=None, help="Custom database path")
@click.option(
    "--duration",
    default=None,
    help="Run as soak test for the given duration (e.g. 20m, 1h, 90s)",
)
@click.option(
    "--save-audio",
    type=click.Path(),
    default=None,
    help="Save audio artifacts (WAV files + report) to this directory",
)
@click.option(
    "--skip-llm-judge",
    is_flag=True,
    help="Skip all llm_judge evaluators and conversation eval (saves API cost)",
)
@click.option(
    "-q", "--questions",
    multiple=True,
    help="Fixed questions to send to the agent (repeatable). Overrides persona/turns in the YAML.",
)
@click.option(
    "--auto",
    "auto_mode",
    is_flag=True,
    help="Use LLM persona for dynamic conversation instead of fixed questions.",
)
@click.option(
    "--concurrent",
    type=int,
    default=1,
    help="Run N simultaneous sessions of the same scenario (load testing)",
)
def run(
    path: str,
    verbose: bool,
    output: str | None,
    parallel: int,
    no_save: bool,
    tag: tuple[str, ...],
    db: str | None,
    duration: str | None,
    save_audio: str | None,
    skip_llm_judge: bool,
    questions: tuple[str, ...],
    auto_mode: bool,
    concurrent: int,
) -> None:
    """Run one or more voice agent test scenarios.

    PATH can be a single YAML file or a directory of YAML files.
    Results are automatically saved to ~/.voicecheck/results.db for history and dashboards.

    Use --duration for soak testing: repeats scenarios in a loop for the specified
    time, then prints an aggregate summary.

    Use --questions to override the scenario's user messages with fixed questions:
      voicecheck run scenario.yaml -q "Hello!" -q "Tell me about planets"

    Use --auto to switch to LLM persona mode (requires OPENAI_API_KEY):
      voicecheck run e2e.yaml --auto
    """
    _setup_logging(verbose)
    _ensure_registrations()

    p = Path(path)
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml"))
        if not files:
            click.echo(f"No YAML files found in {p}", err=True)
            sys.exit(1)
    else:
        click.echo(f"Path not found: {p}", err=True)
        sys.exit(1)

    if concurrent > 1:
        from voicecheck.core.load import run_concurrent, print_load_summary
        async def _load_test() -> None:
            all_passed = True
            for f in files:
                click.echo(f"\nLoad testing {f.name} with {concurrent} concurrent sessions...")
                summary = await run_concurrent(f, concurrent, skip_llm_judge=skip_llm_judge)
                print_load_summary(summary)
                if summary.pass_rate < 100:
                    all_passed = False
            if not all_passed:
                sys.exit(1)
        asyncio.run(_load_test())
    elif duration:
        duration_secs = _parse_duration(duration)
        asyncio.run(
            _run_soak(files, duration_secs, output, parallel, not no_save, list(tag), db)
        )
    else:
        asyncio.run(
            _run_scenarios(
                files, output, parallel, not no_save, list(tag), db,
                save_audio, skip_llm_judge, list(questions), auto_mode,
            )
        )


async def _run_scenarios(
    files: list[Path],
    output: str | None,
    parallel: int,
    save: bool,
    tags: list[str],
    db_path: str | None,
    save_audio: str | None = None,
    skip_llm_judge: bool = False,
    questions: list[str] | None = None,
    auto_mode: bool = False,
) -> None:
    from voicecheck.core.report import print_console_report, save_run_artifacts, write_json_report
    from voicecheck.core.scenario import ScenarioRunner, load_scenario

    store = None
    if save:
        from voicecheck.storage.store import ResultStore
        store = ResultStore(db_path)

    all_passed = True

    def _make_runner(f: Path) -> ScenarioRunner:
        """Load scenario, apply CLI overrides, return runner."""
        scenario = load_scenario(f)
        if auto_mode:
            # Clear questions so the runner falls through to persona mode
            scenario.questions = []
        elif questions:
            scenario.questions = list(questions)
        return ScenarioRunner(scenario, skip_llm_judge=skip_llm_judge)

    def _save_artifacts(report: object, stem: str) -> None:
        """Save audio artifacts if --save-audio was given."""
        if not save_audio:
            return
        if len(files) == 1:
            artifact_dir = Path(save_audio)
        else:
            artifact_dir = Path(save_audio) / stem
        out_path = save_run_artifacts(report, artifact_dir)
        click.echo(f"  Audio saved to {out_path}", err=True)

    if parallel > 1 and len(files) > 1:
        sem = asyncio.Semaphore(parallel)

        async def run_one(f: Path) -> object:
            async with sem:
                runner = _make_runner(f)
                return await runner.run()

        reports = await asyncio.gather(
            *[run_one(f) for f in files], return_exceptions=True
        )

        for f, report in zip(files, reports):
            if isinstance(report, Exception):
                click.echo(f"ERROR running {f.name}: {report}", err=True)
                all_passed = False
            else:
                print_console_report(report)
                if not report.passed:
                    all_passed = False
                if output:
                    out_path = _timestamped_path(Path(output) / f"{f.stem}.json")
                    write_json_report(report, out_path)
                _save_artifacts(report, f.stem)
                if store:
                    run_id = store.save_report(report, tags=tags)
                    click.echo(f"  Saved as run {run_id[:8]}", err=True)
    else:
        for f in files:
            try:
                runner = _make_runner(f)
                report = await runner.run()
                print_console_report(report)
                if not report.passed:
                    all_passed = False
                if output:
                    if len(files) == 1:
                        write_json_report(report, _timestamped_path(output))
                    else:
                        out_path = _timestamped_path(Path(output) / f"{f.stem}.json")
                        write_json_report(report, out_path)
                _save_artifacts(report, f.stem)
                if store:
                    transport_type = runner.scenario.transport.type
                    run_id = store.save_report(report, transport_type=transport_type, tags=tags)
                    click.echo(f"  Saved as run {run_id[:8]}", err=True)
            except Exception as e:
                click.echo(f"ERROR running {f.name}: {e}", err=True)
                all_passed = False

    if store:
        store.close()

    if not all_passed:
        sys.exit(1)


# ── soak ─────────────────────────────────────────────────────────


async def _run_soak(
    files: list[Path],
    duration_secs: int,
    output: str | None,
    parallel: int,
    save: bool,
    tags: list[str],
    db_path: str | None,
) -> None:
    import json as _json

    from voicecheck.core.report import print_console_report
    from voicecheck.core.scenario import ScenarioRunner
    from voicecheck.core.soak import SoakResult, build_soak_summary, print_soak_summary

    store = None
    if save:
        from voicecheck.storage.store import ResultStore
        store = ResultStore(db_path)

    results: list[SoakResult] = []
    iteration = 0
    start = time.monotonic()
    deadline = start + duration_secs

    minutes = duration_secs / 60
    click.echo(f"\nStarting soak test — {minutes:.0f} min, {len(files)} scenario(s), parallel={parallel}")
    click.echo(f"Press Ctrl+C to stop early and see summary.\n")

    try:
        while time.monotonic() < deadline:
            iteration += 1
            elapsed = (time.monotonic() - start) / 60
            remaining = (deadline - time.monotonic()) / 60
            click.echo(f"── Iteration {iteration} ({elapsed:.1f}m elapsed, {remaining:.1f}m remaining) ──")

            iter_tags = tags + ["soak", f"iteration:{iteration}"]

            if parallel > 1 and len(files) > 1:
                sem = asyncio.Semaphore(parallel)

                async def run_one(f: Path, it: int) -> SoakResult:
                    async with sem:
                        try:
                            runner = ScenarioRunner.from_yaml(f)
                            report = await runner.run()
                            return SoakResult(iteration=it, scenario_name=report.scenario_name, report=report)
                        except Exception as e:
                            return SoakResult(iteration=it, scenario_name=f.stem, error=str(e))

                batch = await asyncio.gather(
                    *[run_one(f, iteration) for f in files]
                )

                for sr in batch:
                    results.append(sr)
                    if sr.error:
                        click.echo(f"  ERROR {sr.scenario_name}: {sr.error}", err=True)
                    else:
                        print_console_report(sr.report)
                        if store and sr.report:
                            run_id = store.save_report(sr.report, tags=iter_tags)
                            click.echo(f"  Saved as run {run_id[:8]}", err=True)
            else:
                for f in files:
                    if time.monotonic() >= deadline:
                        break
                    try:
                        runner = ScenarioRunner.from_yaml(f)
                        report = await runner.run()
                        sr = SoakResult(iteration=iteration, scenario_name=report.scenario_name, report=report)
                        results.append(sr)
                        print_console_report(report)
                        if store:
                            transport_type = runner.scenario.transport.type
                            run_id = store.save_report(report, transport_type=transport_type, tags=iter_tags)
                            click.echo(f"  Saved as run {run_id[:8]}", err=True)
                    except Exception as e:
                        sr = SoakResult(iteration=iteration, scenario_name=f.stem, error=str(e))
                        results.append(sr)
                        click.echo(f"  ERROR {f.name}: {e}", err=True)

    except KeyboardInterrupt:
        click.echo("\n\nSoak test interrupted — generating summary...\n")

    total_duration = time.monotonic() - start
    summary = build_soak_summary(results, total_duration, iteration)
    print_soak_summary(summary)

    if output:
        out_path = _timestamped_path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        summary_data = {
            "type": "soak_summary",
            "duration_seconds": summary.duration_seconds,
            "total_iterations": summary.total_iterations,
            "total_runs": summary.total_runs,
            "passed_runs": summary.passed_runs,
            "failed_runs": summary.failed_runs,
            "error_runs": summary.error_runs,
            "pass_rate": summary.pass_rate,
            "avg_first_byte_ms": summary.avg_first_byte_ms,
            "p95_first_byte_ms": summary.p95_first_byte_ms,
            "avg_total_ms": summary.avg_total_ms,
            "per_scenario": summary.per_scenario,
        }
        out_path.write_text(_json.dumps(summary_data, indent=2))
        click.echo(f"\nSoak summary written to {out_path}")

    if store:
        store.close()

    if summary.pass_rate < 100:
        sys.exit(1)


# ── validate ─────────────────────────────────────────────────────


@main.command()
@click.argument("path", type=click.Path(exists=True))
def validate(path: str) -> None:
    """Validate a scenario YAML file without running it."""
    _ensure_registrations()

    from voicecheck.core.scenario import validate_scenario

    p = Path(path)
    files = [p] if p.is_file() else sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml"))

    has_errors = False
    for f in files:
        errors = validate_scenario(f)
        if errors:
            has_errors = True
            click.echo(f"{f.name}: INVALID")
            for error in errors:
                click.echo(f"  - {error}")
        else:
            click.echo(f"{f.name}: OK")

    if has_errors:
        sys.exit(1)


# ── history ──────────────────────────────────────────────────────


@main.command()
@click.option("-n", "--limit", type=int, default=20, help="Number of runs to show")
@click.option("-s", "--scenario", default=None, help="Filter by scenario name")
@click.option("--db", type=click.Path(), default=None, help="Custom database path")
def history(limit: int, scenario: str | None, db: str | None) -> None:
    """Show recent test run history."""
    from voicecheck.storage.store import ResultStore

    store = ResultStore(db)
    runs = store.list_runs(scenario_name=scenario, limit=limit)

    if not runs:
        click.echo("No runs found. Run `voicecheck run` to generate results.")
        return

    # Header
    click.echo(f"\n{'ID':<10} {'Scenario':<30} {'Status':<8} {'Turns':<10} {'Latency':<12} {'Date'}")
    click.echo("-" * 90)

    for r in runs:
        status = click.style("PASS", fg="green") if r["passed"] else click.style("FAIL", fg="red")
        turns = f"{r['passed_turns']}/{r['total_turns']}"
        latency = f"{r['avg_first_byte_ms']:.0f}ms" if r["avg_first_byte_ms"] else "—"
        date = r["created_at"][:19].replace("T", " ")
        click.echo(f"{r['id'][:8]:<10} {r['scenario_name']:<30} {status:<17} {turns:<10} {latency:<12} {date}")

    click.echo(f"\n{len(runs)} runs shown. Use --limit to see more.")
    store.close()


# ── show ─────────────────────────────────────────────────────────


@main.command()
@click.argument("run_id")
@click.option("--db", type=click.Path(), default=None, help="Custom database path")
def show(run_id: str, db: str | None) -> None:
    """Show details of a specific test run."""
    from voicecheck.storage.store import ResultStore

    store = ResultStore(db)

    # Support partial IDs
    runs = store.list_runs(limit=500)
    matching = [r for r in runs if r["id"].startswith(run_id)]
    if not matching:
        click.echo(f"No run found matching '{run_id}'", err=True)
        sys.exit(1)

    full_run = store.get_run(matching[0]["id"])
    if not full_run:
        click.echo(f"Run not found: {run_id}", err=True)
        sys.exit(1)

    status = "PASSED" if full_run["passed"] else "FAILED"
    click.echo(f"\nRun: {full_run['id'][:8]}")
    click.echo(f"Scenario: {full_run['scenario_name']}")
    click.echo(f"Status: {status}")
    click.echo(f"Turns: {full_run['passed_turns']}/{full_run['total_turns']}")
    click.echo(f"Avg Latency: {full_run['avg_first_byte_ms']:.0f}ms")
    if full_run["tags"]:
        click.echo(f"Tags: {', '.join(full_run['tags'])}")
    click.echo(f"Date: {full_run['created_at']}")
    click.echo()

    for t in full_run.get("turns", []):
        turn_status = click.style("PASS", fg="green") if t["passed"] else click.style("FAIL", fg="red")
        click.echo(f"Turn {t['turn_index'] + 1}: [{turn_status}]")
        click.echo(f"  User:  {t['user_text']}")
        click.echo(f"  Agent: {t.get('agent_text', '(no response)')}")
        click.echo(f"  Latency: {t.get('first_byte_ms', 0):.0f}ms first byte, {t.get('total_ms', 0):.0f}ms total")
        for ev in t.get("evaluations", []):
            icon = click.style("+", fg="green") if ev["passed"] else click.style("x", fg="red")
            click.echo(f"  [{icon}] {ev['type']}: {ev['reason']} (score={ev['score']:.2f})")
        click.echo()

    store.close()


# ── dashboard ────────────────────────────────────────────────────


@main.command()
@click.option(
    "-o", "--output",
    type=click.Path(),
    default="voicecheck_dashboard.html",
    help="Output HTML file path",
)
@click.option("-s", "--scenario", default=None, help="Filter by scenario name")
@click.option("-n", "--limit", type=int, default=100, help="Max runs per scenario")
@click.option("--db", type=click.Path(), default=None, help="Custom database path")
@click.option("--open", "open_browser", is_flag=True, help="Open in browser after generating")
def dashboard(
    output: str,
    scenario: str | None,
    limit: int,
    db: str | None,
    open_browser: bool,
) -> None:
    """Generate an HTML dashboard with charts and history.

    The dashboard includes pass/fail trends, latency charts, conversation
    transcripts, and per-scenario quality metrics.
    """
    from voicecheck.storage.dashboard import generate_dashboard
    from voicecheck.storage.store import ResultStore

    store = ResultStore(db)
    out_path = generate_dashboard(store, output, scenario_filter=scenario, limit=limit)
    store.close()

    click.echo(f"Dashboard written to {out_path}")

    if open_browser:
        import webbrowser
        webbrowser.open(f"file://{out_path.resolve()}")


# ── serve ─────────────────────────────────────────────────────────


@main.command()
@click.option("-p", "--port", type=int, default=8989, help="Port to listen on")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--db", type=click.Path(), default=None, help="Custom database path")
def serve(port: int, host: str, db: str | None) -> None:
    """Launch the live web dashboard.

    Opens an interactive dashboard at http://HOST:PORT with scenario overviews,
    run history, detailed transcripts, and latency analytics.

    Requires: pip install voicecheck[dashboard]
    """
    try:
        import uvicorn
    except ImportError:
        click.echo(
            "Dashboard dependencies not installed.\n"
            "Install them with: pip install voicecheck[dashboard]",
            err=True,
        )
        sys.exit(1)

    from voicecheck.web.app import create_app

    app = create_app(db_path=db)

    click.echo(f"VoiceCheck dashboard at http://{host}:{port}")
    click.echo("Press Ctrl+C to stop.\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
