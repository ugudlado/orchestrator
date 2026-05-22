## Phase Review: ORC-70 — Remove Dead `include:` Mechanism

**Date:** 2026-05-22
**Schema:** bugfix · tdd_required=true · complexity=XS
**Commits reviewed:** 1f6100f, 3fb2dde

---

### Tasks Completeness

tasks.md contains no unchecked `- [ ]` items (tasks.md uses section headers + verify commands, not checkbox items). Phase is complete.

---

### Verification Results

#### AC 1: `include:` mechanism gone from generate_plan.py
```
grep -n "_load_include_phase\|include" config/scripts/orchestrator_next/generate_plan.py
```
Result: 0 matches (exit=1). PASS.

#### AC 2: Include-target workflow files deleted
```
ls config/workflows/_complete-phase*.yaml 2>/dev/null
```
Result: no output, exit=1 (no matches — zsh glob non-match). PASS.

#### AC 3: complete-phase-spike test deleted
```
ls config/workflows/__tests__/complete-phase-spike.test.sh 2>/dev/null
```
Result: no output, exit=1. PASS.

#### AC 4: complete-phase-order test deleted
```
ls config/tests/test-complete-phase-order.sh 2>/dev/null
```
Result: no output, exit=1. PASS.

#### AC 5: No shipping schema uses `phases:` key
```
grep -rn "^phases:" config/workflows/feature.yaml config/workflows/bugfix.yaml config/workflows/spike.yaml config/workflows/bootstrap.yaml
```
Result: 0 matches (exit=1). PASS — dead-code premise confirmed.

#### AC 6: generate_plan + schema-load tests pass
```
python -m pytest config/scripts/orchestrator_next/tests/test_generate_plan.py config/scripts/orchestrator_next/tests/test_workflow_schemas_load.py -v
```
Result: 15 passed, 0 failed. `test_include_phase_resolved` not collected. PASS.

#### AC 7: Full suite has no new failures
```
python -m pytest config/scripts/orchestrator_next/tests/ --tb=short
```
Result: 480 passed, 5 failed. All 5 failures match pre-existing baseline:
- `test_smoke_post_migration`
- `test_dispatch_no_path3`
- `test_dispatch_pending_row` (x2)
- `test_dispatch_resume`

No new failures introduced by ORC-70. PASS.

#### Stale references check
```
grep -rn "_complete-phase\|include:" config/grammar.yaml config/scripts/orchestrator_next/record.py
```
Result: 0 matches. PASS.

---

### Scope Check

Files changed vs. main (10 files):
- `config/grammar.yaml` — removed `include: string` grammar line
- `config/scripts/orchestrator_next/generate_plan.py` — deleted `_load_include_phase`, removed `if "include"` arm
- `config/scripts/orchestrator_next/record.py` — updated stale comment
- `config/scripts/orchestrator_next/tests/test_generate_plan.py` — removed `test_include_phase_resolved`
- `config/scripts/orchestrator_next/tests/test_workflow_schemas_load.py` — removed `if "include" in phase:` branch from helper
- `config/tests/test-complete-phase-order.sh` — deleted
- `config/workflows/__tests__/complete-phase-spike.test.sh` — deleted
- `config/workflows/__tests__/spike.test.sh` — deleted
- `config/workflows/_complete-phase-spike.yaml` — deleted
- `config/workflows/_complete-phase.yaml` — deleted

All 10 changes are within the design.md removal targets. Zero scope creep.

---

### Dimension Scores

| Dimension | Score | Notes |
|-----------|-------|-------|
| Spec Compliance | 10/10 | All 7 ACs pass with evidence; every removal target in design.md table is gone |
| Correctness | 10/10 | Dead-code premise confirmed via AC5; 15 targeted tests pass; 480 tests pass with 0 new regressions |
| Security | 10/10 | No security surface area; removal reduces attack surface marginally (dead file-load with no input validation gone) |
| Simplicity | 10/10 | Pure deletion; no new abstractions; loop body simplified from 5 lines (if/else) to 1 (`resolved.append(phase_entry)`) |
| Code Quality | 10/10 | Docstrings updated to remove references to deleted behavior; comment in generate_plan() cleaned; no dead references remain |
| **Overall** | **10/10** | First-pass, no retries, all green |

Score of 10 is the first-pass bonus: no retries this round, all dimensions exceed minimums, no TODOs.

---

### Critical Issues

None.

### Important Issues

None.

### Minor Issues

None.

---

### Verdict: PASS

Overall score 10/10 >= min_phase_review_score 9. Zero regressions. All acceptance criteria verified with evidence. Change is a surgical, well-scoped dead-code removal with no behavioral impact on any shipping schema.
