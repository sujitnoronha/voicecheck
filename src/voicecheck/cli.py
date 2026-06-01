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
        raise click.BadParameter(f"Invalid duration: {value!r}. Use e.g. '20m', '1h', '90s'.")
    num = int(match.group(1))
    unit = match.group(2) or "s"
    multiplier = {"s": 1, "m": 60, "h": 3600}
    return num * multiplier[unit]


def _init_observability_from_first_file(
    first_file: Path,
    *,
    cli_endpoint: str | None,
    cli_console: bool,
    cli_service: str | None,
    cli_tags: list[str],
) -> None:
    """Initialize OTel tracing from the first scenario's ``observability:`` block.

    The first file wins because run-level resource attributes (service.name,
    endpoint) must be fixed before the first span — voicecheck doesn't try
    to mix providers across files in one run. CLI flags override YAML.

    Standard ``OTEL_EXPORTER_OTLP_*`` env vars are honoured by the OTLP
    exporter automatically and don't need to be threaded through here.
    """
    import os as _os

    from voicecheck.core.scenario import load_scenario
    from voicecheck.observability import ObservabilityConfig, init_tracing

    yaml_obs = None
    try:
        scenario = load_scenario(first_file, strict_env=False)
        yaml_obs = scenario.observability
    except Exception:
        # Don't let an unparseable scenario block CLI startup — the
        # subsequent scenario load in _run_scenarios will surface the
        # real error with a clearer message.
        pass

    enabled = bool(
        cli_endpoint
        or cli_console
        or _os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or (yaml_obs and yaml_obs.enabled)
    )
    if not enabled:
        return

    cfg = ObservabilityConfig(
        enabled=True,
        service_name=(cli_service or (yaml_obs.service_name if yaml_obs else None) or "voicecheck"),
        endpoint=cli_endpoint or (yaml_obs.endpoint if yaml_obs else None),
        headers=(yaml_obs.headers if yaml_obs else {}) or {},
        console=cli_console or (yaml_obs.console if yaml_obs else False),
        tags=list(cli_tags),
        resource_attrs=(yaml_obs.resource_attrs if yaml_obs else {}) or {},
    )
    init_tracing(cfg)


def _ensure_registrations() -> None:
    """Import modules that register transports and evaluators."""
    # Echo transport is always available — zero deps, built-in dev transport.
    import voicecheck.transports.echo  # noqa: F401

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
    import voicecheck.evaluators.keyword  # noqa: F401
    import voicecheck.evaluators.latency  # noqa: F401
    import voicecheck.evaluators.tool_called  # noqa: F401
    import voicecheck.evaluators.tool_sequence  # noqa: F401
    import voicecheck.evaluators.turn_count  # noqa: F401

    # LLM-based evaluators — require openai or anthropic SDK
    for _eval_mod in (
        "voicecheck.evaluators.llm_judge",
        "voicecheck.evaluators.rubric_judge",
        "voicecheck.evaluators.emotional_tone",
        "voicecheck.evaluators.memory_recall",
        "voicecheck.evaluators.character_break",
        "voicecheck.evaluators.info_leakage",
        "voicecheck.evaluators.fact_accuracy",
        "voicecheck.evaluators.personality_consistency",
    ):
        try:
            __import__(_eval_mod)
        except ImportError:
            pass


