# Phase Review: implement — ORC-79 (collapse teardown into `complete-workflow`)

Reviewer: independent verification (Mode 2). All checks re-run; nothing trusted from the developer self-report.

| Dimension | Score | Key Issues |
|-----------|-------|------------|
| Spec Compliance | 10/10 | All 10 ACs met; one internal LLD-table row (Components, line 188) drifted — non-AC, see below |
| Algorithm / Correctness | 10/10 | merge→archive→cd→cleanup ordering correct; readiness.py guard correct and minimal |
| UX | n/a | No UI surface |
| Security | 10/10 | No injection, no secrets, no eval; `shlex.quote` on all state reads |
| Performance | 10/10 | Bounded; one extra subprocess per helper — accepted tradeoff |
| Readability | 10/10 | Script header + inline comments explain every non-obvious step |
| Simplicity | 10/10 | Reuses 3 tested helpers + 1 wrapper; no over-engineering |
| Code Quality (DRY) | 10/10 | Tested path == runtime path (e2e drives real `bin/orchestrator`) |
| Functional Completeness | 10/10 | All 10 ACs satisfied |
| **Overall** | **10/10** | min of dimensions 10; first-pass bonus applies (0 retries, all green, no critical/important findings) |

## Verification

- **Type-check / shellcheck**: `shellcheck` not installed in env — not run (project has no type-check gate for bash; `verify_commands.test` is the only declared gate).
- **Tests**: `pytest config/scripts/orchestrator_next/tests/ -q` → **525 passed, 28 warnings** (warnings pre-existing `datetime.utcnow` deprecations, unrelated). Matches developer claim (525, baseline 490, +35). Independently confirmed.
- **AC-relevant subset** (`test_complete_workflow*.py`, `test_archive_step_record_crash.py`, `test_repeat_until.py`, `test_workflow_schemas_load.py`, `test_flags_reshape.py`) → 31 passed.
- **Build**: n/a (no build step in this repo).
- **Uncommitted changes**: design/state/project.yaml edits unrelated to orc-79 present in tree (pre-existing); all orc-79 work is committed across 24 commits.

## Acceptance Criteria — independent verification

