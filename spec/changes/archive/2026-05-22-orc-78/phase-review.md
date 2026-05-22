# Phase Review: orc-78 — Unify phase-opening artifact (`discovery.md`)

Schema: bugfix · Phase: main · HEAD: `18ed778` · Review pass: 2 (re-review after T-6 fix)

## Verdict: PASS

Overall **9/10** — meets `quality_bar.min_phase_review_score` (9). The single
attempt-1 finding (F-1, out-of-scope engine change) is fully closed by T-6
(`18ed778` reverts `c494287`). No new findings. Zero regressions. All 7 ACs
pass with primary evidence; all 6 tasks `[x]`.

The +1 first-pass bonus is unavailable on attempt 2; `green_base` (9) is both
floor and ceiling this round.

| Dimension | Score | Key Issues |
|-----------|-------|------------|
| Spec Compliance | 9/10 | F-1 closed — engine diff vs baseline is empty; net diff is 14 files, all ORC-78-scoped; `minimal-diffs` rule satisfied |
| Algorithm / Correctness | 9/10 | Rename correct; regression tests genuinely exercise producer/consumer resolution |
| Security | 9/10 | Contract-name rename; no security surface touched |
| Simplicity | 9/10 | Minimal atomic rename; the non-minimal `c494287` surface is now reverted out |
| Code Quality (DRY) | 9/10 | Test fixtures updated; bugfix regression test mirrors the feature one |
| **Overall** | **9/10** | min of dimensions; all-green at `green_base` = 9 |

## How T-6 closed the attempt-1 finding

Attempt 1 scored 7/10 needs_work on one important finding: `c494287`
("fix: legacy active-plan readiness and repeat_until redispatch") bundled an
out-of-scope engine change (`dispatch.py` + `readiness.py`, ~65 lines) onto the
bugfix branch, violating design.md's Non-Goal "No engine change."

T-6 (`18ed778`) reverts `c494287` via `git revert`. Verified:

- **Engine diff is EMPTY**:
  `git diff 28dd8a0..HEAD -- config/scripts/orchestrator_next/dispatch.py config/scripts/orchestrator_next/readiness.py`
  produces zero output. Both engine files are byte-identical to the true
  baseline `28dd8a0`. `c494287` remains in history but its net effect is nil.
- **Net tree diff is 14 files, all ORC-78-scoped**: `git diff --stat 28dd8a0..HEAD`
  shows only `diagnose.yaml`, `design-and-draft-artifacts.yaml`, the
  `diagnosis.md → discovery.md` template rename, doc/skill/agent callsites, test
  fixtures, and the two ORC-78 regression tests in `test_prose_contracts.py`. No
  engine file present. The `minimal-diffs` rule is satisfied.

## Verification (re-run independently)

- **pytest** `config/scripts/orchestrator_next/tests/ -q`: **486 passed, 0 failed**.
  - Test-count delta vs attempt 1 (488 → 486) is **expected**: T-6's revert of
    `c494287` removed the two readiness tests that commit had added. 486
    (= `main` + the two ORC-78 regression tests) is the correct post-revert
    green count. No test that passed before now fails.
- **Shell test** `config/tests/test-archive-merges-worktree-artifacts.sh`:
  **7/7 PASS** (run sandbox-disabled — the test writes to absolute `/repo` and
  `/worktree` paths via `mktemp`/`mkdir`, which the sandbox blocks; the failure
  signature is identical on baseline `28dd8a0`, confirming it is environmental,
  not a defect). The test correctly checks `discovery.md` (T-4 fixture update).
- **Baseline comparison** (true baseline `28dd8a0`): no test passing at baseline
  now fails. Zero regressions — bugfix tolerance met.

## ORC-78 fix intact after the revert

The revert did not touch the ORC-78-proper rename. Verified:

- `config/steps/diagnose.yaml`: emits `outputs: [discovery_result]`;
  instruction/verify/COMPLETION name `discovery.md` (lines 51/61/65/72).
  `grep -c "diagnosis_result\|diagnose.md"` = 0.
