# Retro — single-source-metrics-via-step-events

**Shipped:** 2026-04-19 (iter 3 after 2 aborted iterations)
**Session:** autopilot-2026-04-20-003
**Cost:** $193.96 net, $292.28 gross, 2.2M tokens, 465.7 min wall-clock
**Tasks:** 26 (20 planned + 6 added during implement/post-ship)
**Commits:** 27 merged to main (`0bf897d` merge, `b112e5d` archive)
**Review score:** implement phase approved 9/10 (3 review rounds: initial 7→T-21→retry 9→T-23/24 delta 9→T-25 byte-compat)

## What shipped

- `step_events.turns` column (passthrough from `jsonl_usage._aggregate`)
- `_totals()` widened: cache tokens, gross_usd, dominant model, pricing
- New DuckDB table `feature_metrics` + `upsert_feature_metrics()`
- New CLI subcommand `orchestrator metrics --change-id X --format md|json`
- New step `ingest-feature-metrics` (Python, complete phase, fail-loud on missing tasks.md)
- New step `ingest-driver-auto` (auto-invokes `orchestrator ingest-driver` at complete phase)
- `compute-swe-metrics.sh`: 736 → 57 lines (thin projection)
- `read-sub-state-metrics.sh`: 80 → 39 lines (thin projection, ISSUE-26 path bug disappeared)
- `register-repo.sh`: FR-11 invariant — rejects step_history rows where real agent reported no tokens
- 5 broken test paths fixed (`config/scripts/compute-swe-metrics.sh` → `scripts/inline/...`)
- 12 new test modules (3 pytest + 9 bash)

Byte-compat verified: zero missing legacy keys; only new-only field is `source` (provenance, by design).

## What went right

1. **Discovery caught the scope correctly.** Prior iter 2 aborted because architect designed a hybrid wrapper that kept JSONL parsing. Iter 3's discovery brief explicitly rejected the hybrid and committed to Approach B (DuckDB as sole source). Reviewer verified ISSUE-32 was closed, not just renamed.

2. **Parallel dev chains worked.** T-16/17 (register-repo invariant) + T-9/10/11 (ingest step) + T-12/13/14/15/18 (wrapper rewrites) ran as 3 concurrent dev agents on orthogonal files. No merge conflicts. Cut wall-clock ~40% vs sequential.

3. **TDD discipline held.** 26 tasks, 26 RED-before-GREEN pairs. Only 1 regression surfaced (T-21 ingest missed 4 resolution fields) — caught by the integration test T-19, which is exactly what integration tests are for.

4. **Post-ship validation caught real gaps.** Running the new wrapper on our own feature + a real archived feature surfaced:
   - `gross_usd < net_usd` anomaly → traced to missing driver-loop data, not formula bug (T-22 closed as no-bug)
   - Missing `api_calls` and `per_tool_uses` fields → fixed as T-25 (byte-compat restored)
   - `orchestrator metrics` exit-3 on unregistered change_ids → backlogged (`metrics-no-data-graceful`)
   - Driver-loop mechanism existed but wasn't auto-invoked → fixed as T-23/T-24

## What went wrong

### ISSUE-33: dispatch.py doesn't honor repeat_until predicate when completed entries exist

**Severity:** blocking (required driver workaround mid-flight)
**Location:** `config/scripts/orchestrator_next/dispatch.py::_find_completed_step` and surrounding step-selection logic
**Symptom:** After the first `execute-next-task` record, `orchestrator next` returned `run-phase-review` as the next step despite tasks.md having 17+ unchecked items. The `repeat_until: all_tasks_completed` predicate was only evaluated in `record.py` (setting advisory `next_step` in state.yaml), not in `dispatch.py` (which computes next step from `workflow_plan[phase].active` minus completed entries).
**Workaround used:** manually set `tasks_path` in state.yaml + prune phantom `execute-next-task` entries from step_history before re-dispatching.
**Routing:** code bug → file as Linear ticket or high-priority backlog entry. Not this feature's scope.
**Lesson:** the 5-line fix is `dispatch.py` should call `_check_all_tasks_completed(state)` before scanning for next step, and return the current step_id if predicate is False. Workflow-improver cannot fix this via rule — it's CLI code.

### ISSUE-34: reviewer approved output shape without byte-compat check

**Severity:** important (caused post-ship re-review cycle)
**Location:** implement-phase reviewer's AC verification
**Symptom:** Reviewer approved at 9/10. Post-ship diff against legacy compute-swe-metrics.sh output revealed 2 missing top-level keys (`api_calls`, `per_tool_uses`). AC-1 said "single call returns every required field per metrics-schema.md"; reviewer verified REQUIRED-per-schema-feature fields but missed legacy-output superset.
**Workaround used:** added T-25 post-review to restore the 2 fields.
**Routing:** `workflow_improvement` → `config/steps/run-phase-review.yaml` — add rule: when a feature rewrites a script, reviewer must diff the new output vs the legacy script's output on a real archived fixture and flag any top-level key reduction as an important finding.

### ISSUE-35: autopilot driver skipped self-improvement steps to "save tokens"

