# Tasks: ORC-48 — Agent spawn usage flowing into DuckDB metrics

Order matters. T-1 is the regression test (must fail before T-2/T-3 land);
T-2 and T-3 are the two coordinated fixes; T-4 is the integration check.

---

## T-1: Regression tests for the bug

**Type:** test (must fail before fix)
**Files:** `config/scripts/orchestrator_next/tests/test_record_agent_field.py` (new)

**Description:**
Add three pytest cases that fail against HEAD and pass after T-2/T-3 land.
The test file lives next to existing record.py tests; uses the same fixture
patterns (tempfile state.yaml, no DB).

1. `test_missing_agent_rejected_for_agent_step` — construct a state.yaml
   for an agent step (e.g. `diagnose` whose contract declares
   `agent: discoverer`). Call `record()` with a payload that includes
   `agent: discoverer` removed (omitted entirely). Assert the return is
   `(error_dict, 3)` and `error_dict["reason"] == "payload_missing_agent_for_agent_step"`.

2. `test_agent_recorded_from_payload` — call `record()` with
   `payload["agent"] = "developer"`. Read state.yaml back. Assert
   `state["step_history"][-1]["agent"] == "developer"`.

3. `test_jsonl_enrichment_fires_with_agent_id` — using the orc-30 JSONL
   on disk (`~/.claude/projects/-Users-spidey-code-orchestrator/<session>/subagents/agent-a6e7ca188209d1f47.jsonl`,
   confirmed present in diagnose.md), construct a payload with
   `agent: discoverer`, `agent_id: "a6e7ca188209d1f47"`, and a state.yaml
   whose `repo_root` matches the slug. Call `record(db=None)`. Read
   state.yaml back. Assert
   `step_history[-1]["usage"]["output_tokens"] > 0` and
   `step_history[-1]["usage"]["model"] == "claude-sonnet-4-6"`. If the
   JSONL file is not present in the test environment, mark the test
   `skip` with a clear reason — but document the manual command to
   verify it locally.

4. `test_inline_step_no_agent_required` — for an inline-script step
   (contract has no `agent:` field, e.g. `workflow-init` or any
   `run:`-only step), call `record()` with no `agent` in the payload.
   Assert the call succeeds and `step_history[-1]["agent"] == "inline"`.

**Verify:**
- `pytest config/scripts/orchestrator_next/tests/test_record_agent_field.py -v`
  → all four tests fail (or three fail + one passes — the inline-step
  case passes against HEAD since current behavior is to default).
- After T-2 and T-3 land, all four pass.

---

## T-2: record.py — strengthen Check B to require `agent` for agent steps

**Type:** fix
**Files:** `config/scripts/orchestrator_next/record.py`

**Description:**
Modify Check B in `record.py` (around lines 1070-1092) to load the step
contract for `step_id` and reject payloads where the contract declares
`agent:` but the payload omits the `agent` field.

Implementation steps:

1. Locate the existing step contract loader used later in `done` for
   output validation (search for `_load_step_contract` or similar). If
   the loader is not factored out, add a small helper at module scope
   that takes `step_id` and returns the contract dict (or `None` if not
   found).

2. In Check B, before computing `agent = payload.get("agent", "inline")`:
   - Load the contract for `step_id`.
   - Read `contract_agent = (contract or {}).get("agent")`.
   - If `status == "completed"`, `contract_agent` is truthy and != `"inline"`,
     and `"agent"` is not in payload → return error tuple with
     `reason: "payload_missing_agent_for_agent_step"`, `step_id`,
     `expected_agent: contract_agent`, and a hint string referencing
     `skills/orchestrate/SKILL.md`.

3. If contract load fails (file missing, parse error), fall back to
   current behavior (allow default to `"inline"`); log to stderr but do
   not block.

4. Keep the existing `has_tokens` check intact — it now fires for the
   correct value of `agent`.

**Verify:**
- T-1 cases 1, 2, 4 pass.
- All existing tests in `config/scripts/orchestrator_next/tests/` continue
  to pass: `pytest config/scripts/orchestrator_next/tests/ -v`.
- Manual: run the diagnose reproduction script from `diagnose.md`. CASE
  1 should now report `BUG: False` because the call returns an error
  rather than silently writing `inline`.

---

## T-3: SKILL.md — add `agent` and `agent_id` to done payload template

**Type:** fix
**Files:** `skills/orchestrate/SKILL.md`

**Description:**

1. Update line 210 from:
   ```
   orchestrator done state.yaml <<< {step_id, phase, status, outputs, usage, evidence}
   ```
   to:
   ```
   orchestrator done state.yaml <<< {step_id, phase, status, agent, agent_id, outputs, usage, evidence}
   ```

2. In the usage-capture block (lines 193-208), add a new sub-step (#4)
   instructing the driver to:
   - Read `action.agent` from the JSON returned by `orchestrator next`
     and pass it as the `agent` field.
   - Extract `agentId` from the Task tool result text (which contains a
     line `agentId: <17hex>`) and pass it as the `agent_id` field.
   - Omit both fields for inline-script steps (`run:`-only contracts).

   Use the prose from `design.md` Component 1 / Change B as the source.

3. If a global mirror exists at `~/.claude/skills/orchestrate/SKILL.md`,
   call out in the PR/commit description that the global file should be
   re-synced via the existing inline-scripts sync flow (do not edit the
   global file from this worktree — it is sync'd from `config/`).

**Verify:**
- `grep -n "agent.*agent_id" skills/orchestrate/SKILL.md` shows the updated
  template line.
- `grep -E "agentId" skills/orchestrate/SKILL.md` shows at least one
  occurrence in the usage-capture block.
- AC-4 and AC-5 from spec.md pass.

---

## T-4: End-to-end smoke verification

**Type:** verification
**Files:** none (manual; capture evidence in implementation notes)

**Description:**
After T-1, T-2, T-3 land in the worktree:

1. Run a tiny bugfix or feature workflow on a throwaway change ID
   (e.g. `orc-48-smoke`) so at least one agent step (discoverer or
   architect) executes.
2. After the step completes, query DuckDB:
   ```bash
   python3 -c "
   import duckdb
   db = duckdb.connect('/Users/spidey/.config/orchestrator/metrics.duckdb', read_only=True)
   for r in db.execute(\"SELECT step_id, agent_name, output_tokens, model FROM step_events WHERE change_id='orc-48-smoke' ORDER BY ended_at\").fetchall():
       print(r)
   "
   ```
3. Confirm:
   - At least one row has `agent_name != 'inline'` (e.g. `discoverer`).
   - That row has `output_tokens > 0`.
   - That row has `model != '__default__'` (expect e.g. `claude-sonnet-4-6`
     or whichever model the spawn used).

**Verify:**
- AC-7 from spec.md confirmed by the query output above.
- If any row still shows `inline` for an agent step, T-2 or T-3 is
  incomplete — re-open and investigate before signoff.

**Deferral note:** Full end-to-end DuckDB smoke is blocked on global SKILL.md sync
(`~/.claude/skills/orchestrate/SKILL.md` must be updated from `skills/orchestrate/SKILL.md`
before a live workflow driver picks up the new `agent`/`agent_id` fields). The engine-side
code path is verified by unit tests (T-1 case 3: JSONL enrichment fires, output_tokens > 0,
model = claude-sonnet-4-6). AC-7 deferred to feature signoff after global sync.
