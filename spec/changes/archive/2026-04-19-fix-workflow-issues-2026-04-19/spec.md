---
feature-id: fix-workflow-issues-2026-04-19
linear-ticket: ~
---

# Specification: Fix 10 Workflow Issues Surfaced in Autopilot 2026-04-19-002

## Motivation

Autopilot run `2026-04-19-002` surfaced 10 systemic workflow bugs spanning
agent prompts, CLI dispatch, DuckDB schema migration, step contracts, and a
missing backlog-cleanup step. Five of these block telemetry for the next
autopilot run (usage-per-step is currently lost); the rest are correctness
and quality-of-life fixes. One new issue (ISSUE-13, path mismatch in
`compute-swe-metrics.yaml`) was surfaced during discovery and is folded in.

Without this feature, the next autopilot run will again produce empty
`orchestrator cost` reports, write malformed `state.yaml`, return
`complete_workflow` prematurely, and leave stale backlog entries behind.

## What Changes

- Agent prose: `workflow-init.md` names the canonical `active:` key with an
  explicit YAML example; `developer.md` forbids hand-edits to state.yaml;
  `orchestrate/SKILL.md` hoists USAGE CAPTURE to a mandatory numbered step,
  documents `run_in_background: true` default, and adds an explicit
  phase-transition reminder.
- Code: `_migrate_step_events` in `upsert.py` drops and recreates the
  blocking index around `ALTER TABLE RENAME COLUMN`; `dispatch.py` emits a
  loud hint when a phase completes but is not the terminal phase.
- Step contracts: `preview-route.yaml` output renamed from phrase to
  identifier `route_preview`; `compute-swe-metrics.yaml` instruction path
  corrected to `scripts/inline/`; `archive-completed-change.yaml`
  instruction mentions backlog cleanup.
- Shell: `archive-completed-change.sh` removes `spec/changes/backlog/<slug>/`
  after archive commit.
- Config: `spec/project.yaml` `verify_commands` populated with a pytest
  invocation under the `test` key.
- Data: 5 stale backlog entries removed in this PR.

## Requirements

### Functional

1. **FR-1** (ISSUE-1): `agents/workflow-init.md` explicitly names the
   `active:` key in the `workflow_plan` schema, with a canonical YAML
   example block.
2. **FR-2** (ISSUE-2 / ISSUE-10.2): `_migrate_step_events` completes
   without raising `Dependency Error` when `idx_step_events_change`
   exists on a `step_events` table still carrying otel-prefixed columns.
3. **FR-3** (ISSUE-4): `config/steps/preview-route.yaml` declares the
   output name as the bareword `route_preview` (not a phrase).
4. **FR-4** (ISSUE-5): `spec/project.yaml` `verify_commands` includes a
   pytest invocation keyed under `test` such that `capture-test-baseline`
   resolves a non-empty test command.
5. **FR-5** (ISSUE-6): `skills/orchestrate/SKILL.md` dispatch loop
   explicitly states `run_in_background: true` is the default for agent
   spawns, with foreground carve-outs named (ideator, reviewer).
6. **FR-6** (ISSUE-7): `agents/developer.md` forbids direct `state.yaml`
   edits and mandates `orchestrator record` for all `step_history`
   appends; `agents/workflow-init.md` mirrors the same constraint.
7. **FR-7** (ISSUE-8, option c): `dispatch.py` emits a distinguishable
   warning on stderr and `skills/orchestrate/SKILL.md` § 5 documents
   phase-transition responsibility when a non-terminal phase completes.
8. **FR-8** (ISSUE-9): `scripts/inline/archive-completed-change.sh`
   removes `spec/changes/backlog/<slug>/` after the archive commit; the
   5 currently-stale backlog entries are removed in this PR.
9. **FR-9** (ISSUE-10.1): `skills/orchestrate/SKILL.md` `run_step` branch
   restructures USAGE CAPTURE from a comment into a MANDATORY numbered
   step, with a post-record assertion that
   `step_history[-1].usage.input_tokens` is non-null for agent steps.
10. **FR-10** (ISSUE-10.3 / ISSUE-13): `config/steps/compute-swe-metrics.yaml`
    instruction step 2a path matches the actual script location
    (`scripts/inline/compute-swe-metrics.sh`).
