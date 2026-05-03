# Tasks: ORC-36

Bugfix workflow → T-1 is the regression test (must FAIL on HEAD), T-2 is the core fix
(makes T-1 PASS). Subsequent tasks group remaining changes by dependency chain — one
developer spawn per chain root.

---

## [x] T-1: Regression test for path-split failure modes

**Description.** Add a regression test (`tests/integration/test_orc36_path_consolidation.py`
or a shell harness under `tests/regression/orc36-path/`) that reproduces all four failure
modes from `diagnose.md` against current HEAD and verifies the fix:

1. `_resolve_feature_metrics` raises `FileNotFoundError` when `tasks.md` is in
   `spec/changes/<slug>/` but state is at `.state/<slug>/` (current behavior); does NOT
   raise after the fix.
2. `compute-prediction-accuracy.py` returns `predicted=0/actual=0/accuracy=100%` for the
   pre-fix layout; returns nonzero for the post-fix layout.
3. `archive-completed-change.sh` produces an archive missing `tasks.md` and `spec.md`
   under the pre-fix layout; produces a complete archive under the post-fix layout.
4. `seed-state.sh` creates `.state/<slug>/` under pre-fix; creates `spec/changes/<slug>/`
   (and not `.state/`) under post-fix.

**Files.** `tests/regression/orc36-path/` (new), regression script + fixtures.

**Verify.** Test runs as part of CI/local-test loop. Must FAIL on HEAD before T-2 lands
(commit the test first; demonstrate the failure). Must PASS after T-2.

---

## [x] T-2: Core path change — `seed-state.sh`, skills defaults, `.gitignore`

**Description.** Flip the `WORKFLOW_STATE_DIR` default from `$REPO_ROOT/.state` to
`$REPO_ROOT/spec/changes` at every place it is set. Add the two `.gitignore` patterns.

**Files.**
- `skills/orchestrate/scripts/seed-state.sh` (line 49 + header comment line 11, 15)
- `skills/orchestrate/SKILL.md` (line 20)
- `skills/learn/SKILL.md` (line 16)
- `skills/telemetry/SKILL.md` (line 29)
- `.gitignore` (append two patterns; verify glob does NOT match `spec/changes/archive/**`)

**Verify.**
- `bash -c 'cd <fresh tempdir> && WORKFLOW_STATE_DIR= REPO_ROOT=$PWD seed-state.sh
  ...'` produces `spec/changes/<slug>/state.yaml`, no `.state/` dir.
- `git check-ignore -v spec/changes/orc-36/state.yaml` matches.
- `git check-ignore -v spec/changes/archive/2026-05-03-orc-34/state.yaml` does NOT match.
- T-1 sub-test #4 now passes.

---

## [x] T-3: Consumer rewiring — `record.py`, `compute-prediction-accuracy.py`, `compute-swe-metrics.sh`

**Description.** Update `_resolve_feature_metrics_tasks_path` to resolve `tasks.md` under
`$WORKFLOW_STATE_DIR/<change_id>/` (using env-var with `spec/changes` default). Verify
`compute-prediction-accuracy.py:80–81` and `compute-swe-metrics.sh` work without internal
change once `STATE_YAML_PATH` flips. Add a one-line comment to each documenting the
sibling-lookup assumption so a future reader doesn't re-introduce drift.

**Files.**
- `config/scripts/orchestrator_next/record.py` (`_resolve_feature_metrics_tasks_path`,
  ~line 798)
- `scripts/inline/compute-prediction-accuracy.py` (lines 80–81 — comment only)
- `scripts/inline/compute-swe-metrics.sh` (no internal change; verify caller in step
  contract — comment only)

**Verify.**
- T-1 sub-tests #1 and #2 now pass.
- `python -m pytest tests/...` for any existing record.py unit tests still pass.

---

## [x] T-4: Archive script rewrite — `cp -R` → `mv`

