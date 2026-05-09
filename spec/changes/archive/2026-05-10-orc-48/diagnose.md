# Diagnosis: ORC-48 — Agent spawn usage not flowing into DuckDB metrics

## Symptom

All steps in cost reports show as `inline` agent with `output_tokens=0` and
`model=__default__`, even when agent spawns ran and their usage blocks were
passed to `orchestrator done`. In orc-30: the `agent_report` table shows a
single row `(inline, 17 steps, 0 output_tokens)` despite 4+ distinct agent
spawns (discoverer, architect, developer, reviewer). The `rework_ratio` of
100% cited in the bug report is from `feature_metrics.rework_rate` (a git
commit count ratio) and is unrelated to this bug.

## Reproduction

```bash
# Minimal reproduction — no DuckDB or real state.yaml needed.
# Run from any directory with orchestrator_next on sys.path:

cd /Users/spidey/code/feature_worktrees/orc-48
python3 - << 'PYEOF'
import sys, tempfile, yaml
sys.path.insert(0, 'config/scripts')
from orchestrator_next.record import record

state = {
    'schema': 'bugfix', 'change_id': 'orc-48-repro',
    'repo_root': '/tmp', 'phase': 'main', 'flags': {},
    'workflow_plan': {'main': {'active': ['diagnose']}},
    'step_history': []
}
with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
    yaml.safe_dump(state, f)
    state_path = f.name

# CASE 1: payload WITHOUT 'agent' field — how SKILL.md line 210 is followed
payload_missing_agent = {
    'step_id': 'diagnose', 'phase': 'main', 'status': 'completed',
    'outputs': {'diagnosis_result': 'diagnose.md'},
    'usage': {'input_tokens': 74514, 'output_tokens': 3210},
    # 'agent' key ABSENT — driver omits it, following SKILL.md template
}
record(state_path, payload_missing_agent, db=None)

with open(state_path) as f:
    state_after = yaml.safe_load(f)
last = state_after['step_history'][-1]
print(f"[CASE 1] agent written: {last['agent']!r}   EXPECTED: 'discoverer'  BUG: {last['agent'] == 'inline'}")
PYEOF
```

Expected output:
```
[CASE 1] agent written: 'discoverer'   EXPECTED: 'discoverer'  BUG: False
```

Actual output:
```
[CASE 1] agent written: 'inline'   EXPECTED: 'discoverer'  BUG: True
```

Live DuckDB confirmation (orc-30 already ran):
```bash
python3 -c "
import duckdb
db = duckdb.connect('/Users/spidey/.config/orchestrator/metrics.duckdb', read_only=True)
rows = db.execute(\"SELECT agent_name, input_tokens, output_tokens, step_count FROM agent_report WHERE change_id='orc-30'\").fetchall()
for r in rows: print(r)
"
# Actual output:
# ('inline', 242250, 0, 17)
# Expected:
# ('discoverer', <N>, <M>, 2)
# ('architect', <N>, <M>, 1)
# ('developer', <N>, <M>, 3)
# ... (one row per distinct agent)
```

## Root Cause

Two distinct root causes work together to produce the observed symptom.

### Root cause 1 — `agent` missing from `orchestrator done` payload template (PRIMARY)

**File:** `/Users/spidey/.claude/skills/orchestrate/SKILL.md` (global, also
mirrored at `/Users/spidey/code/feature_worktrees/orc-48/skills/orchestrate/SKILL.md`)
**Line:** 210

```
orchestrator done state.yaml <<< {step_id, phase, status, outputs, usage, evidence}
```

The pseudocode template that the LLM driver follows when calling `orchestrator done`
does **not include the `agent` field**. The driver constructs its JSON payload
from this template literally.

In `record.py` at line 1189:
```python
"agent": payload.get("agent", "inline"),
```

When `agent` is absent from the payload, `record.py` defaults to `"inline"`.
Every completed step_history entry is written with `agent: inline` regardless
of whether the step ran as a discoverer, developer, architect, or reviewer spawn.

This is then written to DuckDB via `upsert_step_event` at `upsert.py` line 503:
```python
entry.agent,  # always 'inline' when agent not in payload
```

The `agent_report` view therefore aggregates all steps under the single
`inline` row.

**Why Check B in record.py doesn't catch it:**
Lines 1078-1092 enforce that agent steps must have tokens, but this check
uses `agent = payload.get("agent", "inline")`. Since the payload omits
`agent`, `agent` evaluates to `"inline"`, and the check is bypassed entirely
(the guard is `if agent != "inline"`). This means mis-labelled agent steps
also skip the usage enforcement guard.

### Root cause 2 — `output_tokens` and `model` always NULL / `__default__`

**File:** `/Users/spidey/.claude/skills/orchestrate/SKILL.md`, line 210
**Mechanism:** `agent_id` absent from payload template → JSONL enrichment block never triggered

`record.py` lines 1143-1177 contain a JSONL enrichment block that reads
`output_tokens`, `model`, `cache_read_input_tokens`, and `cache_creation_input_tokens`
from the subagent's Claude Code JSONL file (via `extract_agent_usage()` in
`jsonl_usage.py`). This block is the authoritative source for output token counts
since the driver `<usage>` block is not always complete. The enrichment block
activates only when `agent_id` is present in the payload:

```python
# record.py lines 1143-1148
agent_id = payload.get("agent_id") or usage.get("agent_id")
if agent_id:
    jsonl_usage = extract_agent_usage(repo_root, agent_id)
    for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens", "model", "turns"):
        if jsonl_usage.get(key) is not None:
            usage[key] = jsonl_usage[key]
```

