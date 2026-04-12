---
feature-id: hl-278
linear-ticket: HL-278
---

# Specification: Unify metric collection across all workflow schemas

## Motivation

`compute-swe-metrics.sh` only runs inside `_complete-phase.yaml`, which is included
by feature, bugfix, and chore schemas. Spike has no `complete` phase, so spike
token/cost/churn usage is never captured. Autopilot runs multi-iteration sessions
whose per-iteration token/cost data lives in sub-feature state.yaml files but is
never rolled up — telemetry, learn, and workflow-improver see nothing. Two of the
five workflow schemas are invisible to every metrics consumer.

HL-277 just standardized metrics consumers on archived `state.yaml`. We can bring
spike and autopilot into the same surface with additive changes and no consumer
migration.

## What Changes

- Add a reduced `complete` phase to the spike schema via a new
  `workflows/_complete-phase-spike.yaml` include (churn + tokens/cost +
  per_agent; no tasks/resolution/review).
- Make `compute-swe-metrics.sh` schema-aware: when `schema: spike` (or
  `schema: autopilot`), emit explicit YAML null for `resolution.*` fields and
  skip the `review_scores` block entirely; retain `tokens`, `cost`, `churn`, and
  `per_agent_*` for all schemas.
- Autopilot writes a per-session `state.yaml` at
  `spec/changes/archive/autopilot-<session_id>/state.yaml` with a `metrics:`
  block. Per-iteration metrics are appended during `autopilot-iterate.yaml`;
  the final aggregation happens in the `report` phase.
- New `config/steps/contracts/metrics-schema.md` documents the canonical
  metrics block shape, per-schema field variants, and the null/omit contract.
  `CONVENTIONS.md` gains a one-paragraph `§ Metrics Schema` pointer.

## Requirements

### Functional

1. **FR-1**: Spike schema MUST have a `complete` phase that runs
   `compute-swe-metrics` and `archive-completed-change` (and nothing else from
   the full complete phase — specifically, `run-learn-cycle` and
   `compute-prediction-accuracy` MUST NOT run for spike).
2. **FR-2**: `compute-swe-metrics.sh` MUST dispatch on the `$SCHEMA` value
   already read at line 404. For `schema: spike` and `schema: autopilot`:
   - `resolution.resolve_rate`, `resolution.pass_at_1`, `resolution.pass_at_2`,
     `resolution.regression_rate`, `resolution.tasks_total` MUST be emitted as
     explicit YAML null (`~`) under a present `resolution:` key.
   - `review_scores` block MUST be omitted entirely (no key, no value).
   - `tokens`, `cost`, `churn`, `per_agent_tokens`, `per_agent_cost` MUST be
     emitted unchanged.
3. **FR-3**: For `schema: feature|bugfix|chore`, `compute-swe-metrics.sh` output
   MUST be byte-identical to current behavior (no regression).
4. **FR-4**: `autopilot-iterate.yaml` MUST, on each iteration's archive step,
   read the sub-feature's `~/.workflows/<slug>/state.yaml` (or its archived
   copy at `spec/changes/archive/<slug>/state.yaml` if already moved) and
   append a `metrics:` block to the iteration record containing that
   iteration's `tokens.total`, `cost.total`, and `duration_ms`.
5. **FR-5**: Autopilot `report` phase MUST produce
   `spec/changes/archive/autopilot-<session_id>/state.yaml` with top-level
   `schema: autopilot` and a `metrics:` block aggregating tokens/cost/churn
   across all iterations in the session (including failed ones; `status`
   counts exposed as `metrics.resolution.iterations_{completed,failed,empty}`).
6. **FR-6**: The per-session autopilot `state.yaml` MUST conform to the same
   top-level shape telemetry/learn/workflow-improver glob for
   (`{change_id, schema, status, metrics: {...}}`), so existing consumers pick
   it up with no code change.
7. **FR-7**: `config/steps/contracts/metrics-schema.md` MUST document:
   the full `metrics:` block shape; which fields are required for each schema;
   the explicit-null contract for spike/autopilot resolution fields; the
   omit-on-absence contract for `review_scores`.
8. **FR-8**: `CONVENTIONS.md` MUST contain a `§ Metrics Schema` section with a
   one-paragraph pointer to `contracts/metrics-schema.md`.

### Non-Functional

1. **NFR-1**: No consumer code change required. Telemetry, learn, and
   workflow-improver must continue to function against both the existing
   feature archives and the new spike/autopilot archives without modification.