**Description.** Rewrite `archive-completed-change.sh` to use `mv` instead of
`cp -R`. The single rename atomically moves `state.yaml`, `plan.yaml`, and all artifacts
out of `spec/changes/<slug>/` into `spec/changes/archive/<date>-<slug>/`. Preserve the
existing skip-JSON behavior when source dir is missing. Preserve the `git add` + commit
flow.

**Files.**
- `scripts/inline/archive-completed-change.sh` (lines 21–35 region; replace
  `cp -R "$SRC" "$DST"` with `mv "$SRC" "$DST"`)
- `config/steps/archive-completed-change.yaml` — sweep prose for `.state/` mentions; no
  variable change.

**Verify.**
- T-1 sub-test #3 passes.
- Manual smoke: create a fake `spec/changes/test-slug/{state.yaml,plan.yaml,spec.md}`,
  run script, confirm `spec/changes/test-slug/` is gone and
  `spec/changes/archive/<date>-test-slug/` contains all three files.

---

## [x] T-5: Step contracts + skills + agents — text sweep

**Description.** Mechanical update of every step contract, skill file, and agent file
that references `.state/` literally or hints at the old default. Variable references
(`$WORKFLOW_STATE_DIR/$CHANGE_ID/...`) stay; only prose changes. Also remove the
`.state/*/state.yaml` schema-check block from `scripts/pre-commit.sh` (lines ~30–60 —
the entire `if [ -n "$state_files" ]` block).

**Files.**
- `config/steps/design-and-draft-artifacts.yaml`
- `config/steps/archive-completed-change.yaml` (already touched in T-4 — sweep here too)
- `config/steps/compute-prediction-accuracy.yaml`
- `config/steps/compute-swe-metrics.yaml`
- `config/steps/workflow-init.yaml`
- `config/steps/run-learn-cycle.yaml`
- `config/steps/select-workflow.yaml`
- `config/steps/preview-route.yaml`
- `config/steps/ux-design.yaml`
- `config/steps/write-bootstrap-state.yaml`
- `config/steps/CONVENTIONS.md`
- `agents/workflow-init.md` (lines 72, 92, 101 region)
- `scripts/pre-commit.sh` (delete the state-schema block)

**Verify.**
- `grep -rn "\.state/" config/ skills/ agents/ scripts/` returns zero hits outside this
  ticket's own diagnose.md and any intentional historical comments.
- AC-7 satisfied.
- Pre-commit still runs cleanly on a normal commit (yaml-syntax check still fires).

---

## [x] T-6: CLAUDE.md docs

**Description.** Update `CLAUDE.md` Paths table row "Active workflow state" to point at
`$REPO_ROOT/spec/changes/<slug>/state.yaml`. Sweep the rest of the file for any other
`.state/` references and update or remove.

**Files.** `CLAUDE.md`

**Verify.**
- `grep -n "\.state" CLAUDE.md` returns zero hits.
- AC-8 satisfied.

---

## [x] T-7: One-time orc-36 self-archival + end-to-end verification

**Description.** Two parts:

(a) **Self-archival shim for this run.** Before `archive-completed-change` fires for
orc-36, manually move `.state/orc-36/{state.yaml,plan.yaml}` into `spec/changes/orc-36/`
and remove `.state/orc-36/`. This is a one-off operator action (or a single-shot script
in `spec/changes/orc-36/finalize.sh` that the workflow can invoke once). It is NOT
committed code — it's a transient finalization.

(b) **End-to-end verification (AC-9).** Run a follow-up `/autopilot` on a fresh
backlog ticket (or a synthetic dry-run that exercises seed → diagnose → design →
mark-completed → compute-prediction-accuracy → archive). Confirm:
- No `.state/` directory created.
- No symlinking required.
- `mark-change-completed` succeeds with no `FileNotFoundError`.
- `compute-prediction-accuracy` reports nonzero predicted/actual.
- Archive at `spec/changes/archive/<date>-<slug>/` contains all artifacts.

**Files.** None committed; verification only. May produce a short note in
`spec/changes/orc-36/retro.md` documenting the e2e result.

**Verify.**
- All ACs (1–10) satisfied.
- T-1 regression test still passes.
- Archive completed for the e2e ticket without manual intervention.
