---
feature-id: orchestrator-doctor
linear-ticket: HL-294
---

# Discovery Brief: orchestrator doctor health check subcommand

## Feature Summary

Add `orchestrator doctor` as a new CLI subcommand that runs a battery of structural health checks against the orchestrator installation and all active workflow state. It exits with code 0 (all pass), 1 (warnings), 2 (failures), and prints a concise pass/warn/fail table. An optional `--fix` flag may perform safe auto-remediations. This gives operators a single command to detect drift — stale state directories, invalid YAML, missing scripts, broken contracts, and DuckDB schema mismatches — before those issues surface silently during a workflow run.

## Personas & Actors

- **Workflow operator** (primary) — an engineer running orchestrator-driven workflows who wants to verify their installation is sound after a merge, upgrade, or manual edit.
- **CI system** — an automated job that validates the orchestrator installation on a fresh clone or after a change to `config/steps/` or `scripts/inline/`.
- **Architect/developer** — someone who has just edited a step contract or added an inline script and wants immediate confirmation that the change is self-consistent.

## Use Cases

### Happy Path

UC-1: Full clean pass — a workflow operator runs `orchestrator doctor` on a fresh install with all state valid, all contracts well-formed, all scripts present, and the DuckDB schema current; the command prints a pass table and exits 0.

UC-2: Warning on stale active state — the operator has an `~/.workflows/<change-id>/state.yaml` whose `change_id` already appears in `spec/changes/archive/`; `orchestrator doctor` flags it as stale (warn), exits 1, and prints the offending path.

UC-3: CI gate — a CI job runs `orchestrator doctor` after a PR modifies `config/steps/*.yaml`; a contract missing `id:`, `inputs:`, or `outputs:` causes exit 2, blocking the merge.

UC-4: Script existence check — a developer adds a new inline step contract pointing to `scripts/inline/new-step.sh` but forgets to commit the script; `orchestrator doctor` flags the missing file as a failure and exits 2.

### Error & Edge Cases

UC-E1: Malformed state.yaml — a workflow's `state.yaml` contains a YAML syntax error; `orchestrator doctor` reports the file path, line, and column of the parse error as a failure.

UC-E2: Missing DuckDB file — `metrics.duckdb` does not exist at the resolved path; check #6 is flagged as a failure with a clear message indicating how to create the DB (run `orchestrator next` once or `ensure_schema`).

UC-E3: Agent sentinel skipped — a contract declares `agent: inline`; the agent-existence check must skip it rather than look for `inline.md`.

UC-E4: No ORCHESTRATOR_HOME set — the user runs `orchestrator doctor` without the env var set; the command exits with a clear error explaining the requirement before running any checks.

## Scope

### In Scope

- `orchestrator doctor` subcommand wired into `bin/orchestrator` following the existing `_cost_main` pattern.
- Seven checks as specified: state.yaml validity, active-vs-archived mismatch, contract invariants, script existence, agent existence, DuckDB schema, and workflow plan consistency.
- Exit codes: 0 all-pass, 1 warnings-only, 2 at least one failure.
- Concise tabular output: check name, status (PASS/WARN/FAIL), detail message.
- `agent: inline` treated as a sentinel — skip agent file lookup.
- `workflow_plan.active` step-id normalization consistent with `dispatch.py` and `record.py` (plain string, `step if flag` prefix-trim, dict `.id` key).
- New Python module `config/scripts/orchestrator_next/doctor.py` containing check functions.

### Out of Scope

- `--fix` auto-remediation implementation (flag is optional per spec; leave as Open Question for architect).
- Deprecation or removal of `make doctor` / `make stale` Makefile targets (separate decision).
- Multi-repo fleet scanning (checks run against the local `ORCHESTRATOR_HOME` installation only).
- Per-check verbose mode or machine-readable JSON output format.
- Resolving or repairing archived state (only detection is in scope).

## UI Direction

N/A — no UI components. CLI stdout output only.

## Key Decisions

