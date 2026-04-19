# Design: Fix 10 Workflow Issues Surfaced in Autopilot 2026-04-19-002

## Context

Autopilot 2026-04-19-002 produced a working feature end-to-end but left
a trail of 10 workflow bugs. Discovery traced each to a single file and
confirmed root causes. Nothing new needs to be built — every fix is a
bounded patch of existing prose, YAML, Python, or shell.

Boundaries:
- Core package: `config/scripts/orchestrator_next/` — production code.
- Agent prompts: `agents/*.md` — instruction-driven behavior.
- Skill: `skills/orchestrate/SKILL.md` — driver behavior.
- Step contracts: `config/steps/*.yaml` — step I/O specs.
- Inline scripts: `scripts/inline/*.sh` — shell execution paths.

## Goals / Non-Goals

### Goals

- Fix all 10 issues in a single coherent PR.
- Preserve existing test coverage; add tests for the two code patches
  (`_migrate_step_events`, dispatch phase hint) and a shell test for
  archive cleanup.
- Resolve all four open questions from discovery.

### Non-Goals

- No metrics-DB schema redesign.
- No state.yaml schema extension (`phases_ordered` is deferred).
- No ideator / backlog-creation flow redesign (ISSUE-11 is documented).

## Approaches Considered

### Approach A — Single coherent PR, ISSUE-8 via option (c) (RECOMMENDED)

All 10 fixes in one branch. Blockers land first in task order; polish
follows. ISSUE-8 gets the smallest safe fix (loud hint + doc).

- **Complexity**: M (10 bounded edits; 2 small Python patches; 3 tests)
- **Reuse**: High — extends existing patterns (migration helper,
  dispatcher warning via stderr, backlog-cleanup mirrors
  `.state/<slug>` cleanup already in script).
- **Pros**: Single review gate; atomic fix for autopilot's next run;
  shared test suite; no schema change.
- **Cons**: Larger diff surface; one failing test could block unrelated
  fixes (mitigated by task grouping).

### Approach B — Split blockers-only PR + polish follow-up

Land ISSUE-1, 2/10.2, 7, 8, 10.1 only; defer 4, 5, 6, 9, 10.3 to a
follow-up.

- **Complexity**: S (blockers PR) + S (polish PR) = effectively M.
- **Reuse**: Same as A.
- **Pros**: Unblocks telemetry faster; smaller review bite.
- **Cons**: Two review cycles; polish historically drifts (orchestrator-doctor
  et al are in backlog uncompleted for months); same total work.

### Approach C — Dispatcher phase-advance automation (ISSUE-8 option b)

As Approach A, but replace ISSUE-8 option (c) with option (b):
`phases_ordered` written to state.yaml at init; dispatcher consumes it.

- **Complexity**: L (adds state.yaml schema field, workflow-init
  change, dispatch.py phase-sequencing logic, dispatcher tests).
- **Reuse**: Medium — introduces new state.yaml field.
- **Pros**: Full automation; eliminates driver-error path.
- **Cons**: Schema extension; larger blast radius; option (c) is
  sufficient for a solo operator.

### Selected Approach

**Approach A.** Auto-selection heuristic: Approach B's complexity is
effectively equal to A (same total work, split). Approach C is L >
M. Lowest complexity wins → A. Additionally, A maximizes reuse (no new
state.yaml field, no new script, no new config knob).

## High-Level Design

### Architecture Overview

Ten bounded edits keyed to issue IDs. No new modules, no new agents,
no new steps. The table below summarizes:

