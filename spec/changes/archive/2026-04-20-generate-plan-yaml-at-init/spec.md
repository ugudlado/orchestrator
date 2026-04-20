---
feature-id: generate-plan-yaml-at-init
linear-ticket: null
---

# Specification: Generate plan.yaml at workflow-init

## Motivation

Today, every agent spawn reconstructs its operating context from three sources: `project.yaml` (rules/gotchas/learnings), the active schema (phase goals, flag-conditional steps, rules_when), and the step contract file (inputs, outputs, rules, verify). The merge is documented in `config/steps/contracts/rule-merge.md` but happens implicitly — agents scavenge what they need. This scatters context and means rule-merge precedence is enforced only by convention.

## What Changes

A deterministic Python generator produces a pre-merged `plan.yaml` once at `workflow-init` time, containing only active phases and steps (no filtered branches). Each step block carries its merged `rules`, `goal`, `inputs`, `outputs`, `verify`, `repeat_until`, and bound `agent`. `orchestrator next` reads the matching step block on every dispatch and includes the resolved fields under `step_context` in the action payload. The driver passes `step_context` verbatim into agent spawn prompts.

## Requirements

### Functional

1. **FR-1**: `workflow-init` agent calls the generator as its final sub-step after writing `state.yaml`. Generator produces `$WORKFLOW_STATE_DIR/<slug>/plan.yaml`.
2. **FR-2**: Generator is a deterministic Python module (`orchestrator_next.generate_plan`) runnable as `python -m orchestrator_next.generate_plan <state_yaml_path>`. Given the same inputs, it produces byte-identical output.
3. **FR-3**: `plan.yaml` contains only active phases and steps. No `filtered:` list, no conditional `if <flag>` branches — all resolution pre-applied using `resolved_flags` in state.yaml.
4. **FR-4**: For each step in `plan.yaml`, the generator emits: `id`, `agent`, `goal` (from phase), `inputs`, `outputs`, `rules` (merged per `rule-merge.md` 5-tier precedence), `verify` (phase-level verify block attached to the last step), and `repeat_until` when declared on the schema step entry.
5. **FR-5**: `orchestrator next` reads `plan.yaml` and attaches the resolved step block under `step_context` in `spawn_agent` actions (types `run_step`, `run_inline`, `retry_step`). Omitted for `verify_phase`, `complete_workflow`, and `blocked` action types.
6. **FR-6**: `orchestrator next` fails loudly when `plan.yaml` is required but absent. No fallback to live-merge. Error exit code 3 with a clear stderr message pointing at the missing plan.yaml path.
7. **FR-7**: Adding `plan.yaml` does NOT change state.yaml shape. Existing in-flight workflows in `.state/` that lack a `plan.yaml` are not affected because none exist (verified pre-change).

### Non-Functional

1. **NFR-1**: Generator runs in < 500ms for a typical feature schema (< 20 active steps across 3 phases).
2. **NFR-2**: `plan.yaml` output is stable-sorted for diffability — phases in schema order, steps in schema order, rules in merge-precedence order, dict keys alphabetical within each step block.
3. **NFR-3**: The `step_context` payload addition does not increase state.yaml size — it lives in the action response only, not in state.yaml.

## Acceptance Criteria

1. **AC-1**: Running `/orchestrate --light <some-slug>` produces both `state.yaml` and `plan.yaml` at `.state/<slug>/` after the first `workflow-init` step completes.
2. **AC-2**: `plan.yaml` for a light-mode feature contains exactly 3 phases (`specify`, `implement`, `complete`) with only active steps — no `explore`, `ux-design`, `run-phase-review`, or `run-ux-critique` entries.
3. **AC-3**: Calling `orchestrator next <state.yaml>` after workflow-init completes returns an action JSON whose `step_context` key contains a dict with `id`, `agent`, `goal`, `inputs`, `outputs`, `rules`, `verify` (when applicable), and `repeat_until` (when applicable).
4. **AC-4**: Deleting `plan.yaml` and running `orchestrator next` produces stderr error and exit code 3 — no fallback, no partial action.
5. **AC-5**: Running the generator twice on the same state.yaml produces byte-identical `plan.yaml` output (`diff` returns no differences).
6. **AC-6**: Generator handles all four schemas (`feature`, `bugfix`, `chore` folded into `--light`, `spike`) — at least one smoke-test assertion per schema.
7. **AC-7**: `test_generate_plan.py` passes with coverage for: rule merge across all 5 tiers; filter-by-flag removes inactive-if steps; `repeat_until` preserved; `verify` block attached to phase's last active step; phase-level `include:` resolved.
8. **AC-8**: `test_dispatch_step_context.py` passes: `step_context` populated from plan.yaml; missing plan.yaml causes error exit.

## Out of Scope

- Rewriting existing agents to consume `step_context` (they can keep reading source files; this is a follow-up).
- Changing rule-merge precedence semantics.
- Hot-reloading plan.yaml mid-flight for mid-flight rule fixes (frozen-at-init is correct; self-referential changes apply to next feature).
- Backward compatibility with pre-change workflows (none exist).
- Modifying state.yaml shape or existing `workflow_plan` field.

## Architecture

```
workflow-init agent
    ├── writes state.yaml (existing)
    └── runs: python -m orchestrator_next.generate_plan <state_path>
                          │
                          ▼
                    plan.yaml (new artifact)

orchestrator next
    ├── reads state.yaml (existing)
    ├── reads plan.yaml (NEW)
    └── returns action with step_context (NEW key)
```

Files modified:

| File | Change | Why |
|---|---|---|
| `config/scripts/orchestrator_next/generate_plan.py` | **new** | The merger. |
| `config/scripts/orchestrator_next/dispatch.py` | edit | Read `plan.yaml`, inject `step_context`. |
| `agents/workflow-init.md` | edit | Add final sub-step: run generator. |
| `config/steps/workflow-init.yaml` | edit | Add `plan_yaml_path` to declared outputs. |
| `config/scripts/orchestrator_next/tests/test_generate_plan.py` | **new** | Generator tests. |
| `config/scripts/orchestrator_next/tests/test_dispatch_step_context.py` | **new** | Dispatcher tests. |
