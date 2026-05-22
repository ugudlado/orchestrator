# Diagnosis: ORC-70 — Remove Dead include: Mechanism and _complete-phase*.yaml

## Symptom

Dead, unreachable code exists across three locations:

1. `generate_plan.py` contains `_load_include_phase` (lines 49-56) and the `include:` branch in `_resolve_phases` (lines 110-113) — code that only fires when a schema has a top-level `phases:` key containing an entry with an `include:` directive.
2. `config/workflows/_complete-phase.yaml` and `_complete-phase-spike.yaml` exist as include targets for that dead branch.
3. Two test files assert structure on these dead files: `config/tests/test-complete-phase-order.sh` and `config/workflows/__tests__/complete-phase-spike.test.sh`.
4. A third test file (`config/workflows/__tests__/spike.test.sh`) is **already failing** because it asserts spike.yaml has `phases: [{include: _complete-phase-spike}]` — but spike.yaml is a flat `steps:` schema with no `phases:` key.

## Reproduction

**Command (copy-pasteable):**

```bash
cd /Users/spidey/code/orchestrator

# Confirm no shipping schema uses phases: key (the only trigger for the include: branch)
for f in config/workflows/feature.yaml config/workflows/bugfix.yaml \
          config/workflows/spike.yaml config/workflows/bootstrap.yaml; do
  echo -n "$f: "
  grep -c "^phases:" "$f" && echo "HAS phases:" || echo "no phases:"
done

# Confirm the include: branch exists but can never fire
grep -n "include:" config/scripts/orchestrator_next/generate_plan.py

# Confirm spike.test.sh is already failing (tests a schema shape that doesn't exist)
bash config/workflows/__tests__/spike.test.sh; echo "Exit: $?"
```

**Expected output:** All four schemas show `0` / `no phases:`. The `include:` grep shows lines 110-111. spike.test.sh exits non-zero.

**Actual output (observed):**

```
config/workflows/feature.yaml: no phases:
config/workflows/bugfix.yaml: no phases:
config/workflows/spike.yaml: no phases:
config/workflows/bootstrap.yaml: no phases:

generate_plan.py:110:        if "include" in phase_entry:
generate_plan.py:111:            include_name = phase_entry["include"]  # e.g. "_complete-phase"

FAIL: spike.yaml has a complete phase — got '', expected 'complete'
FAIL: complete phase include is _complete-phase-spike — got '', expected '_complete-phase-spike'
...
Results: 3 passed, 2 failed
Exit: 1
```

## Root Cause

**File:** `config/scripts/orchestrator_next/generate_plan.py`

**Lines 49-56 (`_load_include_phase`):**

```python
def _load_include_phase(include_name: str) -> dict[str, Any]:
    """Load a _<name>.yaml include phase file from $ORCHESTRATOR_HOME/config/workflows/."""
    home = _orchestrator_home()
    path = home / "config" / "workflows" / f"{include_name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Include phase file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
```

**Lines 108-115 (the `include:` branch in `_resolve_phases`):**

```python
resolved: list[dict[str, Any]] = []
for phase_entry in raw_phases:
    if "include" in phase_entry:            # line 110 — dead branch
        include_name = phase_entry["include"]  # e.g. "_complete-phase"
        included = _load_include_phase(include_name)
        resolved.append(included)
    else:
        resolved.append(phase_entry)
return resolved
```

**Why it's unreachable:** `_resolve_phases` only reaches the loop at line 108 when `schema.get("phases")` is truthy (line 93). All four shipping schemas — `feature.yaml`, `bugfix.yaml`, `spike.yaml`, `bootstrap.yaml` — use the flat `steps:` shape with no `phases:` key. The guard at lines 94-106 synthesizes a single `"main"` phase and returns early. The `for phase_entry in raw_phases` loop, and the `include:` branch within it, are therefore unreachable from any current schema.

The `_complete-phase*.yaml` files are valid YAML that could be loaded, but there is no caller path that reaches `_load_include_phase`.

**Secondary dead code confirmed:**

- `config/workflows/_complete-phase.yaml` — include target, never loaded at runtime
- `config/workflows/_complete-phase-spike.yaml` — include target, never loaded at runtime
- `config/grammar.yaml` line 63 — documents `include: string` as a valid phase field