- **Build or reuse?** Build new. `make doctor` (Makefile:16-25) checks only filesystem presence of six top-level directories. `make stale` (Makefile:27-54) checks age-based staleness in `$ORCHESTRATOR_HOME/changes/` — a path structure that no longer matches the current `~/.workflows/<change-id>/` layout. Neither covers YAML validity, contract invariants, DuckDB schema, or workflow plan consistency. No external library covers this problem space. The new command implements all seven checks and supersedes the Makefile targets functionally (though this discovery does not recommend deleting the Make targets — that is a separate decision).
- **Python, not bash.** Project learning `bash-fragility-prefer-python-for-new-code` (`spec/project.yaml:99`) is directly applicable: YAML parsing, DuckDB queries, and workflow_plan normalization all require logic that is fragile in bash. The existing `cost_report.py` / `record.py` pattern is the right model.
- **Module, not binary.** The doctor logic lives in `config/scripts/orchestrator_next/doctor.py` with a `_doctor_main(args)` dispatch function in `bin/orchestrator`, matching how `cost` and `record` are structured.
- **State directory is `~/.workflows/<change-id>/state.yaml`.** Confirmed by `find ~/.workflows -maxdepth 3 -name state.yaml`. The Makefile `stale` target uses a different (outdated) path. The doctor command must glob `~/.workflows/*/state.yaml`.
- **Active-vs-archive match is substring.** Archive entries are date-prefixed slugs (e.g., `2026-04-18-fix-cost-usd-and-widen-token-split`). A `change_id` of `fix-cost-usd-and-widen-token-split` matches any archive entry whose basename contains or ends with that slug. Five of eleven active `~/.workflows/` entries have matching archived entries — the check has real signal.
- **Inline script paths are relative to `ORCHESTRATOR_HOME`.** All eight `inline: true` contracts use `run: scripts/inline/<name>`. The resolved path is `$ORCHESTRATOR_HOME/scripts/inline/<name>`. This is confirmed by the actual files present in `/Users/spidey/code/orchestrator/scripts/inline/`.

## Open Questions

- OQ-1: Should `orchestrator doctor` deprecate and subsume `make doctor` and `make stale`? If yes, are the Makefile targets removed, left for backward compatibility, or delegated to `orchestrator doctor`?
- OQ-2: What is the severity assignment for each check? Proposed starting point: parse failure = FAIL, active-vs-archive mismatch = WARN, missing required contract field = FAIL, missing inline script = FAIL, missing agent file = FAIL, DuckDB table/column missing = FAIL, workflow plan step without contract = WARN. Architect should confirm.
- OQ-3: What `--fix` remediations are safe? Candidates: archive stale active directories, no others appear safe without side effects. Architect should define or defer to a follow-up.
- OQ-4: Should `ORCHESTRATOR_HOME` be required, or should the command gracefully skip checks that depend on it? Current `cost` subcommand hard-fails if neither `METRICS_DB` nor `ORCHESTRATOR_HOME` is set (line 108-113 of `bin/orchestrator`). Recommend the same behavior here.
- OQ-5: Should the DuckDB schema check (`step_events`, `tool_calls` tables and their expected columns) be a hard-coded column list in the doctor module, or derived from `ensure_schema` in `upsert.py`? Deriving from `ensure_schema` is more DRY but couples the doctor check to the DDL string at runtime.

## What Already Exists

### Codebase

- `Makefile:16-25` — `make doctor` target: checks for six top-level directory/file paths only. Does not validate YAML, contracts, DuckDB, or agent files. No exit codes.
- `Makefile:27-54` — `make stale` target: bash loop scanning `$ORCHESTRATOR_HOME/changes/<repo>/<change>/state.yaml` for age. Uses a path structure inconsistent with the current `~/.workflows/<change-id>/state.yaml` layout. Bash-only, no YAML parsing.
- `Makefile:59-69` — `lint-contracts` target: checks `config/steps/*.yaml` for presence of `inputs:` and `outputs:` lines via `grep`. No YAML parsing, no `id:` check. Overlaps with check #3 but is weaker.
- `config/scripts/orchestrator_next/parser.py:89-129` — `_load_contract()`: already validates `inputs:` and `outputs:` presence and raises `ContractError`. Check #3 can reuse this.
- `config/scripts/orchestrator_next/upsert.py:27-51` — `_DDL_STEP_EVENTS` and `_DDL_TOOL_CALLS`: authoritative column lists for check #6.
- `config/scripts/orchestrator_next/upsert.py:156-165` — `ensure_schema()`: idempotent DDL runner; check #6 can call it and then verify the tables exist with `DESCRIBE`.
- `bin/orchestrator:52-187` — `_cost_main()`: the established pattern for a new subcommand (argparse, DB connection, module import, `sys.exit`).
- `config/scripts/orchestrator_next/dispatch.py:64-69` — `_phase_step_ids()`: extracts `workflow_plan.active` step ids; check #7 needs the same normalization. Note: this function returns raw items (including `step if flag` strings and dicts) — the doctor check must apply the same normalization as `record.py:42-44`.

### External

Searched for "CLI health check doctor subcommand Python" and "orchestrator health check pattern". No relevant external libraries found. This is a bespoke internal validation command. Standard pattern is exactly what the codebase already does: a Python module with check functions and a dispatch entry in the CLI.

## Approaches Considered

### Approach A: Single module with flat check functions (mirrors `cost_report.py`)

