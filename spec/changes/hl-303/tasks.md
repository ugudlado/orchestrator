# Tasks: HL-303 — Workflow artifacts to the worktree

<!-- Bugfix shape: T-1 = regression test, T-2 = root-cause fix. -->
<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- (depends: T-N) = dependency -->

- [x] T-1 Regression test: `_check_all_tasks_completed` must fail-close when path is constructible but file missing
  - **Files**:
    - `config/scripts/orchestrator_next/tests/test_resolve_tasks_md.py` (extend)
  - **Why**: Codifies the fail-open bug at `record.py:931` as a failing
    test on main. Must FAIL before T-2 and PASS after.
  - **Approach**: Add `test_check_all_tasks_completed_fail_closed_when_path_missing`.
    Build `state_raw` with `change_id="demo"`, `worktree_path=<tmp>/worktree`,
    `repo_root=<tmp>/repo`. Do not create any tasks.md anywhere. Assert
    `_check_all_tasks_completed(state_raw) is False`. Also add
    `test_resolve_falls_back_to_repo_root_when_worktree_missing` (worktree
    dir absent → resolver returns repo_root candidate).
  - **Verify**:
    - On main: `cd config/scripts/orchestrator_next && python -m pytest tests/test_resolve_tasks_md.py::test_check_all_tasks_completed_fail_closed_when_path_missing -v` exits non-zero (assertion fails — current code returns True).
    - After T-2: same command exits zero.
  - **Extension (added during T-2 architect consultation — see escalation note below)**:
    Also add `test_dispatch_repeats_step_when_predicate_false` to
    `config/scripts/orchestrator_next/tests/test_dispatch.py` (extend or create).
    Build a `State` whose `step_history` contains a `completed` entry for
    `execute-next-task` (which has `repeat_until: all_tasks_completed`),
    a `tasks.md` with at least one `- [ ]` unchecked item, and call
    `dispatch.dispatch(state, state_yaml_path)`. Assert the returned
    `action["step_id"] == "execute-next-task"` (NOT `run-phase-review`).
    Test FAILS on main and on post-T-2 (record.py-only fix); PASSES after T-2.5.

- [ ] T-2 Fix root cause — unify resolvers and tighten fail-open in `record.py` (depends: T-1)
  - **Files**:
    - `config/scripts/orchestrator_next/record.py` (lines 807-819, 889-932)
  - **Why**: Single resolver removes ORC-36 divergence; fail-closed
    prevents silent task-skipping; worktree-aware resolution matches
    where writers will put files.
  - **Approach**:
    1. Add `_resolve_workflow_artifact_path(state_raw, filename) -> Path | None`
       returning `<worktree_path>/spec/changes/<id>/<filename>` when
       `state_raw.get("worktree_path")` exists as a directory, else
       `<repo_root>/spec/changes/<id>/<filename>`. Honors `tasks_path`
       override unchanged.
    2. Reduce `_resolve_tasks_md` to a one-line wrapper calling the new
       helper with `"tasks.md"`.
    3. Reduce `_resolve_feature_metrics_tasks_path` to a one-line wrapper
       calling the new helper.
    4. In `_check_all_tasks_completed`: replace the
       `except FileNotFoundError, OSError: return True` branch with
       `return False` when `path is not None`. Keep `if path is None: return True`
       (the only legitimate fail-open).
  - **Verify**:
    - `cd config/scripts/orchestrator_next && python -m pytest tests/ -v` — all green including T-1's tests.
    - `bash spec/changes/hl-303/repro.sh` prints `OK: predicate correctly detected unchecked tasks`.
    - `grep -c 'def _resolve_tasks_md\|def _resolve_feature_metrics_tasks_path\|def _resolve_workflow_artifact_path' config/scripts/orchestrator_next/record.py` returns `3` (one helper, two thin wrappers).