| AC | Verify method | Result | Evidence |
|----|---------------|--------|----------|
| AC-1 | grep schema tails | PASS | `feature.yaml:14` + `bugfix.yaml:11` end with `complete-workflow`; none of the three removed ids present. Symlink confirmed — one physical tree. |
| AC-2 | `ls config/steps/` | PASS | `complete-workflow.yaml` present; `merge-to-main.yaml`/`remove-worktree.yaml` absent; `archive-completed-change.yaml` retained. (AC-2's literal text says nothing about `outputs:` — that clause lives only in the Components table; see Minor Issues.) |
| AC-3 | ordering test | PASS | `test_body_state_reads_precede_archive_and_cd_precedes_remove` asserts `max(read_state_env) < archive`, `cd "$REPO_ROOT" < remove-worktree`, `archive < remove`. Re-run green. |
| AC-4 | grep `_STATE_MUTATING_INLINE_STEPS` | PASS | `bin/orchestrator:381-384` holds BOTH `archive-completed-change` and `complete-workflow` (T-6c corrective applied). |
| AC-5 | flags-load test | PASS | `flags.yaml:20-21` — `worktree`/`merge_to_main` under `behavioral:`, no `steps:` key; `--autopilot` sets `merge_to_main: true` (`flags.yaml:42`). `test_flags_reshape.py` green. |
| AC-6 | e2e test | PASS | `test_e2e_complete_workflow_full_teardown` — read test body: archive dir + moved state.yaml/tasks.md asserted, worktree-gone asserted, no `FileNotFoundError`/`Traceback`, second `next` exits 1, step_history per-id count == 1 (genuine re-dispatch guard). |
| AC-7 | idempotent test | PASS | `test_merge_false_records_skipped_archive_still_runs`, `test_worktree_flag_false_records_skipped`, `test_worktree_dir_absent_idempotent_exit_zero` — all green. |
| AC-8 | full suite | PASS | `test_archive_step_record_crash.py` (retargeted to `complete-workflow`), `test_repeat_until.py`, `test_workflow_schemas_load.py` all pass within the 525. |
| AC-9 | `grep -rn remove-worktree` over docs | PASS | No step-reference matches in `CONVENTIONS.md`, `merge-to-main.sh`, `autopilot/SKILL.md`. `CONVENTIONS.md:271` + `SKILL.md:57` describe `complete-workflow`. |
| AC-10 | unmerged-branch test | PASS | `test_e2e_merge_false_unmerged_branch_preserved` — worktree removed, branch still in `git branch --list`, exit 0. |

## Developer known_concerns — adjudicated

1. **`outputs:` dropped from the contract.** Confirmed correct. `complete-workflow` is pre-recorded as a state-mutating inline step (`bin/orchestrator:386`) — the script's stdout `completion_record` is never threaded into `step_history.evidence.outputs`, and the archive phase moves `state.yaml` before any post-script record could fire. Declaring `outputs: [completion_record]` would make `record.py._check_declared_outputs` reject the optimistic empty pre-record (exit 3). The retained precedent `archive-completed-change.yaml` declares no `outputs:` for the identical reason (verified — no `outputs:` block). The contract documents this in a 9-line comment and lists the shape under `verify:`. **Finding: internal LLD-table drift, not an AC failure.** AC-2's literal text (`complete-workflow.yaml` exists, merge/removal contracts absent, archive retained — verify via `ls config/steps/`) says nothing about `outputs:` and is fully satisfied. The `outputs: [completion_record]` clause appears only in the design.md Components table (line 188); that one row drifted. Recorded as a low-severity item below; does not block the phase and does not lower spec_compliance.

2. **readiness.py guard.** Confirmed correct and minimal. `repeat_until_redispatch` now early-returns `None` when `next_ready_node(state) is None` (`readiness.py:131-132`) — repeat semantics only fire while the workflow has forward work. Without it, after `complete-workflow` archives `tasks.md`, `_check_all_tasks_completed` fails-closed on the moved file and re-picks the completed `execute-next-task` → exit 2 (the OQ-5 alternative ORC-66 failure mode). The guard is 2 lines plus a docstring; it does not change behavior for any in-flight `repeat_until` node (a ready node still exists in that case). The e2e test exercises the path genuinely: the *second* `orchestrator next` runs against the archived `state.yaml` and asserts `returncode == 1` (not 3) plus per-step-id history counts == 1 — a regression in the guard would flip the second `next` to exit 2/3 or double-count `execute-next-task`. Test would catch it.

## Code Review — full diff (22 files, +1457/-75)

Strengths:
- The hazard class is **structurally dissolved**, not patched — three dispatch nodes collapse to one; no `orchestrator next` boundary can sit between a state-moving and state-reading op. This is the correct fix per root-cause discipline.
- `complete-workflow.sh` reads all `state.yaml` values into bash vars in step 0 before any mutation; `cd "$REPO_ROOT"` precedes both the worktree-remove and any post-archive subprocess (the comment at lines 99-105 correctly notes archive may `rm -rf` the original CWD).
- Helpers invoked as `bash <script>` subprocesses, not `source`d — correct: helpers `exit` on early-return paths and would kill a sourcing parent. `remove-worktree.sh` is explicitly handed captured vars (`STATE_YAML_PATH=""`, `WORKTREE_PATH`, `BRANCH`, `REPO_ROOT`) since `state.yaml` is gone by step 3.
- `merge-to-main.sh` / `archive-completed-change.sh` inherit `STATE_YAML_PATH`/`REPO_ROOT`/`WORKTREE_ROOT` from the wrapper's environment (set by `_inline_script_env`); verified the env chain holds via the passing e2e test against the real CLI.
- `_extract_record` scans for the last JSON-object line — robust against helper git/commit stdout noise; always emits a valid JSON object.
- Error handling: merge failure halts before archive (worktree preserved); archive `cp` failure halts before `rm -rf` (source dir intact); both surface non-zero exit. Matches design's Error Handling table.
- TDD discipline followed (RED test commits precede GREEN impl commits throughout).
- `_STATE_MUTATING_INLINE_STEPS` correctly holds both ids after the T-6c corrective; the inline comment explains why spike retains `archive-completed-change`.

Observations (non-blocking):
- `test-remove-worktree-safe-branch-delete.sh` received a comment-only edit (commit `6e32640`) updating a stale reference to the deleted `remove-worktree.yaml`. Not listed in tasks.md but is a legitimate doc-consistency fix in AC-9's spirit (a comment pointing at a now-deleted contract). Acceptable.
- `complete-workflow.sh` accepts both `"true"` and `"True"` for the flag strings — defensive against YAML bool serialization variance. Reasonable, not over-engineering.

## Critical Issues (must fix before advancing)

None.

## Important Issues (should fix)

None.

## Minor Issues (nice to have — non-blocking, no tasks appended)

- **design.md Components table (line 188) is stale.** The Components table row for `complete-workflow.yaml` states it declares `outputs: [completion_record]`. The implemented contract correctly omits `outputs:` (a pre-recorded state-mutating step cannot satisfy `_check_declared_outputs`). AC-2's *literal text* does not mention `outputs:` and is satisfied — only this LLD-table row drifted. Recommend an architect truth-up: drop the `outputs: [completion_record]` clause from the Components table row and reference the `verify:` block as the documented shape — matching the `archive-completed-change.yaml` precedent. Documentation-only; does not block the phase. The contract's own 9-line comment already records the rationale.
- design.md Data Flow step 9 ("second `orchestrator next` exits 1") is correct as written but silently depends on the `readiness.py:repeat_until_redispatch` guard. A one-line note in Data Flow or State Management pointing at that guard would aid future readers. Optional.

## Verdict: PASS (overall 10/10 ≥ min_phase_review_score 9; no critical findings)

First-pass bonus applied: `run-phase-review` is `attempt: 1`, no top-level `retries:` key exists in `state.yaml` (retries = 0), the full suite is green (525/525), and there are no critical or important findings — all conditions for the `+1` met. The two minor items are documentation truth-ups for an architect, not developer rework, so no `tasks.md` items are appended (per the standard: no tasks for non-blocking suggestions).