| Issue | File | Kind | Patch summary |
|-------|------|------|---------------|
| ISSUE-1 | `agents/workflow-init.md` (§2) | prose | Add explicit YAML example naming `active:` key |
| ISSUE-2 / ISSUE-10.2 | `config/scripts/orchestrator_next/upsert.py` (`_migrate_step_events`, `ensure_schema`) | Python | `DROP INDEX → ALTER RENAME → CREATE INDEX` sequence |
| ISSUE-4 | `config/steps/preview-route.yaml` (line 45) | YAML | `outputs: [route_preview]` |
| ISSUE-5 | `spec/project.yaml` (line 134) | YAML | `verify_commands: {test: pytest ...}` |
| ISSUE-6 | `skills/orchestrate/SKILL.md` (§4 run_step) | prose | Default `run_in_background: true` + carve-outs |
| ISSUE-7 | `agents/developer.md` (+ `agents/workflow-init.md`) | prose | Forbid state.yaml edits; mandate `orchestrator record` |
| ISSUE-8 | `config/scripts/orchestrator_next/dispatch.py` (~line 288) + `skills/orchestrate/SKILL.md` §5 | Python + prose | Loud stderr WARNING when non-terminal phase completes; skill doc spells out driver's advance responsibility |
| ISSUE-9 | `scripts/inline/archive-completed-change.sh` + `config/steps/archive-completed-change.yaml` (step 5) | shell + prose | Remove `spec/changes/backlog/<slug>/`; update instruction |
| ISSUE-10.1 | `skills/orchestrate/SKILL.md` (§4 run_step) | prose | USAGE CAPTURE promoted from comment to numbered mandatory step + post-assert |
| ISSUE-10.3 / ISSUE-13 | `config/steps/compute-swe-metrics.yaml` (instruction step 2a) | prose | Fix path `scripts/` → `scripts/inline/` |
| **FR-11 (root cause for 1, 7, 10.1)** | `config/scripts/orchestrator_next/record.py` | Python | Three asserts at record boundary: workflow_plan.active shape, agent-step usage present, state.yaml parse post-write |
| data cleanup | `spec/changes/backlog/{5 stale entries}/` | data | `git rm -r` each directory |

### Key Abstractions

- **Migration idempotency**: `_migrate_step_events` already uses "if
  old column exists AND new does not" guard; the fix extends it with
  an index drop/recreate guard using the same conditional shape.
- **Driver compliance via prose**: six of ten fixes are prose edits
  relying on the driver (Claude running the orchestrate skill)
  following instructions. This is the existing enforcement model;
  no new mechanism is introduced.

## Low-Level Design

### Components

#### 1. `_migrate_step_events` (FR-2)

**File**: `config/scripts/orchestrator_next/upsert.py` (lines 179–199).

**SQL sketch** (validated against actual schema from upsert.py lines
170–176 + `_CREATE_INDEX`):

```python
_INDEX_NAME = "idx_step_events_change"

def _migrate_step_events(db) -> None:
    """Rename otel-prefixed columns to plain names. Drops and recreates
    idx_step_events_change around the rename because DuckDB refuses
    ALTER TABLE ... RENAME COLUMN while an index depends on the table.
    """
    try:
        existing = {row[0] for row in db.execute("DESCRIBE step_events").fetchall()}
    except Exception:
        return
    needs_rename = any(
        old in existing and new not in existing
        for old, new in _STEP_EVENTS_RENAMES
    )
    if not needs_rename:
        return  # fast path — no-op on fresh / already-migrated tables
    db.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
    for old, new in _STEP_EVENTS_RENAMES:
        if old in existing and new not in existing:
            db.execute(f"ALTER TABLE step_events RENAME COLUMN {old} TO {new}")
    # Index is recreated by ensure_schema() via _CREATE_INDEX below.
```

The `ensure_schema` ordering at lines 197–199 stays as-is:

```python
db.execute(_DDL_STEP_EVENTS)        # CREATE TABLE IF NOT EXISTS
_migrate_step_events(db)             # drops + renames (no-op if already plain)
db.execute(_CREATE_INDEX)            # CREATE INDEX IF NOT EXISTS — idempotent; re-adds after rename
```

Rationale: `_CREATE_INDEX` is `IF NOT EXISTS`, so whether the migration
dropped it or not, the post-call creates or restores it. The fast-path
`needs_rename=False` exit avoids dropping the index on normal calls.

#### 1.5 `record.py` root-cause validation layer (FR-11)

**File**: `config/scripts/orchestrator_next/record.py` — extend the
existing validation block (lines 71–88) with three new checks. All
three follow the established pattern: early return with
`(result_dict, 3)` on rejection.