@click.group()
@click.version_option(package_name="voicecheck")
def main() -> None:
    """VoiceCheck — end-to-end testing for voice agents.

    Write a YAML scenario; VoiceCheck synthesizes real audio, streams it through a
    real transport (LiveKit, Daily, VAPI, Retell), captures the agent's reply, and
    grades every turn (latency, tone, leaks, tool calls, and more).

    \b
    Quick start:
      voicecheck run examples/echo_smoke.yaml --skip-llm-judge   # zero-key smoke test
      voicecheck validate my_test.yaml                           # schema check
      voicecheck run my_test.yaml                                # run a real scenario
      voicecheck serve                                           # web dashboard

    \b
    Or let an AI coding agent set it up and write tests for you:
      voicecheck install-skill   # then run /setup-voicecheck in Claude Code or Codex

    Run `voicecheck COMMAND --help` for details on any command.
    """


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
    "-q",
    "--questions",
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
@click.option(
    "--otel-endpoint",
    default=None,
    help="OTLP HTTP endpoint to export voicecheck traces to (e.g. https://otlp.example.com/v1/traces). "
    "Honors OTEL_EXPORTER_OTLP_ENDPOINT/HEADERS env vars too.",
)
@click.option(
    "--otel-console",
    is_flag=True,
    help="Print OTel spans to stderr (useful for debugging the observability layer).",
)
@click.option(
    "--otel-service",
    default=None,
    help="Override service.name resource attribute (default: 'voicecheck' or YAML value).",
)
@click.option(
    "--baseline",
    default=None,
    help="Compare results against a saved baseline by name.",
)
@click.option(
    "--fail-on-regress",
    is_flag=True,
    help="Exit non-zero if any CI-failure regression is detected vs --baseline.",
)
@click.option(
    "--output-dir",
    type=click.Path(),
    default=None,
    help="Directory for per-run output (report.json, call_log.jsonl, scenario.yaml). "
    "Defaults to ~/.voicecheck.",
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
    otel_endpoint: str | None,
    otel_console: bool,
    otel_service: str | None,
    baseline: str | None,
    fail_on_regress: bool,
    output_dir: str | None,
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

    _init_observability_from_first_file(
        files[0],
        cli_endpoint=otel_endpoint,
        cli_console=otel_console,
        cli_service=otel_service,
        cli_tags=list(tag),
    )

    try:
        if concurrent > 1:
            from voicecheck.core.load import print_load_summary, run_concurrent

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
                    files,
                    output,
                    parallel,
                    not no_save,
                    list(tag),
                    db,
                    save_audio,
                    skip_llm_judge,
                    list(questions),
                    auto_mode,
                    baseline_name=baseline,
                    fail_on_regress=fail_on_regress,
                    output_dir=output_dir,
                )
            )
    finally:
        from voicecheck.observability import shutdown_tracing

        shutdown_tracing()


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
    baseline_name: str | None = None,
    fail_on_regress: bool = False,
    output_dir: str | None = None,
) -> None:
    import uuid as _uuid

    from voicecheck.core.report import print_console_report, save_run_artifacts, write_json_report
    from voicecheck.core.scenario import ScenarioRunner, load_scenario
    from voicecheck.storage.output import RunOutputWriter
    from voicecheck.storage.store import DEFAULT_OUTPUT_DIR

    store = None
    if save:
        from voicecheck.storage.store import ResultStore

        store = ResultStore(db_path)

    out_base = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    all_passed = True
    has_ci_regression = False

    def _make_runner(f: Path) -> ScenarioRunner:
        scenario = load_scenario(f)
        if auto_mode:
            scenario.questions = []
        elif questions:
            scenario.questions = list(questions)
        return ScenarioRunner(scenario, skip_llm_judge=skip_llm_judge)

    def _write_run_dir(
        report: object,
        run_id: str,
        yaml_path: Path,
        artifact_dir: Path | None,
    ) -> Path:
        """Write per-run directory with report, scenario snapshot, and call log."""
        writer = RunOutputWriter(out_base, run_id)
        try:
            writer.write_report(report)
            writer.write_scenario(yaml_path.read_text(encoding="utf-8"))
            writer.reconstruct_call_log(report)
            if artifact_dir and artifact_dir != writer.path:
                # Audio was written elsewhere — symlink or copy WAVs into run dir
                import shutil

                for wav in artifact_dir.glob("*.wav"):
                    dest = writer.path / wav.name
                    if not dest.exists():
                        shutil.copy2(wav, dest)
        finally:
            writer.close()
        return writer.path

    def _save_audio(report: object, run_dir: Path, stem: str) -> Path | None:
        if not save_audio:
            return None
        if len(files) == 1:
            artifact_dir = Path(save_audio)
        else:
            artifact_dir = Path(save_audio) / stem
        out_path = save_run_artifacts(report, artifact_dir)
        click.echo(f"  Audio saved to {out_path}", err=True)
        return out_path

    def _check_baseline(report: object, scenario_name: str, run_id: str) -> None:
        nonlocal has_ci_regression
        if not baseline_name or not store:
            return
        from voicecheck.storage.baselines import (
            compare_metrics,
            format_comparison_table,
            metrics_from_report,
        )

        bl_metrics = store.baselines.metrics_for(baseline_name, scenario_name)
        if not bl_metrics:
            click.echo(
                f"  [warn] Baseline '{baseline_name}' not found for scenario '{scenario_name}' — skipping comparison.",
                err=True,
            )
            return
        current = metrics_from_report(report)
        regressions = compare_metrics(bl_metrics, current)
        table = format_comparison_table(regressions, baseline_name)
        click.echo(table, err=True)
        if any(r.is_ci_failure for r in regressions):
            has_ci_regression = True

    async def _run_one_file(f: Path) -> None:
        nonlocal all_passed
        run_id = str(_uuid.uuid4())
        try:
            runner = _make_runner(f)
            report = await runner.run()
            print_console_report(report)
            if not report.passed:
                all_passed = False
            if output:
                out_path = (
                    _timestamped_path(output)
                    if len(files) == 1
                    else _timestamped_path(Path(output) / f"{f.stem}.json")
                )
                write_json_report(report, out_path)

            artifact_dir = _save_audio(report, out_base / "runs" / run_id, f.stem)

            run_dir: Path | None = None
            if save:
                run_dir = _write_run_dir(report, run_id, f, artifact_dir)
                # If audio went into a separate dir, the run dir also has copies (done in _write_run_dir)
                # Use run_dir as artifacts_dir so dashboard can serve audio from it
                effective_artifacts = (
                    run_dir
                    if (artifact_dir and (run_dir / "turn_1_user.wav").exists())
                    else artifact_dir
                )
                transport_type = runner.scenario.transport.type
                store.save_report(
                    report,
                    transport_type=transport_type,
                    tags=tags,
                    artifacts_dir=effective_artifacts,
                    run_id=run_id,
                    run_dir=run_dir,
                )
                click.echo(f"  Saved run {run_id[:8]} → {run_dir}", err=True)

            _check_baseline(report, report.scenario_name, run_id)

        except Exception as e:
            click.echo(f"ERROR running {f.name}: {e}", err=True)
            all_passed = False

    if parallel > 1 and len(files) > 1:
        sem = asyncio.Semaphore(parallel)

        async def _par(f: Path) -> None:
            async with sem:
                await _run_one_file(f)

        await asyncio.gather(*[_par(f) for f in files])
    else:
        for f in files:
            await _run_one_file(f)

    if store:
        store.close()

    if fail_on_regress and has_ci_regression:
        click.echo("\nFAILED: CI-failure regressions detected vs baseline.", err=True)
        sys.exit(2)

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
    click.echo(
        f"\nStarting soak test — {minutes:.0f} min, {len(files)} scenario(s), parallel={parallel}"
    )
    click.echo("Press Ctrl+C to stop early and see summary.\n")

    try:
        while time.monotonic() < deadline:
            iteration += 1
            elapsed = (time.monotonic() - start) / 60
            remaining = (deadline - time.monotonic()) / 60
            click.echo(
                f"── Iteration {iteration} ({elapsed:.1f}m elapsed, {remaining:.1f}m remaining) ──"
            )

            iter_tags = tags + ["soak", f"iteration:{iteration}"]

            if parallel > 1 and len(files) > 1:
                sem = asyncio.Semaphore(parallel)

                async def run_one(f: Path, it: int) -> SoakResult:
                    async with sem:
                        try:
                            runner = ScenarioRunner.from_yaml(f)
                            report = await runner.run()
                            return SoakResult(
                                iteration=it, scenario_name=report.scenario_name, report=report
                            )
                        except Exception as e:
                            return SoakResult(iteration=it, scenario_name=f.stem, error=str(e))

                batch = await asyncio.gather(*[run_one(f, iteration) for f in files])

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
                        sr = SoakResult(
                            iteration=iteration, scenario_name=report.scenario_name, report=report
                        )
                        results.append(sr)
                        print_console_report(report)
                        if store:
                            transport_type = runner.scenario.transport.type
                            run_id = store.save_report(
                                report, transport_type=transport_type, tags=iter_tags
                            )
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


