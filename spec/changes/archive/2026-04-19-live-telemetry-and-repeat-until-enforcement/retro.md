# Retro: workflow issues surfaced during live-telemetry-and-repeat-until-enforcement

<!-- Backfilled 2026-04-20 for autopilot-2026-04-19-003 retroactively; going
     forward, record.py appends these as workflow_issues arrive in payloads.
     Workflow-improver reads this during run-learn-cycle. -->

## ISSUE-18 — Missing step contract files for schema-declared steps
- **category**: missing-contract
- **severity**: blocker
- **surfaced_at**: implement/execute-next-task → run-simplify, then implement/run-phase-review → run-feature-verification
- **recorded_at**: 2026-04-19T17:15:00Z
- **detail**: The bugfix schema lists `run-simplify` and `run-feature-verification` in `implement.active`, but `config/steps/run-simplify.yaml` and `config/steps/run-feature-verification.yaml` do not exist. `orchestrator next` errors with `Step contract not found for '…'` and halts dispatch.
- **workaround**: Manually moved both entries to `workflow_plan.implement.filtered` with an explanatory reason; dispatcher skipped them. All assertions those steps would have checked were already covered by run-phase-review.
- **fix_direction**: workflow-init should validate every schema-declared step against the contracts directory at workflow start. Missing contracts → pre-filter with reason "contract file missing", emit a single WARNING, never fail init. A stricter sibling rule: a `make doctor` / `orchestrator doctor` check that lists orphan schema refs.
- **backlog_entry**: spec/changes/backlog/fix-missing-step-contracts/

## ISSUE-19 — Self-referential bug: dispatcher couldn't honor repeat_until during its own fix
- **category**: dispatch-bug
- **severity**: workaround-applied
- **surfaced_at**: implement/execute-next-task (all 6 task iterations)
- **recorded_at**: 2026-04-19T13:00:00Z
- **detail**: The very bug being fixed (ISSUE-16 — record.py ignoring repeat_until) affected the workflow run itself. After T-1 landed, `_compute_next_step` advanced past `execute-next-task` because the new code wasn't live yet. Driver had to re-point `next_step.step_id = execute-next-task` manually before each of T-2..T-6, plus demote the first T-1 entry to `status: in_progress` so the dispatcher didn't skip the step.
- **workaround**: Manual state.yaml edits between each task; grouped T-4+T-5+T-6 into one chain-spawn to cut re-point operations.
- **fix_direction**: For self-referential bugfixes, either (a) bugfix schema detects when change_description names a file under `config/scripts/orchestrator_next/` and adds a "Bootstrap Constraint" section to fix-plan.md that the driver reads, or (b) workflow plan supports a lightweight "bootstrap" marker on steps that pre-applies the fix from the working tree before running the step.
- **backlog_entry**: spec/changes/backlog/self-referential-bug-bootstrap/

## ISSUE-20 — Sandbox blocks mktemp in /var/folders for inline scripts
- **category**: sandbox-block
- **severity**: cosmetic
- **surfaced_at**: diagnose/preview-route
- **recorded_at**: 2026-04-19T11:47:57Z
- **detail**: `scripts/inline/preview-route.sh` calls `mktemp` which resolves to `/var/folders/...` under macOS. The Claude Code sandbox policy denies writes there and the script fails. It's marked non-blocking so the workflow proceeded with `route_preview.status: estimate_unavailable`.
- **workaround**: None needed — script already degrades gracefully.
- **fix_direction**: Two-line change in preview-route.sh: prefix `mktemp` calls with `${TMPDIR:-/tmp}/preview-route-XXXXXX`. Sandbox allows writes under $TMPDIR. Same fix pattern applies to any other inline script that uses `mktemp` unadorned.
- **backlog_entry**: spec/changes/backlog/fix-inline-scripts-tmpdir/

## ISSUE-21 — Linear free-quota exceeded on every workflow run (3rd recurrence)
- **category**: quota-exceeded
- **severity**: cosmetic
- **surfaced_at**: diagnose/workflow-init
- **recorded_at**: 2026-04-19T11:28:00Z
- **detail**: workflow-init attempts to create a Linear ticket; workspace returns "Usage limit exceeded — free issue limit". Agent correctly sets `linear_ticket: null`, `linear_skip_reason: "Linear free issue quota exceeded for workspace"`, and flips both `flags.linear` and `resolved_flags.linear` to false. But this happens on every run — it's not a per-run surprise.
- **workaround**: Graceful handling already in place; workflow proceeds without Linear.
- **fix_direction**: For this workspace specifically, default `linear: false` in project.yaml resolved_flags until quota resets. Add a one-time quota detector at `make doctor` that sets the project-level flag and emits a single reminder.
- **backlog_entry**: spec/changes/backlog/linear-quota-default-off/

## ISSUE-22 — workflow-improver emitted 0 tokens; step_events row had NULL usage
- **category**: other
- **severity**: cosmetic
- **surfaced_at**: complete/run-learn-cycle
- **recorded_at**: 2026-04-19T12:25:00Z
- **detail**: workflow-improver's returned output didn't include a USAGE block, so the driver passed `usage: {}` in the record payload. step_events.cost_usd was NULL and the agent appeared "free" in reports.
- **workaround**: Resolved inline during this same session — the JSONL-enrichment path added in 25ee889 now pulls usage from the sub-agent JSONL at record time, so agents no longer need to self-report. Retroactive backfill SQL also ran for this run's rows.
- **fix_direction**: N/A — fixed. Keep as a historical marker.
- **backlog_entry**: (resolved; no entry needed)

## ISSUE-23 — Pricing key drift for dated model IDs (e.g. claude-haiku-4-5-20251001)
- **category**: other
- **severity**: cosmetic
- **surfaced_at**: complete/run-learn-cycle (observed during tool-telemetry sample verification)
- **recorded_at**: 2026-04-19T18:00:00Z
- **detail**: JSONL returns model strings with date suffixes (e.g. `claude-haiku-4-5-20251001`) but `config/pricing.yaml` keys use the unstamped form (`claude-haiku-4-5`). Lookup missed, fell through to `default` (opus-tier) → ~4× overstatement for haiku rows during the sample run.
- **workaround**: Added `claude-haiku-4-5-20251001` alias to pricing.yaml with the same rates as the unstamped key. Committed in 190df05.
- **fix_direction**: Longer-term: add a date-suffix stripper in `_compute_cost_usd` (regex `-\d{8}$`) so future dated aliases work without a pricing.yaml edit. Alternatively, pricing.yaml gains a `aliases:` block.
- **backlog_entry**: spec/changes/backlog/pricing-date-suffix-lookup/
