---
feature-id: orchestrator-doctor
linear-ticket: HL-294
---

# Specification: orchestrator doctor health check subcommand

## Motivation

Recent dogfood findings (HL-287, HL-290) surfaced structural drift that would have been caught by a single health-check command:

- HL-287: contract field renames and flipped input/output contracts shipped before being noticed — a contract-invariant check would have failed fast.
- HL-287: stale active state directories persisted in `~/.workflows/` long after their change-ids had been archived — a stale-state check would have flagged them.
- HL-290: DuckDB `tool_calls` table was referenced from `cost_report.py` before any schema check confirmed it existed on the operator's machine — a schema check would have pointed to `ensure_schema()` at the moment of failure instead of a DuckDB `Catalog Error` deep inside a report run.

There is no single command today that verifies the orchestrator installation is internally consistent. `make doctor` only checks the presence of six top-level paths. `make stale` globs a path structure (`$ORCHESTRATOR_HOME/changes/...`) that no longer matches the real layout (`~/.workflows/<change-id>/state.yaml`). `make lint-contracts` uses `grep` on YAML files and misses the `id:` field.

`orchestrator doctor` closes this gap with a single command that can be run locally by an operator or as a CI gate after a PR touches contracts, scripts, or schema.

## What Changes

A new `orchestrator doctor` subcommand is added to `bin/orchestrator`, backed by a new Python module `config/scripts/orchestrator_next/doctor.py`. The command runs seven independent checks, prints a PASS/WARN/FAIL table to stdout, and exits with 0 (all pass), 1 (warnings only), or 2 (at least one failure). The existing Makefile targets are left in place and untouched.

## Requirements

### Functional

1. **FR-1 (Check 1 — state.yaml validity)**: For each `~/.workflows/*/state.yaml`, attempt to parse via `parser.load_state()`. Parse success → PASS per file; parse failure → FAIL with file path and error detail. Aggregated into a single "state.yaml validity" row showing FAIL if any file failed, PASS otherwise.

2. **FR-2 (Check 2 — active-vs-archived mismatch)**: For each `~/.workflows/<change-id>/`, scan `spec/changes/archive/` basenames. If any archive basename contains `change_id` as a substring, emit WARN with both paths. Row status is WARN if any match is found, PASS otherwise.

3. **FR-3 (Check 3 — contract invariants)**: For each `config/steps/*.yaml`, load via `yaml.safe_load()` on the raw file. Check `"id" in data`, `"inputs" in data`, `"outputs" in data` on the raw dict. Parse errors or missing keys → FAIL with file path and missing field name. Row status is FAIL if any contract is invalid, PASS otherwise.

4. **FR-4 (Check 4 — inline script existence)**: For each contract with `inline: true`, resolve `run:` relative to `$ORCHESTRATOR_HOME`. Missing file → FAIL with contract id and expected script path. Row status is FAIL if any script is missing, PASS otherwise.

5. **FR-5 (Check 5 — agent file existence)**: For each contract, read `agent:` field. If value is `inline`, skip. Otherwise search `$ORCHESTRATOR_HOME/agents/<name>.md` and `~/.claude/agents/<name>.md`. Missing in both → WARN with agent name and contract id (not FAIL: agent files may live in locations this check does not know). Row status is WARN if any agent missing, PASS otherwise.

6. **FR-6 (Check 6 — DuckDB schema)**: Connect to metrics DB (resolved via `METRICS_DB` or `$ORCHESTRATOR_HOME/metrics.duckdb`). Call `upsert.ensure_schema()`. Query DuckDB information schema for `step_events` and `tool_calls` tables. Missing table → FAIL with table name and remediation hint ("run `orchestrator next` once or call `ensure_schema()`"). Row status is FAIL if either table is missing, PASS otherwise.

7. **FR-7 (Check 7 — workflow plan consistency)**: For each `~/.workflows/<change-id>/state.yaml`, read `workflow_plan.active`. Normalize each step (dict → `.id`, `"step if flag"` → `step`, plain string → itself) using the same logic as `dispatch._phase_step_ids` and `record.py:42-44`. For each normalized step id, check `config/steps/<step-id>.yaml` exists. Missing contract → WARN with change-id and step-id. Row status is WARN if any step has no contract, PASS otherwise.

