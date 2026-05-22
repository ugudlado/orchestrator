# Tasks — Collapse workflow teardown into one terminal `complete-workflow` step

## Group 1 — `complete-workflow` step, script, and CLI classification

- [x] T-1: Write tests for `complete-workflow.sh` sequencing and gating (RED — tests must fail)
  Why: AC-3, AC-6, AC-7, AC-10 — proves merge→archive→cd→cleanup ordering, flag gating, idempotent teardown, and unmerged-branch preservation
  Files: config/scripts/orchestrator_next/tests/test_complete_workflow.py (new)
  Change: new pytest module; build a temp git repo + worktree + state.yaml fixture; assert (a) `complete-workflow.sh` body has every `read_state_env`/state-read before the archive invocation and a `cd "$REPO_ROOT"` before any `remove-worktree.sh` invocation; (b) running the script with `merge_to_main=true,worktree=true` produces archive dir, removes worktree, emits `completion_record` JSON; (c) with `merge_to_main=false` + absent worktree dir, merge/cleanup record `skipped` and exit code is 0; (d) with `worktree=true` and an unmerged feature branch, worktree is removed but the branch is preserved
  Test scenarios:
    - all state reads precede the archive call; `cd "$REPO_ROOT"` precedes worktree removal
    - merge_to_main=true,worktree=true → merge ran, archive dir exists, worktree gone, exit 0
    - merge_to_main=false → merge phase records skipped, archive still runs
    - worktree flag false OR worktree dir already absent → cleanup records skipped, exit 0
    - merge conflict → wrapper exits non-zero, archive + cleanup do not run
    - unmerged branch → worktree removed, branch preserved, warning logged, exit 0

- [x] T-2: Implement `complete-workflow.sh` (GREEN — make tests pass)
  Why: Approach 3, AC-3, AC-7 — the orchestration script sequencing merge → archive → cd → cleanup
  Files: config/scripts/inline/complete-workflow.sh (new), ~/.config/orchestrator/config/scripts/inline/complete-workflow.sh (new — dual tree)
  Change: new bash script with `set -uo pipefail`; step 0 sources `_read_state_env.sh` and reads CHANGE_ID, ARCHIVE_PATH, WORKTREE_ROOT, WORKTREE_PATH, REPO_ROOT, BRANCH, MERGE_TO_MAIN, WORKTREE into bash vars; step 1 runs `bash "$(dirname "$0")/merge-to-main.sh"` when MERGE_TO_MAIN true (capture merge_record, exit non-zero on helper failure); step 2 runs `bash "$(dirname "$0")/archive-completed-change.sh"` unconditionally (capture archive_record); step 3 `cd "$REPO_ROOT"` then runs `bash "$(dirname "$0")/remove-worktree.sh"` when WORKTREE true (capture worktree_record); emit `{"completion_record": {merge_record, archive_record, worktree_record}}` on stdout. Write identical file to both config trees.
  Test scenarios:
    - all T-1 tests pass
    - script is executable bash, no LLM-tool references
  depends: T-1

- [x] T-3: Write test for `_read_state_env.sh` flag resolvers (RED — tests must fail)
  Why: AC-5 — `complete-workflow.sh` must read `flags.merge_to_main`/`flags.worktree` from state.yaml
  Files: config/scripts/orchestrator_next/tests/test_read_state_env_flags.py (new)
  Change: new pytest module; write a state.yaml fixture with a `flags:` map; source `_read_state_env.sh` and call `read_state_env` for `MERGE_TO_MAIN` and `WORKTREE`; assert the bash vars resolve to the flag values, and an unknown var still exits non-zero
  Test scenarios:
    - MERGE_TO_MAIN resolves to flags.merge_to_main value (true/false)
    - WORKTREE resolves to flags.worktree value
    - absent flags map → resolvers yield empty string, no crash
    - unknown var name still exits non-zero (allowlist intact)

- [x] T-4: Add `MERGE_TO_MAIN` / `WORKTREE` resolvers to `_read_state_env.sh` (GREEN — make tests pass)
  Why: AC-5, Decisions — keep all state.yaml reads in the one audited allowlist
  Files: config/scripts/inline/_read_state_env.sh, ~/.config/orchestrator/config/scripts/inline/_read_state_env.sh (dual tree)
  Change: in the embedded Python `RESOLVERS` map (lines 26-33), add `"MERGE_TO_MAIN": lambda r: r.get("flags", {}).get("merge_to_main", "")` and `"WORKTREE": lambda r: r.get("flags", {}).get("worktree", "")`. Apply to both config trees.
  Test scenarios:
    - all T-3 tests pass
    - existing resolver tests still pass (CHANGE_ID, BRANCH, etc.)
  depends: T-3