- `config/steps/design-and-draft-artifacts.yaml`: `inputs: [discovery_result]`.
- `config/templates/bugfix/discovery.md` exists; `diagnosis.md` absent
  (`git mv`, similarity 100%).
- `agents/discoverer.md`: both explore and diagnose COMPLETION blocks declare
  `discovery_result: {path: "discovery.md"}`.

## Acceptance Criteria (design.md §149)

| AC | Status | Evidence |
|----|--------|----------|
| AC-1 regression test fails on HEAD, passes after rename | PASS | `test_feature_schema_required_inputs_have_a_producer` walks the feature schema, accumulates per-contract `outputs:`, asserts every required `inputs:` has a producer — fails when `design-and-draft-artifacts` declares `diagnosis_result` (no producer), passes after the rename |
| AC-2 `diagnose.yaml` declares `outputs: [discovery_result]`, names `discovery.md` | PASS | `grep -c "diagnosis_result\|diagnose.md" config/steps/diagnose.yaml` = 0; lines 51/61/65/72 name `discovery_result`/`discovery.md` |
| AC-3 `design-and-draft-artifacts.yaml` declares `inputs: [discovery_result]` | PASS | line 12 = `- discovery_result` |
| AC-4 template renamed via `git mv`, `diagnosis.md` gone | PASS | `discovery.md` exists, `diagnosis.md` absent; `git diff --stat` shows `{diagnosis.md => discovery.md}` rename, 0 line changes |
| AC-5 zero stale `diagnosis_result`/`diagnose.md`/`diagnosis.md` refs in `config/ skills/ agents/` | PASS | `grep -rn` returns ZERO functional matches. The only hits — 4 lines in `tests/test_orc36_path_consolidation.py` — are historical ORC-36 docstring references to the legacy `diagnose.md` artifact, explicitly covered by design.md Non-Goals ("No change to ORC-36 historical docstrings"). Not phase-opening-artifact references. |
| AC-6 feature/spike clears the pre-check after rename | PASS | both `test_feature_schema_required_inputs_have_a_producer` and `test_bugfix_schema_required_inputs_have_a_producer` pass — they confirm the resolution exit-0 path |
| AC-7 full suite green, zero new failures | PASS | 486 passed / 0 failed; baseline comparison confirms no regression; shell test 7/7 |

All 7 ACs pass with primary evidence.

### Regression tests genuinely exercise the bug

`test_feature_schema_required_inputs_have_a_producer` and
`test_bugfix_schema_required_inputs_have_a_producer` (in
`config/scripts/orchestrator_next/tests/test_prose_contracts.py`) load the
respective workflow schema, iterate step IDs in order, accumulate each
contract's declared `outputs:` (plus bootstrap state keys and inline runtime
producers) into an `available` set, and assert every required `inputs:` entry
resolves to a producer. This is exactly the producer/consumer resolution the bug
broke — `design-and-draft-artifacts` consuming `diagnosis_result` with no
upstream producer. Both pass at HEAD.

## Task completion

All 6 tasks in `tasks.md` (T-1…T-6) are `[x]`; 0 unchecked. Not an
`incomplete_phase`. No `quarantine_events` — quarantine review skipped.

## Findings

No findings. F-1 from attempt 1 is closed (see "How T-6 closed the attempt-1
finding"). No new issues surfaced in re-review.

## Scoring notes

- All five dimensions all-green at `green_base` = 9.
- Overall = min(dimensions) = 9.
- First-pass +1 bonus NOT available — this is review attempt 2 (a retry was
  used). Max overall this round = 9.
- 9 ≥ `quality_bar.min_phase_review_score` (9) AND zero critical findings → PASS.
- Baseline comparison (5b): bugfix archive `review_score_avg` ≈ 8.75; current 9
  is within range — no deviation warning.
