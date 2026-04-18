# Design: orchestrator doctor health check subcommand

## Context

The orchestrator codebase has grown three validation surfaces (Makefile, parser, DDL) that are not tied together by any single command. Operators discover drift — malformed state, missing scripts, outdated DuckDB schema — at workflow execution time, often deep in a dispatch or report run. Two recent dogfood merges (HL-287, HL-290) landed defects that a structural health check would have caught. The `cost_report.py` / `record.py` pattern already establishes how a Python module is wired into `bin/orchestrator` via a `_xxx_main(args)` dispatch. The doctor feature extends that pattern rather than inventing a new one.

## Goals / Non-Goals

### Goals

- One command that runs all seven structural checks and produces actionable, copy-pastable output.
- Clear exit code semantics so CI can gate on it (0 pass, 1 warn, 2 fail).
- Reuse of `parser.load_state()` and `upsert.ensure_schema()` where appropriate; raw `yaml.safe_load` for contract structural checks (Check 3/4/5) since `_load_contract` is a runtime loader that defaults missing fields and cannot detect absent keys.
- Minimum viable abstraction: flat check functions, no registry, no `--fix` plumbing.

### Non-Goals

- `--fix` auto-remediation in this iteration.
- Column-level DuckDB schema validation (table presence only).
- Multi-repo fleet scanning.
- JSON output, colored output, or verbose/quiet modes.
- Removal or modification of `make doctor`, `make stale`, `make lint-contracts`.

## Approaches Considered

### Approach 1: Flat check functions in a single module (mirrors `cost_report.py`)

Seven `check_*` functions each returning a `CheckResult(name, status, detail)` namedtuple. A `run_all(args)` function invokes them in order, accumulates results, prints a table, and returns an exit code.

- Pros: Matches the existing pattern exactly. Each check is independently unit-testable. No new abstractions. Fits in ~100 lines.
- Cons: Adding `--fix` later needs a parallel fix function per check. Not a concern now.

### Approach 2: Check registry with dataclass per check

`@register` decorator populates a module-level list of `Check(name, run_fn, fix_fn, severity)` records.

- Pros: Cleaner `--fix` extension path. Consistent interface if many more checks are added.
- Cons: Over-engineered for seven checks; `--fix` is explicitly out of scope; adds indirection that obscures the seven-line loop that `run_all` would otherwise be.

### Selected Approach

Approach 1. Constraint: HL-294 caps the module at ~100 lines and `--fix` is out of scope — both of which make Approach 2's main benefit irrelevant. Approach 1 is strictly simpler and matches the nearest existing neighbor (`cost_report.py`).

## High-Level Design

### Architecture Overview

```
bin/orchestrator
  └── _doctor_main(args)          # argparse + dispatch, ~10 lines
       └── doctor.run_all(args)
            ├── check_state_valid()
            ├── check_active_vs_archive()
            ├── check_contracts()
            ├── check_inline_scripts()
            ├── check_agent_files()
            ├── check_duckdb_schema()
            └── check_workflow_plans()
       └── _format_table(results) → stdout
       └── sys.exit(exit_code_from(results))
```

Each `check_*` function returns a single `CheckResult`. Detail strings are constructed inside each check and may include newline-separated per-item detail when multiple items failed, but the row itself is one line in the table.

### Key Abstractions

- `CheckResult = namedtuple("CheckResult", ["name", "status", "detail"])` where `status` is the string `"PASS"`, `"WARN"`, or `"FAIL"`.
- No base class, no registry, no decorator. Each check is a plain function taking no arguments (or a single `orch_home: Path` argument for the two checks that need it — determined at implementation time).

## Low-Level Design

### Components

**`config/scripts/orchestrator_next/doctor.py`** (new file, ~100 lines)

```
imports: os, sys, pathlib, glob, collections.namedtuple, yaml, duckdb
         from . import parser, upsert

CheckResult = namedtuple("CheckResult", "name status detail")

EXPECTED_TABLES = ("step_events", "tool_calls")   # hard-coded per OQ-5

def check_state_valid() -> CheckResult: ...
def check_active_vs_archive(orch_home: Path) -> CheckResult: ...
def check_contracts(orch_home: Path) -> CheckResult: ...
def check_inline_scripts(orch_home: Path) -> CheckResult: ...
def check_agent_files(orch_home: Path) -> CheckResult: ...
def check_duckdb_schema(db_path: Path) -> CheckResult: ...
def check_workflow_plans(orch_home: Path) -> CheckResult: ...

def run_all(args) -> int:           # returns exit code
def _format_table(results) -> str:  # fixed-width 3-col output
def _doctor_main(argv) -> int:      # re-exported for bin/orchestrator
```