11. **FR-11** (root-cause enforcement for ISSUE-1, ISSUE-7, ISSUE-10.1):
    `record.py` rejects invalid payloads at the earliest opportunity —
    symptom-surfacing is downstream; enforcement is upstream.
    Three asserts:
    a. When `step_id == "workflow-init"` and `status == "completed"`,
       assert `state.workflow_plan[phase].active` is a non-empty list
       for every phase in the plan; reject with
       `{action: validation_error, reason: "workflow_plan_active_missing_or_empty", phase: <p>}`
       otherwise. (Root cause of ISSUE-1 — agent can write any shape; record now validates.)
    b. When `payload.agent` is set AND `!= "inline"`, require
       `payload.usage` contains `input_tokens` (or `output_tokens`) > 0.
       Reject with `{action: validation_error, reason: "agent_step_missing_usage", agent: <a>}` otherwise.
       (Root cause of ISSUE-10.1 — driver can skip USAGE CAPTURE; record now enforces.)
    c. After writing state.yaml, re-parse the file with `yaml.safe_load`;
       if parse fails, restore the pre-write bytes and return
       `{action: error, reason: "state_yaml_parse_failure", detail: <msg>}`.
       (Root cause of ISSUE-7 — hand-edits corrupt YAML silently; record now
       fails loudly at the edit boundary, pointing at the last touching agent.)
    Prose prohibitions in `workflow-init.md` / `developer.md` remain as
    documentation but are no longer the sole enforcement.

### Non-Functional

1. **NFR-1**: No regression on existing tests in
   `config/scripts/orchestrator_next/tests/` — `pytest -q` remains
   green after all fixes.
2. **NFR-2**: `orchestrator cost --change-id <seeded-change>` produces a
   non-empty events report on a seeded small change after fixes land.
3. **NFR-3**: Changes touch only prose, Python, shell, YAML — no new
   runtime dependencies introduced.

## Architecture

See `design.md`. Summary: 10 targeted, bounded edits across
prose / YAML / Python / shell files; no new components; no schema
extensions beyond one in-place `DROP INDEX / ALTER / CREATE INDEX`
sequence inside a single DuckDB connection.

## Test Strategy

### Test File Paths

| Component | Test file |
|-----------|-----------|
| `upsert.py::_migrate_step_events` (FR-2) | `config/scripts/orchestrator_next/tests/test_upsert_migration.py` (new) |
| `dispatch.py` phase-transition hint (FR-7) | `config/scripts/orchestrator_next/tests/test_dispatch_phase_hint.py` (new) |
| `archive-completed-change.sh` backlog cleanup (FR-8) | `config/scripts/orchestrator_next/tests/test_archive_backlog_cleanup.py` (new; pytest shell-out) |
| Prose / contract fixes (FR-1, FR-3, FR-5, FR-6, FR-9, FR-10) | `config/scripts/orchestrator_next/tests/test_prose_contracts.py` (new; grep-assertion style) |
| Config fix (FR-4) | same as prose file (grep-assert `verify_commands.test` non-empty) |

### Coverage Targets

- Existing `orchestrator_next` coverage not regressed.
- New code paths (index-drop branch in `_migrate_step_events`, warning
  branch in `dispatch.py`) covered by explicit tests.

### Key Test Scenarios

- Migration with blocking index + otel column names → succeeds,
  columns renamed, index present after.
- Migration idempotent: second call on fresh schema is a no-op.
- Dispatcher on a multi-phase plan with last step of `specify`
  complete → does NOT return `complete_workflow` while `implement` /
  `complete` remain; stderr carries a WARNING line.
- `archive-completed-change.sh` with a dummy backlog entry → entry
  removed; git state clean; exit 0.

## Acceptance Criteria

- **AC-1**: `agents/workflow-init.md` contains an explicit YAML example
  using the key `active:` (not `active_steps:`). A fresh `workflow-init`
  spawn on a seeded slug writes `workflow_plan.<phase>.active:` such
  that `grep -c 'active_steps:' state.yaml` = 0 and `grep -c 'active:'
  state.yaml` > 0. [traces: UC-1]
- **AC-2**: `orchestrator record` is the documented sole path for
  `step_history` appends. `agents/developer.md` and
  `agents/workflow-init.md` contain an explicit prohibition against
  direct `state.yaml` edits. [traces: UC-E3]
- **AC-3**: `orchestrator cost --change-id <cid>` returns non-empty
  events for a change that completed with agent spawns; the underlying
  DuckDB `step_events` ALTER / index ordering bug does not fire.
  [traces: UC-2, UC-E1]
- **AC-4**: For any agent step in `state.yaml.step_history`,
  `usage.input_tokens` and `usage.output_tokens` fields are present.
  [traces: UC-2]