# ── baseline ─────────────────────────────────────────────────────


@main.group()
def baseline() -> None:
    """Manage regression baselines."""


@baseline.command("save")
@click.argument("name")
@click.option("-s", "--scenario", required=True, help="Scenario name to snapshot.")
@click.option(
    "--from-run", "from_run", default=None, help="Use a specific run ID (defaults to latest)."
)
@click.option("--notes", default="", help="Optional notes for this baseline.")
@click.option("--db", type=click.Path(), default=None)
def baseline_save(
    name: str, scenario: str, from_run: str | None, notes: str, db: str | None
) -> None:
    """Save a baseline snapshot for a scenario."""
    from voicecheck.storage.baselines import metrics_from_report
    from voicecheck.storage.store import ResultStore

    store = ResultStore(db)
    if from_run:
        runs = store.list_runs(limit=500)
        matching = [r for r in runs if r["id"].startswith(from_run)]
        if not matching:
            click.echo(f"No run found matching '{from_run}'", err=True)
            sys.exit(1)
        run = store.get_run(matching[0]["id"])
    else:
        runs = store.list_runs(scenario_name=scenario, limit=1)
        if not runs:
            click.echo(f"No runs found for scenario '{scenario}'", err=True)
            sys.exit(1)
        run = store.get_run(runs[0]["id"])

    if not run:
        click.echo("Run not found.", err=True)
        sys.exit(1)

    # Reconstruct a minimal report-like object for metric extraction
    from types import SimpleNamespace

    from voicecheck.core.types import EvalResult

    turns = []
    for t in run.get("turns", []):
        evals = [
            EvalResult(
                evaluator_type=e["type"],
                passed=e["passed"],
                score=e.get("score", 0.0),
                reason=e.get("reason", ""),
                details=e.get("details"),
            )
            for e in t.get("evaluations", [])
        ]
        turns.append(
            SimpleNamespace(
                turn_index=t["turn_index"],
                user_text=t["user_text"],
                agent_text=t.get("agent_text") or "",
                passed=bool(t["passed"]),
                metrics=SimpleNamespace(
                    first_byte_ms=t.get("first_byte_ms") or 0.0,
                    total_ms=t.get("total_ms") or 0.0,
                ),
                eval_results=evals,
                tool_calls=[],
            )
        )

    fake_report = SimpleNamespace(
        scenario_name=run["scenario_name"],
        turns=turns,
        conversation_eval=run.get("conversation_eval"),
        passed=bool(run["passed"]),
        total_turns=run["total_turns"],
        passed_turns=run["passed_turns"],
    )

    metrics = metrics_from_report(fake_report)
    bid = store.baselines.save(
        name=name,
        scenario_name=run["scenario_name"],
        run_id=run["id"],
        metrics=metrics,
        notes=notes,
    )
    click.echo(f"Baseline '{name}' saved for scenario '{run['scenario_name']}' (id={bid[:8]})")
    store.close()


