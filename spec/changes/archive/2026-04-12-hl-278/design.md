# Design: Unify metric collection across all workflow schemas

## Context

Five workflow schemas exist: feature, bugfix, chore, spike, autopilot.
The first three already include `workflows/_complete-phase.yaml`, which
runs `compute-swe-metrics.sh` and archives the change — producing a
`metrics:` block in `spec/changes/archive/<slug>/state.yaml` that
telemetry/learn/workflow-improver consume (post HL-277).

Spike has no `complete` phase at all — no metrics, no archive.
Autopilot runs multi-iteration sessions whose per-iteration token/cost
data lives in the spawned sub-feature's `~/.workflows/<slug>/state.yaml`
but is never rolled up.

Constraints:
- HL-277 consumers MUST NOT require code changes; block shape must
  remain stable.
- `compute-swe-metrics.sh` already reads `$SCHEMA` from state.yaml
  (line 404) but currently emits the same shape regardless.
- Spike inherently has no task resolution, no pass@k, no regression
  rate — these fields are structurally meaningless for it.
- Autopilot sessions are composite: each iteration is a full sub-feature
  workflow that writes its own state.yaml; the session itself is not a
  direct consumer of `compute-swe-metrics.sh`.

## Goals / Non-Goals

### Goals

- Every workflow schema produces a capturable `metrics:` block in an
  archived `state.yaml`.
- Telemetry/learn/workflow-improver function unchanged against spike
  and autopilot archives.
- Schema-dispatch in `compute-swe-metrics.sh` is local, surgical, and
  uses the variable already parsed.
- Documentation (`contracts/metrics-schema.md`) makes the per-schema
  field contract explicit so future workflow schemas have a clear recipe.

### Non-Goals

- Not changing the metrics block shape for feature/bugfix/chore
  (byte-identical regression required).
- Not backfilling historical spike/autopilot runs.
- Not adding new metric fields (coverage, lint scores, etc.).
- Not changing autopilot's iteration selection/execution logic.
- Not introducing new consumer surfaces — we reuse the existing
  archived state.yaml path.

## Approaches Considered

### Approach A: Flag on shared `_complete-phase.yaml`

Add `reduced_metrics: true` flag; gate resolution/review steps on it.

- Pros: one shared file.
- Cons: couples spike-specific shape into the canonical include. Silent
  drift risk for feature/bugfix/chore if a future edit forgets the flag.

### Approach B: New `_complete-phase-spike.yaml` + schema-dispatch + autopilot rollup (SELECTED)

Additive. New reduced include; `compute-swe-metrics.sh` branches on
`$SCHEMA`; autopilot writes its own per-session state.yaml.

- Pros: canonical include untouched; divergence is intentional and
  explicit. Consumers unchanged. Reuse count = 2 (existing `SCHEMA` var
  + existing include-mechanism pattern).
- Cons: two include files to keep honest (mitigated by
  `contracts/metrics-schema.md` as single source of truth).

### Approach C: Per-schema complete phases for all workflows

Fork `_complete-phase.yaml` four ways.

- Pros: total clarity per schema.
- Cons: 4-way fork for a 2-way difference. Rejected as over-engineered.

### Selected Approach

**Approach B.** Discovery tiebreaker: A and B both "small" complexity
→ higher reuse count wins → B (reuse=2 > A reuse=1). Approach B is
additive, protects existing consumers, and keeps the reduced shape
owned by a spike-specific file.

## High-Level Design

### Architecture Overview

```
┌──────────────────┐
│ spike.yaml       │──► complete phase ──include──► _complete-phase-spike.yaml
│                  │                                   ├─ compute-swe-metrics  ──► state.yaml (spike)
│                  │                                   └─ archive-completed-change
└──────────────────┘

┌──────────────────┐
│ autopilot.yaml   │──► iterate phase (N loops)
│                  │       │
│                  │       ├─ spawn /develop <sub-feature>
│                  │       │   └─► ~/.workflows/<sub-slug>/state.yaml
│                  │       │
│                  │       └─ read sub state.yaml usage ──► append metrics to
│                  │                                        spec/changes/archive/autopilot-<session_id>/state.yaml
│                  │
│                  │──► report phase
│                  │       └─ aggregate iteration metrics → session metrics:
│                  │          finalize archive state.yaml
└──────────────────┘
```

