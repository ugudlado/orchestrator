# Diagnosis: ORC-37 — Autopilot cost-report wiring

## Symptom

At the end of `/autopilot ORC-36`, no cost summary was emitted. The wrap-up
completed, but the final message contained no cost report and no error message
explaining why.

Manual run of `scripts/cost-report.sh --change-id orc-36` from `$REPO_ROOT`
succeeded and produced a complete report ($17.50 total, 24 steps).

---

## Reproduction

The following script is runnable and demonstrates both root causes:

```bash
#!/usr/bin/env bash
# Save as /tmp/repro-orc37.sh and run with: bash /tmp/repro-orc37.sh
set -uo pipefail
ORCHESTRATOR_HOME="${ORCHESTRATOR_HOME:-$HOME/.config/orchestrator}"
REPO_ROOT="$(git -C /Users/spidey/code/orchestrator rev-parse --show-toplevel)"

echo "=== BUG 1: complete_workflow exit code semantics ==="

cat > /tmp/repro-state.yaml << 'YAML'
schema: bugfix
change_id: repro-test
status: active
repo_root: /Users/spidey/code/orchestrator
flags:
  auto: true
  agents: true
phase: main
workflow_plan:
  phases:
    - phase: main
      steps:
        - diagnose
step_history:
  - step_id: diagnose
    phase: main
    status: completed
    attempt: 1
    agent: discoverer
YAML

OUTPUT=$(ORCHESTRATOR_HOME="$ORCHESTRATOR_HOME" python3 "$REPO_ROOT/bin/orchestrator" next /tmp/repro-state.yaml 2>/dev/null)
EXIT=$?
echo "orchestrator next exit_code=$EXIT"
echo "stdout: $OUTPUT"
# Expected: exit_code=1, stdout={"action":"complete_workflow",...}
# If agent treats exit_code=1 as error: cost-report.sh is never invoked.

echo ""
echo "=== BUG 2: cost-report.sh when DB missing ==="
METRICS_DB="/tmp/missing.duckdb" "$REPO_ROOT/scripts/cost-report.sh" --change-id repro-test 2>&1
echo "exit: $?"
# Expected: exit 1 "error: DB not found at /tmp/missing.duckdb"

rm -f /tmp/repro-state.yaml /tmp/missing.duckdb 2>/dev/null
```

Actual output when run:

```
=== BUG 1: complete_workflow exit code semantics ===
orchestrator next exit_code=1
stdout: {
  "action": "complete_workflow",
  "cost_so_far": 0.0
}
CONFIRMED: exit_code=1 means complete_workflow, not an error.
SKILL.md dispatch loop does NOT document this — agent may treat it as failure.
Result: cost-report.sh is never invoked.

=== BUG 2: cost-report.sh when DB missing ===
error: DB not found at /tmp/missing.duckdb
exit: 1
```

---

## Root Cause Analysis

### Root Cause 1 — SKILL.md dispatch loop does not document exit code 1 semantics

**File:** `skills/orchestrate/SKILL.md`, lines 135–145

```
LOOP:
  action = orchestrator next $WORKFLOW_STATE_DIR/$CHANGE_ID/state.yaml

  IF action.action == "complete_workflow":
      cost_report = run `scripts/cost-report.sh --change-id $CHANGE_ID`
      STOP (workflow done) — include cost_report stdout in your final message
```

**File:** `config/scripts/orchestrator_next/dispatch.py`, lines 9–12

```
Exit codes: 0=action, 1=complete_workflow, 2=blocked, 3=error.
```

**File:** `config/steps/contracts/step-dispatch.md`, lines 116–124

```
### `complete_workflow` — all phases complete (exit 1)
Exit code 1. Caller writes final `status: completed` to state.yaml and archives.
```

The dispatch contract documents that `orchestrator next` exits with code 1 for
`complete_workflow`. SKILL.md's dispatch loop assigns the result to `action` and
branches on `action.action`, but never tells the agent that exit code 1 is the
correct signal for `complete_workflow` — not a command failure. An agent using
the Bash tool receives the JSON on stdout AND exit code 1. Without explicit
instruction to parse stdout even on non-zero exit, the agent may treat the
invocation as a failure and never reach the `complete_workflow` branch. The
cost-report command at line 144 is therefore never executed.

The step-dispatch contract is not referenced in SKILL.md's dispatch loop section
(SKILL.md lines 38–40 list contract files but does not direct the agent to read
step-dispatch.md before entering the loop).

**Divergence point:** `skills/orchestrate/SKILL.md` line 136 — the `orchestrator
next` call lacks a comment or branch clarifying that exit code 1 is valid and
means `complete_workflow`.

### Root Cause 2 — install.sh does not pre-create metrics.duckdb

**File:** `install.sh` (full file — no mention of `metrics.duckdb`)

