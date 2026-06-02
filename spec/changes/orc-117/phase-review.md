# Phase Review — ORC-117: Remove flags system from codebase

**Date:** 2026-06-02
**Phase:** implement
**Verdict:** PASS
**Overall score:** 9

---

## Summary

All 5 tasks completed. The flags system has been fully removed from non-test source
code: `seed_parse_overrides.py` no longer constructs or emits a `flags` JSON key,
`seed_write_state.py` no longer writes `flags` to state.yaml, and `generate_plan.py`
has had `_evaluate_rules_when`, the `when:` named-rule filter, `verify_when`, and all
`flags` parameter threading removed. Test fixtures were cleaned of inert `flags: {}`
entries. The test suite runs with 3 failures — all within the named baseline set.

---

## Pre-flight: Fixture dirt check

```
git diff HEAD -- tests/fixtures/
```
**Output:** clean (no fixture mutations by dispatcher).

---

## Task status check

All tasks `completed`. No pending tasks remain. No quarantine events in state.yaml.

| Task | Title | Status |
|------|-------|--------|
| T-1 | Remove flags from the seed pipeline (parse + write) | completed |
| T-2 | Remove flags machinery from generate_plan.py | completed |
| T-3 | Update generate_plan tests coupled to removed flags machinery | completed |
| T-4 | Sweep inert flags keys from state-shaped test fixtures | completed |
| T-5 | Phase gate — full suite at baseline parity, no flags symbols remain | completed |

---

## AC Verification

### AC-1 + AC-2: Seed pipeline no longer emits or writes `flags`

```
python orchestrator_next/scripts/lib/seed_parse_overrides.py x feature . config/workflows/feature.yaml \
  | python -c "import json,sys; d=json.load(sys.stdin); print('flags in output:', 'flags' in d); print('keys:', list(d.keys()))"
```
**Output:** `flags in output: False` / `keys: ['slug', 'schema_name', 'repo_root', 'active', 'filtered']`

Full pipe-through verify (T-1 verify command):
**Output:** `seed pipeline ok, no flags`

**Result: PASS**

### AC-3: generate_plan.py is flags-free

```
python -c "import inspect; import orchestrator_next.generate_plan as g; src=inspect.getsource(g);
assert '_evaluate_rules_when' not in src; assert 'verify_when' not in src;
assert 'flags' not in inspect.signature(g._merge_rules).parameters;
assert 'flags' not in inspect.signature(g._build_step_block).parameters; print('generate_plan flags-free')"
```
**Output:** `generate_plan flags-free`

Symbol sweep (non-test source):
```
grep -rn 'rules_when|verify_when|_evaluate_rules_when' orchestrator_next --include='*.py' | grep -v test
```
**Output:** (empty — zero hits)

Remaining `\bflags\b` in non-test source (2 hits, both cosmetic):
- `state_inspect.py:260` — comment about subprocess flags (unrelated)
- `doctor.py:418` — comment about argparse flags (unrelated)

Both confirmed false positives per design.md Context.

**Result: PASS**

### AC-4: Test suite at baseline parity

```
pytest orchestrator_next/tests/ -q
```
**Failures observed (3 total):**
1. `test_step_runner.py::test_capture_test_baseline_script_uses_step_dir_env` ← in baseline
2. `test_workflow_schemas_load.py::test_schema_ends_at_expected_terminal[feature-ticket-qa]` ← in baseline
3. `test_workflow_schemas_load.py::test_schema_ends_at_expected_terminal[bugfix-ticket-qa]` ← in baseline

`test_agent_runner.py` tests: `1 xfailed` (not FAILED — ORC-118 committed them
with `@pytest.mark.xfail`, they appear as xfailed not as failures).

AC-4 explicitly requires:
- `test_generate_plan.py` — 15 passed ✓
- `test_generate_plan_directory_layout.py` — included, 15 passed ✓
- `test_workflow_schemas_load.py` terminal-step failures — in named baseline ✓

**Result: PASS** — no failures outside the named baseline set.

---

## Scoring

### Scoring config
- critical_cap: 5 | important_cap: 7 | green_base: 9.25

### Findings

**None.** No critical or important findings.

### Dimension scores

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| spec_compliance | 9 | All 4 ACs verified with evidence. Non-Goals respected (resolved_flags untouched, doctor.py/state_inspect.py cosmetic hits preserved). Design.md structural contract fully satisfied. |
| correctness | 9 | No new failures. Symbol sweep clean. Pipe-through verify confirms no runtime KeyError in seed path. Legacy state.yaml with `flags:{}` is harmless (UC-E1 — unread key). |
| security | 9 | Pure deletion. No new attack surface. |
| simplicity | 9 | Strictly subtractive. No new abstractions introduced. Three source files simplified, two test files updated. |
| code_quality | 9 | Clean atomic commits per task (T-1 through T-4 on main). Green at every commit boundary. Fixture cleanup keeps suite honest. |

**Overall = min(9, 9, 9, 9, 9) = 9**

---

## Baseline comparison

Historical avg `review_score_avg` for feature schema archives: **7.69** (7 entries).
Current score 9 is 1.31 above average — no regression.

---

## Verdict: PASS

Score **9** meets `min_phase_review_score: 9`. No critical findings. No important findings.
Workflow advances to `ticket-qa`.
