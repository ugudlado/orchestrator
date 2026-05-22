---
name: learn
description: "Evaluate last feature's workflow compliance and route learned rules to step contracts and project.yaml learnings. Use after completing a feature, or when the user says \"learn\", \"evaluate workflow\", \"what did we learn\", \"update rules\", \"reflect\", \"review sessions\", \"extract learnings\", \"diagnose\", \"analyze errors\", \"what's going wrong\", \"improve workflow\", \"validate hooks\", \"check step contracts\"."
user-invocable: true
args:
  - name: feature-id
    description: Feature ID to evaluate (defaults to most recently completed feature)
    required: false
  - name: --scope
    description: "Bias the evaluation; one of: all (default — full compliance pass), errors (error-pattern → step-contract fixes, formerly /diagnose), workflow (hook/contract/schema infra, formerly /workflow-improve), session (session-mistake → project.yaml learnings, formerly /reflect)."
    required: false
---

## Learn from Last Feature

$ARGUMENTS

## Overview

Delegates to the `workflow-learner` agent. Resolves the feature ID from
$ARGUMENTS (or auto-detects the most recent completed feature), then spawns
`workflow-learner` with the feature-id and --scope args passed through.

## Execution

1. Parse feature-id and --scope from $ARGUMENTS (same logic as §1 of the
   workflow-learner agent — check for an explicit feature ID, else
   auto-detect from $WORKFLOW_STATE_DIR/*/state.yaml most-recent completed).

2. Spawn `workflow-learner` agent with:
   - feature_id: the resolved feature ID
   - scope: the --scope value (default: all)
   - state_dir: $WORKFLOW_STATE_DIR
   - orchestrator_home: $ORCHESTRATOR_HOME

3. The agent runs the full evaluation and routing pipeline.
