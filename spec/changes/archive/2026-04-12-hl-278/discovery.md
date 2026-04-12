---
feature-id: hl-278
linear-ticket: HL-278
---

# Discovery Brief: Unify metric collection across all workflow schemas

## Problem Summary

Metric collection via `compute-swe-metrics.sh` only runs in `_complete-phase.yaml`, which is included by feature, bugfix, and chore schemas. Spike has no complete phase at all — real token/cost usage evaporates. Autopilot tracks iterations in a sessions archive with no metrics rollup into `state.yaml`. This leaves two of the five workflow schemas invisible to every metrics consumer (telemetry, learn, workflow-improver).

## Personas & Actors

- **Spike author** — runs a spike workflow; expects token/cost usage to be captured like any other workflow.
- **Autopilot operator** — runs multi-iteration autopilot sessions; expects per-session and aggregate metrics to be available to telemetry/learn.
- **Metrics consumers** (telemetry, learn, workflow-improver) — read `spec/changes/archive/*/state.yaml` for `metrics:` blocks; must tolerate schema-specific fields being absent without crashing.
- **compute-swe-metrics.sh** — the bash script that produces the `metrics:` YAML block; currently schema-unaware.

## Use Cases

### Happy Path

UC-1: Spike produces capturable metrics — spike author completes a spike (explore → prototype → summarize); after `summarize`, a metrics block is written to `state.yaml` and the change is archived. Telemetry can report token/cost/churn for the spike. Fields `resolve_rate`, `pass_at_1`, `pass_at_2`, `regression_rate` are absent or null since there are no tasks to resolve.

UC-2: Autopilot session has rolled-up metrics — autopilot completes a session (one or more iterations); `sessions_archive.yaml` contains each iteration's outcome. A metrics rollup is computed from iteration records (tokens, cost, wall-clock per iteration) and written as a `metrics:` block in the autopilot session state so telemetry can report on it.

UC-3: Consumer reads feature metrics — existing feature/bugfix/chore workflows continue producing full metrics blocks (tokens, cost, resolution, pass_at_k, churn, review_scores) unchanged. No behavior change for these schemas.

### Error & Edge Cases

UC-E1: Consumer handles missing resolve_rate without crashing — telemetry skill reads a spike's `state.yaml` which has no `metrics.resolution.resolve_rate`. The consumer skips that field (uses null/omit) rather than crashing or showing "null" to the user. Current telemetry SKILL.md already says "Use null values gracefully — skip metrics where the data field is null" (line 143). No contract change needed; but the metrics block shape must not include the field at all or use null, not a garbage value from division-by-zero.

UC-E2: Spike with no git commits — a pure exploration spike produced no commits. `churn.files_changed` is 0. The metrics block should be written with `files_changed: 0` without failing. Currently `compute-swe-metrics.sh` handles the zero-commits case gracefully (COMMIT_HASHES check at line 235).

UC-E3: Autopilot session with failed iterations — some iterations in a session archive have `status: failed`. The rollup metrics should aggregate all iterations (not just completed ones) and surface the failure count.

## Scope

### In Scope

- Add a `complete` phase to spike schema with reduced metrics (tokens, cost, churn, per_agent — skip resolve_rate/pass_at_k/regression_rate/review_scores).
- Make `compute-swe-metrics.sh` schema-aware: accept a schema argument (or read `schema:` from `state.yaml`, which it already does at line 404) and skip inapplicable fields when schema is `spike`.
- Roll up autopilot session metrics: compute aggregate tokens/cost from iteration records and write a `metrics:` block, either into `sessions_archive.yaml` or into a companion `state.yaml` per session.
- Document the canonical metrics schema in `CONVENTIONS.md` — a new `§ Metrics Schema` section covering field definitions, which fields are schema-required vs schema-optional, and the null/omit contract for optional fields.

### Out of Scope

- Changing the shape of the metrics block that feature/bugfix/chore already write — HL-277 consumers migrated to read from archived `state.yaml`; the block format must not change.
- Retroactively backfilling metrics for previously completed spikes — no migration.
- Changing how autopilot picks or executes work — only the metrics capture changes.
- Adding new metric fields not already computed by `compute-swe-metrics.sh` (e.g., test coverage, lint scores).

## UI Direction

N/A — no UI components. All changes are to bash scripts, YAML step contracts, workflow schemas, and documentation.

## Key Decisions

### Build or Reuse?

**Reuse and extend.** The existing `compute-swe-metrics.sh` already reads `schema:` from `state.yaml` (line 404) but does nothing with it. The script can be extended with a schema-dispatch section that conditionally omits resolution/review fields when `SCHEMA=spike`. This avoids a new script and keeps a single metrics code path.