SKILL.md line 210's payload template omits `agent_id` (confirmed: zero occurrences
of `agent_id` in either SKILL.md copy). The JSONL enrichment block therefore
**never executes** for any agent step. The driver passes whatever partial `usage`
dict it collected from the result `<usage>` block — which for orc-30 yielded
`input_tokens` but not `output_tokens` (orc-30's state.yaml confirms all token-carrying
entries have `input_tokens` non-null and `output_tokens: None`).

The `model: __default__` is a downstream consequence: `_compute_cost_usd` in
`record.py` (lines 484-559) falls back to `__default__` when `usage.model` is
absent. With JSONL enrichment skipped, `model` is never populated.

## Impact

**Scope:** All workflows run via the `orchestrate` or `autopilot` skills where
agent steps complete. Every feature or bugfix since the ORC-45 two-path
dispatch protocol was introduced (approx. 2026-04-19) is affected.

**Data loss status — partially recoverable.** The step_history entries in
state.yaml have `agent: inline` baked in. The DuckDB rows have `agent_name = 'inline'`.
Reprocessing state.yaml via `orchestrator next` would re-upsert with the same
wrong values since the source (state.yaml) is also corrupted.

However, subagent JSONL files from affected workflows remain on disk under
`~/.claude/projects/-Users-spidey-code-orchestrator/<driver-session>/subagents/agent-*.jsonl`.
For orc-30 specifically, files from the 21:22-21:33 UTC window (e.g.
`agent-a6e7ca188209d1f47.jsonl`, modified 21:33:10) are still present and
have real token data (37 turns, `output_tokens=4548`, `model=claude-sonnet-4-6`
confirmed by direct inspection). Since `agent_id` was never recorded in
state.yaml, backfill would require correlating JSONL timestamps against
step_history `started_at`/`ended_at` windows — feasible but not trivial.
Agent identity (which agent ran which step) is recoverable from the step
contract YAML since each step contract declares its `agent:` field.

**Affected tables in DuckDB:**
- `step_events.agent_name` — always `inline`
- `step_events.output_tokens` — always `NULL`
- `step_events.model` — always `__default__` (not NULL, but incorrect)
- `agent_report` view — derived from step_events, shows only `inline` row
- `phase_events` — aggregated from step_events, so `output_tokens` sums to 0

**Existing tests:** The test suite in
`/Users/spidey/code/feature_worktrees/orc-48/config/scripts/orchestrator_next/tests/`
tests `record.py` in isolation with payloads that explicitly include `agent`.
The tests do not simulate the LLM driver constructing a payload from SKILL.md,
so the omission was not caught. No test checks that when `agent` is absent the
result is flagged as an error.

## Proposed Approach

Two fields must be added to the `orchestrator done` payload template in SKILL.md
line 210:

1. **`agent`** — sourced from `action.agent` returned by `orchestrator next`.
   Fixes root cause 1: record.py will write the correct agent name to state.yaml
   and step_events.

2. **`agent_id`** — the Claude Code subagent ID available in the Agent tool
   result (the `id` field of the spawned task). Fixes root cause 2: record.py's
   JSONL enrichment block (lines 1143-1177) will execute, pulling `output_tokens`,
   `model`, and cache token counts from the subagent's JSONL file.

Updated template (line 210):
```
orchestrator done state.yaml <<< {step_id, phase, status, agent, agent_id, outputs, usage, evidence}
```

Additionally, strengthen Check B in `record.py` to treat a payload with
`agent` absent (defaulting to `"inline"`) for a step whose contract declares
`agent:` as a validation error. This provides an engine-level guard independent
of driver fidelity. The step contract is already loaded during `done` for output
validation, so the contract's `agent` field is available without additional I/O.

## Unresolved Questions

1. **Data recovery:** The subagent JSONL files for orc-30 are still on disk.
   Is it worth writing a backfill script that: (a) reads each completed
   step_history entry, (b) looks up the step contract to get the expected agent
   name, (c) correlates the entry's `started_at`/`ended_at` window against
   JSONL file timestamps to identify the matching subagent, (d) re-upserts
   step_events with correct `agent_name`, `output_tokens`, and `model`? This is
   feasible but requires user decision on whether historical accuracy in DuckDB
   matters for reporting.

2. **Record.py enforcement vs. SKILL.md fix:** Should `record.py` enforce that
   `agent` must be present in any payload for a step with a non-inline contract?
   This would provide an engine-level guard independent of SKILL.md changes, but
   requires reading the step contract during `done` (it already does for output
   validation). The trade-off is added strictness vs. backward compatibility
   for callers that don't know the step contract at record time.

3. **How does the driver capture `agent_id`?** The JSONL filename format is
   `agent-<17hex>.jsonl` (e.g. `a6e7ca188209d1f47`). It is not confirmed that
   the Claude Code SDK Task tool result exposes this exact ID to the LLM driver
   — Task tool-use IDs are typically `toolu_*` format, not the subagent filename
   stem. If the driver cannot capture the right ID from the result, the
   driver-passes-agent_id approach breaks. An alternative is to have the
   `orchestrator` binary itself call `extract_agent_usage()` post-spawn (the
   binary already has access to `repo_root` and could scan JSONL files by
   timestamp window) rather than relying on the driver to pass it through.
   Needs verification before the architect commits to either approach.
