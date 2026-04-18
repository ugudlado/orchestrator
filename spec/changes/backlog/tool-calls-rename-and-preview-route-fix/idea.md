# tool_calls Rename + Preview-Route Path Fix (HL-292 + HL-293)

## Idea

Two XS mechanical fixes bundled together:

**HL-292 — Standardize tool_calls key naming**
Skill prose in `skills/orchestrate/SKILL.md` and some docs use `"tools": {...}` inside usage context, but the canonical key is `tool_calls` per `config/scripts/orchestrator_next/otel_map.py`. This drift caused the `tool_calls` DuckDB table fan-out to silently produce zero rows during HL-290 dogfood.

Fix: grep for `"tools":` in usage/step_history context throughout codebase (skill prose, docs, test fixtures, agent defs) and rename to `tool_calls`. Add a CI grep to prevent regression.

**HL-293 — Fix estimate-cost.sh state.yaml path**
`preview-route` always returns `{status: estimate_unavailable}` because `config/scripts/estimate-cost.sh` expects state.yaml at `$WORKFLOW_DIR/state.yaml`, but HL-287 moved state.yaml to `~/.workflows/<slug>/state.yaml`. The estimator never finds it.

Fix: update `estimate-cost.sh` to accept state.yaml path as an argument (or env var) and update preview-route.sh to pass it explicitly.

## Why Now
Both are XS fixes that unblock correctness. HL-292 makes tool attribution reporting accurate. HL-293 makes preview-route usable. Hit on every workflow run.

## Acceptance Criteria
- `grep -r '"tools":' config/ skills/ agents/` returns zero matches in usage/step_history context
- `preview-route` returns a real `route_preview:` block (not estimate_unavailable) when pricing data exists
- Tests for both fixes pass

## Priority
- User value: 7/10
- Strategic fit: 8/10
- Size: XS (combined ~20 lines + tests)
- Linear: HL-292, HL-293
