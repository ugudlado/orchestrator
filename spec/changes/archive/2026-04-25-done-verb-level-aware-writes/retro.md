# Retro: done-verb-level-aware-writes (Phase 4)

## What shipped

- Renamed `orchestrator record` → `orchestrator done` (CLI verb only; Python module `record.py` kept stable to preserve `_compute_cost_usd` imports at `bin/orchestrator`).
- New DuckDB tables `phase_events` and `driver_sessions` via migration `0003`.
- Level-aware writes inside `done`: phase boundary writes step + phase rows in one DuckDB transaction; feature boundary additionally writes driver_session row + per-subagent synthetic step_events rows.
- `payload.status` dispatch: `completed` (normal), `recovered` (skips boundary), `abandoned` (sets state.status=blocked).
- Absorbed `_ingest_driver_main` and `_ingest_subagents_main` (213 lines deleted from `bin/orchestrator`); deleted `ingest-driver-auto` and `ingest-subagents-auto` step contracts and inline scripts.
- Staged migration completed all 3 stages (alias → migrate callers → deprecate) in one feature run.
- 28 commits on `feature/done-verb-level-aware-writes` (27 task commits + 1 minor follow-up for M-1).
- Tests: 288 passing (54 new), 2 pre-existing failures (test_archive_backlog_cleanup) unchanged.

## Discoveries / bugs to file as backlog items

### 1. Dispatcher `_resolve_tasks_md` doesn't find `.state/<slug>/tasks.md`

`config/scripts/orchestrator_next/record.py:_resolve_tasks_md()` only looks for tasks.md at:
- `<tasks_path>` if explicitly set in state.yaml, OR
- `<worktree_path>/spec/changes/<change_id>/tasks.md`

Tasks.md for this feature lives at `.state/done-verb-level-aware-writes/tasks.md` (the canonical state directory per the workflow-engine refactor's path convention). Neither lookup path matches that location, so `_check_all_tasks_completed()` reads no file and **fails open (returns True)**. This means the `repeat_until: all_tasks_completed` loop terminates on the first `execute-next-task` recording — even when 16 of 29 tasks are still `[ ]`.

This shipped feature ran four developer agent chains externally (driver-orchestrated) so the bug didn't bite. Per-task recording would have hit this differently — the dispatcher would have advanced to phase review after task 1.

**Fix direction**: extend `_resolve_tasks_md` to also try `<state.yaml dir>/tasks.md` (i.e., `.state/<slug>/tasks.md`). One-line addition.

### 2. estimate-cost.sh datetime JSON serialization

`scripts/inline/preview-route.sh` invokes `config/scripts/estimate-cost.sh`, which produces YAML with a `generated_at` datetime field. The Python wrapper that re-encodes to JSON fails because `datetime` is not JSON-serializable. Today this is non-blocking (preview-route is fail-soft) but the estimate is silently unavailable for every workflow.

**Fix direction**: convert the datetime to ISO string before JSON encoding inside the wrapper.

### 3. Orphan `in_progress` row pattern

`orchestrator next` pre-stamps an `in_progress` row in step_history before returning the action. When the spawned agent calls `orchestrator record` with `status: completed`, the completion is appended after the in_progress row — but the next `next` call reads the LAST entry, which is the in_progress one (not the agent's completion). Result: the dispatcher returns `resume_step` for a step that's already done.

This bit four times in this run (explore, design-and-draft-artifacts, run-phase-review, execute-next-task). Workaround used: re-record with status:completed to push a fresh tail entry.

**Fix direction**: this is the `done` salvage path's territory. When `record()` sees an existing `in_progress` row for the same `step_id` + `attempt`, replace it (don't append). Or: have `next` only pre-stamp if no completion row already exists for the step.

## What worked

- **Staged migration approach** (Stage A additive → Stage B caller migration → Stage C deprecation) made the bootstrap-self-modifying problem trivial. The `done` alias accepted both verbs throughout, so no spawn ever broke even though SKILL.md was being edited live.
- **Atomic boundary writes**: parsing JSONL outside the transaction + inserts inside kept the BEGIN window short. Per-row fail-soft for subagent rows survived malformed transcripts cleanly.
- **TDD with 7 RED tasks**: every helper got a failing test before implementation. The pre-transaction subagent parse pattern was caught by the round-1 phase review (FT-1 finding) before code was written.
- **Chain-grouped developer spawns** (per dependency root, not per task): 4 chains × ~7 tasks each cut spawn count from 29 to 4. Worked cleanly because Stage A is internally TDD-ordered.

## What didn't work / friction points

- The dispatcher `_resolve_tasks_md` bug forced manual chain orchestration instead of letting `repeat_until` drive it. Should have noticed earlier.
- The orphan `in_progress` pattern surfaced the underlying data-integrity issue this very feature is designed to fix; ironic but instructive.
- Linear ticket couldn't be filed (MCP unauthenticated). Still tracked here in retro.

## Numbers

- Specify phase: 2 review rounds (5 → 9), 4 minor findings remained.
- Implement phase: 1 review round (9), AC verification 11/11 PASS, 0 critical, 0 important, 2 minor.
- Cost so far: ~$5+ (precise figure in step_history).
- Lines net: -213 (`_ingest_*_main` deletion) + ~600 added (helpers + migration + tests + new tables) = +~400 net.
- Commits: 28 on feature branch.