- **AC-5**: `spec/project.yaml.verify_commands.test` is populated and
  `capture-test-baseline` parses it to a non-empty command. [traces: UC-1]
- **AC-6**: `config/steps/preview-route.yaml.outputs` is
  `[route_preview]`; `orchestrator record` validates payloads that
  carry `{"outputs": {"route_preview": ...}}`. [traces: UC-1]
- **AC-7**: The 5 stale backlog entries (feature-complexity-tracking,
  orchestrator-doctor, per-step-allowed-tools,
  fix-cost-usd-and-widen-token-split,
  tool-calls-rename-and-preview-route-fix) are absent from
  `spec/changes/backlog/` after this PR; `archive-completed-change`
  contract removes the backlog dir on success. [traces: UC-4]
- **AC-8**: `skills/orchestrate/SKILL.md` dispatch loop documents
  `run_in_background: true` as default for agent spawns AND explicitly
  states phase-transition responsibility in § 5. [traces: UC-3]
- **AC-9**: ISSUE-3 resolved with evidence — discovery.md contains the
  grep evidence; no prose change required.
- **AC-10** (FR-11): `orchestrator record` returns exit code 3 and
  `action: validation_error` (or `action: error`) when passed:
  (a) a workflow-init completion payload whose `workflow_plan` contains
  a phase with missing/empty `active`, (b) an agent (non-inline) step
  completion payload with missing or zero-valued `usage.input_tokens`
  AND `usage.output_tokens`, (c) a payload whose write would leave
  state.yaml unparseable (simulated via a pre-corrupted state.yaml
  fixture). Tests cover all three branches. [traces: UC-E2, UC-E3]

## Alternatives Considered

**Alternative B — split into two PRs (blockers + polish).** Rejected.
Delivers the same code in two commits with double review overhead; the
polish bucket historically gets deprioritized indefinitely.

**Alternative C — full phase-advance automation (ISSUE-8 option b).**
Rejected for this feature. Requires a state.yaml schema extension
(`phases_ordered`) plus workflow-init and dispatcher changes; option
(c) — loud hint + doc — is sufficient for current driver compliance
and can be upgraded to (b) as a follow-up.

## Out of Scope

- **ISSUE-3**: CLOSED as stale — grep evidence confirms docs are correct.
  No code change in this feature.
- **ISSUE-11** (orchestrate skill assumes backlog entry exists before
  workflow-init): documented in issue log; actionable fix deferred to a
  follow-up feature (needs ideator/skill prose redesign).
- **ISSUE-12** (Linear account limit): external — not a workflow bug.
- **ISSUE-14** (workflow_plan dict keys lose schema phase order):
  constraint documented; only actionable if ISSUE-8 option (b) is chosen,
  which it isn't this feature.
- Not redesigning metrics DB schema beyond fixing the `step_events`
  ALTER dependency.
- Not changing the feature-workflow schema or state.yaml format.
- Not rewriting the ideator or backlog-picking flow.
- Not implementing automated phase-advance in the dispatcher (follow-up).

## Impact

- **Breaking changes**: none. All state.yaml / contract / schema
  changes are backward-compatible (one rename on an output name that
  was previously invalid).
- **Migration**: the `_migrate_step_events` fix is self-migrating on
  the next `ensure_schema()` call against production `metrics.duckdb`.
- **Affected areas**: orchestrator CLI telemetry, archive cleanup,
  feature-workflow dispatch prose.

## Decisions

- **Single-PR bundle (Approach A)**: 10 fixes in one feature, two tiers
  (blockers / polish), shared test suite.
- **ISSUE-8 → option (c)**: loud hint + skill doc, not
  `phases_ordered` automation.
- **ISSUE-1, 7, 10.1 → root-cause at `record.py`**: added FR-11 during
  review. Prose-only fixes were symptom patches — all three bugs land
  invalid data in state.yaml and surface far downstream. Three asserts
  at the record boundary reject them at the source. Prose prohibitions
  in agent files remain as documentation.
- **ISSUE-6 → prose-only, accepted**: background spawn is a driver
  convention with no code path to enforce. Leaving as prose is the
  correct scope; noting it explicitly as a conscious choice, not an
  oversight.
- **ISSUE-5 verify_commands shape**: dict form `{test: pytest ...}` —
  `capture-test-baseline.sh` reads `vc.get('test')` first.
- **ISSUE-9 commit strategy**: fold backlog cleanup into a commit
  inside `archive-completed-change.sh`; do not introduce a third
  orphan commit.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
