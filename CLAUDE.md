# CLAUDE.md

Read `spec/project.yaml` for all project context — vision, architecture, tech stack, quality bars, rules, gotchas, and learnings.

## Workflow

Feature work must go through the formal `/autopilot` or `/orchestrate` workflow — session directory, phase gating, checkpoints. Never jump straight to implementation and never skip the complete phase.

## Approach Before Implementation

Before starting any implementation step (writing code, running destructive commands, or creating multi-file artifacts), state the approach in 3 bullets:

1. **Files**: which files will be created or modified
2. **Approach**: the specific change in one sentence — not the goal, the mechanism
3. **Not doing**: what's deliberately out of scope for this step

Skip this only for trivial single-file edits under ~10 lines. Under `--auto`, emit the bullets and proceed; a human can intervene if the approach is wrong. Under interactive mode, wait for confirmation unless the user has pre-approved the step.

## Minimal Fixes

When making changes, do not introduce unnecessary fallbacks, abstractions, or generalizations. Prefer the smallest targeted fix. If a refactor starts feeling generic or "while I'm here," stop and confirm direction.

## Root-Cause Debugging

When a bug is reported, fix the structural root cause — don't layer more rules or guardrails on top. If the root cause is unclear, ask rather than patching symptoms.

## Repo Wiring

Rules every agent must follow when running in this repo. CLAUDE.md auto-loads into all spawns, so agent files don't need to restate these.

### State updates

- **Use `orchestrator done <state.yaml>`** with a JSON payload on stdin for all `step_history` appends. Never edit `state.yaml` directly with Write/Edit — `record.py` validates shape; direct edits have corrupted state in past runs.
- State file lives at `$WORKFLOW_STATE_DIR/<change_id>/state.yaml`. Required top-level keys: `schema`, `flags`. Merge precedence for flags: `cli_flags` > `state_flags` > `schema_defaults`.

### Paths

| Purpose | Path |
|---|---|
| Active workflow state | `$REPO_ROOT/.state/<slug>/state.yaml` |
| Archived features | `$REPO_ROOT/spec/changes/archive/<date>-<slug>/` |
| Backlog (single file) | `$REPO_ROOT/spec/changes/backlog.md` |
| Global schemas | `$ORCHESTRATOR_HOME/config/workflows/<schema>.yaml` |
| Global step contracts | `$ORCHESTRATOR_HOME/config/steps/<step>.yaml` |
| Repo overrides | `$REPO_ROOT/.orchestrator/{workflows,steps,templates}/<path>` |
| Per-feature retro | `spec/changes/<change_id>/retro.md` (active) or under `archive/<date>-<slug>/` |

### Repo overrides

Files under `$REPO_ROOT/.orchestrator/` take precedence over `$ORCHESTRATOR_HOME/config/` at dispatch time. Overrides are whole-file replacements — when creating one, copy the global file first, then edit. Protocol contracts (`config/steps/contracts/*`) are global-only, never overridable. See `config/steps/contracts/workflow-override.md` before writing under `.orchestrator/`.

### Learned-rule metadata

When appending a learned rule to a step contract (global or override), include an inline comment on the rule line:

```
<!-- learned: YYYY-MM-DD, source: FEATURE-ID, cycle: N, repo: REPO_NAME -->
```

- `repo: REPO_NAME` — rule applies only when run from this repo (default).
- `repo: *` — universal workflow-mechanics rule. Do not use for tool, command, or domain rules.

### Linear integration

- Linear config lives at `~/.config/linear/config.yaml` (team ID, project ID, label IDs per repo).
- If the current repo is not in the config's `repos:` map, skip Linear creation and set `flags.linear = false`.
- Ticket IDs match `^<PREFIX>-\d+$` per the team prefix (e.g., `HL-287`).

### Architect escalation

Full protocol: `config/steps/contracts/architect-escalation.md`. Escalate only for: design contradiction, missing coverage, scope ambiguity with cascade risk, or architectural dependencies affecting other tasks. Not for implementation details, test strategy, or library usage.

### Frontend tech list (ux_design auto-detect)

If `flags.ux_design` is not explicitly set, default `false` unless `project.yaml.tech_stack` contains any of: `react, nextjs, vue, svelte, angular, html, css, tailwind, scss, sass, less, webpack, vite, typescript-frontend, flutter, swift-ui`.