@baseline.command("list")
@click.option("-s", "--scenario", default=None, help="Filter by scenario name.")
@click.option("--db", type=click.Path(), default=None)
def baseline_list(scenario: str | None, db: str | None) -> None:
    """List saved baselines."""
    from voicecheck.storage.store import ResultStore

    store = ResultStore(db)
    rows = store.baselines.list(scenario_name=scenario)
    store.close()

    if not rows:
        click.echo("No baselines found.")
        return

    click.echo(f"\n{'Name':<20} {'Scenario':<30} {'Run ID':<10} {'Date'}")
    click.echo("-" * 80)
    for r in rows:
        click.echo(
            f"{r['name']:<20} {r['scenario_name']:<30} {r['run_id'][:8]:<10} {r['created_at'][:19]}"
        )


@baseline.command("show")
@click.argument("name")
@click.option("-s", "--scenario", required=True, help="Scenario name.")
@click.option("--db", type=click.Path(), default=None)
def baseline_show(name: str, scenario: str, db: str | None) -> None:
    """Show baseline metrics."""
    import json as _json

    from voicecheck.storage.store import ResultStore

    store = ResultStore(db)
    row = store.baselines.get(name, scenario)
    store.close()

    if not row:
        click.echo(f"Baseline '{name}' not found for scenario '{scenario}'", err=True)
        sys.exit(1)

    click.echo(f"\nBaseline: {row['name']}  ({row['scenario_name']})")
    click.echo(f"Run:      {row['run_id'][:8]}  |  Saved: {row['created_at'][:19]}")
    if row.get("notes"):
        click.echo(f"Notes:    {row['notes']}")
    click.echo()
    m = _json.loads(row["metrics_json"])
    click.echo(
        f"  pass_rate:          {m['pass_rate'] * 100:.1f}%  ({m['passed_turns']}/{m['total_turns']} turns)"
    )
    click.echo(f"  avg_first_byte_ms:  {m['avg_first_byte_ms']:.0f}ms")
    click.echo(f"  p95_first_byte_ms:  {m['p95_first_byte_ms']:.0f}ms")
    click.echo(f"  avg_total_ms:       {m['avg_total_ms']:.0f}ms")
    click.echo(f"  p95_total_ms:       {m['p95_total_ms']:.0f}ms")
    if m.get("evaluator_pass_rates"):
        click.echo("  evaluator_pass_rates:")
        for ev, rate in m["evaluator_pass_rates"].items():
            click.echo(f"    {ev}: {rate * 100:.1f}%")