8. **FR-8 (Exit codes)**: Exit 0 if all rows PASS. Exit 1 if at least one WARN and no FAIL. Exit 2 if at least one FAIL.

9. **FR-9 (Output format)**: Print a three-column table (check name, status, detail) to stdout. Detail is a single line per row; multi-file failures are summarized as "N of M failed; first: <path>" with full list on the row below or truncated to fit. No JSON, no verbose flag, no `--fix` flag in this iteration.

10. **FR-10 (ORCHESTRATOR_HOME required)**: If `ORCHESTRATOR_HOME` is not set, the command prints an error and exits with a non-zero code (same behavior as `orchestrator cost`) before running any checks.

### Non-Functional

1. **NFR-1 (Python-only)**: All logic is Python. No subprocess, no bash pipelines, no shelling out to `make`. Justification: project learning `bash-fragility-prefer-python-for-new-code` in `spec/project.yaml`.

2. **NFR-2 (Table output)**: Output is pure stdout, no TTY detection, no color codes, no third-party table library. Format with fixed-width columns using Python string formatting so the output is copy-pastable and CI-log-friendly.

3. **NFR-3 (Size budget)**: `doctor.py` should fit in roughly 100 lines of Python excluding imports and docstrings, per HL-294 scope.

4. **NFR-4 (No new dependencies)**: Reuse `parser`, `upsert`, and stdlib only. No new third-party packages.

5. **NFR-5 (Idempotent)**: Running `orchestrator doctor` never mutates state. `ensure_schema()` is idempotent so calling it during the DuckDB check is safe.

## Architecture

| File | Change |
|------|--------|
| `config/scripts/orchestrator_next/doctor.py` | **Create.** Contains `CheckResult` namedtuple, seven `check_*` functions, `run_all()`, and a small `_format_table()` helper. |
| `bin/orchestrator` | **Modify.** Add `"doctor"` to the known-subcommand guard (around line 192), add a `_doctor_main(args[1:])` import-and-dispatch branch modelled on the existing `cost` branch (around line 197). |
| `config/scripts/orchestrator_next/tests/test_doctor.py` | **Create.** One test per check function plus a CLI smoke test. |

Data flow: CLI → `_doctor_main(args)` → `doctor.run_all(args)` → seven check functions, each returning a `CheckResult` → `_format_table(results)` → stdout → exit code derived from aggregated statuses.

Reused components:
- `parser.load_state()` for FR-1, FR-7
- `yaml.safe_load()` directly for FR-3, FR-4, FR-5 (raw dict key checks; `_load_contract` is a runtime loader that defaults missing fields and cannot detect absent keys)
- `upsert.ensure_schema()` for FR-6
- `upsert._DDL_STEP_EVENTS` / `_DDL_TOOL_CALLS` column lists (read-only, for expected-table names only in this iteration — column-level schema validation is not required)

## Test Strategy

### Test File Paths

- `config/scripts/orchestrator_next/doctor.py` → `config/scripts/orchestrator_next/tests/test_doctor.py`
- `bin/orchestrator` doctor dispatch → same test file, CLI smoke test invoking `_doctor_main` directly with argparse-compatible args

### Coverage Targets

90% overall for `doctor.py`. Each of the seven check functions has at least one PASS test and one failure-path test (WARN or FAIL as appropriate).

### Key Test Scenarios

- UC-1 happy path: all seven checks return PASS on a clean fixture → exit 0.
- UC-2 stale state: fixture with active `change_id` matching an archive basename → check 2 returns WARN, `run_all` exits 1.
- UC-3 missing contract field: fixture `steps/bad.yaml` without `id:` → check 3 returns FAIL, `run_all` exits 2.
- UC-4 missing inline script: fixture contract `run: scripts/inline/missing.sh` with no file on disk → check 4 returns FAIL.
- UC-E1 malformed state.yaml: fixture with syntax error → check 1 returns FAIL with path and error snippet in detail.
- UC-E2 missing DuckDB table: point `METRICS_DB` at a fixture DB that has `step_events` but not `tool_calls`; run check 6 → FAIL with table name in detail. (Note: `duckdb.connect` + `ensure_schema` creates tables on a fresh file, so the failure scenario is a pre-existing DB missing a table, not an absent file.)
- UC-E3 agent sentinel: contract with `agent: inline` → check 5 skips it (contract counted, not flagged).
- UC-E4 no `ORCHESTRATOR_HOME`: unset env, run `_doctor_main` → non-zero exit and clear error before any check runs.
- CLI smoke test: `_doctor_main([])` on the real repo with valid env returns an int exit code and produces non-empty stdout.