- [x] T-5: Write test for `complete-workflow` state-mutating classification (RED — tests must fail)
  Why: AC-4 — without classification `record.py` crashes on the post-archive path
  Files: config/scripts/orchestrator_next/tests/test_complete_workflow_classification.py (new)
  Change: new pytest module asserting `bin/orchestrator`'s `_STATE_MUTATING_INLINE_STEPS` set contains `complete-workflow` and does NOT contain `archive-completed-change`
  Test scenarios:
    - `_STATE_MUTATING_INLINE_STEPS` contains `complete-workflow`
    - `_STATE_MUTATING_INLINE_STEPS` does NOT contain `archive-completed-change`

- [x] T-6: Replace `_STATE_MUTATING_INLINE_STEPS` contents with `complete-workflow` (GREEN — make tests pass)
  Why: AC-4, Constraints — `complete-workflow` becomes the sole state-mutating inline step; pre-record it before its script runs (ORC-66 crash-avoidance contract). `archive-completed-change` is removed from the set because its step id no longer dispatches in any schema (T-13 deletes its contract).
  Files: bin/orchestrator
  Change: at bin/orchestrator:372, change `_STATE_MUTATING_INLINE_STEPS = {"archive-completed-change"}` to `{"complete-workflow"}` (replace, not add)
  Test scenarios:
    - all T-5 tests pass
  depends: T-5

- [x] T-6b: Retarget `test_archive_step_record_crash.py` to `complete-workflow` (no RED — test retarget; keeps the ORC-66 crash guard alive)
  Why: AC-4 — the existing crash-regression test keys its fixture on the literal step id `archive-completed-change`; once that id leaves `_STATE_MUTATING_INLINE_STEPS` (T-6) the test would RED for a stale reason. The crash class (record-after-state-deletion) is now embodied by `complete-workflow` — retarget the guard, do not delete it.
  Files: config/scripts/orchestrator_next/tests/test_archive_step_record_crash.py
  Change: in `_write_archive_step_state` and `test_state_deleting_inline_step_is_recorded_before_the_move`, replace the fixture step id `archive-completed-change` with `complete-workflow` (contract filename, `id:`, `workflow_plan.main.nodes[].id`, and the `step_ids` assertion); update the module docstring to name `complete-workflow` as the sole state-mutating inline step.
  Test scenarios:
    - `next` on a state-deleting `complete-workflow` inline step does not crash (exit 0, no FileNotFoundError/Traceback)
    - the `complete-workflow` step_history entry is recorded into state.yaml before the script deletes the dir
  depends: T-6

- [x] T-7: Write test for `complete-workflow.yaml` step contract (RED — tests must fail)
  Why: AC-2 — the new step needs a dispatchable contract in both trees
  Files: config/scripts/orchestrator_next/tests/test_complete_workflow_contract.py (new)
  Change: new pytest module asserting `complete-workflow.yaml` exists in both `config/steps/` and `$ORCHESTRATOR_HOME/config/steps/`, declares `run: scripts/inline/complete-workflow.sh`, and `outputs: [completion_record]`
  Test scenarios:
    - complete-workflow.yaml present in both trees
    - contract has `run:` pointing at complete-workflow.sh and `outputs: [completion_record]`

- [ ] T-8: Create `complete-workflow.yaml` step contract (GREEN — make tests pass)
  Why: AC-2, Components — dispatch must resolve the new step
  Files: config/steps/complete-workflow.yaml (new), ~/.config/orchestrator/config/steps/complete-workflow.yaml (new — dual tree)
  Change: new contract `id: complete-workflow`, `version: 1`, `run: scripts/inline/complete-workflow.sh`, `outputs: [completion_record]`, plus rules describing the merge→archive→cd→cleanup ordering and flag gating; no LLM-tool references. Write to both trees.
  Test scenarios:
    - all T-7 tests pass
  depends: T-7