@baseline.command("delete")
@click.argument("name")
@click.option("-s", "--scenario", required=True, help="Scenario name.")
@click.option("--db", type=click.Path(), default=None)
def baseline_delete(name: str, scenario: str, db: str | None) -> None:
    """Delete a baseline."""
    from voicecheck.storage.store import ResultStore

    store = ResultStore(db)
    deleted = store.baselines.delete(name, scenario)
    store.close()
    if deleted:
        click.echo(f"Deleted baseline '{name}' for scenario '{scenario}'")
    else:
        click.echo(f"Baseline '{name}' not found for scenario '{scenario}'", err=True)
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
    click.echo(
        f"\n{'ID':<10} {'Scenario':<30} {'Status':<8} {'Turns':<10} {'Latency':<12} {'Date'}"
    )
    click.echo("-" * 90)

    for r in runs:
        status = click.style("PASS", fg="green") if r["passed"] else click.style("FAIL", fg="red")
        turns = f"{r['passed_turns']}/{r['total_turns']}"
        latency = f"{r['avg_first_byte_ms']:.0f}ms" if r["avg_first_byte_ms"] else "—"
        date = r["created_at"][:19].replace("T", " ")
        click.echo(
            f"{r['id'][:8]:<10} {r['scenario_name']:<30} {status:<17} {turns:<10} {latency:<12} {date}"
        )

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
        turn_status = (
            click.style("PASS", fg="green") if t["passed"] else click.style("FAIL", fg="red")
        )
        click.echo(f"Turn {t['turn_index'] + 1}: [{turn_status}]")
        click.echo(f"  User:  {t['user_text']}")
        click.echo(f"  Agent: {t.get('agent_text', '(no response)')}")
        click.echo(
            f"  Latency: {t.get('first_byte_ms', 0):.0f}ms first byte, {t.get('total_ms', 0):.0f}ms total"
        )
        for ev in t.get("evaluations", []):
            icon = click.style("+", fg="green") if ev["passed"] else click.style("x", fg="red")
            click.echo(f"  [{icon}] {ev['type']}: {ev['reason']} (score={ev['score']:.2f})")
        click.echo()

    store.close()


# ── dashboard ────────────────────────────────────────────────────


