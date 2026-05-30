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

1. **Metrics prep** — `orchestrator learn <change-id>` (runs step `gather-learn-metrics` directly).
2. **Evaluation** — spawn the `workflow-learner` agent with the feature id, scope, and JSON metrics from step 1.

Step defaults: `config/steps/gather-learn-metrics/contract.yaml`. Override scope via
`LEARN_SCOPE` env before invoke.

## Execution

1. Resolve `change_id` from `$ARGUMENTS` (explicit feature id, else most recent completed under `spec/changes/` or archive).
2. Parse `--scope` (default `all`) and export when not the contract default:

```bash
export LEARN_SCOPE="${SCOPE:-all}"
```

3. Run metrics prep:

```bash
ORCHESTRATOR_CLI=${ORCHESTRATOR_CLI:-$(command -v orchestrator || echo "$(git rev-parse --show-toplevel)/bin/orchestrator")}
"$ORCHESTRATOR_CLI" learn "$change_id"
```

Capture stdout JSON (`learn_metrics` object) for the agent prompt.

4. Spawn `workflow-learner` with:
   - `change_id`, `scope`, `state_yaml_path` (from stderr lines or resolve as in workflow-learner skill §1)
   - `learn_metrics`: parsed JSON from step 3
   - `orchestrator_home`: `$ORCHESTRATOR_HOME`

5. The agent runs the full evaluation and routing pipeline (see `skills/workflow-learner/SKILL.md`).

Do not call `metrics-query.sh` from this skill — use the CLI prep step or the agent fallbacks documented in workflow-learner.
