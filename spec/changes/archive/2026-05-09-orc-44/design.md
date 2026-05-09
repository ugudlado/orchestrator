# Design: tool_calls key rename in CONVENTIONS.md

## Approach Selected: Rename in prose + fixture (XS)

**Rationale:** The canonical key is `tool_calls` — confirmed by upsert.py, grammar.yaml,
metrics-schema.md, and existing archived state.yaml files. The only divergence is in
CONVENTIONS.md (documentation) and a test fixture. Renaming these two files is the
minimal correct fix with zero risk of regression.

**Rejected alternatives:**
- *Tolerate both keys in upsert.py*: adds permanent dead code for a transient doc bug;
  would silently accept misspelled keys going forward.
- *Migration script + validator*: over-engineered for a 2-file rename.

## Change Map

### File 1: `config/steps/CONVENTIONS.md`

Two occurrences of `tools:` in usage examples:

1. **Line ~224** — inline usage example block:
   ```yaml
   # Before:
     tool_uses: 7
     tools:
       Read: 3
   # After:
     tool_uses: 7
     tool_calls:
       Read: 3
   ```

2. **Line ~300** — step_history standard entry example:
   ```yaml
   # Before:
       tool_uses: 7
       tools:
         Read: 3
   # After:
       tool_uses: 7
       tool_calls:
         Read: 3
   ```

### File 2: `config/scripts/test-fixtures/state-with-tools.yaml`

Four occurrences of `      tools:` (6-space indent usage child key). Each usage block
in the fixture uses `tools:` — all must be renamed to `tool_calls:`. Also update the
file header comment to reflect the rename.

### File 3 (new): `config/scripts/tests/test_tool_calls_key_conventions.py`

Regression test verifying that a step_history entry with `tool_calls:` dict (exactly
as documented in CONVENTIONS.md after the fix) produces non-zero rows in DuckDB.
Must fail before the fix (using `tools:`) and pass after.

## Verification

After changes, run:
```bash
# AC-1: no bare 'tools:' in CONVENTIONS.md
grep -n 'tools:' config/steps/CONVENTIONS.md | grep -v 'tool_calls\|allowed_tools'

# AC-2: no bare 'tools:' in fixture
grep -n '      tools:' config/scripts/test-fixtures/state-with-tools.yaml

# AC-3+4: test suite
python -m pytest config/scripts/tests/test_tool_calls_key_conventions.py config/scripts/tests/test_upsert_tool_calls.py -v
```

## Complexity

XS — 2 prose files modified, 1 test file added. No code changes.