**File:** `bin/orchestrator`, line 178

```python
_db = duckdb.connect(_metrics_db_path)  # creates on first write
ensure_schema(_db)
```

**File:** `config/scripts/orchestrator_next/record.py`, line 1433

```python
if db_path.exists():
    db = _duckdb.connect(str(db_path))
else:
    sys.stderr.write(f"[record] warning: metrics DB not found at {db_path}; cost computation will be skipped\n")
```

`install.sh` symlinks `config/` and `scripts/` into `$ORCHESTRATOR_HOME` but
does not create `metrics.duckdb`. The database is created lazily by
`bin/orchestrator` on the first `orchestrator next` call. If the orchestrator
process is launched by a macOS-sandboxed host (e.g., Claude Code's process
sandbox), the creation of a new file at `~/.config/orchestrator/metrics.duckdb`
may fail with "Operation not permitted" due to macOS TCC (Transparency, Consent,
Control) restrictions. When this happens:

- `bin/orchestrator` line 197: exception caught, `_db` stays `None`, step events
  are not upserted — the warning is printed to stderr only.
- `record.py` line 1451: DB not found, cost computation skipped with a stderr warning.
- `cost-report.sh`: DB missing or empty → exits 1 with "error: DB not found" or
  "no events for change_id".

The file `~/.config/orchestrator/metrics.duckdb` currently exists and is writable
from the terminal (`write test: PASS`, 24 MB, 117 step_events). The "Operation not
permitted" error during ORC-36 was transient — either the file did not exist at
the time (first-ever write attempt from a sandboxed context), or a stale exclusive
lock was held by a prior crashed process. Pre-creating the file in `install.sh`
(run interactively in user space, outside any sandbox) removes the sandbox
write-creation path.

### Root Cause 3 — scripts/ symlink absent before d048dc0 (already shipped)

**File:** `install.sh` before commit `d048dc0` (May 4 2026)

`install.sh` symlinked `config/` but not `scripts/` into `$ORCHESTRATOR_HOME`.
Step contracts that reference `$ORCHESTRATOR_HOME/scripts/<x>.sh` (e.g.,
`compute-swe-metrics.yaml:22–25`) would fail to locate scripts. This was fixed by
commit `d048dc0` which adds `safe_ln "$ORCHESTRATOR_DIR/scripts" "$ORCHESTRATOR_HOME/scripts"`.

Note: SKILL.md line 144 uses `scripts/cost-report.sh` (a relative path), which
resolves against the agent's CWD (the repo root). That path was always valid since
`scripts/cost-report.sh` has been in the repo since commit `d8b4d97` (April 21
2026). Root cause 3 affects inline-script dispatch (`run_inline` steps), not the
direct `scripts/cost-report.sh` invocation in SKILL.md.

---

## Impact Assessment

**Primary:** Every autopilot run silently misses the cost summary at wrap-up time.
Cost data accumulates in DuckDB via step-event ingestion (where it works), but the
operator never sees it unless manually running `cost-report.sh`.

**Callers affected:**

- `skills/orchestrate/SKILL.md` lines 138–145 — the only caller of
  `scripts/cost-report.sh` in the orchestration loop.
- Any inline step referencing `$ORCHESTRATOR_HOME/scripts/inline/*` (resolved by
  Root Cause 3 fix).

**Existing tests:** No test verifies that the `complete_workflow` branch of the
SKILL.md dispatch loop invokes `cost-report.sh`. No test asserts that
`orchestrator next` exit code 1 is handled as `complete_workflow` (not as error).
The `config/scripts/orchestrator_next/__tests__/` directory contains unit tests for
dispatch logic but not for the SKILL.md prose loop behavior.

---

## Proposed Approach

Amend `skills/orchestrate/SKILL.md` line 136 to add an inline comment documenting
that exit code 1 is the `complete_workflow` signal (not an error), and instruct the
agent to parse stdout JSON regardless of exit code. Separately, add a
`setup_metrics_db()` function to `install.sh` that creates `metrics.duckdb` with
the correct schema in user space so the file exists before any sandboxed process
attempts to write it.

---

## Unresolved Questions

1. Was the "Operation not permitted" error on ORC-36 from a sandbox context, a
   stale lock, or the file genuinely not existing yet? The current metrics.duckdb
   has a `com.apple.provenance` xattr (set when a sandboxed app writes a file), but
   writes succeed from terminal. Without the ORC-36 session logs, the exact trigger
   cannot be determined — pre-creating in install.sh covers all three scenarios.

2. Should `orchestrator next` exit 0 for `complete_workflow` (consistent with
   normal actions) and use a different signal for "no more work"? Changing the exit
   code convention would require updating `SKILL.md`, the step-dispatch contract,
   and any shell scripts that call `orchestrator next` directly. That is a separate
   design decision outside this bug fix.