2. **NFR-2**: Null-safety. Consumers reading spike/autopilot archives MUST NOT
   crash, null-deref, or render "null" to the user — null-skip behavior is
   already documented in `skills/telemetry/SKILL.md:143` and must remain valid.
3. **NFR-3**: No migration. Previously archived spikes and autopilot sessions
   are NOT backfilled.

## Architecture

| File | Change |
|------|--------|
| `workflows/_complete-phase-spike.yaml` | **NEW** — reduced include: `compute-swe-metrics` + `archive-completed-change` only |
| `workflows/spike.yaml` | Add `complete` phase using `include: _complete-phase-spike` |
| `scripts/compute-swe-metrics.sh` | Wrap resolution/review emission blocks in `case "$SCHEMA"` dispatch |
| `steps/autopilot-iterate.yaml` | Per-iteration: read sub-feature state.yaml usage totals; append `metrics:` to iteration record + session state.yaml |
| `workflows/autopilot.yaml` | `report` phase: aggregate iteration metrics into session-level `metrics:` block in `spec/changes/archive/autopilot-<session_id>/state.yaml` |
| `config/steps/contracts/metrics-schema.md` | **NEW** — canonical metrics schema doc |
| `config/steps/CONVENTIONS.md` | Add `§ Metrics Schema` pointer paragraph |
| `agents/workflow-improver.md` | Minor: mark resolution fields N/A for spike/autopilot categories |

Data flow:

```
spike workflow:
  explore → prototype → summarize → complete[compute-swe-metrics → archive]
                                         └─▶ state.yaml (metrics: tokens/cost/churn, resolution: null)
                                         └─▶ spec/changes/archive/<slug>/state.yaml

autopilot session:
  iterate (N times):
    spawn /develop <sub-feature>
    read ~/.workflows/<sub-slug>/state.yaml usage totals
    append iteration metrics → spec/changes/archive/autopilot-<session_id>/state.yaml
  report:
    sum iteration metrics → session metrics block
    finalize spec/changes/archive/autopilot-<session_id>/state.yaml
```

## Test Strategy

### Test File Paths

- `scripts/compute-swe-metrics.sh` → `scripts/__tests__/compute-swe-metrics.test.sh`
  (bash test harness using fixture `state.yaml` inputs for each schema)
- `workflows/_complete-phase-spike.yaml` → `workflows/__tests__/complete-phase-spike.test.sh`
  (yq-based structural check: steps list)
- `workflows/spike.yaml` → `workflows/__tests__/spike.test.sh`
  (resolves `complete` phase through include chain)
- `steps/autopilot-iterate.yaml` → `steps/__tests__/autopilot-iterate-metrics.test.sh`
  (fixture: fake sub-feature state.yaml with known usage; assert rollup)
- `workflows/autopilot.yaml` report phase →
  `workflows/__tests__/autopilot-session-close.test.sh`
  (fixture: multi-iteration session; assert archived state.yaml shape)
- `config/steps/contracts/metrics-schema.md` → lint check only (markdownlint)
- End-to-end regression: run a dry-run feature and a dry-run spike workflow;
  diff metrics blocks against golden fixtures.

### Coverage Targets

90% line coverage on `compute-swe-metrics.sh` schema-dispatch branches
(the touched function); 100% branch coverage on the schema case statement
(every arm exercised by a test).

### Key Test Scenarios

- `schema: feature` produces byte-identical output to current behavior
  (regression fixture).
- `schema: spike` produces `resolution: { resolve_rate: ~, pass_at_1: ~, ... }`
  with `review_scores` key absent.
- `schema: autopilot` produces the same reduced shape as spike plus
  `resolution.iterations_{completed,failed,empty}` counts.
- Autopilot session with 3 iterations (2 completed, 1 failed) writes one
  session `state.yaml` with summed tokens/cost and per-status iteration counts.
- Spike with zero commits (pure exploration) writes `churn.files_changed: 0`
  without failure.
- Telemetry skill reads a spike `state.yaml` and a feature `state.yaml` from
  the same glob; no null-deref; per-schema sections render.

## Acceptance Criteria

- **AC-1**: Given a completed spike workflow, when `compute-swe-metrics` runs,
  then an archived `state.yaml` exists at
  `spec/changes/archive/<slug>/state.yaml` containing a `metrics:` block with
  non-null `tokens`, `cost`, `churn`, and with `resolution.resolve_rate: ~`.
  [traces: UC-1, UC-E2]
