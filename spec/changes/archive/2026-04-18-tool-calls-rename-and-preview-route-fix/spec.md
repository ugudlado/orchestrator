---
feature-id: tool-calls-rename-and-preview-route-fix
linear-ticket: HL-292, HL-293
---

# Specification: tool_calls rename + preview-route path fix

## Motivation

Two XS dogfood findings from HL-287/HL-290:

1. **HL-292**: `cost_report.py` reads `fm.get("tools")` but the canonical step_history key is `tool_calls` per `otel_map.py`. This causes the `tool_calls` DuckDB table fan-out to silently produce zero rows.
2. **HL-293**: `estimate-cost.sh` receives the state directory but checks for `state.yaml` inside it — this works. However `preview-route.yaml` passes `$WORKFLOW_STATE_DIR/$CHANGE_ID` which is the state dir, not the actual state path that was moved to `~/.workflows/<slug>/state.yaml` by HL-287.

## What Changes

- `cost_report.py`: rename `fm.get("tools")` → `fm.get("tool_calls")`
- `write-bootstrap-state.yaml`: rename `"tools"` key → `"tool_calls"` in the usage dict example
- `estimate-cost.sh` + `preview-route.yaml`: fix path so state.yaml is found correctly
- Add grep guard to Makefile or CI script to prevent key name regression

## Requirements

### Functional

1. **FR-1**: `cost_report.py` reads `tool_calls` key from step_history usage dict (not `tools`)
2. **FR-2**: `preview-route` correctly locates state.yaml and returns a real `route_preview:` block
3. **FR-3**: No other file in config/, skills/, agents/ references `"tools":` in a usage/step_history context

### Non-Functional

1. **NFR-1**: No behavior changes — pure rename + path fix

## Acceptance Criteria

- AC-1: Given a step_history entry with `tool_calls: {Read: 5}`, when cost_report.py processes it, then the tool_calls DuckDB table receives rows (not zero rows)
- AC-2: Given a valid state.yaml at `~/.workflows/<slug>/state.yaml`, when preview-route runs, then estimate-cost.sh finds the file and returns a `route_preview:` block
- AC-3: `grep -rn '"tools":' config/ skills/ agents/ bin/` in usage context returns zero matches

## Impact

No breaking changes. Pure mechanical rename + path fix.