### Key Abstractions

- **`_complete-phase-spike.yaml`** — a reduced sibling of
  `_complete-phase.yaml`. Shares the same include mechanism; omits
  `run-learn-cycle`, `compute-prediction-accuracy`, and any review
  steps. Relation to `_complete-phase.yaml`: sibling file, not a
  reduction-via-flag. Documented intentional fork; divergence is
  acceptable because spike has no tasks → no resolution data →
  learn/prediction loops have nothing to learn.
- **Schema-dispatch in `compute-swe-metrics.sh`** — a `case "$SCHEMA"`
  statement around the resolution/review emission blocks. Uses the
  existing `SCHEMA` variable at line 404 (no new parsing).
- **Autopilot per-session state.yaml** — a runtime artifact at
  `spec/changes/archive/autopilot-<session_id>/state.yaml` shaped to
  match archived feature state.yaml so telemetry's existing glob picks
  it up with no code change.
- **`contracts/metrics-schema.md`** — single source of truth for the
  metrics block shape and per-schema optionality.

## Low-Level Design

### Components

**1. `workflows/_complete-phase-spike.yaml`** (NEW)

```yaml
# Reduced complete phase for spike schema.
# Spike has no tasks → no resolution, no pass@k, no review.
steps:
  - compute-swe-metrics
  - archive-completed-change
```

Note deliberate exclusions: `run-learn-cycle` (nothing to learn from a
spike — no task outcomes) and `compute-prediction-accuracy` (no
predicted task count to compare against).

**2. `workflows/spike.yaml`** (modified)

Add to phases:

```yaml
complete:
  include: _complete-phase-spike
```

**3. `scripts/compute-swe-metrics.sh`** (modified — schema-dispatch)

At line 404 `SCHEMA=` is already set. Wrap the resolution and
review emission sections with:

```bash
case "$SCHEMA" in
  spike|autopilot)
    # Resolution block: emit keys with explicit YAML null
    cat <<EOF
  resolution:
    resolve_rate: ~
    pass_at_1: ~
    pass_at_2: ~
    regression_rate: ~
    tasks_total: ~
EOF
    # review_scores: omit entirely (no key)
    ;;
  feature|bugfix|chore|*)
    # existing emission code: resolution with real values + review_scores block
    ;;
esac
```

`tokens`, `cost`, `churn`, `per_agent_tokens`, `per_agent_cost` emission
code remains outside the `case` — unchanged for all schemas.

**4. `steps/autopilot-iterate.yaml`** (modified)

After each iteration's spawn completes and before the sub-feature
state.yaml is archived or deleted, the step:

1. Resolves the sub-feature's state.yaml path:
   - First try `~/.workflows/<sub-slug>/state.yaml` (active).
   - Fallback to `spec/changes/archive/<sub-slug>/state.yaml` (archived
     — if the sub-feature completed and already archived before we read).
2. Extracts `step_history[].usage.total_tokens`, `tool_uses`,
   `duration_ms` — sums to iteration totals.
3. Appends to the session state.yaml
   (`spec/changes/archive/autopilot-<session_id>/state.yaml`) under
   `iterations[]`:

```yaml
iterations:
  - slug: <sub-slug>
    status: completed|failed|empty_backlog
    metrics:
      tokens:
        total: <sum>
      cost:
        total: <sum>
      duration_ms: <sum>
      churn:
        files_changed: <from sub state.yaml if present, else 0>
```

If the session state.yaml doesn't exist yet (first iteration), create it
with `change_id: <session_id>`, `schema: autopilot`, `status: active`
scaffolding.

**5. `workflows/autopilot.yaml`** (modified — report phase)

