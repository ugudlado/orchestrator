# Step Contract: `agent:` and `run:` Fields

Every step contract that appears in a workflow's `active` list MUST declare either
`agent:` or `run:` (or both). Steps with neither cause `orchestrator next` to exit 3.

## Two-path dispatch model

`orchestrator next` uses field presence to determine execution:

```
if contract.agent:
    → exit 0 + JSON  {agent, instruction, rules, inputs, step_context, ...}
    → driver spawns the named agent via Agent tool
    → agent calls `orchestrator done` with usage payload

elif contract.run:
    → CLI executes the shell script synchronously
    → records result in state.yaml
    → exit 0, no JSON
    → driver loops to next step

else:
    → exit 3 (ContractDispatchError: step_contract_missing_run: <step_id>)
```

## Exit codes

| Exit | Meaning |
|------|---------|
| 0 + JSON with `agent` | Agent step ready — driver spawns |
| 0 + no JSON | Inline script executed and recorded |
| 1 | Workflow complete — no JSON |
| 2 | Step blocked — driver reads state.yaml |
| 3 | Contract missing `agent:` and `run:` |

## Adding an agent step

For steps that require LLM judgment, assign an agent:

```yaml
id: my-step
version: 2
agent: developer         # or: discoverer, architect, reviewer, etc.
instruction: |
  ...
```

The driver spawns `$ORCHESTRATOR_HOME/agents/<agent>.md` via Agent tool.
Usage (tokens, cost) is recorded when the agent calls `orchestrator done`.

## Adding an inline shell step

For steps that are pure shell work (no LLM), add a `run:` script:

```yaml
id: my-step
version: 2
run: scripts/inline/my-step.sh
instruction: |
  Fallback description (used only if script is missing).
```

The script must:
- Start with `#!/usr/bin/env bash` and `set -euo pipefail`
- Be idempotent (safe to re-run if already completed)
- Exit 0 on success, non-zero on failure
- Be executable (`chmod +x`)

Scripts live in `config/scripts/inline/` for orchestrator-internal steps,
or `scripts/inline/` for repo-specific steps.

## Bumping `version:`

Every time a contract's `agent:` or `run:` field is added or changed, bump `version:` by 1.
