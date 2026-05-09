# Spec: ORC-48 — Agent spawn usage not flowing into DuckDB metrics

## Motivation

Every workflow step that runs as an agent spawn (discoverer, architect, developer,
reviewer, etc.) is currently recorded in `state.yaml` and `step_events` with
`agent="inline"`, `output_tokens=NULL`, and `model="__default__"`. The
`agent_report` view collapses every step into a single `inline` row, making
per-agent cost telemetry useless. This has affected every workflow run since
the ORC-45 two-path dispatch was introduced (~2026-04-19).

The driver template in `skills/orchestrate/SKILL.md` line 210 shows a
`done` payload that omits the `agent` and `agent_id` fields entirely.
`record.py` then defaults `agent` to `"inline"` and skips JSONL enrichment
because `agent_id` is missing. Two missing fields cascade into the entire
metrics breakdown being wrong.

## Requirements

### Functional

- **FR-1**: When an agent step completes, the recorded `step_history` entry
  in `state.yaml` MUST have `agent` equal to the contract's declared agent
  (e.g. `discoverer`, `architect`, `developer`), not `"inline"`.
- **FR-2**: When an agent step completes, the recorded entry MUST have
  `usage.output_tokens` populated (non-null, > 0 for any non-trivial spawn)
  and `usage.model` equal to the actual model used by the subagent.
- **FR-3**: `record.py` MUST reject any payload where the step contract
  declares `agent:` but the payload omits `agent`. The error MUST be
  actionable (`reason: payload_missing_agent_for_agent_step`).
- **FR-4**: The driver template in SKILL.md MUST instruct the LLM to extract
  `agentId` from the Task tool result text and include both `agent` and
  `agent_id` in the `done` payload.

### Non-functional

- **NFR-1**: Backward compatibility — payloads from inline-script steps
  (contract has no `agent:` field, or `agent: inline`) MUST continue to work
  without `agent` or `agent_id` in the payload.
- **NFR-2**: The fix MUST not require any new I/O at `done` time beyond what
  already happens — JSONL enrichment is already gated on `agent_id`; this
  bug fix only ensures it actually fires.

## Acceptance Criteria

- **AC-1** [traces: FR-1, FR-3]: Calling `record()` with a payload that
  includes `agent` but lacks it for a step contract that declares `agent:`
  returns `(error, exit_code=3)` with `reason: payload_missing_agent_for_agent_step`.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_record.py::test_missing_agent_rejected`.
- **AC-2** [traces: FR-1]: Calling `record()` with `agent="developer"` in
  the payload writes `step_history[-1].agent == "developer"`, not `"inline"`.
  Verify: `pytest .../test_record.py::test_agent_recorded_from_payload`.
- **AC-3** [traces: FR-2]: Calling `record()` with both `agent` and a valid
  `agent_id` populates `usage.output_tokens` and `usage.model` from the
  matching subagent JSONL on disk. Verify: `pytest .../test_record.py::test_jsonl_enrichment_fires_with_agent_id`
  using the existing orc-30 JSONL fixture (`agent-a6e7ca188209d1f47.jsonl`,
  `output_tokens=4548`, `model=claude-sonnet-4-6`).
- **AC-4** [traces: FR-4]: SKILL.md line 210's payload template includes
  `agent` and `agent_id` fields. Verify: `grep -E "agent.*agent_id" skills/orchestrate/SKILL.md`
  returns the updated template line.
- **AC-5** [traces: FR-4]: SKILL.md contains a usage-capture step instructing
  the driver to extract `agentId` from the Task result text (the line
  `agentId: <17hex>`). Verify: `grep -E "agentId.*Task.*result|extract.*agentId" skills/orchestrate/SKILL.md`.
- **AC-6** [traces: NFR-1]: Calling `record()` with no `agent` field for an
  inline-script step contract (no `agent:` declared) continues to default to
  `agent="inline"` without error. Verify: `pytest .../test_record.py::test_inline_step_no_agent_required`.
- **AC-7** [traces: FR-1, FR-2]: After running a real workflow (any bugfix
  or feature), querying DuckDB `SELECT DISTINCT agent_name FROM step_events
  WHERE change_id='<id>'` returns the actual agent names, not just
  `inline`. Verify: end-to-end smoke run on a trivial change in another
  branch.

## Alternatives Considered

### Alt A: Engine-side window correlation (rejected)

`record.py` would correlate the step's `started_at`/`ended_at` window
against subagent JSONL file timestamps under the driver session, picking
the matching agent without driver passthrough. This avoids any SKILL.md
change but adds correlation complexity, fails for parallel spawns of the
same agent type, and ignores already-available data — the Task tool result
explicitly contains `agentId: <17hex>`, the exact JSONL filename stem.
Verified by inspecting a recent driver JSONL (`eb954b82-...jsonl`) which
shows tool_result text starting with
`Async agent launched successfully.\nagentId: add15d9599de8615a (internal ID...)`.
Driver passthrough is simpler and authoritative.

### Alt B: Driver passes `agent` only; engine uses `discover_subagents` for enrichment (rejected)

The existing `_resolve_subagent_rows` path already enumerates subagents
from disk and writes synthetic `phase: meta` rows. Extending it to enrich
the actual step row would require correlating subagent JSONLs against
in-flight steps by window — same parallelism risk as Alt A, and it
duplicates the JSONL enrichment block already present in `record.py`
(lines 1143-1177). The enrichment block was *designed* to consume
driver-supplied `agent_id`; using it as designed is simpler.

### Alt C (chosen): Driver passes both `agent` and `agent_id`; engine validates

SKILL.md template adds two fields; `record.py` strengthens Check B to
reject missing `agent` for agent steps. Existing JSONL enrichment block
fires unchanged. Smallest delta, uses already-present infrastructure.

## In Scope

- SKILL.md template update (line 210 and the usage-capture instructions).
- `record.py` Check B strengthening using the loaded step contract.
- Regression test that simulates the driver omitting `agent`.
- Test verifying JSONL enrichment fires when `agent_id` is supplied.

## Out of Scope

- **Backfill of historical DuckDB data.** Subagent JSONL files for orc-30
  and other affected workflows remain on disk; a backfill script could
  re-correlate them. Tracked as a separate follow-up; not blocking this fix.
- **Refactoring `_resolve_subagent_rows` / synthetic `phase: meta` rows.**
  Those continue to work; this fix is orthogonal.
- **Driver-loop or feature-boundary telemetry changes.** Only per-step
  recording is affected.