**Check 1 — `check_state_valid()`**
- Input: glob `~/.workflows/*/state.yaml`.
- For each file, call `parser.load_state(path)`. Catch parse exceptions, collect `(path, error)` tuples.
- Return: `CheckResult("state.yaml validity", "FAIL"|"PASS", detail)`.

**Check 2 — `check_active_vs_archive(orch_home)`**
- Input: list of dirnames under `~/.workflows/`, list of basenames under `orch_home / "spec" / "changes" / "archive"`.
- For each active change id, test if `change_id in archive_basename` for any archive basename. Collect matches.
- Return: `CheckResult("active vs archived", "WARN"|"PASS", detail)`.

**Check 3 — `check_contracts(orch_home)`**
- Input: glob `orch_home / "config" / "steps" / "*.yaml"`.
- For each file, call `yaml.safe_load(open(path))` to get the raw dict. Check `"id" in data`, `"inputs" in data`, `"outputs" in data` on the raw dict. Collect `(path, missing_keys)` for any file with missing required keys. Catch YAML parse errors and collect them as failures too.
- Return: `CheckResult("contract invariants", "FAIL"|"PASS", detail)`.

**Check 4 — `check_inline_scripts(orch_home)`**
- Input: glob `orch_home / "config" / "steps" / "*.yaml"`. Parse each via `yaml.safe_load`. For each dict where `data.get("inline") is True`, resolve `data.get("run", "")` relative to `orch_home`. Check `Path.exists()`.
- Return: `CheckResult("inline scripts exist", "FAIL"|"PASS", detail)`.

**Check 5 — `check_agent_files(orch_home)`**
- Input: glob `orch_home / "config" / "steps" / "*.yaml"`. Parse each via `yaml.safe_load`. For each dict, read `data.get("agent")`. If value is the sentinel string `"inline"` or absent, skip. Otherwise look for `orch_home / "agents" / f"{name}.md"` and `Path.home() / ".claude" / "agents" / f"{name}.md"`. Missing in both → collect.
- Return: `CheckResult("agent files exist", "WARN"|"PASS", detail)`.

**Check 6 — `check_duckdb_schema(db_path)`**
- Connect via `duckdb.connect(str(db_path))`. Call `upsert.ensure_schema(conn)` (idempotent, safe on a fresh db).
- Query `SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'`. Compare result set to `EXPECTED_TABLES`.
- Missing table → FAIL with remediation hint.
- Return: `CheckResult("duckdb schema", "FAIL"|"PASS", detail)`.

**Check 7 — `check_workflow_plans(orch_home)`**
- Input: glob `~/.workflows/*/state.yaml`.
- For each state, read `workflow_plan.active` (list). Normalize each item:
  - `dict` → `item["id"]`
  - `str` with ` if ` → split on ` if `, take left side, strip
  - plain `str` → itself
- For each normalized id, test `orch_home / "config" / "steps" / f"{id}.yaml"` exists. Missing → collect `(change_id, step_id)`.
- Return: `CheckResult("workflow plan consistency", "WARN"|"PASS", detail)`.

This normalization mirrors `dispatch._phase_step_ids` (discovery: `dispatch.py:64`) and `record.py:42-44`. The same three-line helper may be duplicated inline in this check rather than exported from `dispatch.py`, since pulling it out of `dispatch.py` is a refactor that exceeds the scope of HL-294.

**`run_all(args)`**
```
1. orch_home = Path(os.environ["ORCHESTRATOR_HOME"])   # already validated in _doctor_main
2. db_path = Path(os.environ.get("METRICS_DB") or orch_home / "metrics.duckdb")
3. results = [
     check_state_valid(),
     check_active_vs_archive(orch_home),
     check_contracts(orch_home),
     check_inline_scripts(orch_home),
     check_agent_files(orch_home),
     check_duckdb_schema(db_path),
     check_workflow_plans(orch_home),
   ]
4. print(_format_table(results))
5. return 2 if any FAIL else (1 if any WARN else 0)
```