- [ ] T-9: Review checkpoint — Group 1 (phase gate)
  Why: phase gate — confirm the new step, script, resolvers, and CLI classification integrate before schema cleanup
  Test scenarios:
    - type-check / shellcheck clean on new scripts
    - full test suite green (`pytest config/scripts/orchestrator_next/tests/ -q`)
    - `orchestrator next` on a fixture state at `complete-workflow` dispatches the inline action
  depends: T-2, T-4, T-6, T-6b, T-8

## Group 2 — Schema, contract, flags, and docs cleanup

- [ ] T-10: Write/update schema-load test for the new step list (RED — tests must fail)
  Why: AC-1, AC-8 — schemas must end with `complete-workflow` and drop the three old steps
  Files: config/scripts/orchestrator_next/tests/test_workflow_schemas_load.py
  Change: update assertions so `feature.yaml` and `bugfix.yaml` (both trees) `steps:` lists end with `complete-workflow` and contain none of `archive-completed-change`, `merge-to-main`, `remove-worktree`; assert spike/bootstrap unchanged. Tests fail until T-11 lands.
  Test scenarios:
    - feature.yaml / bugfix.yaml steps end with `complete-workflow`
    - the three removed step ids absent from feature.yaml / bugfix.yaml
    - spike.yaml / bootstrap.yaml step lists unchanged
  depends: T-9

- [ ] T-11: Replace the three tail steps with `complete-workflow` in schemas (GREEN — make tests pass)
  Why: AC-1 — the workflow must dispatch the single terminal step (no RED — mechanical change; T-10 is the regression guard)
  Files: config/workflows/feature.yaml, config/workflows/bugfix.yaml, ~/.config/orchestrator/config/workflows/feature.yaml, ~/.config/orchestrator/config/workflows/bugfix.yaml (dual tree)
  Change: in each file, replace the trailing `- archive-completed-change` / `- merge-to-main` / `- remove-worktree` lines with a single `- complete-workflow`. Apply to all four files.
  Test scenarios:
    - all T-10 tests pass
  depends: T-10

- [ ] T-12: Update step-contract presence test for removals (RED — tests must fail)
  Why: AC-2 — merge/removal contracts deleted, archive contract deleted (step id gone)
  Files: config/scripts/orchestrator_next/tests/test_complete_workflow_contract.py
  Change: extend the T-7 test module to also assert `merge-to-main.yaml`, `remove-worktree.yaml`, and `archive-completed-change.yaml` are absent in both `config/steps/` trees. Fails until T-13 lands.
  Test scenarios:
    - merge-to-main.yaml / remove-worktree.yaml / archive-completed-change.yaml absent in both trees
    - complete-workflow.yaml still present (from T-8)
  depends: T-11

- [ ] T-13: Delete the three obsolete step contracts (GREEN — make tests pass)
  Why: AC-2 — those step ids no longer exist in any schema (no RED — mechanical change; T-12 is the regression guard)
  Files: config/steps/merge-to-main.yaml, config/steps/remove-worktree.yaml, config/steps/archive-completed-change.yaml, and the three `~/.config/orchestrator/config/steps/` copies (dual tree)
  Change: `git rm` (repo tree) / delete (HOME tree) all six files. The `.sh` helper scripts (`merge-to-main.sh`, `remove-worktree.sh`, `archive-completed-change.sh`) are NOT deleted — they remain, invoked by `complete-workflow.sh`.
  Test scenarios:
    - all T-12 tests pass
    - the three `.sh` helper scripts still exist in both trees
  depends: T-12

- [ ] T-14: Write flags.yaml reshape test (RED — tests must fail)
  Why: AC-5 — `merge_to_main`/`worktree` must move to `behavioral:` with no step binding
  Files: config/scripts/orchestrator_next/tests/test_flags_reshape.py (new)
  Change: new pytest module asserting in both `flags.yaml` trees that `merge_to_main` and `worktree` are under `behavioral:`, absent from `gates:`, carry no `steps:` key; and that loading flags via the seed-state merge still yields `merge_to_main`/`worktree` keys with their defaults, and `--autopilot` `sets` still resolves `merge_to_main: true`
  Test scenarios:
    - `worktree` and `merge_to_main` under `behavioral:`, not `gates:`
    - neither carries a `steps:` key
    - flag-default merge still produces both keys; `--autopilot` resolves merge_to_main=true
  depends: T-9

