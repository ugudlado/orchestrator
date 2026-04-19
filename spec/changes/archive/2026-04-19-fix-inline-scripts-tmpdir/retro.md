# Retro: workflow issues surfaced during fix-inline-scripts-tmpdir

<!-- Recorded live by the driver during autopilot-2026-04-20-001 iter 1.
     Workflow-improver will read this during the NEXT run-learn-cycle. -->

## ISSUE-24 — Orchestrate resume hijacks dispatch when a stale active state.yaml exists
- **category**: driver-bug
- **severity**: blocker (silently wrong)
- **surfaced_at**: autopilot preflight (before iter 1 began)
- **recorded_at**: 2026-04-20T00:50:00Z
- **detail**: `.state/fix-cost-usd-and-widen-token-split/state.yaml` had `status: active` leftover from an abandoned Apr-17 iter (the fix had shipped under a different slug via commit cd46edb). Step 2 of the orchestrate skill globs `.state/*/state.yaml` for `status: active` and resumes the FIRST match — dropping the user's `--focus` flag and resurrecting a dead bugfix. Only caught because the driver paused to inspect before invoking orchestrate.
- **workaround**: Moved the stale dir to `.state/autopilot/archive/stale-states/` and flipped `status: aborted` with reason. Took one round of advisor reflection to avoid silently resuming.
- **fix_direction**: `orchestrator doctor` (or a preflight check) should list all active state.yaml files and flag any whose worktree or branch no longer exists, or whose archive commit is already on main. Auto-marking them aborted would be ideal; at minimum emit a loud warning on orchestrate entry when multiple actives exist. Alternative: orchestrate should match by change_id (requested ticket) before falling through to "first active".
- **backlog_entry**: propose `orchestrator-doctor-stale-state-detector`

