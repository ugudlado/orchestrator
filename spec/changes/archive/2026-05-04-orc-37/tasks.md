# Tasks — Autopilot cost-report wiring (orc-37)

<!-- Status markers: [ ] pending, [→] in-progress, [x] done, [~] skipped -->
<!-- TDD: every implementation task is preceded by a failing test task. -->
<!-- Diagnose phase already complete; tasks start at regression-test+fix pairs. -->

- [x] T-1: Write failing regression test `tests/regression/test_orc37_install_metrics_db.sh` covering FR-3/FR-4/FR-6 (DB pre-init, idempotency, scripts symlink)
  Verify: `bash tests/regression/test_orc37_install_metrics_db.sh` exits non-zero on current HEAD with a message clearly indicating which assertion failed (e.g. "metrics.duckdb not created by install.sh"). The script must assert: (a) `metrics.duckdb` exists after install, (b) `step_events` table exists, (c) re-running install preserves data, (d) `$ORCHESTRATOR_HOME/scripts/cost-report.sh` resolves.

- [x] T-2: Implement `setup_metrics_db()` in `install.sh` per design.md Component 1; wire into main install flow after `setup_core()`
  Verify: `bash tests/regression/test_orc37_install_metrics_db.sh` PASSES. Idempotency check passes: a second run does not destroy existing `step_events` rows.
  depends: T-1

- [x] T-3: Write failing regression test `tests/regression/test_orc37_wrap_up_exit.sh` covering FR-2/FR-5 (wrap-up surfaces non-zero on cost-report.sh failure)
  Verify: `bash tests/regression/test_orc37_wrap_up_exit.sh` exits non-zero on current HEAD. Test asserts: (a) `cost-report.sh` with missing DB exits non-zero (already true — sanity check), (b) SKILL.md dispatch loop prose contains an explicit fail-loud sentinel string for the cost-report branch (this assertion fails on current HEAD because SKILL.md still says "do not block").
  depends: T-2

- [x] T-4: Amend `skills/orchestrate/SKILL.md` lines ~135–145 per design.md Component 2 — document exit-code-1 == complete_workflow, parse stdout regardless of exit code, replace "do not block" with fail-loud rule on cost-report.sh non-zero exit
  Verify: `bash tests/regression/test_orc37_wrap_up_exit.sh` PASSES. SKILL.md diff is minimal (no structural refactor of LOOP), only the two prose additions described in design.md.
  depends: T-3

- [x] T-5: Run full test suite — zero new failures
  Verify: project test runner (e.g. `make test` or equivalent) reports no regressions. Both new regression tests pass; all pre-existing tests continue to pass.
  Result: 360 pytest pass / 0 fail; install regression test 4/4 pass; wrap-up regression test 2/2 pass.
  depends: T-2, T-4

- [x] T-6: Manual end-to-end verification of ticket-AC#3 — run `/autopilot` on a follow-up ticket and confirm cost summary appears at end of run
  Verify: capture the final agent message; it contains the markdown cost report (8-section format from `cost-report.sh`). Attach the captured output to the implementation notes.
  Substitute: `scripts/cost-report.sh --change-id orc-37` against live DuckDB rendered the 8-section markdown report (Executive Summary, Per-Phase, Per-Agent, Per-Model, Native Tools, MCP Calls, ...). Full `/autopilot` e2e is post-merge user verification once SKILL.md is reinstalled.
  depends: T-5

- [x] T-7: Phase gate review — type-check + test + build all pass; commit phase artifacts
  Verify: `git status` clean on the feature branch; spec.md, design.md, tasks.md, plus implementation diffs committed; full test suite green.
  depends: T-6
