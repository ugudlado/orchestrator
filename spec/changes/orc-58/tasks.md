# Tasks: Fix orchestrator CLI repeat-loop exit and step advancement bugs

- [x] T-1: Add regression tests for both bugs (resolver fall-through + reconcile terminal-entry skip)
  Verify: New file `config/scripts/orchestrator_next/tests/test_resolve_artifact_fallback.py` contains at least 3 cases (worktree-missing-file → repo_root path returned with unchecked items → False; same with all checked → True; worktree-relative file present → worktree path returned). New file `config/scripts/orchestrator_next/tests/test_reconcile_terminal_skip.py` contains at least 2 cases (YAML completed + DB in_progress for same triple → no append; YAML empty + DB in_progress → append). Run `pytest config/scripts/orchestrator_next/tests/test_resolve_artifact_fallback.py config/scripts/orchestrator_next/tests/test_reconcile_terminal_skip.py` — both new test files must FAIL with the current (unfixed) code, with failure messages matching the documented root causes (FileNotFoundError fail-closed for Bug 1; duplicate in_progress append for Bug 2).

- [ ] T-2: Implement both fixes in record.py and reconcile.py (depends: T-1)
  Verify: In `config/scripts/orchestrator_next/record.py` the priority-2 branch of `_resolve_workflow_artifact_path` now returns the worktree candidate only when `candidate.is_file()` is true; otherwise control falls through to priority 3. In `config/scripts/orchestrator_next/reconcile.py` the FR-5 `yaml_keys` comprehension no longer filters by `e.status == "in_progress"`. Run `pytest config/scripts/orchestrator_next/tests/test_resolve_artifact_fallback.py config/scripts/orchestrator_next/tests/test_reconcile_terminal_skip.py` — all cases pass.
  depends: T-1

- [ ] T-3: Run full orchestrator_next test suite — zero new failures (depends: T-2)
  Verify: `pytest config/scripts/orchestrator_next/tests/` exits 0 with no regressions in existing tests (test_repeat_until.py, test_reconcile_in_progress.py, test_dispatch*.py, etc.).
  depends: T-2

- [ ] T-4: Verify reproduction scripts now reflect fixed behaviour (depends: T-2)
  Verify: `python3 spec/changes/orc-58/repro_bug1.py` shows `_check_all_tasks_completed` correctly reads tasks.md from repo_root when worktree-relative copy is absent. `python3 spec/changes/orc-58/repro_bug2.py` shows `step_history` length unchanged after reconcile when YAML has terminal entry for the same triple as a DB in_progress row.
  depends: T-2

- [ ] T-5: Review checkpoint (phase gate)
  Verify: All AC-1 through AC-6 from spec.md satisfied. `pytest config/scripts/orchestrator_next/tests/` exits 0. Diff limited to `record.py`, `reconcile.py`, and the two new test files.
  depends: T-3, T-4