For spike: **extend `_complete-phase.yaml`** vs **create `_complete-phase-reduced.yaml`** — both viable. See open questions.

For autopilot: the session record format (`sessions_archive.yaml`) is a list of iterations with no `metrics:` block today. The rollup can be written into a new `metrics:` key on each session entry in `sessions_archive.yaml`, OR into a separate state.yaml per session stored alongside sessions_archive.yaml. This is an unresolved placement question.

### Schema-aware dispatch in compute-swe-metrics.sh

The `SCHEMA` variable is already populated (line 404). Option A: add `if [[ "$SCHEMA" == "spike" ]]; then` guards around the resolution/review output section. Option B: add a `--schema` CLI flag so callers can override. Option A is simpler; Option B is more explicit. Either way, the output YAML block simply omits the guarded fields rather than emitting null strings.

## Integration Points

| Component | Current Role | Change Required |
|---|---|---|
| `scripts/compute-swe-metrics.sh` | Computes and emits metrics YAML block | Add schema-dispatch: skip resolution/review output for spike schema |
| `workflows/_complete-phase.yaml` | Shared complete phase for feature/bugfix/chore | Either: add spike as a user of this include (with a flag), or create `_complete-phase-reduced.yaml` |
| `workflows/spike.yaml` | Has explore/prototype/summarize; no complete phase | Add a `complete` phase (possibly via include) |
| `steps/compute-swe-metrics.yaml` | Invokes the script, writes to state.yaml | No change needed — fallback null block already covers missing fields |
| `steps/autopilot-iterate.yaml` | Archives session records to sessions_archive.yaml | Add: write per-iteration metrics to session record (tokens from sub-feature state.yaml) |
| `workflows/autopilot.yaml` | report phase calls autopilot-session-report | Possibly: extend report phase or add a new step for session metrics rollup |
| `config/steps/CONVENTIONS.md` | Documents state field registry and state updates | Add: `§ Metrics Schema` section documenting full block shape, per-schema field optionality |
| `skills/telemetry/SKILL.md` | Reads `metrics:` blocks from archived state.yaml | No change needed — already handles null gracefully |
| `skills/learn/SKILL.md` | Reads `metrics.retries.total` and `metrics.resolution.tasks_total` | No change needed — uses fallback to 0 |
| `agents/workflow-improver.md` | Reads per-feature metrics table including resolve/pass@1 | Minor update: mark resolution fields as N/A for spike category |

## Existing Reusable Pieces

1. **`SCHEMA` variable in compute-swe-metrics.sh** (line 404): already reads `schema:` from `state.yaml`. No new parsing needed — just add conditional output guards.

2. **`_complete-phase.yaml` include mechanism**: feature/bugfix/chore already use `include: _complete-phase`. Spike could use the same include with minimal additions — or use a new `_complete-phase-reduced.yaml` if the reduced set diverges significantly.

3. **Null fallback block in `compute-swe-metrics.yaml` step contract**: the placeholder block already uses `resolve_rate: null` etc. The same pattern can be used for spike's intentional omission.

4. **`metrics.category` field** (already emitted as `$SCHEMA`): consumers can use this to skip resolution fields for spike entries when doing cross-schema aggregation.

5. **Autopilot per-iteration `status` field**: `autopilot-iterate.yaml` already records `status: completed|failed|empty_backlog` per iteration. Token data is available in the spawned feature's `state.yaml` (the `/develop` invocation writes a full state with `step_history[].usage`). Aggregation is straightforward.

## Open Questions

- **OQ-1**: Should spike get its own `_complete-phase-reduced.yaml` include, or a flag on `_complete-phase.yaml`? A new file is more explicit and leaves the shared include untouched; a flag adds complexity but avoids a near-duplicate file.

- **OQ-2**: Where does autopilot write per-session metrics — as a `metrics:` key on each session entry in `sessions_archive.yaml`, or into a new `state.yaml` per session stored in `~/.workflows/autopilot/archive/<session_id>/state.yaml`? The latter would make autopilot sessions visible to existing `spec/changes/archive/*/state.yaml` consumers without any consumer change; the former keeps session data consolidated in one file.

- **OQ-3**: Does autopilot metric rollup happen in `autopilot-iterate.yaml` (at archive time, per iteration) or in a new step in the `report` phase (aggregate at session end)? Per-iteration is more resilient to interruption; session-end is simpler.

- **OQ-4**: Do bugfix and chore inherit any spike-specific changes? No — they already have the full `_complete-phase.yaml`. The only risk is if a new `_complete-phase-reduced.yaml` drifts from the shared one over time.