Each of the seven checks is a function returning a `CheckResult(name, status, detail)` namedtuple. A `run_all(args)` function calls them in order, accumulates results, prints a table, and returns an exit code. Wired into `bin/orchestrator` as `_doctor_main(args)`.

- Pros: Matches the existing codebase pattern exactly. Simple to test — each check function is independently unit-testable. No new abstractions.
- Cons: Adding `--fix` later requires modifying each check function or adding a parallel fix-function per check. No shared interface for third-party extension.
- Effort: Small.

### Approach B: Check registry with dataclass per check

Define a `Check` dataclass with fields `name`, `run_fn`, `fix_fn`, `severity`. Register checks at module load. Runner iterates the registry. `--fix` calls `fix_fn` for checks that fail and have one defined.

- Pros: Cleaner `--fix` extension path. Consistent interface if more checks are added later.
- Cons: More abstraction than the current problem justifies — seven checks with mostly-similar logic do not need a registry pattern. Over-engineers before `--fix` requirements are defined.
- Effort: Medium.

### Approach C: Shell out to `make doctor` + `make lint-contracts` and add only the delta

Reuse existing Makefile targets for directory and contract presence checks; implement only the new checks (DuckDB, agent existence, workflow plan) in Python.

- Pros: Zero duplication for checks already covered.
- Cons: `make doctor` uses incorrect path assumptions (no exit codes, no YAML parsing). `make stale` globs the wrong path structure. Mixing bash and Python output makes table formatting impossible. Fragility introduced by subprocess calls.
- Effort: Small (but produces a worse result than Approach A for roughly the same effort).

## Recommendation

Approach A. It is the smallest, cleanest fit to the existing codebase pattern. Approach B over-engineers before `--fix` requirements are confirmed. Approach C inherits the bugs and limitations of the bash Makefile targets and complicates output formatting.

## Technical Context

| File | Role |
|------|------|
| `bin/orchestrator` | CLI entry point. Add `"doctor"` to the `args[0] not in (...)` guard (line 192), add `_doctor_main(args[1:])` dispatch (after the `cost` branch). |
| `config/scripts/orchestrator_next/doctor.py` | New module. All seven check functions + `run_all()` + `CheckResult` namedtuple. |
| `config/scripts/orchestrator_next/parser.py` | Reuse `_load_contract()` (line 89) and `load_state()` (line 150) for checks #1, #3, #7. |
| `config/scripts/orchestrator_next/upsert.py` | Reuse `_DDL_STEP_EVENTS` / `_DDL_TOOL_CALLS` column lists (lines 27-51) and `ensure_schema()` (line 156) for check #6. |
| `config/steps/*.yaml` | Inputs for checks #3, #4, #5. Currently 8 files with `inline: true`; `run:` paths are `scripts/inline/<name>`. |
| `scripts/inline/` | Target dir for check #4 (`$ORCHESTRATOR_HOME/scripts/inline/`). Contains 8 scripts matching the 8 inline contracts. |
| `agents/*.md` + `~/.claude/agents/*.md` | Target dirs for check #5. Agent name → `<name>.md` resolution. Sentinel: `agent: inline` is skipped. |
| `~/.workflows/*/state.yaml` | Active state files for checks #1, #2, #7. Glob pattern confirmed by `find ~/.workflows -maxdepth 2 -name state.yaml`. |
| `spec/changes/archive/` | Archive directory for check #2. Match rule: archive entry basename contains `change_id` as a substring (date-prefixed slugs). |

Key line numbers:
- `bin/orchestrator:192` — subcommand guard to extend.
- `bin/orchestrator:197-199` — `_cost_main` dispatch to mirror.
- `config/scripts/orchestrator_next/parser.py:89` — `_load_contract()`.
- `config/scripts/orchestrator_next/parser.py:150` — `load_state()`.
- `config/scripts/orchestrator_next/upsert.py:27` — `_DDL_STEP_EVENTS` DDL string.
- `config/scripts/orchestrator_next/upsert.py:53` — `_DDL_TOOL_CALLS` DDL string.
- `config/scripts/orchestrator_next/dispatch.py:64` — `_phase_step_ids()` normalization to replicate in check #7.
- `config/scripts/orchestrator_next/record.py:42-44` — step-id normalization (dict `.id`, `step if flag` trim) to replicate in check #7.

DuckDB expected tables and key columns (from `upsert.py`):
- `step_events`: `repo_root, change_id, phase, step_id, attempt, agent_name, status, gen_ai_usage_cost_usd, tool_calls_json`.
- `tool_calls`: `repo_root, change_id, phase, step_id, attempt, agent_name, tool_name, is_mcp, call_seq`.