- [ ] T-15: Move `worktree`/`merge_to_main` to `behavioral:` in flags.yaml (GREEN — make tests pass)
  Why: AC-5 — with no `steps:` binding they are behavioral flags, not step-filtering gates (no RED — mechanical change; T-14 is the regression guard)
  Files: config/flags.yaml, ~/.config/orchestrator/config/flags.yaml (dual tree)
  Change: remove the `worktree:` and `merge_to_main:` entries (and the stale comment at lines 11-13 referencing `remove-worktree`) from the `gates:` section; add `worktree: { default: true, description: "Run in a git worktree; gates complete-workflow cleanup phase" }` and `merge_to_main: { default: false, description: "Merge feature branch to default in complete-workflow" }` under `behavioral:`. Apply to both trees.
  Test scenarios:
    - all T-14 tests pass
  depends: T-14

- [ ] T-16: Update docs referencing the removed steps (no RED — mechanical change)
  Why: AC-9 — CONVENTIONS.md, merge-to-main.sh, and autopilot SKILL.md must reflect the new step
  Files: config/steps/CONVENTIONS.md, config/scripts/inline/merge-to-main.sh, skills/autopilot/SKILL.md, and the `~/.config/orchestrator/config/` copies of CONVENTIONS.md and merge-to-main.sh (dual tree)
  Change: in CONVENTIONS.md lines 270-273 rewrite the lifecycle invariant to describe the single `complete-workflow` step (merge→archive→cd→cleanup ordering is internal); in `merge-to-main.sh` remove the "must run before remove-worktree" comment line; in `skills/autopilot/SKILL.md:57` replace the "runs `merge-to-main` then `remove-worktree`" sentence with a description of the `complete-workflow` terminal step.
  Test scenarios:
    - `grep -rn remove-worktree` over these files yields no step-reference matches
    - CONVENTIONS.md lifecycle text names `complete-workflow`
  depends: T-13, T-15

- [ ] T-17: Write the end-to-end completion regression test (RED — tests must fail)
  Why: AC-6, AC-10 — proves the full path: no re-dispatch, no FileNotFoundError/exit-3 (per OQ-5 the real failure mode), and unmerged-branch preservation
  Files: config/scripts/orchestrator_next/tests/test_complete_workflow_e2e.py (new)
  Change: new pytest module; build a temp git repo + worktree + a state.yaml whose `workflow_plan.main.nodes` has all nodes `completed` except a final `complete-workflow` node, with `flags.worktree=true, flags.merge_to_main=true`; run `orchestrator next` (dispatches + runs `complete-workflow.sh`), then run `orchestrator next` again; assert (i) archive dir exists with moved state.yaml/tasks.md, (ii) worktree dir gone, (iii) no FileNotFoundError raised and second `next` exits 1 (not 3), (iv) no already-completed step id appears re-dispatched in step_history. Add a second case with `flags.merge_to_main=false` and an unmerged feature branch: worktree removed, branch still present after teardown, exit 0.
  Test scenarios:
    - first `orchestrator next` dispatches and completes `complete-workflow`
    - archive dir created; worktree removed
    - second `orchestrator next` exits 1 (complete), not 3 — no FileNotFoundError
    - no completed step id is re-dispatched
    - merge_to_main=false + unmerged branch → worktree removed, branch preserved, exit 0
  depends: T-11

- [ ] T-18: Verify the e2e regression test passes after the implementation (GREEN)
  Why: AC-6, AC-8 — the e2e test must pass once schema, script, and CLI changes are all in
  Files: (no source change — verification task; fix any gap surfaced by T-17)
  Change: run T-17's test against the completed implementation; if it fails, add a fix task before proceeding (do not skip ahead)
  Test scenarios:
    - all T-17 tests pass
    - test_archive_step_record_crash.py (retargeted in T-6b), test_repeat_until.py, test_workflow_schemas_load.py all pass
  depends: T-16, T-17

- [ ] T-19: Review checkpoint — Group 2 (phase gate)
  Why: phase gate — confirm schema/contract/flags/docs cleanup integrates cleanly and the full suite is green
  Test scenarios:
    - full test suite green (`pytest config/scripts/orchestrator_next/tests/ -q`)
    - `grep -rn` for the three removed step ids finds no schema/contract references
    - both config trees consistent (repo and `$ORCHESTRATOR_HOME`)
  depends: T-18

<!-- Format contract: contracts/artifact-formats.md § Task Format Contract -->
