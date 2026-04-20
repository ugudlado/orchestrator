---
feature-id: generate-plan-yaml-at-init
---

# Design: Generate plan.yaml at workflow-init

## Design Approach

Three candidates were considered. The simplest was chosen per the `--light` auto-selection heuristic (lowest complexity).

### Candidate A — Monolithic generator in one file (SELECTED, complexity: XS)

Single file `orchestrator_next/generate_plan.py` exporting `generate_plan(state_path: str) -> dict` and providing a `python -m` entry point. Reuses `orchestrator_next.parser` for loading schema + step contracts. Reuses `orchestrator_next.resolver` for `$ORCHESTRATOR_HOME` resolution.

**Pros:** minimal new code; all merge logic in one place; easy to test; matches the existing `orchestrator_next/*.py` file-per-concern convention.

**Cons:** 5-tier merge logic is visible in one function rather than split.

### Candidate B — Separate `merge.py` + `generate_plan.py` (complexity: S)

Extract merge logic into a reusable `merge.py` used by both `generate_plan` and future code paths.

**Pros:** reusability.

**Cons:** no second caller exists today; premature abstraction.

### Candidate C — Generate on every dispatch, skip frozen plan.yaml (complexity: M)

Make `orchestrator next` merge live on each call, writing results to a tempfile or in-memory only. No persisted `plan.yaml`.

**Pros:** mid-flight rule edits take effect immediately.

**Cons:** loses the "one reference document" goal; per-dispatch merge cost; breaks frozen-plan reproducibility; doesn't match user request.

**Selected:** A. Rationale — lowest complexity (XS=1), exactly one call site, no known second consumer. Monolithic is fine until a second consumer emerges.

## Component Breakdown

### `orchestrator_next/generate_plan.py`

```python
def generate_plan(state_yaml_path: str) -> None:
    """Read state.yaml, merge, write plan.yaml next to state.yaml."""
    state = parser.load_state(state_yaml_path)
    schema = parser.load_schema(state.schema)
    project = parser.load_project(state.repo_root)

    phases_out = []
    for phase_name in state.workflow_plan.keys():
        active_step_ids = state.workflow_plan[phase_name].get("active", [])
        phase_def = _resolve_phase(schema, phase_name)  # handles include: _complete-phase
        steps_out = [_build_step_block(step_id, phase_def, schema, project, state)
                     for step_id in active_step_ids]
        phases_out.append({"name": phase_name, "goal": phase_def.get("goal", ""), "steps": steps_out})

    plan = {
        "feature": state.slug,
        "schema": state.schema,
        "resolved_flags": state.flags,
        "phases": phases_out,
    }
    write_yaml_stable(plan, state_yaml_dir / "plan.yaml")
```

`_build_step_block` applies the 5-tier merge from `rule-merge.md`:

1. Step entry injections (`rules_when`, `extra_rules` from schema `phases[].steps[]`)
2. Step contract rules (from `config/steps/<step>.yaml`)
3. Phase rules (`phases[].rules`)
4. Schema rules (top-level `rules:`, filtered by `when:` against flags)
5. Project rules (`project.yaml rules:`, filtered by `when:`)

Named rules (schema + project) dedupe by `id` — schema wins. Plain string rules (phase, contract, injected) concatenate in precedence order.

### `dispatch.py` change

Inside `dispatch()`, after computing `next_step_id`, load `plan.yaml` and look up the matching step block. Attach under action key `step_context`:

```python
plan_path = Path(state_yaml_path).parent / "plan.yaml"
if not plan_path.exists():
    print(f"ERROR: plan.yaml not found at {plan_path}", file=sys.stderr)
    sys.exit(3)
plan = yaml.safe_load(plan_path.read_text())
step_block = _find_step_in_plan(plan, state.phase, next_step_id)
action["step_context"] = step_block
```

Only attach for action types that spawn agents: `run_step`, `run_inline`, `retry_step`. Not for `verify_phase`, `complete_workflow`, `blocked`.

### `agents/workflow-init.md` change

Add one responsibility after step 5 (state.yaml write):

> 6. **Generate plan.yaml**: run `python -m orchestrator_next.generate_plan $WORKFLOW_STATE_DIR/<slug>/state.yaml`. Verify `plan.yaml` was written next to state.yaml.

### `config/steps/workflow-init.yaml` change

Append `plan_yaml_path` to `outputs:`:

```yaml
outputs:
  - worktree_path
  - branch
  - linear_ticket_id
  - workflow_plan
  - resolved_flags
  - plan_yaml_path   # NEW
```

## Data Flow

```
state.yaml (written by workflow-init)
    │
    │ (workflow-init's final sub-step)
    ▼
python -m orchestrator_next.generate_plan
    │
    ├── reads config/workflows/<schema>.yaml
    ├── reads config/steps/<step>.yaml × N (one per active step)
    ├── reads spec/project.yaml
    └── writes plan.yaml
                │
                │ (per-dispatch read by orchestrator next)
                ▼
        dispatch.py action builder
                │
                └── attaches step_context to spawn_agent actions
```

## Error Handling

- **Missing schema file**: generator raises `FileNotFoundError` with schema path. Caller (workflow-init agent) must surface to user.
- **Missing step contract**: generator skips the step with a warning to stderr (schema may declare steps without contracts; dispatcher already handles this).
- **Missing plan.yaml at dispatch time**: dispatcher exits 3 with stderr `plan.yaml not found at <path>`. No fallback.
- **plan.yaml missing a step block that dispatch asked for**: dispatcher exits 3 with stderr `step_context missing for <phase>/<step_id> in plan.yaml`.

## Simplicity Rationale

- No new package, no subclasses, no caching. One module, one function, one call site.
- Reuses existing `parser.py` — no duplicate YAML loading logic.
- Serialization uses `yaml.safe_dump(plan, sort_keys=False, default_flow_style=False)` with an explicit key-order helper — no custom serializer.

## Testing Strategy

Two test files, both under `config/scripts/orchestrator_next/tests/`:

- **`test_generate_plan.py`**:
  - `test_light_flag_drops_filtered_steps` — no `explore`/`ux-design` in specify phase
  - `test_rule_merge_precedence` — step-entry injection > contract > phase > schema > project, with named-rule dedupe
  - `test_byte_stable_output` — two runs produce identical bytes
  - `test_repeat_until_preserved` — `execute-next-task` carries `repeat_until`
  - `test_phase_verify_attached_to_last_step` — `verify:` block appears on the last active step of each phase
  - `test_include_phase_resolved` — `_complete-phase` resolves inline
- **`test_dispatch_step_context.py`**:
  - `test_run_inline_has_step_context`
  - `test_run_step_has_step_context`
  - `test_verify_phase_omits_step_context`
  - `test_missing_plan_yaml_exits_3`
  - `test_step_missing_in_plan_exits_3`
