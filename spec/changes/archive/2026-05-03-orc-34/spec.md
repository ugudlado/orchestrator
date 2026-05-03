---
feature-id: ORC-34
linear-ticket: none
---

# Specification: Add started_at to seed-state.sh canonical state.yaml

## Motivation

`seed-state.sh` writes the canonical-minimum `state.yaml` for newly-seeded
features and bugfixes, but omits `started_at`. The downstream consumer
`_resolve_feature_metrics` (record.py:816) unconditionally requires
`started_at` for `feature` and `bugfix` schemas. As a result, every seeded
workflow that reaches `mark-change-completed` raises `RuntimeError` and
the SWE metrics step is skipped with a `feature_metrics_resolution_failed`
non-fatal warning. The DuckDB `feature_metrics` table is never populated for
these features. Root cause and evidence are documented in `diagnose.md`.

## What Changes

- `seed-state.sh` adds a `started_at` key to the inline-Python `state` dict,
  set to the same ISO-8601 UTC timestamp value used for `created_at`.
- `test_seed_state.py` asserts both `created_at` and `started_at` exist and
  are equal in the seeded `state.yaml`.

## Requirements

### Functional

1. **FR-1**: `seed-state.sh` MUST write both `created_at` and `started_at`
   to the seeded `state.yaml`, with identical ISO-8601 UTC timestamp values.
2. **FR-2**: `test_seed_state.py::test_seed_state_produces_dispatch_ready_pair`
   (or a sibling assertion within the same test module) MUST assert that the
   seeded `state.yaml` contains both keys and that they are equal.
3. **FR-3**: After seeding a fresh bugfix or feature state, calling
   `orchestrator done` on the resulting `state.yaml` MUST NOT raise
   `RuntimeError("...missing started_at...")` from `_resolve_feature_metrics`.

### Non-Functional

1. **NFR-1**: No new dependencies. No new abstractions. The fix is a single
   added dict key in the existing inline Python block.

## Architecture

| File | Change |
|---|---|
| `skills/orchestrate/scripts/seed-state.sh` | Add `started_at` key to the `state = {...}` dict at line 237; bind it to the same timestamp expression used for `created_at`. |
| `config/scripts/orchestrator_next/tests/test_seed_state.py` | Add assertion that the seeded state has both keys and they match. |

## Test Strategy

### Test File Paths

- `config/scripts/orchestrator_next/tests/test_seed_state.py` — extends the
  existing `test_seed_state_produces_dispatch_ready_pair` test (or adds one
  focused sibling) to assert both timestamps.

### Coverage Targets

- The new assertion fails on `main` (pre-fix) and passes on the fix branch.

### Key Test Scenarios

- Fresh seed of bugfix schema → `state.yaml` contains both `created_at` and
  `started_at`, equal values.
- E2E: seed fresh state, run no-op `orchestrator done` payload, observe no
  `feature_metrics_resolution_failed` warning in `step_history`.

## Acceptance Criteria

- AC-1: Running `seed-state.sh <slug> bugfix` produces a `state.yaml` whose
  parsed YAML contains both `started_at` and `created_at` as ISO-8601 UTC
  strings, equal to each other. Verify:
  `python3 -c "import yaml,sys; s=yaml.safe_load(open(sys.argv[1])); assert s['created_at']==s['started_at']" <state.yaml>` exits 0. [traces: UC-1]
- AC-2: `pytest config/scripts/orchestrator_next/tests/test_seed_state.py`
  passes, and includes an assertion that both keys are present and equal.
  Verify: `pytest config/scripts/orchestrator_next/tests/test_seed_state.py -k seed_state -q` exits 0; the new assertion is visible in the test source. [traces: UC-1]
- AC-3: Given a freshly-seeded bugfix `state.yaml`, piping a minimal
  `orchestrator done` step-completion payload does NOT produce a
  `feature_metrics_resolution_failed` non-fatal warning attributable to
  missing `started_at`. Verify: shell repro from `diagnose.md` § Reproduction
  Steps run on the fix branch shows `step_history` entries free of the
  `missing started_at/completed_at` warning. [traces: UC-E1]

## Alternatives Considered

**Alternative 1: Make `_resolve_feature_metrics` fall back to `created_at` when `started_at` is missing.**
Rejected. Pushes a workaround into the consumer; both timestamps are
semantically distinct (creation vs. lifecycle start), and other consumers
will increasingly assume `started_at` is canonical. Fixing the producer is
correct.

**Alternative 2: Add `started_at` only at `workflow-init` time, not at seed time.**
Rejected. `_resolve_feature_metrics` is called at `mark-change-completed`,
which can run on a state that was seeded but not advanced through
`workflow-init` in degenerate paths. The seeder is the single canonical
producer of `state.yaml` — it owns the contract.

## Impact

No breaking changes. Existing seeded state files on disk are not migrated
(out of scope), but they are short-lived workflow state and any active
in-flight workflow already has `started_at` written by `workflow-init`.

## Decisions

- `started_at` and `created_at` get the same timestamp value at seed time.
  Rationale: at the moment of seeding, the workflow simultaneously enters
  existence (`created_at`) and begins execution (`started_at`). There is no
  meaningful gap. This matches what the manual ORC-27 unblock did.
