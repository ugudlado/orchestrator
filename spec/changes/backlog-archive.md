# Backlog Archive

<!-- Entries retired from backlog.md. Each block records a `dropped_at`
     or `shipped_at` date and a one-paragraph reason. Kept for historical
     reference so the decision is traceable; ideator ignores this file.
 -->

## autopilot-wakeup-discipline

**Rule: under --auto with background agents, minimize redundant ScheduleWakeup polling** (score 5.5 at time of retirement)

**Dropped:** 2026-05-03
**Reason:** Invalidated by autopilot collapse (commit fa6112d). The entry described
discipline for the multi-iteration autopilot driver loop's ScheduleWakeup polling
between iterations. After the collapse, `/autopilot` is a single-iteration thin
wrapper around `/orchestrate` — there is no driver loop owning wakeups, no
between-iteration polling. The general "don't ScheduleWakeup when task notifications
suffice" heuristic is now an orchestrate-skill prompt concern (not a separate ticket)
and is implicitly addressed by the simpler dispatch shape.

**Recurrence at retirement:** 1

### Retired body

> **Idea**: Autopilot driver emits ScheduleWakeup calls to check on background agents
> (`dev running on T-X, check in 4min`). Each wakeup = full conversation re-hydration =
> millions of cache_read tokens. Task completions already fire `<task-notification>`
> automatically. The wakeup polling is redundant with the notification system and
> adds ~$40 to a typical feature's driver-loop cost.
>
> **Scope**: Add a rule to autopilot/orchestrate skill dispatch prompt: rely on
> task-notification events, not ScheduleWakeup, for wait-only purposes. Allowed:
> external resources, time-gated re-assessment, user-requested periodic reports.
> Forbidden: "dev still running, check in 5min".
>
> **Expected savings**: ~30% driver-loop cache_reads per autopilot feature.
>
> **Source**: single-source-metrics-via-step-events post-ship cost analysis (2026-04-20).

---

## backfill-step-history-jsonl

**Backfill step_history Coverage from JSONL** (score 8.5 at time of retirement)

**Dropped:** 2026-04-20
**Reason:** User decision during autopilot-2026-04-20-002 planning: forward-only
metric correctness, no history repair. The archived-row-coverage gap (39/193 had
`total_tokens`) is acknowledged but not worth a dedicated repair feature.
Going forward, the replacement feature `single-source-metrics-via-step-events`
captures all metrics (including resolution + churn) in DuckDB at complete-phase
time, and the `register-repo.sh` invariant that would prevent the gap reopening
is folded into that feature's scope.

**Recurrence at retirement:** 2 — sources: original-entry,
fix-inline-scripts-tmpdir/ISSUE-27

### Retired body

> **Idea**: Re-run JSONL enrichment across archived features to backfill missing
> tokens and `tools_json` on `step_history` rows, then add an invariant preventing
> the gap from reopening.
>
> **Evidence**:
> - `step_history` has 193 rows; only 39 have `total_tokens`, only 3 have `tools_json`.
> - `per_agent_tool_uses` has 2 rows across 20 ingested features — per-agent-per-tool breakdown is effectively empty.
> - JSONL session data exists for most archives but the initial enrichment script missed many rows (path-resolution bugs, quoted-timestamp bugs — several fixed after the bulk of archives were ingested).
>
> **Fix**:
> 1. Re-run JSONL enrichment pass over every archived `state.yaml` with JSONL sessions available.
> 2. Re-run `register-repo.sh` to reingest.
> 3. Add invariant in register-repo: any `step_history` row with `agent != NULL AND status = completed` must have `total_tokens > 0` OR be marked `agent: inline`.

**Fold-forward note:** item (3) above — the register-repo invariant — is
preserved inside `single-source-metrics-via-step-events` as part of its
ingestion-contract scope. Items (1) and (2) (historical repair) are dropped.

---