**`_format_table(results)`**
- 3 columns: name (left-padded to widest name), status (4-char field), detail.
- One line per result. Detail is truncated to terminal-agnostic width (e.g., 120 chars) with an ellipsis. Full detail is not hidden — multi-item detail is joined with `"; "` first, then truncated.
- Pure string formatting. No `tabulate`, no `rich`.

### CLI Wiring (`bin/orchestrator`)

Two edits only. Exact locations from discovery `Technical Context`:

1. **Line ~192** (subcommand guard): the existing tuple `("next", "cost", "record", ...)` — add `"doctor"` so unknown-subcommand detection does not reject it.

2. **Line ~197-199** (dispatch branch after `cost`): add
   ```python
   if sub == "doctor":
       from orchestrator_next.doctor import _doctor_main
       sys.exit(_doctor_main(args[1:]))
   ```

`_doctor_main(argv)`:
- Builds a tiny `argparse.ArgumentParser(prog="orchestrator doctor")`. No flags in this iteration (argparse is used for `--help` only and future extension).
- Validates `ORCHESTRATOR_HOME` is set — if not, prints error to stderr and returns a non-zero code (mirrors cost subcommand behavior).
- Calls `run_all(args)` and returns its int.

### Data Flow

```
env(ORCHESTRATOR_HOME, METRICS_DB?)
        │
        ▼
  _doctor_main(argv)
        │
        ▼
   run_all(args)
        │
        ├── filesystem (config/steps, scripts/inline, agents, ~/.workflows, spec/changes/archive)
        ├── duckdb (metrics.duckdb)
        │
        ▼
  list[CheckResult]
        │
        ▼
 _format_table → stdout
        │
        ▼
   exit code (0/1/2)
```

No mutation of filesystem or DB. `ensure_schema()` is idempotent DDL.

### State Management

None. Command is read-only (plus idempotent DDL via `ensure_schema`).

### Error Handling

- `ORCHESTRATOR_HOME` unset → error to stderr, non-zero exit before any check runs.
- Per-check exceptions never propagate out of a check function. Each `check_*` wraps its file/DB operations in `try/except Exception as e` and converts the exception to a FAIL `CheckResult` with the exception class and message in the detail. This keeps one broken check from masking the other six.
- The DuckDB connection is opened inside `check_duckdb_schema` and closed in a `finally` block. If the file cannot be opened, the check returns FAIL with the OS error message.
- YAML parse errors from `load_state` / `_load_contract` already raise specific exception types; they are caught by the outer per-check `try` and rendered as FAIL detail.

## Constraints

- No new third-party dependencies.
- No subprocess calls.
- ~100 lines of Python for `doctor.py` excluding imports and docstrings.
- Must not delete or modify existing Makefile targets.

## Trade-offs

- **Table rendering is hand-rolled.** A `tabulate` dependency would be 2 lines shorter. Rejected because NFR-4 forbids new deps and string-format output is trivial.
- **Workflow-plan normalization is duplicated** from `dispatch.py` / `record.py` instead of extracted into a shared helper. Accepted because extracting the helper touches three files and exceeds HL-294 scope; the duplication is three lines and documented in this design.
- **Agent file check returns WARN instead of FAIL.** Trade-off against OQ-2's original FAIL proposal. Agent files genuinely may live in other locations, and the architect downgrades this to WARN to avoid false positives on valid installs.
- **Each check re-globs / re-parses** rather than sharing a cached contracts list. Accepted because seven checks over ~8 contracts and ~11 state files is negligible I/O and sharing state would introduce an implicit ordering between checks.

## Decisions

- Flat functions, not registry → matches `cost_report.py` pattern, smallest code → future `--fix` adds one parallel function per check if/when needed.
- Hard-code expected DuckDB tables in `doctor.py` (OQ-5) → avoids coupling to DDL string parsing → doctor must be updated alongside new tables (acceptable, single-site change).
- `ORCHESTRATOR_HOME` required (OQ-4) → matches `cost` subcommand → consistent CLI behavior across orchestrator subcommands.
- `--fix` out of scope (OQ-3) → keeps iteration small → separate ticket when remediations are defined.
- Agent missing = WARN (OQ-2) → avoids false positives from agents stored in user-specific dirs → operator still sees the signal.
- Normalization duplicated in check 7 rather than extracted → keeps HL-294 scope tight → refactor ticket can extract the helper later.

## Open Questions

- None blocking implementation. All OQ-1 through OQ-5 have been resolved in the spec Decisions section.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