`report` phase runs a new step (or extends `autopilot-session-report.yaml`)
that:

1. Reads all `iterations[].metrics` from the session state.yaml.
2. Sums them into a top-level `metrics:` block matching the canonical shape:

```yaml
metrics:
  category: autopilot
  tokens:
    total: <sum>
  cost:
    total: <sum>
  churn:
    files_changed: <sum>
  per_agent_tokens: { ... }   # merged
  per_agent_cost: { ... }     # merged
  resolution:
    resolve_rate: ~
    pass_at_1: ~
    pass_at_2: ~
    regression_rate: ~
    tasks_total: ~
    iterations_completed: <count>
    iterations_failed: <count>
    iterations_empty: <count>
  # review_scores omitted
```

3. Sets `status: completed`, `completed_at: <now>`.

**6. `config/steps/contracts/metrics-schema.md`** (NEW)

Canonical schema doc. Sections:
- Field registry (name, type, description) for every metrics field.
- Per-schema table: which fields are required, null, or omitted for
  feature / bugfix / chore / spike / autopilot.
- Contract rules: explicit null for resolution fields under
  spike/autopilot; omit `review_scores` entirely when inapplicable;
  `tokens/cost/churn/per_agent_*` always present.
- Consumer guidance: use null-skip (not null-render); do not rely on
  key presence for `review_scores`.

**7. `config/steps/CONVENTIONS.md`** (modified)

Add `## Metrics Schema` section (one paragraph) pointing at
`contracts/metrics-schema.md`. Follows existing pattern where
CONVENTIONS.md delegates to `contracts/` files.

**8. `agents/workflow-improver.md`** (modified, minor)

Update the per-feature metrics table description: note that
`resolution.*` fields are N/A (null) for spike and autopilot categories;
aggregation across categories must group by `metrics.category`.

### Data Flow

**Spike flow:**

```
spike run → summarize writes final state.yaml in ~/.workflows/<slug>/
         → complete phase:
             compute-swe-metrics.sh reads schema=spike
               → emits metrics: { tokens, cost, churn, resolution:null, no review_scores }
               → writes into state.yaml
             archive-completed-change moves ~/.workflows/<slug>/state.yaml
               → spec/changes/archive/<slug>/state.yaml
         → telemetry globs spec/changes/archive/*/state.yaml, finds spike entry, renders
```

**Autopilot flow:**

```
autopilot session starts → session_id generated
  iteration N:
    spawn /develop <sub-slug>
      → sub state.yaml at ~/.workflows/<sub-slug>/state.yaml
        (step_history[].usage populated throughout)
    autopilot-iterate reads sub state.yaml
      → sums tokens/cost/duration/churn
      → appends to spec/changes/archive/autopilot-<session_id>/state.yaml
         under iterations[]
  report phase:
    reads iterations[].metrics
    sums → top-level metrics: block
    sets status: completed
    state.yaml is already in archive path → telemetry picks it up on next glob
```

### State Management

**Spike state:**
- Same lifecycle as feature: `~/.workflows/<slug>/state.yaml` →
  `spec/changes/archive/<slug>/state.yaml` via
  `archive-completed-change`.

**Autopilot session state (new):**
- Lives throughout the session at
  `spec/changes/archive/autopilot-<session_id>/state.yaml`.
- Written incrementally by `autopilot-iterate.yaml` (append to
  `iterations[]`).
- Finalized by `report` phase (top-level `metrics:` + status=completed).
- Already under `spec/changes/archive/*/` — picked up by telemetry,
  learn, and workflow-improver globs with no consumer-side change.

### Error Handling

- **Missing sub-feature state.yaml for an iteration**:
  `autopilot-iterate.yaml` logs a warning, records the iteration with
  `status: <whatever>` and `metrics: { tokens: { total: 0 }, ... }`
  (zero-filled, not null — allows aggregation to proceed).
- **Zero-commit spike** (pure exploration): already handled at
  `compute-swe-metrics.sh:235` (`COMMIT_HASHES` check) —
  `churn.files_changed: 0` emitted without error.
