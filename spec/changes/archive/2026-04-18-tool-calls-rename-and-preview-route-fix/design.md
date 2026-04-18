# Design: tool_calls rename + preview-route path fix

## HL-292: tool_calls key rename

### Files to change

| File | Change |
|------|--------|
| `config/scripts/orchestrator_next/cost_report.py:98` | `fm.get("tools")` → `fm.get("tool_calls")` |
| `config/steps/write-bootstrap-state.yaml:29` | `"tools": {...}` → `"tool_calls": {...}` in usage example |

### CI guard (optional, out of scope for now)

A `grep -c '"tools":' config/scripts/orchestrator_next/cost_report.py` assertion returning 0 is sufficient.

## HL-293: estimate-cost.sh path fix

### Root cause

`preview-route.yaml` calls:
```
estimate-cost.sh $WORKFLOW_STATE_DIR/$CHANGE_ID
```
`estimate-cost.sh` then does `STATE_FILE="$STATE_DIR/state.yaml"` — looking for state.yaml inside the passed dir.

HL-287 moved state.yaml to `~/.workflows/<slug>/state.yaml` which IS `$WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml` — so the path is actually correct per the current layout. The bug is that `WORKFLOW_STATE_DIR` env var isn't set in the estimate-cost.sh subprocess.

### Fix approach

In `preview-route.yaml` (or its step contract), pass the full state.yaml path explicitly:
```
estimate-cost.sh $WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml
```
And update `estimate-cost.sh` to accept a full path to state.yaml (not just a dir), or support both forms.

### Files to change

| File | Change |
|------|--------|
| `config/steps/preview-route.yaml` | Pass full state.yaml path to estimate-cost.sh |
| `config/scripts/estimate-cost.sh` | Accept full path to state.yaml as arg (or auto-detect file vs dir) |

## Complexity

XS. Total ~10 line changes across 3-4 files.