**Tests that are dead or already failing:**

| File | Status | What it tests |
|------|--------|---------------|
| `config/tests/test-complete-phase-order.sh` | Passes (tests dead file directly) | Step ordering inside `_complete-phase.yaml` |
| `config/workflows/__tests__/complete-phase-spike.test.sh` | Passes (tests dead file directly) | Step structure of `_complete-phase-spike.yaml` |
| `config/workflows/__tests__/spike.test.sh` | **Already FAILING** | Asserts spike.yaml has `phases: [{include: _complete-phase-spike}]` — spike.yaml has no `phases:` key |
| `config/scripts/orchestrator_next/tests/test_generate_plan.py::test_include_phase_resolved` | Passes (tests include: with an ad-hoc schema) | The include mechanism itself, using a synthetic schema — would need removal or update |

**test_include_phase_resolved** (lines 438-502 in test_generate_plan.py) creates a synthetic schema with a `phases: [{include: "_complete-phase"}]` entry and writes a temporary `_complete-phase.yaml` to a temp workflows dir. It tests the mechanism, not a real schema. After removal it becomes dead-test coverage of a removed feature and should be deleted.

## Impact

All callers and dependents identified:

| Location | Type | Impact on removal |
|----------|------|-------------------|
| `generate_plan.py:49-56` | Dead function | Delete entirely |
| `generate_plan.py:108-115` | Dead branch in `_resolve_phases` | Delete the `if "include"` arm; keep the `else` path |
| `config/workflows/_complete-phase.yaml` | Dead file | Delete |
| `config/workflows/_complete-phase-spike.yaml` | Dead file | Delete |
| `config/tests/test-complete-phase-order.sh` | Tests dead file | Delete |
| `config/workflows/__tests__/complete-phase-spike.test.sh` | Tests dead file | Delete |
| `config/workflows/__tests__/spike.test.sh` | Already failing; tests dead schema shape | Delete or replace with a test of spike.yaml's actual flat `steps:` structure |
| `config/scripts/orchestrator_next/tests/test_generate_plan.py::test_include_phase_resolved` | Tests removed mechanism | Delete the test function |
| `config/scripts/orchestrator_next/tests/test_workflow_schemas_load.py:_resolve_phases_for_test` | Helper mirrors the include: expansion | Remove the `if "include" in phase:` branch from this helper (lines 59-66); the four user-facing schemas never trigger it |
| `config/grammar.yaml:63` | Documents the `include:` field | Remove the `include: string` line |
| `config/scripts/orchestrator_next/record.py:1724` | Comment mentioning `_complete-phase.yaml` | Update the comment to remove the reference |

**Existing tests that must still pass after removal:**

- All 12 tests in `test_generate_plan.py` except `test_include_phase_resolved` (delete that one)
- All 4 parametrized tests in `test_workflow_schemas_load.py` (feature, bugfix, spike, bootstrap)
- The 5 pre-existing non-related test failures are unrelated to this change (verified: `test_smoke_post_migration`, `test_dispatch_no_path3`, `test_dispatch_pending_row` x2, `test_dispatch_resume`)

## Proposed Approach

Delete both `_complete-phase*.yaml` files, remove `_load_include_phase` and the `include:` branch from `_resolve_phases` in `generate_plan.py`, delete or update all three shell tests, delete `test_include_phase_resolved` from the Python test file, strip the `include:` branch from `_resolve_phases_for_test` in `test_workflow_schemas_load.py`, remove the `include: string` line from `grammar.yaml`, and update the comment in `record.py`.

## Unresolved Questions

None. The scope is fully bounded by grep evidence. The 5 pre-existing test failures in the broader suite are unrelated and pre-date this change.

## Baseline Test State

Before any change:

- `test_generate_plan.py` + `test_workflow_schemas_load.py`: **16 passed, 0 failed**
- Full suite: **481 passed, 5 failed** (pre-existing failures unrelated to ORC-70)
- `config/tests/test-complete-phase-order.sh`: **13 passed, 0 failed**
- `config/workflows/__tests__/complete-phase-spike.test.sh`: **5 passed, 0 failed**
- `config/workflows/__tests__/spike.test.sh`: **3 passed, 2 failed** (already broken before this change)
