# Learn evaluation — single-source-metrics-via-step-events (cycle 12)

Feature: `single-source-metrics-via-step-events`
Archive: `/Users/spidey/code/orchestrator/spec/changes/archive/2026-04-19-single-source-metrics-via-step-events/`
state.yaml step_history entries: 31
Decay evaluation (§5b-decay): **skipped** (K=12 is not a multiple of 5).

---

## Part 1: Compliance

- **Verdict:** PASS_WITH_ISSUES

Walk of step_history (31 entries):

| Phase | Step | Attempts | Status | Notes |
|-------|------|----------|--------|-------|
| specify | workflow-init | 1 | completed | Agent step; usage populated. |
| specify | explore | 1 | completed | discoverer; `ingest-driver` CLI not inventoried — see Part 3. |
| specify | design-and-draft-artifacts | 1 | completed | architect; 20 tasks emitted (later grew to 26). |
| specify | preview-route | 1 | completed | inline. |
| specify | run-phase-review | 1 | completed | score 7 (approved_with_changes) — T-11 TDD split. |
| implement | capture-test-baseline | 1 | completed | 132/134 passing (2 pre-existing fail documented). |
| implement | execute-next-task | 1..18 | completed (all) | T-5..T-21; attempt counter is sequence, not per-task retry. |
| implement | run-phase-review | 1 | completed | score 7; FINDING-1 → T-21 fix task. |
| implement | run-phase-review | 2 | completed | score 9; approved. |
| complete | compute-prediction-accuracy | 1 | completed | inline. |
| complete | run-learn-cycle | 1 | **completed-with-skip** | `skipped: true, reason: "autopilot session budget"` — this is a permanent learned rule violation (see Part 4). |
| complete | mark-change-completed | 1 | completed | inline. |
| complete | compute-swe-metrics | 1 | completed | inline. |
| implement | run-phase-review | 3 | completed | score 9; post-ship scope-delta re-review of T-23/T-24. |

