---
name: autopilot
description: "Autonomous self-improving development loop. Picks work from backlog, runs the develop workflow with full autonomy flags (--auto --agents), learns and improves workflow. This skill should be used when user says 'autopilot', 'autonomous', 'run N iterations', 'self-improve'."
user-invocable: true
args:
  - name: iterations
    description: "Number of iterations to run (default: 1)"
    required: false
  - name: "--focus"
    description: "Steering hint for ideator prioritization (e.g., focus on workflow reliability)"
    type: option
---

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
REPO_NAME=${REPO_NAME:-$(basename "$REPO_ROOT")}
ORCHESTRATOR_HOME=${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}
WORKFLOW_STATE_DIR=${WORKFLOW_STATE_DIR:-$REPO_ROOT/.state}
```

## Input

$ARGUMENTS

## Overview

This skill is the entry point for the autonomous development loop. It delegates
entirely to the `autopilot` workflow schema — no loop logic lives in this skill.

The schema (`$ORCHESTRATOR_HOME/config/workflows/autopilot.yaml`) owns all execution:
- **preflight** phase — repo checks, session init
- **iterate** phase — pick → develop → record → clear (repeated N times)
- **report** phase — session summary table + resume instructions

Each phase's steps are defined as step contracts in `$ORCHESTRATOR_HOME/config/steps/autopilot-*.yaml`.

## Execution

### 1. Parse Arguments

- Extract iteration count (default: 1). Must be a positive integer.
- Extract `--focus` hint if provided (passed through to schema flags).

### 2. Invoke autopilot schema via orchestrate

```
Skill({ skill: "orchestrate", args: "autopilot [N] [--focus \"$FOCUS\"]" })
```

The word "autopilot" in the request is the intent signal — orchestrate detects it,
loads the autopilot schema, and passes `N` and `--focus` through as schema flags.
The schema walks its three phases using the step contracts — all logic is in the contracts.

## What This Skill Does NOT Do

- Does not implement the iteration loop — that's `autopilot-iterate.yaml`
- Does not manage session files — that's `autopilot-session-init.yaml`
- Does not pick work — that's the ideator step inside `autopilot-iterate.yaml`
- Does not duplicate logic from the develop or orchestrate skills

## Error Handling

All error handling is defined in the schema phase `verify:` blocks and step contracts.
If the schema aborts (pre-flight failure, empty backlog, consecutive spawn failures),
the step contracts write the appropriate state and surface clear messages.