- **Unknown schema** in `compute-swe-metrics.sh`: the `case *)` default
  falls to the feature emission path (no regression for any existing
  schema; any new schema gets the full block until explicitly added
  to the spike/autopilot arm).
- **Partial iteration** (interrupted autopilot): per-iteration write
  means the session state.yaml has whatever completed iterations
  persisted. The `report` phase can still run manually to finalize
  or can be retried.

## Constraints

- Must not alter the metrics block shape for feature/bugfix/chore
  (regression-tested via golden fixture).
- `compute-swe-metrics.sh` must continue to work when invoked directly
  outside workflow context (its existing CLI contract) — schema defaults
  to what's in state.yaml; unknown → feature path.
- Autopilot session state.yaml must glob-match the path pattern telemetry
  uses. Concretely: telemetry already looks at archived state.yaml files
  keyed on `schema:` — putting the session state.yaml under an archive/
  directory and setting `schema: autopilot` is sufficient.

## Trade-offs

- **Two include files instead of one parameterized one.** We accept a
  slightly larger surface area in exchange for strong isolation between
  the canonical (feature/bugfix/chore) complete phase and the reduced
  (spike) complete phase. A future "oops, flag got dropped" mutation
  in `_complete-phase.yaml` would silently break feature metrics; a
  sibling file eliminates that failure mode.
- **Explicit null over key omission.** Slightly more verbose output,
  but keeps block shape uniform across schemas so consumers iterate
  fields without key-existence guards.
- **Per-iteration write instead of single end-of-session write.**
  More writes (one per iteration) but resilient to session
  interruption; a crashed autopilot session still has metrics for
  completed iterations.
- **Autopilot session state.yaml duplicates iteration data that also
  lives in `sessions_archive.yaml`.** Acceptable because the two serve
  different consumers: `sessions_archive.yaml` is the operational
  ledger (what ran, in what order); session state.yaml is the metrics
  surface (telemetry/learn). Single-source-of-truth would force
  consumers to learn a new path.

## Decisions

- **New `_complete-phase-spike.yaml` file** → keeps the canonical include
  safe from spike-specific changes → two files to maintain; mitigated
  by `metrics-schema.md` as source of truth.
- **Schema-dispatch inside compute-swe-metrics.sh (not via CLI flag)**
  → single source of truth (state.yaml schema field) → one less
  caller contract to maintain.
- **Explicit null for spike/autopilot resolution fields** → consumers
  iterate fields uniformly → slight output verbosity, no code impact.
- **Autopilot session state.yaml at
  `spec/changes/archive/autopilot-<session_id>/state.yaml`** → reuses
  HL-277 consumer path → zero consumer changes.
- **Per-iteration append + report-phase finalize** → crash-resilient
  metrics capture → two write points to coordinate, but each is simple.
- **`contracts/metrics-schema.md` as the source of truth** → spec-like
  doc consumers can reference; CONVENTIONS.md stays navigable.

## Open Questions

- None blocking. All OQ-1 through OQ-5 resolved in discovery.md
  Design-Exploration Decisions section.

## Risks

- **Consumer null-handling regression.** If any consumer has latent
  null-deref bugs, spike/autopilot archives could surface them.
  Mitigation: AC-4 explicitly tests telemetry reads a spike state.yaml.
- **Schema-dispatch divergence over time.** The new spike/autopilot arm
  in `compute-swe-metrics.sh` could drift from the feature arm.
  Mitigation: `contracts/metrics-schema.md` documents the intended
  shape; regression fixture (AC-6) catches feature-side drift.
- **Autopilot session state.yaml schema doesn't match feature state.yaml
  shape exactly.** If telemetry expects fields that autopilot sessions
  can't provide (e.g., `worktree_path`), the consumer could fail.
  Mitigation: session state.yaml follows the canonical top-level
  contract; any missing fields are emitted as null; tested via AC-3.

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