**Severity:** meta (defeats the purpose of autopilot's self-improving loop)
**Symptom:** Driver initially recorded `run-learn-cycle` as `{"skipped": true, "reason": "autopilot session budget"}`. Retro was not written. User called this out; both were run manually post-ship.
**Routing:** `workflow_improvement` → `config/steps/autopilot-iterate.yaml` or the orchestrate skill — the learn cycle and retro are the autopilot's mechanism for getting better. Skipping them to save tokens is self-defeating. Rule: under `--auto`, `run-learn-cycle` is mandatory, not best-effort. Retro.md must be written before `archive-completed-change`.

### ISSUE-36: 31 redundant ScheduleWakeup calls cost ~$40 in cache reads

**Severity:** cost-only (no functional impact)
**Symptom:** Driver emitted 31 ScheduleWakeup calls ("dev still running, check in 4min") despite the `<task-notification>` system firing automatically on agent completion. Each wakeup = full context re-hydration = ~3M cache reads. 31 × 3M ≈ 93M cache reads @ $1.50/M = $140+ cache-read cost, ~$40 of which is attributable to redundant polling.
**Routing:** `workflow_improvement` or agent prompt addition. Rule: "Do not emit ScheduleWakeup for wait-only purposes when background agents will notify on completion."
**Backlog:** filed as `autopilot-wakeup-discipline` (score 5.5)

### ISSUE-37: register-repo.test.sh T-5b pre-dates FR-11 invariant

**Severity:** minor (2 assertions test the old buggy behavior that FR-11 now correctly rejects)
**Location:** `config/scripts/__tests__/register-repo.test.sh` T-5b subtest
**Symptom:** T-17 added FR-11 invariant to register-repo.sh (rejects silent-failure step_history rows). T-5b's existing assertions expected the old behavior (silent acceptance with NULL numerics + no stderr). Now fail correctly but block `register-repo.test.sh` from being fully green.
**Routing:** code change (2-line assertion update) → Linear ticket or backlog entry for post-merge followup. T-17 dev correctly declined to modify the test (outside allowed touch-set).

### ISSUE-38: discoverer missed that driver-loop mechanism already existed

**Severity:** moderate (caused scope gap that T-23/T-24 closed late)
**Symptom:** Discoverer brief and architect design both treated driver-loop token attribution as absent. In reality, `orchestrator ingest-driver` + synthetic `agent_name='driver-loop'` step_events row both existed in main. The gap was that `ingest-driver` was never auto-invoked in any workflow — required manual command.
**Routing:** `agent_improvement` → `agents/discoverer.md` — add checklist item: when feature touches an existing subsystem (cost, metrics, ingest, etc.), grep `bin/` and `config/scripts/` for ALL existing subcommands in that subsystem before declaring "mechanism X does not exist." Exhaustive CLI inventory.

## Cost anomalies

Driver-loop cost $190.11 — roughly tied with prior autopilot feature `live-telemetry-and-repeat-until-enforcement` at $192.92. Not anomalous; this is the cost shape of a ~7-hour opus-4.7 autopilot session at this complexity.

74% of cost ($141.50) is cache reads. 94M cache_read tokens × $1.50/M. Each turn re-reads ~200K cached prefix → 465 turns × 200K = 93M. Matches within 1%.

The main lever for cost reduction is **fewer total turns**, not smaller prompts. Primary sources of unnecessary turns:
- 31 ScheduleWakeup (ISSUE-36)
- Chain sizes too small early on (T-5/T-6 as 2-task chain vs T-12..T-18 as 5-task chain)
- Parallel spawns with < 4 tasks each (T-16+T-17 chain was 2 tasks; separate cache-ingest cost exceeded wall-clock savings)

## Learned rules proposed

**Routed to step contracts (pending workflow-evaluator confirmation):**

1. `run-phase-review.yaml`: for feature rewrites of existing scripts/commands, diff new vs legacy output on a real fixture. Missing top-level keys are an important finding. (Source: ISSUE-34)

2. `autopilot-iterate.yaml`: under `--auto`, run-learn-cycle is mandatory. Retro.md is written before archive-completed-change. Budget is not a valid skip reason for self-improvement steps. (Source: ISSUE-35)

3. Driver session polling rule (target TBD, likely orchestrate skill or autopilot step): do not emit ScheduleWakeup for wait-only purposes when background agents will notify on completion. Exceptions: watching external non-notifying resources, time-gated events, explicit periodic reports. (Source: ISSUE-36)

**Routed to agent prompts:**

4. `agents/discoverer.md`: when feature touches an existing subsystem, exhaustive CLI/script inventory is required before declaring "mechanism X does not exist." Grep `bin/` and `config/scripts/` for ALL subcommands in that subsystem. (Source: ISSUE-38)

**Routed to code (Linear / backlog):**

5. `dispatch.py._find_completed_step` honor `repeat_until` predicate before skipping to next step_id. (ISSUE-33)
6. `register-repo.test.sh` T-5b: update 2 assertions to match FR-11 behavior. (ISSUE-37)
7. `metrics-no-data-graceful`: already in backlog.
8. `autopilot-wakeup-discipline`: already in backlog.

## Meta-observation

The feature shipped successfully on iter 3 because iter 2's retro (captured in `.state/autopilot/archive/aborted/2026-04-20-single-source-metrics-via-step-events/retro.md`) explicitly named ISSUE-32 and the discovery brief rejected the hybrid. Without that retro, iter 3's architect would likely have made the same mistake.

**Corollary for ISSUE-35**: if this iter 3 had skipped retro.md (as initially attempted), future iterations of this repo's workflow would repeat ISSUES 33/34/36/38 because the learnings wouldn't be durable. Retros are load-bearing for autopilot's self-improvement premise.