- [ ] T-2.5 Close the second fail-open seam in `dispatch.py` — honor `repeat_until` in the history-walk (depends: T-2)
  - **Files**:
    - `config/scripts/orchestrator_next/dispatch.py` (lines 314-319)
    - `config/scripts/orchestrator_next/record.py` (export `_REPEAT_PREDICATES`
      so dispatch can import it without circularity — promote to module-level
      shared symbol or move to a small `predicates.py` if imports are awkward)
    - `config/scripts/orchestrator_next/tests/test_dispatch.py` (extend or create)
  - **Why**: `dispatch.dispatch()` independently walks `step_history` and
    treats any step with a `completed` entry as advanced — ignoring both
    `state.next_step` (set by `record._compute_next_step`) and
    `contract.repeat_until`. Without this fix, hl-303's T-2 record.py fix is
    insufficient: dispatch silently advances `execute-next-task` →
    `run-phase-review` while unchecked tasks remain, recreating ORC-37's
    manual-reviewer-rejection workaround inside this very run. See design.md
    § "Two fail-open seams, not one".
  - **Approach**:
    1. In `dispatch.py`, inside the history-walk loop (lines 314-319), when
       `_find_completed_step` returns True for a step, also load that step's
       contract and check `contract.repeat_until`. If a predicate is declared
       and returns False against `state_raw`, select that step as
       `next_step_id` (re-emit) and break.
    2. Reuse the predicate map already defined in `record.py` —
       `_REPEAT_PREDICATES = {"all_tasks_completed": _check_all_tasks_completed}`.
       Either import it into dispatch or lift both predicate map and
       `_check_all_tasks_completed` into a small shared module
       (`predicates.py`) imported by both. Pick whichever yields cleaner
       imports (no circular import). One source of truth for predicate
       evaluation.
    3. Convert `state` (typed `State`) to the raw dict form the predicate
       expects — reuse the same approach `record.py` uses. If `State`
       does not already expose a `to_raw()` round-trip, read state.yaml
       once via the existing `_load_plan` style helper.
  - **Verify**:
    - `cd config/scripts/orchestrator_next && python -m pytest tests/test_dispatch.py::test_dispatch_repeats_step_when_predicate_false -v` exits zero.
    - `cd config/scripts/orchestrator_next && python -m pytest tests/ -v` — all green.
    - Manual: with hl-303's own state.yaml (T-2 marked complete, T-3+ unchecked),
      `orchestrator next /Users/spidey/code/orchestrator/spec/changes/hl-303/state.yaml`
      returns `step_id: execute-next-task` (NOT `run-phase-review`).
    - `grep -c '_REPEAT_PREDICATES\|repeat_until' config/scripts/orchestrator_next/dispatch.py` returns ≥ 1.

- [ ] T-3 Propagate `WORKTREE_ARTIFACT_DIR` env from parser + step-dispatch (depends: T-2.5)
  - **Files**:
    - `config/scripts/orchestrator_next/parser.py` (~line 180)
    - `config/steps/contracts/step-dispatch.md` (env table)
  - **Why**: Writers need a worktree-aware target var distinct from
    `WORKFLOW_STATE_DIR`.
  - **Approach**: In `parser.py`, after `workflow_dir = ...`, compute
    `worktree_artifact_dir = (worktree_path or repo_root) + "/spec/changes"`.
    Add to the dataclass and to the env dict produced by step-dispatch.
    Add `ORCHESTRATOR_WORKTREE_ARTIFACT_DIR` row to the table at
    `config/steps/contracts/step-dispatch.md:160`.
  - **Verify**:
    - `python -c "from orchestrator_next.parser import load_state; s = load_state('spec/changes/hl-303/state.yaml'); print(s.worktree_artifact_dir)"` prints the worktree path.
    - `grep ORCHESTRATOR_WORKTREE_ARTIFACT_DIR config/steps/contracts/step-dispatch.md` returns one line.

- [ ] T-4 Redirect writer step contracts to `$WORKTREE_ARTIFACT_DIR` (depends: T-3)
  - **Files**:
    - `config/steps/design-and-draft-artifacts.yaml` (lines 41, 72, 90, 96-97)
    - `config/steps/diagnose.yaml`
    - `config/steps/ux-design.yaml` (lines 30-31, 43)
  - **Why**: Writers must target the worktree for tracked artifacts so the
    reader's worktree-first resolver finds them.
  - **Approach**: Sed/edit each occurrence of `$WORKFLOW_STATE_DIR/$CHANGE_ID/`
    referring to **tracked artifacts** (spec.md, design.md, tasks.md,
    diagnose.md, ux-prototype.html, ux-artifacts.yaml) to
    `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/`. Leave `state.yaml`/`plan.yaml`
    references untouched.
  - **Verify**:
    - `grep -n WORKTREE_ARTIFACT_DIR config/steps/design-and-draft-artifacts.yaml config/steps/diagnose.yaml config/steps/ux-design.yaml` returns one or more lines per file.
    - `grep -n 'WORKFLOW_STATE_DIR/$CHANGE_ID/\(spec\|design\|tasks\|diagnose\|ux-\)' config/steps/*.yaml` returns no matches.

- [ ] T-5 Update workflow-init verify-block + ensure worktree artifact dir exists (depends: T-4)
  - **Files**:
    - `config/steps/workflow-init.yaml` (verify block lines 50-51 and instruction body)
  - **Why**: After `git worktree add`, the artifact directory under the
    worktree must exist before any writer step runs.
  - **Approach**: In the workflow-init instruction (after worktree
    creation and before state.yaml write), add:
    `mkdir -p $WORKTREE_ROOT/spec/changes/<slug>`. Update verify block:
    keep `state.yaml at $WORKFLOW_STATE_DIR/<slug>/state.yaml`; add
    `worktree artifact dir at $WORKTREE_ROOT/spec/changes/<slug>/ exists when flags.worktree is true`.
  - **Verify**:
    - `grep -A4 '^verify:' config/steps/workflow-init.yaml` shows both checks.
    - Dry-run a workflow-init: artifact dir exists post-step.

