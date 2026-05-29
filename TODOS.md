# TODOS

Follow-ups surfaced by the v0.2.0 post-merge review (`/review`, 2026-05-28).
Fixed in branch `fix/v0.2.0-review`: scenario_name path traversal, temp-file +
live-run-queue leaks, shared-SQLite-connection transaction safety.

## P1 — Security

- [ ] **Dashboard auth + non-loopback guard.** The web dashboard has no auth,
  CSRF, or CORS on state-changing routes (write/delete scenario files, delete
  runs/baselines, trigger runs). Default bind is `127.0.0.1`, but
  `serve --host 0.0.0.0` exposes the full surface unauthenticated. Add a token
  gate and refuse non-loopback bind without it. `web/app.py`.
- [ ] **`${ENV_VAR}` exfil via dashboard-authored scenarios.** `_expand_env_vars`
  (`core/scenario.py:250`) expands against the full `os.environ`, so a scenario
  authored in the dashboard can inject any server env var (e.g.
  `${AWS_SECRET_ACCESS_KEY}`) into a transport field sent to an attacker-chosen
  destination. Deferred pending the auth decision — gate dashboard-triggered
  expansion through an operator allowlist (`VOICECHECK_DASHBOARD_ENV_ALLOW`) once
  the trust boundary is settled. CLI runs are operator-trusted and unaffected.

## P2 — Performance / robustness

- [ ] **Event-loop blocking.** Async route handlers do synchronous SQLite + file
  I/O on the loop; `/api/scenarios` runs N+1 ordered-JOIN percentile queries per
  scenario. Wrap store/file calls in `asyncio.to_thread`; make
  `get_all_scenario_stats` set-based. `web/app.py`, `storage/store.py`.
- [ ] **Run/turn retention.** `runs`/`turns` tables and per-run output dirs grow
  unbounded; add a retention/prune policy.

## P3 — Maintainability

- [ ] De-duplicate `_coerce_json_args` + tool-result handling across
  `transports/vapi.py` and `transports/retell.py` into a shared helper.
- [ ] Extract the duplicated "fake `ScenarioReport` from DB row" block in
  `api_save_baseline` / `api_compare_baseline` (`web/app.py`).
- [ ] `shutdown_tracing(timeout_s=5.0)` ignores `timeout_s` — wire it into
  `force_flush` or drop the parameter. `observability/tracing.py:180`.

## Test gaps (cheap to add)

- [ ] `RunOutputWriter` (zero coverage), `compare_metrics` regression-detection
  branches, `BaselineStore` upsert/delete, malformed-JSON transport decode paths,
  cancel endpoint + SSE replay.
