# Diagnosis: Add started_at to seed-state.sh canonical state.yaml

## Symptoms

`orchestrator done` exits non-zero during the `mark-change-completed` → `compute-swe-metrics` step
sequence with the following error on stderr:

```
[done] feature_metrics resolution failed: _resolve_feature_metrics: state missing started_at/completed_at for schema=bugfix
```

The `step_history` entry for the failing step records:

```yaml
non_fatal_warnings:
  - reason: feature_metrics_resolution_failed
```

## Reproduction Steps

1. Seed a fresh bugfix state with `seed-state.sh`:

```bash
TMPDIR=$(mktemp -d)
mkdir -p "$TMPDIR/repo/spec" "$TMPDIR/state"
python3 -c "
import yaml
project = {
    'version': 1,
    'project': {'name': 'test-repo', 'repo': 'test-repo', 'summary': 'Integration test project'},
    'rules': [],
    'verify_commands': {'test': 'pytest'},
}
with open('$TMPDIR/repo/spec/project.yaml', 'w') as f:
    yaml.safe_dump(project, f)
"
REPO_ROOT="$TMPDIR/repo" \
WORKFLOW_STATE_DIR="$TMPDIR/state" \
ORCHESTRATOR_HOME="$HOME/.config/orchestrator" \
PYTHONPATH="/path/to/orchestrator/config/scripts" \
bash /path/to/orchestrator/skills/orchestrate/scripts/seed-state.sh repro-orc-34 bugfix
```

2. Inspect the seeded state.yaml:

```bash
cat "$TMPDIR/state/repro-orc-34/state.yaml"
```

3. Observe `created_at` is present but `started_at` is absent:

```
grep "started_at" "$TMPDIR/state/repro-orc-34/state.yaml"
# Output: (empty — NOT FOUND)
```

4. At `mark-change-completed` time, `orchestrator done` calls `_resolve_feature_metrics(state, change_id)` in `record.py`, which raises `RuntimeError` because `state.get("started_at")` is falsy.

## Expected vs Actual

- **Expected**: `state.yaml` produced by `seed-state.sh` contains both `created_at` and `started_at` set to the same ISO-8601 UTC timestamp (matching the seeding moment), allowing `_resolve_feature_metrics` to proceed without error.
- **Actual**: `state.yaml` contains only `created_at`; `started_at` is absent. `_resolve_feature_metrics` raises `RuntimeError("state missing started_at/completed_at for schema=bugfix")` and the metrics step is skipped with a non-fatal warning in the step history.

## Investigation

### Evidence Gathered

- Read `skills/orchestrate/scripts/seed-state.sh` lines 222–238: the inline Python dict that writes `state.yaml` lists `created_at` but no `started_at` key.
- Read `config/scripts/orchestrator_next/record.py` lines 815–820: `_resolve_feature_metrics` raises `RuntimeError` when `state.get("started_at")` is falsy for `feature` or `bugfix` schemas.
- Confirmed with live reproduction: `seed-state.sh repro-orc-34 bugfix` exits 0, writes `state.yaml`, `grep started_at` finds nothing.
- ORC-27 autopilot run log (referenced in bug description): a manual edit added `started_at: '<created_at value>'` to unblock the workflow — confirming the gap was hit in production.
- Confirmed same gap reproduced on fresh orc-34 seed (the current state dir at `/Users/spidey/code/orchestrator/.state/orc-34/state.yaml` has `started_at` only because `workflow-init` wrote it during its own step; the seed-phase state was missing it).

### Data Flow Trace

1. `seed-state.sh` delegates state construction to an inline Python block (lines 124–244).
2. The inline Python builds a `state` dict (lines 223–238) and writes it with `yaml.safe_dump`.
3. The `state` dict includes `"created_at": datetime.now(timezone.utc).strftime(...)` at line 237, but no `started_at` key.
4. Later, during `mark-change-completed`, `orchestrator done` loads `state.yaml` and calls `_resolve_feature_metrics(state_raw, change_id_val)` (record.py line 1276).
5. `_resolve_feature_metrics` (record.py line 816) checks `state.get("started_at")` — this is `None` because the seed never wrote it — and raises `RuntimeError`.
6. The caller catches the error (record.py line 1278) and records `feature_metrics_resolution_failed` as a non-fatal warning.

## Root Cause

The inline Python block in `seed-state.sh` that constructs the canonical-minimum `state` dict omits `started_at` from the field set it writes to `state.yaml`.

Reference: `skills/orchestrate/scripts/seed-state.sh:237` (the `created_at` line in the `state = {...}` dict — `started_at` is simply not present in this dict)

The consumer `_resolve_feature_metrics` unconditionally requires `started_at` for `feature` and `bugfix` schemas.

Reference: `config/scripts/orchestrator_next/record.py:816`

## Impact

### Severity

high

### Affected Areas

- Every `bugfix` or `feature` workflow seeded via `seed-state.sh` that reaches `mark-change-completed` / `compute-swe-metrics`. The SWE metrics for the feature are silently skipped (non-fatal), so the DuckDB `feature_metrics` table is never populated for these features.
- `test_seed_state.py` does not currently assert `started_at` is present, so the test suite passes while the field is missing.

### Since When

Introduced when ORC-27 shipped `seed-state.sh` — commit `b4ea5df` (merge: ORC-27 document-or-script-state-seeding). The field was never in scope for ORC-27's acceptance criteria, so it was never added.

## Linear Ticket

none
