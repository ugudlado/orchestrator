# Tasks: ORC-70 — Remove Dead `include:` Mechanism

Schema: bugfix · TDD required · Complexity: XS

All paths are relative to the worktree root
`/Users/spidey/code/feature_worktrees/orc-70`. Run commands from there.

> **TDD note (bugfix + tdd_required + mechanical deletion):** This is a pure
> dead-code removal with no behavioral change. Per the learned rule — when
> `tdd_required=true` AND the task is a pure mechanical change, satisfy the
> requirement with a regression-guard test sequenced first rather than a
> fabricated failing test — T-1 is the regression guard that proves the
> removal is safe, and T-2 is the fix.

---

## T-1: Regression guard — confirm no shipping schema uses the `phases:` key

**Type:** regression test (must run first — proves the dead-code premise)

**Description:** Confirm the four shipping schemas use the flat `steps:` shape
with no top-level `phases:` key. This is the entire premise of the removal: the
`include:` branch is only reachable when a schema has `phases:`. If any schema
has it, the removal is unsafe and the change must stop.

**Files (read-only):**
- `config/workflows/feature.yaml`
- `config/workflows/bugfix.yaml`
- `config/workflows/spike.yaml`
- `config/workflows/bootstrap.yaml`

**Verify:**
```bash
grep -rn "^phases:" config/workflows/feature.yaml config/workflows/bugfix.yaml \
  config/workflows/spike.yaml config/workflows/bootstrap.yaml
```
→ no output, exit code 1. Confirms the `include:` branch is unreachable and the
removal is safe to proceed.

---

## T-2: Remove `_load_include_phase` and the `include:` branch from generate_plan.py

**Type:** fix

**Description:** Delete the dead `_load_include_phase` function (lines 49-56)
and the `if "include" in phase_entry:` arm inside `_resolve_phases`
(lines 110-113). Keep the `else` path — after removal the loop body becomes a
plain `resolved.append(phase_entry)` for every entry. Also scrub the two stale
`include:` references that the verify grep below will otherwise flag:
1. The `_resolve_phases` docstring — drop the "Inline-expands `include:`
   entries" sentence, since that behavior no longer exists.
2. The comment inside `generate_plan()` (currently
   `# Resolve phases — expand include: _<name> directives inline`) — drop the
   `include:` clause so it reads e.g. `# Resolve schema phases`.

**Files:**
- `config/scripts/orchestrator_next/generate_plan.py`

**Verify:**
```bash
grep -n "_load_include_phase\|include" config/scripts/orchestrator_next/generate_plan.py
```
→ 0 matches.

---

## T-3: Delete dead workflow files, dead tests, and stale references

**Type:** fix (cleanup)

**Description:** Remove all remaining dead artifacts and references that exist
only because of the `include:` mechanism.

**Files to delete:**
- `config/workflows/_complete-phase.yaml`
- `config/workflows/_complete-phase-spike.yaml`
- `config/tests/test-complete-phase-order.sh` (tests the dead `_complete-phase.yaml`)
- `config/workflows/__tests__/complete-phase-spike.test.sh` (tests the dead `_complete-phase-spike.yaml`)
- `config/workflows/__tests__/spike.test.sh` (already failing; asserts a `phases:`/`include:` shape spike.yaml never had — deleted, not rewritten, per design.md non-goals)

**Files to edit:**
- `config/scripts/orchestrator_next/tests/test_generate_plan.py` — delete the
  `test_include_phase_resolved` function and its section-header comment block.
- `config/scripts/orchestrator_next/tests/test_workflow_schemas_load.py` —
  remove the `if "include" in phase:` branch from `_resolve_phases_for_test`
  (the helper keeps only the `else: out.append(phase)` path; update the
  docstring to drop "expanding `include:` entries").
- `config/grammar.yaml` — remove the `include: string` line (line 63) under
  the phase `optional:` grammar.
- `config/scripts/orchestrator_next/record.py` — update the comment near
  line 1724 that references `_complete-phase.yaml` so it no longer names the
  deleted file (keep the surrounding logic explanation intact).

**Verify:**
```bash
ls config/workflows/_complete-phase*.yaml 2>/dev/null; echo "exit=$?"
ls config/workflows/__tests__/complete-phase-spike.test.sh config/workflows/__tests__/spike.test.sh config/tests/test-complete-phase-order.sh 2>/dev/null; echo "exit=$?"
grep -rn "_complete-phase\|include:" config/grammar.yaml config/scripts/orchestrator_next/record.py
python -m pytest config/scripts/orchestrator_next/tests/test_generate_plan.py config/scripts/orchestrator_next/tests/test_workflow_schemas_load.py
```
→ both `ls` commands print nothing and report `exit=2` (no such file);
`grep` finds no `_complete-phase` / phase-`include:` references; pytest passes
with `test_include_phase_resolved` no longer collected.

---

## T-4: Run full test suite and confirm no regressions

**Type:** verification

**Description:** Run the full test suite and confirm the change introduces zero
new failures. The 5 pre-existing failures recorded in diagnose.md
(`test_smoke_post_migration`, `test_dispatch_no_path3`,
`test_dispatch_pending_row` x2, `test_dispatch_resume`) are unrelated and
acceptable as a known baseline.

**Files:** none (verification only)

**Verify:**
```bash
cd /Users/spidey/code/feature_worktrees/orc-70 && \
  python -m pytest config/tests/ --tb=short 2>&1 | tail -20
```
→ failure count is ≤ 5 and every failure name matches the pre-existing
baseline set from diagnose.md. No new failure attributable to ORC-70.