```python
# Check A: workflow_plan.active shape on workflow-init completion.
# Root cause of ISSUE-1.
if step_id == "workflow-init" and status == "completed":
    plan = outputs.get("workflow_plan") or {}
    bad_phases = [
        p for p, body in plan.items()
        if not isinstance(body, dict)
        or not isinstance(body.get("active"), list)
        or len(body["active"]) == 0
    ]
    if bad_phases:
        return (
            {
                "action": "validation_error",
                "reason": "workflow_plan_active_missing_or_empty",
                "phases": bad_phases,
                "hint": "workflow_plan[<phase>].active must be a non-empty list of step IDs",
            },
            3,
        )

# Check B: usage required for agent (non-inline) steps.
# Root cause of ISSUE-10.1.
agent = payload.get("agent", "inline")
if status == "completed" and agent != "inline":
    usage = payload.get("usage") or {}
    has_tokens = (
        (isinstance(usage.get("input_tokens"), (int, float)) and usage["input_tokens"] > 0)
        or (isinstance(usage.get("output_tokens"), (int, float)) and usage["output_tokens"] > 0)
    )
    if not has_tokens:
        return (
            {
                "action": "validation_error",
                "reason": "agent_step_missing_usage",
                "step_id": step_id,
                "agent": agent,
                "hint": "agent steps must record usage.input_tokens or usage.output_tokens > 0",
            },
            3,
        )
```

```python
# Check C: re-parse state.yaml after write. Root cause of ISSUE-7.
# Hand-edits that corrupt YAML now fail at the next record call — at
# the boundary of the last-touching agent — not three dispatch calls later.
with open(path) as f:
    pre_write_bytes = f.read()
# ... existing append + write ...
try:
    with open(path) as f:
        yaml.safe_load(f)
except yaml.YAMLError as e:
    # Restore pre-write state so the caller can re-attempt.
    with open(path, "w") as f:
        f.write(pre_write_bytes)
    return (
        {
            "action": "error",
            "reason": "state_yaml_parse_failure",
            "detail": str(e),
            "hint": "state.yaml parse failed after record. Likely an earlier hand-edit corrupted the file.",
        },
        4,
    )
```

**Idempotency / re-entry**: validation is pure (reads input + outputs
+ state); no side effects before exit-3 return. Check C restores pre-write
state so callers can fix and retry without corrupted history.

**Enforcement vs. documentation**: prose prohibitions in `workflow-init.md`
and `developer.md` (FR-1, FR-6) remain; they explain *why* to a reader.
The asserts in this section are the *how* — structural enforcement. The
pair is intentional: docs answer "what should I do?"; asserts answer
"what happens if I don't?".

#### 2. Dispatcher phase hint (FR-7)

**File**: `config/scripts/orchestrator_next/dispatch.py` (around line 288).

```python
# When phase is complete and phase verify already evaluated, decide:
# are there more phases after this one per workflow_plan? If so, emit
# a warning to stderr and still return complete_workflow (option c —
# driver is responsible for advancing phase). Otherwise, complete
# normally.
plan = (state.workflow_plan or {})
phase_names = list(plan.keys())  # note: dict order is insertion order in py3.7+
# Fall-back: if dict is alphabetical (ISSUE-14), we cannot reliably
# compute "next phase" — but we CAN detect "not the only phase"
# which is sufficient for a loud hint.
if len(phase_names) > 1 and state.phase in phase_names:
    idx = phase_names.index(state.phase)
    remaining = [p for p in phase_names if p != state.phase]
    if remaining:
        print(
            f"WARNING: phase '{state.phase}' is complete but "
            f"workflow_plan has other phases ({', '.join(remaining)}). "
            f"Driver must advance state.yaml 'phase' field and re-run "
            f"'orchestrator next' before completing workflow.",
            file=sys.stderr,
        )
return {"action": "complete_workflow"}, 1
```

Note: ISSUE-14 (alphabetical dict order) means we cannot compute the
*correct next* phase from workflow_plan alone. Option (c) does not
need to — it only needs to tell the driver "you are not done". The
skill doc directs the driver to consult the schema for ordering.

