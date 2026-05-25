# Phase Review — ORC-84 (implement)

**Verdict:** pass
**Overall:** 9
**Scoring:** critical_cap=5, important_cap=7, green_base=9

## Dimensions

| Dimension | Score | Notes |
|---|---|---|
| spec_compliance | 9 | All 9 ACs verified; design.md / tasks.yaml conform to format contracts. One important finding on T-3 design note prose (see F-1). |
| correctness | 9 | 20/21 bats tests pass; 1 unrelated environmental failure isolated and explained (F-2). |
| security | 9 | No new attack surface; stderr-only, read-only state inspection, no new persisted fields. |
| simplicity | 9 | Implementation reuses `record.py` and `cost-report.sh --tail`; helpers are ~50 LOC of bash + python heredoc. Approach 1 (selected) chose smallest-surface option over Approaches 2/3. |
| code_quality | 9 | Bash + heredoc style matches existing run-workflow.sh; failure modes silent per design; `|| true` keeps `set -e` honest. |

Overall = min(dims) = 9. Bonus +1 withheld: T-3's narrative needed correction (see F-1), so not "first-pass clean."

## Workflow plan task-nodes

All implement-phase task-nodes are completed:
- task-T-1: completed (bats tests added)
- task-T-2: completed (9-line `_log_step_usage` agent-vs-script guard)
- task-T-3: completed (contract preservation verified)

No quarantined tasks.

## AC verification (evidence)

Verified against design.md `## Acceptance Criteria` (AC-1 … AC-9).

| AC | Result | Evidence |
|---|---|---|
| AC-1 (local time, stderr only) | PASS | Tests 11 & 12 (`AC-1: Progress timestamp uses local HH:MM:SS without UTC suffix`, `AC-1: Local time differs between TZ=UTC and TZ=Asia/Kolkata`) both green. Helper uses `date +%H:%M:%S` (no `-u`). |
| AC-2 (model + token line) | PASS | Test 13 (`AC-2/AC-3/AC-4: run_inline step logs model, tokens, cost, duration from step_history`) green. |
| AC-3 (cost segment) | PASS | Same test 13 asserts `cost=$0.0234`. |
| AC-4 (duration formatting) | PASS | Same test 13 asserts `duration=4.2s`; helper at run-workflow.sh:340-347 selects ms/s/m by magnitude. |
| AC-5 (rollup on both exit paths) | PASS | Tests 15 (`complete_workflow exit 1 prints feature complete`) and 16 (`archived state after script step still prints feature complete before exit 1`) green. |
| AC-6 (inline/script no-tokens note) | PASS | Test 14 (`AC-6: run_step script step with zero tokens logs no-tokens line and continues`) green. |
| AC-7 (silent when usage missing) | PASS | Tests 17, 18, 19 green. T-2's `model_lc == "none"` guard added at run-workflow.sh:351-356 distinguishes script silence from agent silence. |
| AC-8 (cost-report failure soft-fails) | PASS | Tests 20 & 21 (non-zero exit / empty stdout) green; exit code remains 1. |
| AC-9 (no contract change) | PASS — with caveat | `git diff main...HEAD -- bin/orchestrator config/scripts/orchestrator_next/record.py` (three-dot, branch-only) is **empty** — this branch made zero edits to either file. The two-dot diff (`git diff main -- …`) shows 25 lines, but those are upstream advances on `main` (commits d9ea4cd, 3a0ca1a) that this branch hasn't picked up. See F-1 for the wording fix to design.md's T-3 note. |

Counted scope checks:
- `git diff main...HEAD -- bin/orchestrator` → empty (0 lines, 0/1 file touched, asserts AC-9 contract preservation for bin entry-point).
- `git diff main...HEAD -- config/scripts/orchestrator_next/record.py` → empty (0 lines, 0/1 file).
- `grep -nE '_log_step_usage|_emit_feature_rollup' scripts/run-workflow.sh` → both are read-only; no `state.yaml` writes (asserts AC-9 "no new top-level keys").

## Findings

### F-1 (important, spec_compliance, dimension cap 7)

design.md `### T-3 contract verification` paragraph says helpers "delegate to read-only `state_inspect.py log-step-usage` and `cost-report.sh --tail`," but this branch's `scripts/run-workflow.sh` contains an inline python heredoc for `_log_step_usage` and does **not** reference `state_inspect.py`. That delegation exists on `main` (commit c93e4e4) but not on this branch. The wording describes a post-rebase state, not the shipped state.

**Fix direction:** edit the T-3 paragraph to say "helpers `_log_step_usage` (inline python heredoc reading `step_history[-1]`) and `_emit_feature_rollup` (shells to `cost-report.sh --tail`) are read-only — neither writes `state.yaml` or introduces new top-level keys."

Not a critical finding because: the actual behavior is contract-preserving; the inaccuracy is in prose, not in code, and an external reader following the prose would still arrive at the right conclusion (helpers are read-only).

### F-2 (informational, not scored)

`config/tests/test_run_workflow.bats` test 2 (`Agent run_inline step: routing resolves developer->cursor`) fails on this branch but passes on `main`. Root cause: branch's `record.py` (an older revision than main's) does not yet accept `status: "failed"` from done payloads, and the cursor-agent-stub in test 2 returns a stub that triggers a `failed` recording. This is **not** an ORC-84 regression — it's a pre-existing branch-base issue that disappears on rebase onto current `main` (which contains commit d9ea4cd `fix(record): accept failed and blocked statuses from shell-loop drivers`). Not blocking implement-phase signoff because ORC-84 explicitly does not touch `record.py`.

**Follow-up (not a fix task):** rebase `feature/orc-84` onto current `main` before merge. The branch's only own edits (per `git diff main...HEAD`) are: T-1 bats tests, T-2 nine-line guard in run-workflow.sh, T-3 design.md doc, and state.yaml/discovery.md scaffolding. A rebase should be conflict-free for run-workflow.sh (T-2 hunk is in `_log_step_usage` python heredoc which `main` also rewrote — manual conflict likely; resolve by re-applying the `model_lc == "none"` guard inside main's `state_inspect.py log-step-usage` helper, or against the heredoc if state_inspect path is preferred).

### Quarantined tasks
None.

## Baseline comparison

Historical average `review_score_avg` across 7 archived feature workflows: **7.69**. Current overall 9 is **above** baseline. No quality-regression warning.

## Verify block (from schema)

- `verify.commands`: none defined for this phase.
- `verify.assertions`: phase task-nodes complete (all true).
- `verify.metrics.review_score.min`: per `project.yaml` quality_bar (read at score time).

COMPLETION:
  status: completed
  outputs:
    phase_review_report:
      verdict: pass
  review_score:
    overall: 9
    dimensions:
      spec_compliance: 9
      correctness: 9
      security: 9
      simplicity: 9
      code_quality: 9
  artifacts:
    - spec/changes/orc-84/phase-review.md
