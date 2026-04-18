# Per-Step allowed_tools Enforcement + Tool-Use Attribution (HL-295)

## Idea

Agent roles declare allowed tools in frontmatter today. Step contracts don't. That means every invocation of a role gets the full tool list, even if this step only needs a subset. No tool attribution to step-specific needs.

## Scope

1. **Step contract gains `allowed_tools:` field** — optional; when present, intersected with role frontmatter tools at spawn time
2. **Dispatcher surfaces resolved tool list** in the action dict under `resolved_allowed_tools: [...]` alongside inputs/expected_outputs
3. **Skill spawns with the narrowed set** — pass to Agent tool's `allowed_tools` parameter or include in prompt as constraint
4. **Anomaly detection extension** (cost report anomaly section): flag tools used that aren't in the step's `allowed_tools:` (not just the role's)
5. **Least-privilege defaults** — if a step contract doesn't declare `allowed_tools:`, default to the role's full list (backward-compatible)

## Acceptance Criteria
- `orchestrator next` action dict includes `resolved_allowed_tools` (intersection of role and step)
- Step contracts declaring `allowed_tools:` not in the role's list fail validation with clear error (can't widen)
- Cost report anomaly section distinguishes "tool not in role" from "tool not in step allowlist"
- Existing step contracts (without `allowed_tools:`) continue working unchanged

## Dependencies
- HL-287 typed step contracts — landed
- HL-290 cost report infrastructure — landed

## Priority
- User value: 7/10
- Strategic fit: 8/10
- Size: M (touches parser + dispatcher + skill prose + cost report + tests)
- Linear: HL-295