## Acceptance Criteria

- AC-1: Given a clean install, when the operator runs `orchestrator doctor`, then all seven rows print PASS and the command exits 0. [traces: UC-1]
- AC-2: Given a `~/.workflows/<id>/` whose id is a substring of an entry in `spec/changes/archive/`, when `orchestrator doctor` runs, then check 2 prints WARN with both paths and exit code is 1. [traces: UC-2]
- AC-3: Given a `config/steps/*.yaml` missing `id:`, `inputs:`, or `outputs:`, when `orchestrator doctor` runs, then check 3 prints FAIL with file path and missing field and exit code is 2. [traces: UC-3]
- AC-4: Given a contract with `inline: true` whose `run:` path does not exist under `$ORCHESTRATOR_HOME`, when `orchestrator doctor` runs, then check 4 prints FAIL with the contract id and expected path and exit code is 2. [traces: UC-4]
- AC-5: Given a `state.yaml` with a YAML syntax error, when `orchestrator doctor` runs, then check 1 prints FAIL with the file path and a parser error snippet in the detail column. [traces: UC-E1]
- AC-6: Given a `metrics.duckdb` that exists but is missing the `tool_calls` table, when `orchestrator doctor` runs, then check 6 prints FAIL with the table name and a remediation hint and exit code is 2. [traces: UC-E2]
- AC-7: Given a contract with `agent: inline`, when check 5 runs, then the contract is not flagged as a missing agent. [traces: UC-E3]
- AC-8: Given `ORCHESTRATOR_HOME` is unset, when `orchestrator doctor` runs, then the command exits non-zero with a clear error message and no checks are executed. [traces: UC-E4]
- AC-9: Given all checks produce at least one WARN and no FAILs, when `orchestrator doctor` runs, then exit code is 1. [traces: UC-2]
- AC-10: Given at least one FAIL, when `orchestrator doctor` runs, then exit code is 2 regardless of how many WARNs exist. [traces: UC-3, UC-4]

## Alternatives Considered

**Alternative 1: Check registry with dataclass per check (Approach B from discovery)**
Rejected. Over-engineers before `--fix` requirements are defined. Seven similar check functions do not justify a registry abstraction.

**Alternative 2: Delegate to existing Makefile targets (Approach C from discovery)**
Rejected. `make stale` uses an outdated path layout; `make lint-contracts` is weaker than the required contract check; mixing bash and Python output makes table formatting unworkable.

**Alternative 3: Derive expected DuckDB columns from `ensure_schema()` DDL at runtime (OQ-5 option)**
Rejected. Hard-coding expected table/column names in `doctor.py` is simpler, decouples the check from DDL string parsing, and this iteration only requires table presence, not column equality.

**Alternative 4: Implement `--fix` in this iteration (OQ-3)**
Rejected. Out of scope. Only safe candidate (archive stale active dirs) is deferred until requirements are defined.

## Impact

No breaking changes. New subcommand is additive. Existing Makefile targets are left in place and will be deprecated in a separate decision.

## Decisions

- Severity assignments per OQ-2: parse failure = FAIL; stale active state = WARN; missing required contract field = FAIL; missing inline script = FAIL; missing agent file = WARN (relaxed from proposal — agent files may live in locations this check does not know); missing DuckDB table = FAIL; workflow plan step without contract = WARN.
- `--fix` is out of scope for this iteration. Flag is not implemented.
- `ORCHESTRATOR_HOME` is required. Unset → hard error before any checks run, mirroring the `cost` subcommand.
- DuckDB expected column and table names are hard-coded in `doctor.py`. No runtime DDL parsing.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