## ISSUE-25 — Dispatcher signals "complete_workflow" when a phase ends mid-plan (driver must advance phase manually)
- **category**: driver-contract-ambiguity
- **severity**: workaround-applied (requires driver awareness)
- **surfaced_at**: end of specify phase AND end of implement phase
- **recorded_at**: 2026-04-20T00:58:00Z
- **detail**: After the last step of a phase runs, `orchestrator next` returns `{"action": "complete_workflow"}` while stderr warns `WARNING: phase 'X' is complete but workflow_plan has other phases (...). Driver must advance state.yaml 'phase' field...`. The orchestrate skill prose documents this (§5), but it's an easy-to-miss contract: naive drivers would treat `complete_workflow` as terminal and archive half-done. Hit it twice in this run.
- **workaround**: Manually edited state.yaml `phase:` + `next_step:` at each transition and re-ran `orchestrator next`.
- **fix_direction**: Either (a) `orchestrator next` auto-advances the phase field when workflow_plan has more phases (returning the next phase's first-action dict in the same call), or (b) introduce a distinct action `advance_phase` with the target phase embedded, so drivers never see the misleading `complete_workflow` until the whole plan is done.
- **backlog_entry**: propose `orchestrator-auto-advance-phase`

## ISSUE-26 — read-sub-state-metrics.sh uses outdated state paths
- **category**: telemetry-helper-drift
- **severity**: cosmetic
- **surfaced_at**: autopilot STEP D.5 (per-iteration metrics capture)
- **recorded_at**: 2026-04-20T01:18:00Z
- **detail**: `config/scripts/read-sub-state-metrics.sh` looks for state.yaml under `$HOME/.workflows/<slug>/state.yaml` and `$REPO_ROOT/spec/changes/archive/<slug>/state.yaml`. Neither path matches the current layout: active states live at `$REPO_ROOT/.state/<slug>/` and archives are date-prefixed at `$REPO_ROOT/spec/changes/archive/YYYY-MM-DD-<slug>/`. Result: autopilot iteration metrics appended to sessions.yaml always show zeros unless the driver sums them manually.
- **workaround**: Summed from `step_history[].usage` directly via an inline python3 script against the date-prefixed archive path.
- **fix_direction**: Update the helper to try (1) `$REPO_ROOT/.state/<slug>/state.yaml`, (2) `$REPO_ROOT/spec/changes/archive/YYYY-MM-DD-<slug>/state.yaml` (glob for the date prefix), (3) keep the old paths as last-resort fallbacks.
- **backlog_entry**: propose `fix-read-sub-state-metrics-paths`

## ISSUE-27 — compute-swe-metrics reports $0 cost for inline-step-heavy workflows
- **category**: metrics-accuracy
- **severity**: cosmetic (accurate data is reachable; just not via this helper)
- **surfaced_at**: complete/compute-swe-metrics
- **recorded_at**: 2026-04-20T01:08:00Z
- **detail**: `compute-swe-metrics.sh` emits `metrics.cost.gross_usd: 0` and `model: unknown` because step_history entries for inline steps have `usage: {}` (no model, no tokens, no cost_usd). `orchestrator cost --change-id` meanwhile reports the accurate $0.246 total because it aggregates from step_events including agent usage payloads. The two data paths disagree — one is the state.yaml view (limited) and one is the DuckDB view (complete).
- **workaround**: None needed for this run — canonical cost is in `orchestrator cost`, not in metrics.cost.*. But any consumer reading `state.yaml → metrics → cost → net_usd` will see $0 for --light flows.
- **fix_direction**: Either (a) have compute-swe-metrics query step_events for the change_id instead of re-computing from state.yaml step_history, or (b) during orchestrator record, join inline steps against step_events to backfill usage into state.yaml before archive. (a) is simpler and more truthful — step_events is the source of truth.
- **backlog_entry**: propose `compute-swe-metrics-from-step-events` (or fold into existing `backfill-step-history-jsonl`)

## ISSUE-28 — Self-referential fix: capture-test-baseline failed to run because of the very bug it triggers
- **category**: workflow-gate-too-strict
- **severity**: workaround-applied
- **surfaced_at**: implement/capture-test-baseline
- **recorded_at**: 2026-04-20T01:02:00Z
- **detail**: `scripts/inline/capture-test-baseline.sh` was one of the 3 mktemp call sites being fixed. Running it pre-fix produced `mktemp: mkstemp failed on /var/folders/... Operation not permitted`. The step contract says "Never fail the phase on baseline capture" but the shell script itself exits non-zero with the mktemp error, so the dispatcher's wrapper surfaced a partial failure. Driver wrote `baseline.skipped: true` by hand via `orchestrator record`.
- **workaround**: Manually recorded a `test_baseline.skipped: true` outcome and advanced the dispatcher; the developer agent then smoke-tested the fixed script.
- **fix_direction**: The inline script should trap the mktemp failure, write `{"baseline": {"skipped": true, "reason": "mktemp failed: <err>"}}` to stdout, and exit 0. Today it falls through and tries to `source` an empty file path. Defense-in-depth beyond this specific fix.
- **backlog_entry**: (partially resolved — the mktemp fix removes the trigger; but the defensive-exit-0 pattern is still worth adding)

## ISSUE-29 — workflow-improver used `advisor` tool, which is not in its declared tools list
- **category**: agent-contract-drift
- **severity**: cosmetic
- **surfaced_at**: complete/run-learn-cycle (via post-hoc `orchestrator cost` anomaly check)
- **recorded_at**: 2026-04-20T01:17:00Z
- **detail**: `orchestrator cost --change-id fix-inline-scripts-tmpdir` ended with an `## Anomalies` section: "workflow-improver used advisor (1 calls) — not in declared tools list". Either the agent's declared tools in `agents/workflow-improver.md` need to include `advisor`, or the spawn prompt shouldn't suggest it.
- **workaround**: None needed — the call succeeded.
- **fix_direction**: Add `advisor` to `agents/workflow-improver.md` tools frontmatter if it's considered a legitimate capability for that role; otherwise scrub the prompt/examples to not reach for it.
- **backlog_entry**: propose `workflow-improver-tools-frontmatter-update`