#### 3. Backlog cleanup in archive (FR-8)

**File**: `scripts/inline/archive-completed-change.sh` — append cleanup
block after existing archive commit; integrate into the same commit
if possible (git amend is avoided per CLAUDE.md — use a separate
cleanup commit, which is the script's existing pattern).

```bash
# After the archive commit succeeds, remove the backlog entry (idempotent).
BACKLOG_DIR="$REPO_ROOT/spec/changes/backlog/$CHANGE_ID"
if [ -d "$BACKLOG_DIR" ]; then
  git -C "$REPO_ROOT" rm -r "$BACKLOG_DIR" >/dev/null 2>&1 || rm -rf "$BACKLOG_DIR"
  git -C "$REPO_ROOT" commit -m "cleanup: remove $CHANGE_ID from backlog" >/dev/null 2>&1 || true
fi
```

Consumer-glob check (per dispatch instruction learned rule): grep
showed no consumer of `spec/changes/backlog/<slug>/` other than the
ideator's "pick from backlog" scan and manual inspection. Removing an
already-archived entry is safe.

Also commit the 5 currently-stale entries' removal as a one-time
`git rm -r` in this PR's diff (not in the script).

**Contract update**: `config/steps/archive-completed-change.yaml`
instruction step 5 adds a bullet: "Remove
`spec/changes/backlog/<change_id>/` if present."

#### 4. Prose fixes (FR-1, FR-5, FR-6, FR-9, FR-10)

- `agents/workflow-init.md` §2: insert canonical example block showing
  `workflow_plan: {<phase>: {active: [...], filtered: [...]}}`. Add a
  new "State Updates" constraint: "Use `orchestrator record` for
  step_history appends; do not Edit/Write state.yaml directly."
- `agents/developer.md`: add a "State Updates" section with the same
  prohibition.
- `skills/orchestrate/SKILL.md` §4 `run_step` branch: (a) add
  `run_in_background: true` as the default on the spawn call, with
  "exceptions: ideator, reviewer" inline; (b) convert the
  USAGE CAPTURE comment block into a numbered step labeled
  "MANDATORY: USAGE CAPTURE" with an explicit post-step assertion.
- `skills/orchestrate/SKILL.md` §5 "Phase transitions": add an
  explicit note: "If `orchestrator next` returns `complete_workflow`
  with a stderr WARNING about remaining phases, do NOT complete — update
  state.yaml `phase` field to the next phase from the schema ordering
  and continue the loop."
- `config/steps/compute-swe-metrics.yaml` line 25: change
  `$ORCHESTRATOR_HOME/scripts/compute-swe-metrics.sh` →
  `$ORCHESTRATOR_HOME/scripts/inline/compute-swe-metrics.sh`.

#### 5. Contract fix (FR-3)

- `config/steps/preview-route.yaml` line 45: replace `- state.yaml
  route_preview block` with `[route_preview]`.

#### 6. Project config (FR-4)

- `spec/project.yaml` line 134: replace `verify_commands: []` with:
  ```yaml
  verify_commands:
    test: pytest config/scripts/orchestrator_next/tests/ -q
  ```
  (Dict form confirmed via `scripts/inline/capture-test-baseline.sh`
  which reads `vc.get('test')` first — OQ-1 resolved.)

### Data Flow

1. Driver runs `orchestrator next` → dispatcher consults state.yaml's
   `workflow_plan.<phase>.active`. (FR-1 ensures key name correct.)
2. Driver spawns agent with `run_in_background: true`. (FR-5.)
3. Agent completes → driver extracts `<usage>` block. (FR-9.)
4. Driver runs `orchestrator record` with usage payload. (FR-6.)
5. `record.py` calls `upsert.ensure_schema()` → `_migrate_step_events`
   → `CREATE INDEX` — all succeed. (FR-2.)
6. On phase completion, dispatcher emits WARNING if non-terminal.
   Driver advances phase. (FR-7.)
7. On workflow complete, `archive-completed-change.sh` removes
   `.state/<slug>/` AND `spec/changes/backlog/<slug>/`. (FR-8.)

### State Management

No new state. The one in-place schema change is the `step_events`
table column renames, which are already in flight; this feature
only unblocks the migration.

### Error Handling

- `_migrate_step_events`: existing try/except around DESCRIBE stays.
  The DROP INDEX and ALTER RENAME calls are inside the try path; any
  unexpected DB error propagates (not silently swallowed) — this is
  correct per existing module style.
- `archive-completed-change.sh` cleanup block: failures are
  non-blocking (`|| true`) to match the script's existing pattern
  (archive commit itself already uses `|| true` for the state-cleanup
  commit).
- Dispatcher WARNING: stderr-only; does not change the return code
  or action. Driver compliance is required (documented in §5).

## Constraints

- DuckDB ALTER RENAME COLUMN cannot proceed while an index exists on
  the same table. Confirmed by reproduction (discovery).
- PyYAML serializes `workflow_plan` dict keys alphabetically
  (ISSUE-14). The dispatcher cannot compute the *ordered* next
  phase from workflow_plan alone; option (c) works around this by
  delegating ordering to the driver (which can read the schema).
- `run_in_background: true` is a driver-side convention, not a code
  flag; it only takes effect if the driver obeys the prose.

## Trade-offs

- ISSUE-8 option (c) instead of (b): trades automation for
  simplicity. Accepts that the driver must manually advance `phase`
  — but with a loud stderr signal so the failure mode is visible,
  not silent.
- Prose-only fixes rely on driver/agent compliance. This matches the
  existing enforcement model; no new mechanism is warranted for a
  10-issue cleanup.
- Backlog cleanup as a separate commit (vs amending archive commit):
  matches script's existing multi-commit pattern. Amending would
  conflict with CLAUDE.md "never amend" rule.

## Decisions

- **Approach A (single coherent PR, blockers + polish together)** →
  reason: smallest total work; single review gate; no schema change;
  maximizes reuse → consequence: one moderately-sized diff.
- **ISSUE-8 → option (c): loud hint + doc** → reason: zero schema
  change; driver compliance already required for USAGE CAPTURE etc.
  → consequence: ISSUE-14 remains a constraint, not a bug to fix.
- **OQ-1 resolved: verify_commands as dict** `{test: ...}` → reason:
  `capture-test-baseline.sh` reads `vc.get('test')` first →
  consequence: exact match with tested code path; list form would
  also work but is less self-documenting.
- **OQ-3 resolved: backlog cleanup as separate commit from archive
  commit** → reason: matches script's existing multi-commit style;
  amending forbidden by CLAUDE.md → consequence: one additional
  cleanup commit per archived change (noisy but acceptable).
- **OQ-4 resolved (compute-swe-metrics run: path)**: `run:` path is
  canonical (`scripts/inline/compute-swe-metrics.sh`). Instruction
  prose is aligned to it. No script move.
- **Migration fast-path**: `_migrate_step_events` short-circuits when
  no renames are needed → reason: avoids unnecessary DROP/CREATE on
  normal calls → consequence: typical per-record overhead unchanged.
- **FR-11 root-cause enforcement in `record.py`** → reason: ISSUE-1, 7,
  10.1 all land invalid data in state.yaml and surface far downstream
  (dispatcher returns premature `complete_workflow`; cost report is
  empty; next dispatch crashes on malformed YAML). Three asserts at
  the record boundary reject them at the source → consequence: prose
  prohibitions become documentation, not enforcement. Future violations
  of the same class fail immediately with an actionable error.
- **ISSUE-6 prose-only, conscious choice** → reason: background spawn
  is a driver-harness convention (`run_in_background: true` is a Task
  tool argument; no orchestrator code path observes it) → consequence:
  no structural enforcement possible without a Claude Code harness
  change, which is out of scope. Accepted.

## Open Questions

- None remaining. OQ-1..OQ-4 resolved above; OQ-2 decided in favor
  of option (c) (see Decisions).

<!-- Format contract: contracts/artifact-formats.md § Design Format Contract -->