- [ ] T-6 Update archive script to merge tracked artifacts (worktree) with state (repo_root) (depends: T-4)
  - **Files**:
    - `scripts/inline/archive-completed-change.sh`
    - `config/steps/archive-completed-change.yaml` (rules + instruction body)
  - **Why**: Archive must collect both root classes; current script only
    sources from repo_root.
  - **Approach**: In the shell script, replace the single
    `mv "$SRC" "$DST"` with:
    ```
    WT_SRC="$WORKTREE_ROOT/spec/changes/$CHANGE_ID"
    RR_SRC="$REPO_ROOT/spec/changes/$CHANGE_ID"
    mkdir -p "$DST"
    [ -d "$WT_SRC" ] && cp -a "$WT_SRC"/. "$DST"/ && rm -rf "$WT_SRC"
    [ -d "$RR_SRC" ] && cp -a "$RR_SRC"/. "$DST"/ && rm -rf "$RR_SRC"
    ```
    Update the YAML rules to document the dual source. Pass
    `WORKTREE_ROOT` env to the script.
  - **Verify**:
    - New shell test `config/tests/test-archive-merges-worktree-artifacts.sh`
      (created in this task): seeds a fake worktree with `spec.md`,
      a fake repo_root with `state.yaml`, runs the script, asserts
      both files in the archive destination. `bash config/tests/test-archive-merges-worktree-artifacts.sh` exits zero.

- [ ] T-7 Update CONVENTIONS.md and skill headers (depends: T-4)
  - **Files**:
    - `config/steps/CONVENTIONS.md` (line 297 and surrounding section)
    - `skills/orchestrate/SKILL.md` (line 20, 82-83, 139)
    - `skills/learn/SKILL.md` (lines 16, 31-32)
    - `skills/telemetry/SKILL.md` (line 29, 57)
    - `CLAUDE.md` (Repo Wiring § Paths table)
  - **Why**: Document the split (state at repo_root, artifacts at worktree)
    so future contributors and agents do not regress.
  - **Approach**: Add explanatory blurb in CONVENTIONS.md naming both
    vars and stating which content goes where. Update SKILL.md headers
    to set both vars. Update CLAUDE.md Paths table with two rows:
    "Active workflow state (state.yaml/plan.yaml)" → repo_root, and
    "Active workflow artifacts (spec/design/tasks/diagnose)" → worktree.
  - **Verify**:
    - `grep -n WORKTREE_ARTIFACT_DIR config/steps/CONVENTIONS.md skills/*/SKILL.md CLAUDE.md` returns matches in each file.

- [ ] T-8 Confirm `.gitignore` covers state/plan only (no change unless gap) (depends: T-7)
  - **Files**:
    - `.gitignore` (lines 20-21)
  - **Why**: Confirm scope — state.yaml/plan.yaml ignored at repo_root;
    tracked artifacts in worktree must be committable. Current entries
    (`spec/changes/*/state.yaml`, `spec/changes/*/plan.yaml`) already
    match — verify nothing else is needed.
  - **Approach**: `grep -n 'spec/changes' .gitignore` and confirm the
    two patterns. If any pattern matches tracked artifacts (e.g., a
    broader `spec/changes/*/` rule appears later), narrow it.
  - **Verify**:
    - `cd /Users/spidey/code/feature_worktrees/hl-303 && touch spec/changes/hl-303/probe.md && git check-ignore -v spec/changes/hl-303/probe.md; rm spec/changes/hl-303/probe.md` → `git check-ignore` exits non-zero (file is NOT ignored).
    - `cd /Users/spidey/code/feature_worktrees/hl-303 && touch spec/changes/hl-303/state.yaml; git check-ignore -v spec/changes/hl-303/state.yaml; rm spec/changes/hl-303/state.yaml` → `git check-ignore` exits zero (file IS ignored).

- [ ] T-9 Run full test suite — zero new failures (depends: T-2, T-6)
  - **Files**: none (verification step)
  - **Why**: Ensure resolver + writer changes do not regress other tests.
  - **Verify**:
    - `cd config/scripts/orchestrator_next && python -m pytest tests/ -v` — all green.
    - `bash config/tests/test-archive-merges-worktree-artifacts.sh` — pass.
    - `bash config/tests/test-remove-worktree-safe-branch-delete.sh` — pass.
    - `bash spec/changes/hl-303/repro.sh` — prints OK line.

- [ ] T-10 End-to-end validation on this run (depends: T-9)
  - **Files**: none (validation only)
  - **Why**: hl-303 is the migration test case; the run itself proves the layout works.
  - **Verify**:
    - `ls /Users/spidey/code/feature_worktrees/hl-303/spec/changes/hl-303/` lists `spec.md`, `design.md`, `tasks.md`, `diagnose.md`.
    - `ls /Users/spidey/code/orchestrator/spec/changes/hl-303/` lists `state.yaml`, `plan.yaml` (no tracked artifacts).
    - `_check_all_tasks_completed` reads tasks.md from worktree successfully (validated implicitly by the dispatcher iterating tasks rather than skipping).
    - On final archive, `ls spec/changes/archive/2026-05-04-hl-303/` lists all of `spec.md`, `design.md`, `tasks.md`, `diagnose.md`, `state.yaml`, `plan.yaml`.

<!-- VERIFICATION BUGS: If verification reveals new issues, add them as tasks -->
<!-- before proceeding. Do NOT skip ahead. -->
