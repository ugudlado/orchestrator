# Phase Review — orc-96 (implement phase, attempt 1)

**Verdict: needs_work** · Overall: 5/10 · Schema: feature

## Summary

ORC-96 adds test coverage for the project.yaml learnings-injection feature. The
test file is well-constructed and the implementation it exercises is correct.
**But the implementation is uncommitted.** The entire learnings feature
(`_load_learnings`, `_relevant_learnings`, the agent + resume injection sites —
96 insertions) exists only as an unstaged `M dispatch.py` working-tree change. The
three ORC-96 commits (`f456440`, `0fdcda7`, `970f7dd`) added the test file alone.
A clean checkout of `feature/orc-96` ships tests with no implementation.

## Verification commands

| Command | Result |
|---------|--------|
| `pytest …/tests/test_dispatch_learnings.py -q` (working tree) | 21 passed |
| `pytest …/tests/ -q` (working tree) | 705 passed, 2 skipped, 1 xfailed |
| `pytest …/tests/test_dispatch_learnings.py -q` (committed HEAD, dispatch.py stashed) | **ERROR — ImportError: cannot import name `_load_learnings`** |
| `git show HEAD:…/dispatch.py \| grep -c learnings` | **0** |
| `git diff HEAD --stat -- …/dispatch.py` | **96 insertions, 7 deletions — uncommitted** |
| `git diff f456440~1 HEAD --stat` (ORC-96 commits) | test file only, 498 insertions |

## Critical finding

**CF-1 (spec_compliance + correctness): Feature implementation is uncommitted.**

- **Finding:** `git show HEAD:config/scripts/orchestrator_next/dispatch.py` contains
  zero `learnings` references. The functions the tests import (`_load_learnings`,
  `_relevant_learnings`) and both injection sites live only in the working tree as
  an unstaged modification. Proven conclusively: stashing the dispatch.py edit and
  re-running the tests fails collection with
  `ImportError: cannot import name '_load_learnings' from 'orchestrator_next.dispatch'`.
- **Why it caps the phase:** AC-1, AC-3, AC-4, AC-6 (all dispatch/injection ACs)
  and AC-7 (suite green) pass *only* because of uncommitted code. On a clean
  checkout or after merge, the test suite breaks. The phase cannot pass with its
  core implementation uncommitted — the branch would merge as
  tests-without-implementation.
- **Scope:** `config/scripts/orchestrator_next/dispatch.py` only. The doctor.py /
  test_doctor.py deltas seen in `git diff main` are already committed on this branch
  (ORC-94 base), not uncommitted. The dispatch.py edit has no `doctor` references —
  cleanly separable. Untracked `pytest-of-spidey/` (cache) and `spec/changes/orc-96/`
  (artifacts) are not feature code.
- **Approach (fix-1):** Stage and commit **only** dispatch.py. Do not rewrite the
  implementation — it is correct and validated by the T-1..T-3 tests against the
  working tree. Re-verify with `git diff HEAD --quiet -- …/dispatch.py` (exit 0) and
  a green suite against the committed tree.

## AC verification (evidence)

All ACs verified against the **working tree** (where the implementation currently
lives). Each is satisfied *functionally* but blocked by CF-1 until the
implementation is committed.

| AC | Test | Working-tree evidence |
|----|------|-----------------------|
| AC-1 inject filtered learnings on agent path | `test_agent_path_injects_filtered_learnings` | `action["learnings"]` ids == `[behavior-1, dev-only]` (info excluded, dev-tag kept) ✓ |
| AC-2 exclude `kind: informational` | `test_informational_excluded_behavioral_retained` | `result == [learnings[1]]` ✓ |
| AC-3 fresh `run:` path omits key | `test_fresh_run_path_omits_learnings_key` | `"learnings" not in action` ✓; dispatch.py:617-637 has no key ✓ |
| AC-4 no project.yaml → `[]`, exit 0 | loader + `test_agent_path_no_project_yaml_learnings_empty_exit_0` | `[]`, `code==0` ✓ |
| AC-5 absent/non-list/unreadable → `[]` | `TestLoadLearningsMissingOrEmpty` (4) + malformed | all `== []` ✓ |
| AC-6 tag filter; untagged universal | `TestRelevantLearnings{Agent,Phase,Combined,Universal}` | match/non-match/both/untagged covered ✓ |
| AC-7 full suite green | full run | 705 passed, 0 failed (working tree) ✓ — **but fails against committed HEAD** |

OQ-A as-built resume edge: `test_resume_inline_run_path_carries_learnings_as_built`
pins (not asserts-clean) the unconditional resume injection — consistent with the
documented decision. JSON date-flatten covered by `test_yaml_date_coerced_to_string`.

## Scope / hygiene (non-blocking)

- Test commits touch **only** the test file (498 insertions) — respects the
  "do not edit dispatch.py" task constraint. ✓
- No TODO/FIXME/placeholder in the test file. ✓
- `pytest-of-spidey/` cache dir is untracked clutter in the worktree root — remove
  before finalizing (cleanup, not a finding).

## Dimension scores

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| spec_compliance | 5 | CF-1 critical — ACs pass only on uncommitted code (capped at critical_cap 5) |
| correctness | 5 | CF-1 critical — committed branch fails test collection (capped at critical_cap 5) |
| security | 9 | No security surface; read-only load, best-effort try/except |
| simplicity | 9 | Focused tests, fixture reuse, no over-engineering |
| code_quality | 9 | Class-grouped by AC, parametrized, docstrings cite ACs, no placeholders |

**Overall = min(dimensions) = 5.** Below `min_phase_review_score` (9) and a critical
finding is present → **needs_work**.

## Baseline comparison

Historical feature-schema `review_score_avg`: 9.5, 9.0, 8.3, 9.0, 9.0, 9.0
(avg ≈ 8.97; one malformed `0` outlier excluded). Current overall 5 is >2 below the
average — but this is an expected dip from a blocking commit-hygiene finding, not a
quality regression in the produced artifacts. Recorded for transparency.

## Action taken

- Appended `fix-1` to `tasks.yaml` (commit the uncommitted dispatch.py
  implementation; scoped to dispatch.py only; no re-derivation).
- Ran `orchestrator expand-plan` — injected `task-fix-1` node (depends_on
  `task-T-4`) and rewired `run-phase-review.depends_on` → `task-fix-1`. The
  dispatcher will schedule the fix before re-running this review.
