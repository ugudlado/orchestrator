# orchestrator doctor — Health Check Subcommand (HL-294)

## Idea

New CLI subcommand `orchestrator doctor` running a battery of health checks. Exit codes: 0 = all pass, 1 = warnings, 2 = failures.

During HL-287 + HL-290 dogfood we hit several silent issues that a proactive health check would have caught:
- state.yaml corrupted by careless `cat >>` append (YAML unparseable at line N)
- step contracts missing `inputs:` / `outputs:` fields
- scripts/inline/ referenced by contracts but file missing
- Stale `status: active` state.yaml files for merged changes
- DuckDB schema version drift between repo and `~/.config/orchestrator/metrics.duckdb`

## Checks to Implement

1. **state.yaml validity** — parse every `~/.workflows/*/state.yaml` as YAML; report line/col on failure
2. **Active vs archived mismatch** — `status: active` state.yaml whose `change_id` already appears in `spec/changes/archive/`? Flag as stale
3. **Contract invariants** — every step contract has required fields: id, inputs, outputs. No refs to deleted step ids in workflow schemas
4. **Script existence** — every contract with `inline: true` + `run:` points to a file that exists in `$ORCHESTRATOR_HOME/scripts/inline/`
5. **Agent existence** — every contract with `agent:` points to an agent file under `$ORCHESTRATOR_HOME/agents/` or `~/.claude/agents/`
6. **DuckDB schema** — confirm `step_events` and `tool_calls` tables exist with expected columns
7. **Workflow plan consistency** — for each active state.yaml, every step_id in workflow_plan.active exists as a contract

Output: concise pass/warn/fail table + suggested remediation. `--fix` flag for safe auto-remediations (stale state archival).

## Acceptance Criteria
- `orchestrator doctor` exits 0 when everything is clean
- Exits 2 with a clear report when any state.yaml is corrupt
- Subsumes `make m8-gates` and `make doctor`

## Priority
- User value: 8/10
- Strategic fit: 9/10
- Size: S (~100 lines + tests)
- Linear: HL-294
