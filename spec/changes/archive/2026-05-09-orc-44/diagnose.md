# Diagnosis: ORC-44 — tool_calls key naming inconsistency

## Symptom

The `tool_calls` DuckDB table fan-out produces empty rows for workflow runs where
agents followed CONVENTIONS.md's usage format. The `/telemetry` and per-tool
cost breakdown reports show no tool attribution data.

## Reproduction

```bash
# 1. Create a minimal state.yaml with a step using "tools:" (per CONVENTIONS.md)
cat > /tmp/repro-state.yaml << 'EOF'
schema: bugfix
status: completed
change_id: repro-orc-44
started_at: "2026-05-09T10:00:00Z"
step_history:
  - step_id: execute-next-task
    phase: main
    status: completed
    agent: developer
    attempt: 1
    started_at: "2026-05-09T10:00:00Z"
    ended_at: "2026-05-09T10:30:00Z"
    usage:
      input_tokens: 5000
      output_tokens: 1000
      tool_uses: 7
      tools:          # <- "tools:" key as documented in CONVENTIONS.md
        Read: 3
        Bash: 2
        Edit: 1
        Grep: 1
      duration_ms: 18000
EOF

# 2. Trigger upsert
python3 -c "
import sys; sys.path.insert(0, 'config/scripts')
import yaml, duckdb, tempfile
from orchestrator_next.upsert import upsert_step_event, init_db
from orchestrator_next.parser import StepHistoryEntry

with open('/tmp/repro-state.yaml') as f:
    state = yaml.safe_load(f)

entry_data = state['step_history'][0]
entry = StepHistoryEntry(
    step_id=entry_data['step_id'],
    phase=entry_data['phase'],
    status=entry_data['status'],
    agent=entry_data['agent'],
    attempt=entry_data['attempt'],
    started_at=entry_data['started_at'],
    ended_at=entry_data['ended_at'],
    usage=entry_data['usage'],
    escalation=None,
)

with tempfile.NamedTemporaryFile(suffix='.duckdb', delete=False) as f:
    db_path = f.name

db = duckdb.connect(db_path)
init_db(db)
upsert_step_event(db, '/tmp', 'repro-orc-44', entry)
count = db.execute('SELECT COUNT(*) FROM tool_calls').fetchone()[0]
print(f'tool_calls rows: {count}')  # Expected: 7, Actual: 0
"
```

**Expected output:** `tool_calls rows: 7`
**Actual output:** `tool_calls rows: 0`

## Root Cause

**File:** `config/steps/CONVENTIONS.md` — lines 224 and 300
**File:** `config/scripts/test-fixtures/state-with-tools.yaml` — lines 37, 56, 89, 104

CONVENTIONS.md documents the per-tool breakdown key as `tools:`:
```yaml
usage:
  tool_uses: 7
  tools:          # <-- WRONG: documented as "tools:"
    Read: 3
    Bash: 2
```

However, `upsert.py` reads `usage.get("tool_calls")` exclusively:
- `config/scripts/orchestrator_next/upsert.py` line 484: `tool_calls_raw = usage.get("tool_calls")`
- `config/scripts/orchestrator_next/upsert.py` line 528: `isinstance(entry.usage.get("tool_calls"), dict)`

When an LLM agent follows CONVENTIONS.md and writes `tools: {Read: 3, ...}` into
state.yaml, `upsert.py` receives `None` for `tool_calls_raw` and silently writes
zero rows to the `tool_calls` table.

The canonical key is `tool_calls` — confirmed by:
- `metrics-schema.md` line 229: `usage.tool_calls`
- `orchestrate/SKILL.md` lines 190–196: `tool_calls` in example payload
- `grammar.yaml` line 183: `tool_calls` in usage map definition
- Real archived state.yaml files (e.g., `archive/2026-04-19-fix-inline-scripts-tmpdir/state.yaml`)
  which correctly use `tool_calls:`

The test fixture `state-with-tools.yaml` also uses `tools:` — meaning
`compute-swe-metrics.sh` reads from DuckDB (not state.yaml directly), so the
fixture is currently only used for a compute-swe-metrics test that doesn't go
through upsert. However the fixture is misleading and should also be corrected.

## Impact

1. **CONVENTIONS.md** (2 occurrences): Primary doc LLMs read to populate usage blocks.
   Any agent following this doc produces `tools:` instead of `tool_calls:`, causing
   silent zero rows in DuckDB `tool_calls` table.

2. **`config/scripts/test-fixtures/state-with-tools.yaml`** (4 occurrences): Test
   fixture documents the wrong key. If `compute-swe-metrics.sh` is ever refactored
   to read directly from state.yaml, this fixture would silently test nothing.

3. **Archived state.yaml files** (2026-04-17, 2026-04-18 archives): These already
   contain `tools:` — they represent historical data loss. No fix needed (archives
   are immutable), but the gap in historical tool attribution data is expected.

4. **`otel_map.py` mentioned in ticket**: `otel_map.py` does not exist in the codebase.
   The bug description likely refers to the canonical mapping defined in `upsert.py`
   and `metrics-schema.md`.

**Not affected:** `compute-swe-metrics.sh` reads from DuckDB views (not state.yaml
directly), so it's only indirectly impacted via missing upstream upsert rows.

## Proposed Approach

Update CONVENTIONS.md to rename `tools:` → `tool_calls:` in both the inline usage
example (line 224) and the step_history example (line 300). Update
`state-with-tools.yaml` to use `tool_calls:` to match. No changes to upsert.py
needed — it already uses the correct key.

## Unresolved Questions

None — the fix direction is clear. The two occurrences in CONVENTIONS.md plus the
test fixture are the complete scope.