**Violations:**
1. `run-learn-cycle` was recorded `status: completed` but its output is `{skipped: true, reason: "autopilot session budget"}`. The step has an explicit permanent learned rule (`run-learn-cycle.yaml:15`): *"Never skip compute-prediction-accuracy or run-learn-cycle steps during autopilot — these feed the self-improving loop and must run even when --auto is set."* The skip directly violates this. (Driver recognized this as a meta-learning after user pushback — this very re-run is the recovery.)
2. `run-phase-review` ran 3× on implement. Attempts 1 and 2 are a legitimate fail→fix→pass. Attempt 3 (2026-04-20T06:56:05Z) is a post-archive re-review of scope-additions (T-23/T-24) — it violates the append-only phase ordering (runs after `complete` phase steps). Contract silence: the phase review contract has no guidance for post-signoff scope deltas.
3. Missing state update: the phantom `execute-next-task` entries (per issue #2 in context) had to be manually pruned. No trace in the final state.yaml, but this is a known workflow-infrastructure bug (ISSUE-33).

---

## Part 2: Step analysis

### Skipped steps

| Step | Reason given | Justified per contract? |
|------|--------------|-------------------------|
| `ux-design` | flag `ux_design=false` (auto-disabled, no frontend tech) | Yes — schema-gated. |
| `run-ux-critique` | flag `ux_design=false` | Yes — schema-gated. |
| `run-learn-cycle` (de-facto) | "autopilot session budget" | **No** — explicit permanent rule forbids this skip. Budget is not a sanctioned skip reason. |

### Retried steps

| Step | Attempt count | Reasons | Systemic? |
|------|---------------|---------|-----------|
| `run-phase-review` (implement) | 3 | att1: T-11 bundles RED+GREEN → split into T-11a/b (mechanical); att2: `compute_retries()` never populates 4 resolution fields → T-21 (implementation gap); att3: scope-delta re-review for T-23/T-24 (outside contract) | Partially — att2 is exactly the `reviewer-retry-cost-is-atomic-vs-design` pattern we already codified (2026-04-18). att3 is novel: a post-ship scope delta path. |

### Mistakes / insights

Mistakes (with evidence):
- **M1**: discoverer missed existing `orchestrator ingest-driver` CLI → T-23/T-24 added mid-flight (file: state.yaml step `explore`, evidence outputs list only discovery.md — no CLI inventory).
- **M2**: reviewer approved 9/10 at implement att2 but a byte-compat gap was discovered only during post-ship validation (T-25). Reviewer's byte-compat check was key-presence, not value/shape (review-implement-retry.md score dims all 9/9/9/9/9; no byte-compat finding).
- **M3**: dispatch bug forced manual step_history surgery before the loop would route past the first `execute-next-task` (per context issue #1 + #2). Primary source: `config/scripts/orchestrator_next/dispatch.py:140-147` — `_find_completed_step` returns true on *any* completed entry and does not consult the step's `repeat_until` predicate.
- **M4**: 31 ScheduleWakeup calls during autopilot → ~30% of driver-loop cost redundant polling (context #6). Already filed as `autopilot-wakeup-discipline` in backlog.
- **M5**: `run-learn-cycle` skip with "budget" reason (context #7).

Insights (candidate rules):
- **I1**: When a feature touches an area with multiple CLI surfaces (e.g. `orchestrator *`, scripts, skills), discoverer must inventory the *full set of callable entrypoints* before handing off, not just the files it opens.
- **I2**: When a spec says "thin projection" / "byte-compatible rewrite", implement-phase review must include a shape/value parity test for at least one real payload, not just schema-key presence.
- **I3**: "Autopilot session budget" is not a valid skip reason for any feedback-loop step. Contract should surface a structured enum of sanctioned skip reasons.

### Outliers

- Step timing suspicious: many `execute-next-task` entries have identical `started_at` and `ended_at` (e.g. T-12 both 2026-04-19T22:09:52Z) — durations not actually captured. Part of the broader `inline-steps-are-tokenless` pattern; not a new finding.

---

## Part 2b: Cross-feature retry patterns

`metrics-query.sh retry-hotspots --fleet --limit 10` returned empty/no output (DuckDB query has no data for fleet hotspots in this shape). Fallback: scan of `spec/changes/archive/*/state.yaml` sorted by `completed_at`:

Last 5 features by completion:
| Feature | Final score | "Retry rate" (attempt>1 / total) | Notes |
|---------|-------------|----------------------------------|-------|
| single-source-metrics-via-step-events | 9 | 0.61 (19/31) | attempt counter conflated with retry; real retries: 2 (run-phase-review att 2 + 3) |
| fix-inline-scripts-tmpdir | — | 0.10 | 1 retry |
| live-telemetry-and-repeat-until-enforcement | — | 0.08 | 1 retry |
| fix-workflow-issues-2026-04-19 | — | 0.28 | 5 retries |
| split-cost-report-package | — | 0.00 | 0 retries |

**Pattern detection** — steps with real re-runs (same step_id + attempt>1):
- `run-phase-review` implement: this feature (2 real retries), across the archive this repeats every 2-3 features. Already tagged as `reviewer-retry-cost-is-atomic-vs-design` (2026-04-18).
- `execute-next-task` retry signal is **noisy** here — `attempt` is a sequential counter, not a retry counter. This is itself a workflow observability bug worth surfacing: retry hotspot analysis cannot distinguish repeat-until iterations from true retries. (Not raised in this cycle because feature_count with the pathology is 1 visible so far.)

No new systemic pattern crosses the `feature_count >= 3 AND retry_rate > 30%` threshold after discounting the attempt-counter conflation.

---

## Part 3: Patterns

### P1 — Reviewer gap: byte-compat/shape-equality missing from implement review

Evidence: `config/steps/run-phase-review.yaml` has no rule requiring value/shape parity for "rewrite" or "projection" tasks. Implement-phase review attempt 2 scored 9/9 across all dimensions; T-25 byte-compat gap surfaced only post-ship during user validation. Rule 5c (AC verification with evidence) inspects ACs but has no "parity check for rewrites" primitive.

### P2 — "Budget" as skip reason for feedback-loop steps

Evidence: `run-learn-cycle.yaml:15` forbids this skip. The step_history entry at line 658-671 of state.yaml skipped it anyway with `reason: autopilot session budget`. Contract permits skipping (fail-soft) but does not enumerate sanctioned reasons. Meta-learning per context #7.

### P3 — Dispatch `_find_completed_step` ignores `repeat_until`

Evidence: `config/scripts/orchestrator_next/dispatch.py:140-147`. Any single completed entry for a step_id+phase pair ends dispatch of that step. For `execute-next-task` with `repeat_until: all_tasks_completed`, this forces manual phantom-entry pruning. This is code, not workflow rule — route to Linear/backlog.

### P4 — Discoverer CLI-inventory gap

Evidence: state.yaml `explore` step evidence.outputs has only `discovery.md`, not an enumeration of `orchestrator` subcommands or shell scripts in-area. The `ingest-driver` CLI existed before this feature but was invisible to the discoverer → mid-flight task additions T-23/T-24.

### P5 — Post-ship scope-delta re-review has no contract path

Evidence: state.yaml line 708-734 — a `run-phase-review` entry with phase=implement, attempt=3, started 8 hours after `mark-change-completed`. This is an out-of-order phase step. Contract silence (not a violation per se, but an undefined path).

### P6 — Autopilot redundant wakeup polling

Already filed as `autopilot-wakeup-discipline` in backlog. Include here for completeness — no new routing.

---

## Part 4: Routing plan

| # | Finding | Bucket | Target file | Proposed change |
|---|---------|--------|-------------|-----------------|
| F1 | P1 — no shape/byte parity check for rewrite tasks | workflow_improvement | `config/steps/run-phase-review.yaml` (rules:) | Add learned rule: *"For tasks that spec describes as a rewrite, projection, or byte-compatible replacement of an existing producer, AC verification MUST include a value/shape parity check against at least one real payload from the prior implementation — key-presence alone is insufficient."* Tag: `learned: 2026-04-20, source: single-source-metrics-via-step-events, cycle: 12, hits: 0, misses: 0, repo: orchestrator`. |
| F2 | P2 — "budget" not a valid skip reason | agent_improvement | `agents/workflow-improver.md` AND/OR `config/steps/run-learn-cycle.yaml` (rules:) | Tighten existing learned rule — append: *"A `skipped: true` outcome is only valid when the enumerated gating flag is set (e.g. ux_design=false for ux steps). Session token budget, time pressure, or 'capture via retro' are NEVER valid skip reasons for feedback-loop steps."* Repo-scope the existing rule (currently universal) as `repo: *` stays, but add the enum clarification. |
| F3 | P4 — discoverer missed `ingest-driver` CLI | agent_improvement | `agents/discoverer.md` | Add rule: *"When discovery touches an area with CLI surfaces (any of: `orchestrator` subcommands, `bin/*`, `scripts/inline/*`, or skill entrypoints), enumerate every callable in that area in the Constraints section — not just the files read. Missed entrypoints cause mid-implementation task additions."* |
| F4 | I1 promoted — general "inventory before design" | project_learning | `spec/project.yaml` learnings[] | Add entry `id: discoverer-cli-surface-inventory, learned: 2026-04-20, source: single-source-metrics-via-step-events, rule: "For features that touch a CLI/script surface area (orchestrator subcommands, scripts/inline, bin/), the discovery phase must enumerate existing callables before design begins; undiscovered entrypoints become fix tasks post-review."` |
| F5 | P5 — no post-ship scope-delta path | workflow_improvement | (defer — single occurrence) | **Do NOT write rule yet.** Flag for re-visit at cycle 15 if it recurs. Single-instance pattern. |
| F6 | M3 / ISSUE-33 — dispatch bug | code bug | Backlog entry (Linear unavailable / feature flag linear=false) | File `spec/changes/backlog/dispatch-repeat-until-honor.md` with: repro, fix direction (teach `_find_completed_step` to consult step's `repeat_until` predicate and treat step as completed only when predicate holds), test: `test_dispatch.py` case with `execute-next-task` + `repeat_until: all_tasks_completed` and one completed entry + unchecked tasks in tasks.md. Severity: high (affects every repeat_until step). |
| F7 | register-repo.test.sh T-5b — 2 assertions test pre-FR-11 behavior | code bug | Backlog entry | File `spec/changes/backlog/register-repo-test-t5b-post-fr11-cleanup.md`: 2 assertions in `config/tests/test-register-repo.test.sh` break because they encode pre-FR-11 behavior. Trivial delete/update. |
| F8 | M4 / 31 ScheduleWakeup — already filed | — | (already backlog: `autopilot-wakeup-discipline`) | No action. |
| F9 | M5 — meta-learning about budget-skipping | project_learning | `spec/project.yaml` learnings[] | Add entry `id: no-budget-skip-feedback-loops, learned: 2026-04-20, rule: "Autopilot must never skip compute-prediction-accuracy, run-learn-cycle, or retro steps for 'session budget' reasons. These feed the self-improving loop; skipping them defeats autopilot's purpose. Budget pressure is a signal to stop earlier, not to skip learning."` |

Agent improvements proposed: 2 (F2, F3)
Workflow improvements proposed: 1 (F1)
Project learnings proposed: 2 (F4, F9)
Code bugs to file: 2 (F6 ISSUE-33, F7 T-5b)

---

## Part 5: Rule effectiveness updates

Build `step_retries[step_id]` from state.yaml step_history attempts > 1 (real retries, not sequence counters):
- `run-phase-review` (implement): 2 real retries (att 2 fix, att 3 delta)
- `run-phase-review` (specify): 0 real retries (1 attempt, approved_with_changes but no re-run — that T-11 split was absorbed forward)
- All other steps in this feature: 0 retries

Walk `config/steps/*.yaml` for `<!-- learned:` comments and evaluate hit/miss for steps actually in this feature's step_history:

| Rule file | Rule excerpt (truncated) | Step in history? | Retries on that step? | Delta |
|-----------|--------------------------|------------------|-----------------------|-------|
| `run-phase-review.yaml` | "When a finding requires a new requirement, the fix MUST update spec.md (FR + AC), design.md, and tasks.md atomically…" (learned 2026-04-17, cycle 11, hits:1 misses:3, repo:orchestrator) | Yes (specify x1, implement x3) | Implement had retries (2) | **misses + 1** → hits:1, misses:4 |
| `execute-next-task.yaml` | "When removing or renaming a step/artifact from a workflow schema, grep the entire config directory…" (learned 2026-04-11, cycle 14, hits:6 misses:0, repo:orchestrator) | Yes (18 sequence attempts, 0 real retries on a single task's re-execution) | No | **hits + 1** → hits:7, misses:0 |
| `execute-next-task.yaml` | "When removing a block from a prose file …update forward references atomically" (learned 2026-04-17, cycle 12, hits:3 misses:0, repo:orchestrator) | Yes | No real retry | **hits + 1** → hits:4, misses:0 |
| `execute-next-task.yaml` | "In TypeScript projects, never use the `Function` type…" (repo:valet) | Repo mismatch (this is `orchestrator`) | — | **skip** (not applicable per `repo:` filter) |
| `execute-next-task.yaml` | "When a security hook rejects test code…" (repo:algotrade) | Repo mismatch | — | **skip** |
| `execute-next-task.yaml` | "When adding fields to a model dataclass…" (repo:algotrade) | Repo mismatch | — | **skip** |
| `design-and-draft-artifacts.yaml` | "When spec or design introduces a new archive/state path …grep existing consumer globs" (learned 2026-04-16, hits:5 misses:0, repo:orchestrator) | Yes (1 attempt, 0 retries) | No | **hits + 1** → hits:6, misses:0 |
| `design-and-draft-artifacts.yaml` | "SQL sketches in design.md …must be validated against a live row…" (learned 2026-04-17, cycle 12, hits:3 misses:0, repo:orchestrator) | Yes (feature uses DuckDB + adds `feature_metrics` DDL) | No real retries on design step | **hits + 1** → hits:4, misses:0 |
| `run-learn-cycle.yaml` | "Never skip compute-prediction-accuracy or run-learn-cycle steps during autopilot…" (learned 2026-04-05, cycle 6, hits:3 misses:0, repo:orchestrator) | Yes (step executed; skip was reverted by this very re-run) | The original run skipped (violated rule); this re-run honors it. Net: rule *was* violated for this feature. | **misses + 1** → hits:3, misses:1 |
| `autopilot-iterate.yaml` | "The /develop invocation MUST execute the full workflow including the complete phase…" (learned 2026-04-11, cycle 14, hits:0 misses:0, repo:orchestrator) | Step not in this feature's step_history (applies to autopilot wrapper, not a single feature) | — | **skip** |

Total updates: 5 hits+1, 2 misses+1, 4 skips.

---

## Part 5c: Adaptive quality bar

`config/scripts/metrics-query.sh quality-trend --limit 5` returned rows but all `quality_score` values are `NULL` — the DuckDB view does not yet have review_score wired through for the 5 newest features (this is a known data-plumbing gap; it's essentially what THIS feature was trying to fix).

Fallback: scan of last 5 archives by `completed_at`:
- `single-source-metrics-via-step-events`: final_score = **9**, real retries = 2
- `fix-inline-scripts-tmpdir`: score not recorded in state.yaml (None)
- `live-telemetry-and-repeat-until-enforcement`: score not recorded
- `fix-workflow-issues-2026-04-19`: score not recorded
- `split-cost-report-package`: score not recorded

Reliable sample size: **1** (only this feature has a recorded final review score in state.yaml).

- **Current `green_base`**: 9
- **avg_review_score**: 9.0 (n=1 — insufficient)
- **avg_retry_rate**: 0.215 across 5 features, but heavily skewed by the attempt-counter conflation
- **Decision: STABLE** — not enough signal to tighten or loosen. Defer until at least 3 features in the window carry a recorded final review score.
- **Linear ticket needed**: no (Linear flag disabled on this repo; use backlog). Do file a backlog entry for the review-score capture gap — the quality-trend query can't function without it.

---

## Summary

- Agent improvements proposed: **2** (workflow-improver/run-learn-cycle skip enum, discoverer CLI-inventory)
- Workflow improvements proposed: **1** (run-phase-review shape/byte parity rule for rewrites)
- Project learnings proposed: **2** (discoverer-cli-surface-inventory, no-budget-skip-feedback-loops)
- Code bugs to file: **2** (ISSUE-33 dispatch `_find_completed_step` ignores `repeat_until`; register-repo.test.sh T-5b pre-FR-11 assertions)
- Rule hit/miss updates: **7** (5 hits+1, 2 misses+1) + 4 skips (repo-mismatch or step-not-in-history)
- Quality bar decision: **stable at 9** (insufficient signal; defer)
- Decay evaluation: **skipped** (K=12 not multiple of 5)
