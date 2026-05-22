---
change_id: orc-70
schema: bugfix
title: Remove dead include: phase-loading mechanism
design_direction: Surgical Dead-Code Removal
complexity: XS
---

# Design: ORC-70 — Remove Dead `include:` Mechanism

## Goal

Remove the `include:` phase-loading mechanism from the workflow engine. It is
dead, unreachable code: no shipping schema (`feature`, `bugfix`, `spike`,
`bootstrap`) uses a top-level `phases:` key, which is the only trigger for the
`include:` branch. The two include-target files (`_complete-phase.yaml`,
`_complete-phase-spike.yaml`) are never loaded at runtime, and the tests that
assert on them either test dead files directly or are already failing because
they assert a schema shape that no longer exists.

## Root Cause (from diagnose.md)

`_resolve_phases` in `generate_plan.py` only reaches the phase-loop at line 108
when `schema.get("phases")` is truthy. All four shipping schemas use the flat
`steps:` shape with no `phases:` key, so the early-return guard (lines 94-106)
synthesizes a single `main` phase and the `for phase_entry in raw_phases` loop —
and the `include:` branch inside it — is unreachable. `_load_include_phase` has
no live caller.

## Approach: Surgical Dead-Code Removal

Mechanical deletion of unreachable code paths and their dead dependents. No
behavioral change: every shipping schema produces an identical plan before and
after, because the deleted branch never executed for any of them.

Removal targets (all confirmed dead by grep evidence in diagnose.md):

| Location | Action |
|----------|--------|
| `generate_plan.py:49-56` `_load_include_phase` | Delete function |
| `generate_plan.py:108-115` `include:` branch in `_resolve_phases` | Delete `if "include"` arm; keep the `else` (the loop becomes `resolved.append(phase_entry)` for every entry) |
| `config/workflows/_complete-phase.yaml` | Delete file |
| `config/workflows/_complete-phase-spike.yaml` | Delete file |
| `config/tests/test-complete-phase-order.sh` | Delete file (tests a dead file) |
| `config/workflows/__tests__/complete-phase-spike.test.sh` | Delete file (tests a dead file) |
| `config/workflows/__tests__/spike.test.sh` | Delete file (already failing; asserts a `phases:`/`include:` shape spike.yaml never had) |
| `test_generate_plan.py::test_include_phase_resolved` | Delete test function (tests the removed mechanism via a synthetic schema) |
| `test_workflow_schemas_load.py::_resolve_phases_for_test` | Remove the `if "include" in phase:` branch from the helper |
| `config/grammar.yaml:63` | Remove the `include: string` grammar line |
| `config/scripts/orchestrator_next/record.py:1724` | Update the comment that references `_complete-phase.yaml` |

## Non-goals

- No refactoring of `_resolve_phases` beyond removing the dead `include:`
  branch. The phase-loop and the flat-`steps:` synthesis path stay as-is.
- No DAG-epic (ORC-63/64/65) changes. The `nodes`-shape promotion is untouched.
- No replacement test for `spike.yaml`. `spike.test.sh` is deleted, not
  rewritten — writing a new flat-`steps:` test for spike.yaml would be scope
  creep into unrelated coverage. The regression guard (AC 5 below / T-1)
  already asserts spike.yaml has no `phases:` key.
- No fix for the 5 pre-existing, unrelated test failures
  (`test_smoke_post_migration`, `test_dispatch_no_path3`,
  `test_dispatch_pending_row` x2, `test_dispatch_resume`). They pre-date this
  change and are out of scope.

## Acceptance Criteria

All commands run from the worktree root
`/Users/spidey/code/feature_worktrees/orc-70`.

1. **`include:` mechanism gone from generate_plan.py**
   `grep -n "_load_include_phase\|include" config/scripts/orchestrator_next/generate_plan.py`
   → 0 matches.

2. **Include-target workflow files deleted**
   `ls config/workflows/_complete-phase*.yaml 2>/dev/null` → no files (non-zero exit).

3. **complete-phase-spike test deleted**
   `ls config/workflows/__tests__/complete-phase-spike.test.sh 2>/dev/null` → no file.

4. **complete-phase-order test deleted**
   `ls config/tests/test-complete-phase-order.sh 2>/dev/null` → no file.

5. **No shipping schema uses the `phases:` key (dead-code premise holds)**
   `grep -rn "^phases:" config/workflows/feature.yaml config/workflows/bugfix.yaml config/workflows/spike.yaml config/workflows/bootstrap.yaml`
   → no output (confirms the removed branch was genuinely unreachable).

6. **generate_plan + schema-load tests pass with the include test removed**
   `python -m pytest config/scripts/orchestrator_next/tests/test_generate_plan.py config/scripts/orchestrator_next/tests/test_workflow_schemas_load.py`
   → all pass; `test_include_phase_resolved` no longer collected.

7. **Full suite has no new failures**
   `python -m pytest config/tests/` → no failures beyond the 5 pre-existing
   unrelated baseline failures recorded in diagnose.md.

## Risk

Minimal. The only behavior-change risk would be a schema that uses `phases:` +
`include:` — AC 5 / T-1 is the explicit regression guard that proves none does.
The deleted shell tests and `test_include_phase_resolved` cover only the removed
code, so deleting them is correct, not a coverage loss.