- **AC-2**: Given fixtures `state.yaml.feature` and `state.yaml.spike`, when
  `compute-swe-metrics.sh` runs on each, then the outputs differ exactly by:
  (a) `resolution.*` fields null in spike, populated in feature; (b)
  `review_scores` block present in feature, absent in spike;
  all other fields identical in shape. [traces: UC-3]
- **AC-3**: Given an autopilot session completing with ≥1 iteration, when the
  `report` phase finishes, then
  `spec/changes/archive/autopilot-<session_id>/state.yaml` exists with
  `schema: autopilot`, `status: completed`, and a `metrics:` block whose
  `tokens.total` equals the sum of each iteration's sub-feature
  `tokens.total`. [traces: UC-2, UC-E3]
- **AC-4**: Given a spike's archived `state.yaml`, when the telemetry skill
  is invoked, then it reads the file without crash or null-deref and omits
  null-valued metric fields from its output. [traces: UC-E1]
- **AC-5**: Given `CONVENTIONS.md`, when a reader follows the `§ Metrics
  Schema` pointer, then they land on `contracts/metrics-schema.md`, which
  documents every `metrics:` field, which fields are null/omitted for spike
  vs autopilot vs feature, and the explicit-null vs omit contract.
  [traces: UC-3]
- **AC-6**: Given an existing feature workflow run end-to-end, when metrics
  are computed, then the `metrics:` block in archived `state.yaml` is
  byte-equivalent to the pre-change output (captured in a golden fixture
  diff). [traces: UC-3]

## Alternatives Considered

**Alternative A: Flag on shared `_complete-phase.yaml`** — Add a boolean flag
(e.g., `reduced_metrics: true`) to the canonical include and gate step
emission on it. Rejected: couples spike's reduced shape into the shared
include and risks silent drift for feature/bugfix/chore if future edits
forget the flag. One shared file with branching logic is more fragile than
two small files with clear ownership.

**Alternative B: Per-schema complete phases for all workflows** — Fork
`_complete-phase.yaml` into `_complete-phase-feature.yaml`,
`_complete-phase-bugfix.yaml`, etc. Rejected: five-way fork for a two-way
difference. Feature/bugfix/chore are identical today; forking them creates
maintenance burden with no benefit.

**Alternative C: `--schema` CLI flag on compute-swe-metrics.sh** — Accept
an explicit flag instead of reading `$SCHEMA` from state.yaml. Rejected:
the variable is already parsed at line 404 with no caller change needed;
adding a flag creates two sources of truth that could disagree.

**Alternative D: Autopilot metrics as a `metrics:` key on each session
entry in `sessions_archive.yaml`** — Rejected: would require telemetry/
learn/workflow-improver to learn a second file path. Per-session state.yaml
reuses the existing archived-state.yaml consumer path standardized in
HL-277 with zero consumer changes.

**Alternative E: Omit `resolution` key entirely for spike** — Rejected: would
force every consumer to add `has_key` guards. Explicit null under a present
`resolution:` key keeps block shape stable across schemas;
telemetry SKILL.md:143 already documents null-skip.

## Impact

**No breaking changes.** All consumers of the metrics block (telemetry,
learn, workflow-improver) require zero modification — they already tolerate
null fields. Feature/bugfix/chore outputs are byte-identical.

**Additive changes only:**
- New include file `_complete-phase-spike.yaml`
- New contract file `contracts/metrics-schema.md`
- Schema-dispatch branch in one existing script
- Autopilot sessions archive as `spec/changes/archive/autopilot-<session_id>/` (already under existing consumer glob — no glob changes required)

**Migration:** None. Old spike and autopilot runs are not backfilled.

## Decisions

- **Explicit null over key omission**: `resolution.resolve_rate: ~` keeps block
  shape stable across schemas; consumers iterate fields uniformly.
- **Per-session state.yaml for autopilot**: reuses HL-277 consumer path; zero
  consumer changes.
- **Schema-dispatch reads `$SCHEMA` from state.yaml**: the variable is already
  populated at line 404; no new CLI surface area.
- **New include file over flag on shared include**: protects feature/bugfix/
  chore from accidental regression from spike-only changes.
- **Per-iteration write + report-phase finalize**: interruption-resilient
  (each iteration persists its metrics immediately) and yields a clean final
  aggregate at session close.

<!-- Format contract: contracts/artifact-formats.md § Specification Format Contract -->