- **OQ-5**: Should `compute-swe-metrics.sh` emit `resolution: ~` (explicit YAML null) or omit the `resolution:` key entirely when schema is spike? Explicit null is more consumer-friendly (block shape is always present); omitting is cleaner but requires all consumers to guard for missing key. Current telemetry already handles null gracefully.

- **OQ-6**: The `CONVENTIONS.md § Metrics Schema` section — where exactly does it live? The file currently delegates sections to `contracts/` subdirectory files. Should the metrics schema go into `contracts/metrics-schema.md` (consistent with other contracts) or stay inline in `CONVENTIONS.md`?

## Design-Exploration Decisions (2026-04-12, autopilot)

### Chosen Approach: B — Reduced spike include + schema-dispatch + autopilot rollup + CONVENTIONS.md docs

Auto-selection heuristic (auto_approve_phases=true):

| Approach | Complexity | Reuse count | Notes |
|---|---|---|---|
| A — flag on shared `_complete-phase.yaml` | small | 1 (shared include) | Couples spike's reduced shape into the canonical include; risks silent drift for feature/bugfix/chore |
| B — new `_complete-phase-spike.yaml` + schema-dispatch in script + autopilot rollup | small | 2 (existing `SCHEMA` var at line 404 + include-mechanism pattern) | Additive; no change to shared include; consumers unaffected |
| C — per-schema complete phases for all workflows | medium | — | Rejected on complexity |

Tiebreaker: A and B both "small" → higher reuse count wins → **B** (reuse=2 > A reuse=1). Alphabetical fallback not needed.

### Resolutions: OQ-1 through OQ-5

- **OQ-1 — Spike include file shape**: **New `workflows/_complete-phase-spike.yaml`**. Keeps the canonical `_complete-phase.yaml` untouched so feature/bugfix/chore are not at risk of regression from a spike-only flag; divergence is acceptable because the reduced set is intentionally narrower (no tasks → no resolve/pass@k).

- **OQ-2 — Autopilot session metrics location**: **New per-session `state.yaml` at `~/.workflows/autopilot/archive/<session_id>/state.yaml`**. Reuses the archived-state.yaml consumer path HL-277 just standardized on — telemetry/learn/workflow-improver need zero changes to pick up autopilot sessions. `sessions_archive.yaml` stays the operational ledger; `state.yaml` is the metrics surface.

- **OQ-3 — Autopilot rollup timing**: **Per-iteration write in `autopilot-iterate.yaml`, finalized in `report` phase**. Each iteration appends its metrics into the session `state.yaml` at archive time (interruption-resilient); the `report` phase performs the final aggregate pass and writes the session-level `metrics:` block. Best of both: crash-safe and simple final shape.

- **OQ-4 — Null vs omit for spike-inapplicable fields**: **Emit explicit YAML null (`resolve_rate: ~`, etc.) under a present `resolution:` key**. Block shape stays stable across schemas, so consumers iterate fields uniformly; telemetry SKILL.md already documents null-skip behavior (line 143). Omitting keys would force every consumer to add `has_key` guards.

- **OQ-5 — CONVENTIONS.md Metrics Schema placement**: **New `config/steps/contracts/metrics-schema.md`, referenced from `CONVENTIONS.md § Metrics Schema` with a one-paragraph pointer**. Matches the existing pattern where `CONVENTIONS.md` delegates to `contracts/` files; keeps the top-level file navigable.

(OQ-6 is subsumed by OQ-5 above — same decision.)

### Impact Summary

**Files created**:
- `workflows/_complete-phase-spike.yaml` (reduced include: churn + tokens/cost + per_agent; no tasks/review)
- `config/steps/contracts/metrics-schema.md` (canonical field registry + per-schema optionality)
- `~/.workflows/autopilot/archive/<session_id>/state.yaml` (runtime artifact; written by autopilot)

**Files modified**:
- `scripts/compute-swe-metrics.sh` — schema-dispatch guards around resolution/review emission when `SCHEMA=spike|autopilot`; null-value emission (not omission) for inapplicable fields
- `workflows/spike.yaml` — add `complete` phase using the new reduced include
- `steps/autopilot-iterate.yaml` — append per-iteration metrics to session `state.yaml`
- `workflows/autopilot.yaml` — `report` phase invokes metrics rollup producing final session `metrics:` block
- `config/steps/CONVENTIONS.md` — add `§ Metrics Schema` pointer paragraph
- `agents/workflow-improver.md` — minor: resolution fields marked N/A for spike/autopilot categories

**Consumer-side changes required**: **None.** The metrics block shape HL-277 consumers rely on is preserved — spike/autopilot simply emit null for inapplicable fields under the same keys. Telemetry, learn, and workflow-improver continue reading archived `state.yaml` unchanged.