@main.command()
@click.option(
    "-o",
    "--output",
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
@click.option(
    "--output-dir",
    type=click.Path(),
    default=None,
    help="Per-run output directory (defaults to ~/.voicecheck).",
)
def serve(port: int, host: str, db: str | None, output_dir: str | None) -> None:
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

    app = create_app(db_path=db, output_dir=output_dir)

    click.echo(f"VoiceCheck dashboard at http://{host}:{port}")
    click.echo("Press Ctrl+C to stop.\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


# ── schema ────────────────────────────────────────────────────────


@main.command()
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    default=None,
    help="Write schema to file (default: print to stdout).",
)
def schema(output: str | None) -> None:
    """Print the JSON schema for scenario YAML files.

    Pipe into a file and point your editor at it for autocomplete:

      voicecheck schema > voicecheck.schema.json

    VS Code: add to settings.json:
      "yaml.schemas": { "./voicecheck.schema.json": "*.yaml" }
    """
    import json as _json

    from voicecheck.core.scenario import Scenario

    s = _json.dumps(Scenario.model_json_schema(), indent=2)
    if output:
        Path(output).write_text(s)
        click.echo(f"Schema written to {output}")
    else:
        click.echo(s)


# ── install-skill ─────────────────────────────────────────────────

_BUNDLED_SKILLS = ["setup-voicecheck", "write-voicecheck-test"]


@main.command(name="install-skill")
@click.argument("name", required=False)
@click.option("--codex", is_flag=True, help="Also install into ~/.agents/skills (Codex).")
@click.option(
    "--claude/--no-claude",
    default=True,
    help="Install into ~/.claude/skills (Claude Code). Default: yes.",
)
@click.option(
    "--dir", "target_dir", type=click.Path(), default=None, help="Override the skills base dir."
)
@click.option("--force", is_flag=True, help="Overwrite an existing installed skill.")
def install_skill(
    name: str | None, codex: bool, claude: bool, target_dir: str | None, force: bool
) -> None:
    """Install the bundled Agent Skills so AI coding agents can drive VoiceCheck.

    After `pip install voicecheck`, the skills ship inside the wheel but aren't yet
    in your agent's skills directory. This copies them there:

        voicecheck install-skill              # all skills, into ~/.claude/skills
        voicecheck install-skill --codex      # also into ~/.agents/skills (Codex)
        voicecheck install-skill setup-voicecheck   # just one

    Then open Claude Code (or Codex) anywhere and run /setup-voicecheck or
    /write-voicecheck-test.
    """
    import shutil
    from importlib import resources

    names = [name] if name else list(_BUNDLED_SKILLS)
    unknown = [n for n in names if n not in _BUNDLED_SKILLS]
    if unknown:
        click.echo(
            f"Unknown skill(s): {', '.join(unknown)}. Available: {', '.join(_BUNDLED_SKILLS)}",
            err=True,
        )
        sys.exit(1)

    if target_dir:
        targets = [Path(target_dir)]
    else:
        targets = []
        if claude:
            targets.append(Path.home() / ".claude" / "skills")
        if codex:
            targets.append(Path.home() / ".agents" / "skills")
    if not targets:
        click.echo("Nothing to do — pass --claude and/or --codex (or --dir).", err=True)
        sys.exit(1)

    skills_root = Path(str(resources.files("voicecheck"))) / "skills"
    installed = 0
    for skill in names:
        src = skills_root / skill
        if not src.is_dir():
            click.echo(f"Bundled skill not found in package: {skill}", err=True)
            sys.exit(1)
        for base in targets:
            dest = base / skill
            if dest.exists():
                if not force:
                    click.echo(f"skip  {dest}  (exists — use --force to overwrite)")
                    continue
                shutil.rmtree(dest)
            base.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest)
            for script in dest.glob("scripts/*"):
                script.chmod(0o755)
            click.echo(f"ok    {dest}")
            installed += 1

    if installed:
        click.echo(
            f"\nInstalled {installed} skill copy(ies). Open Claude Code or Codex and run "
            "/setup-voicecheck."
        )


if __name__ == "__main__":
    main()
