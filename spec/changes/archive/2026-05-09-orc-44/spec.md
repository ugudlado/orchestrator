---
feature-id: orc-44
linear-ticket: HL-292
---

# Spec: Standardize tool_calls key naming in CONVENTIONS.md and test fixtures

## Context

CONVENTIONS.md documents the per-tool breakdown field in usage blocks as `tools:`,
but `upsert.py` exclusively reads `usage["tool_calls"]`. When LLM agents follow
CONVENTIONS.md and write `tools: {Read: 3, Bash: 2, ...}`, the upsert gets `None`
and fans out zero rows to the DuckDB `tool_calls` table. Tool attribution data is
silently lost.

## In Scope

- Rename `tools:` → `tool_calls:` in CONVENTIONS.md (2 occurrences: lines 224, 300)
- Update `state-with-tools.yaml` test fixture to use `tool_calls:` (4 occurrences)
- Add a regression test confirming the CONVENTIONS.md example value populates
  DuckDB `tool_calls` rows

## Out of Scope

- Changes to `upsert.py` — it already uses the correct key
- Backfilling historical archived state.yaml files (immutable)
- Adding backward-compatibility aliases in upsert.py
- Changes to `compute-swe-metrics.sh` — reads from DuckDB, not state.yaml

## Acceptance Criteria

- AC-1: CONVENTIONS.md contains `tool_calls:` (not `tools:`) in all usage examples
  Verify: `grep -n 'tools:' config/steps/CONVENTIONS.md | grep -v 'tool_calls\|allowed_tools'` → zero matches
- AC-2: `state-with-tools.yaml` uses `tool_calls:` in all usage blocks
  Verify: `grep -n '      tools:' config/scripts/test-fixtures/state-with-tools.yaml` → zero matches
- AC-3: Regression test `test_tool_calls_key_conventions.py` passes with tool_calls dict
  producing non-zero DuckDB rows
  Verify: `python -m pytest config/scripts/tests/test_tool_calls_key_conventions.py -v` → PASSED
- AC-4: Full test suite passes with no regressions
  Verify: `python -m pytest config/scripts/ -q --tb=short` → all previously-passing tests still pass
