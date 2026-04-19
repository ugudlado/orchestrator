---
feature-id: fix-workflow-issues-2026-04-19
linear-ticket: ~
---

# Discovery Brief: Fix Workflow Issues 2026-04-19

## Feature Summary

Autopilot session `2026-04-19-002` surfaced 10 systemic workflow bugs spanning
agent prose, CLI dispatch logic, DuckDB schema migration, and step contract
definitions. This feature fixes all of them in one pass so the next autopilot
run produces usable telemetry, has stable phase dispatch, and cleans up 5 stale
backlog entries. Two tiers: blockers (must land for telemetry to work) and
polish (correctness / quality-of-life).

---

## Existing Codebase Analysis

### ISSUE-1 — workflow-init writes `active_steps:` / dispatcher reads `active:`

**Location:**
- `agents/workflow-init.md` line 57: "list the active steps" — no key name specified, no example YAML block.
- `config/scripts/orchestrator_next/dispatch.py` line 71: `phase_plan.get("active", [])` — canonical read key.
- Confirmed in archived state.yaml files (e.g. `spec/changes/archive/2026-04-18-tool-calls-rename-and-preview-route-fix/state.yaml`): all use `active:` under each phase.

**Root cause:** `workflow-init.md` section 2 describes computing the workflow_plan but omits naming the YAML key for the step list. An agent reading the prose writes `active_steps:` (descriptively natural) instead of `active:` (what the dispatcher expects).

**Symptom:** `orchestrator next` returns `complete_workflow` immediately after init because `_phase_step_ids()` reads an empty list.

**MVF sketch:** Add one explicit YAML example block to `agents/workflow-init.md` section 2 showing the exact shape:
```yaml
workflow_plan:
  <phase>:
    active: [step-a, step-b]
    filtered: []
```
No code change needed — the dispatcher (`dispatch.py:71`) is already correct.

**Test:** Run workflow-init on a fresh slug; grep resulting `state.yaml` for `active_steps:` (must be absent) and `active:` (must be present under each phase).

**Risk:** Low. Single prose-only edit to agent prompt.

---

### ISSUE-2 / ISSUE-10.2 — `step_events` ALTER fails with Dependency Error

**Location:**
- `config/scripts/orchestrator_next/upsert.py` lines 179–187 (`_migrate_step_events`).
- Production `metrics.duckdb`: columns still have otel-prefixed names (`gen_ai_request_model`, `gen_ai_usage_input_tokens`, etc.).
- Index `idx_step_events_change` on `step_events(repo_root, change_id)` is created at line 199 before `_migrate_step_events` is called at line 198.

**Root cause (confirmed by reproduction):** DuckDB cannot `ALTER TABLE … RENAME COLUMN` when an index exists on that table. The `ensure_schema()` call order creates the DDL (table + index) at lines 197–199, then calls `_migrate_step_events` at line 198 — but the index was created before the rename is attempted. Even when the table is pre-existing (from a prior session), the rename fails because `idx_step_events_change` exists.

**MVF sketch:** In `_migrate_step_events`, before each `RENAME COLUMN`, drop the blocking index and recreate it after all renames complete:
```python
db.execute("DROP INDEX IF EXISTS idx_step_events_change")
for old, new in _STEP_EVENTS_RENAMES:
    if old in existing and new not in existing:
        db.execute(f"ALTER TABLE step_events RENAME COLUMN {old} TO {new}")
db.execute(_CREATE_INDEX)
```
The `ensure_schema` call order can also be reordered: `_migrate_step_events` before `_CREATE_INDEX`, with `CREATE INDEX IF NOT EXISTS` handling the idempotent case.

**Test:** Seed a temp DuckDB with otel-prefixed columns + the blocking index; call `ensure_schema()`; assert columns are now `model`, `input_tokens`, etc.; assert no exception raised; assert index still exists.

**Risk:** Medium. Index drop/recreate is transactional in DuckDB but must be done in the same connection. If another process holds the connection during the window, the index is absent temporarily. In practice metrics.duckdb is not multi-writer, so risk is low.

---

### ISSUE-3 — ORCHESTRATOR_HOME path inconsistency (CLOSED: stale)

**Investigation result:** `ORCHESTRATOR_HOME/workflows/` does NOT exist at any path. All workflow files live under `config/workflows/`. Every reference in skill/agent/contract prose uses `$ORCHESTRATOR_HOME/config/workflows/` — consistent and correct.

```
Grep: "ORCHESTRATOR_HOME/workflows" → 0 matches
Grep: "ORCHESTRATOR_HOME/config/workflows" → 8 matches (all correct)
```

**Action:** Close ISSUE-3 as stale. No fix needed.

---

### ISSUE-4 — `preview-route.yaml` output name is a phrase, not an identifier

**Location:** `config/steps/preview-route.yaml` line 45:
```yaml
outputs:
  - state.yaml route_preview block
```

**Root cause:** The output name `"state.yaml route_preview block"` is a multi-word phrase. `record.py` validates `payload.outputs` must contain every name in `contract.outputs`. Downstream callers trying to key this output by string are error-prone.

**MVF sketch:** Change line 45 to `outputs: [route_preview]`. Also update `instruction:` step 3 reference if any. The actual state.yaml key written is `route_preview:` (already correct per instruction step 3), so no state.yaml format change is needed.

**Test:** `orchestrator record` call with `{"outputs": {"route_preview": {...}}}` passes validation; call with the old phrase name fails validation.

**Risk:** Low. Output name rename only; the state.yaml key written by the script is unchanged.

---

### ISSUE-5 — `verify_commands: []` disables test gating

**Location:** `spec/project.yaml` line 134: `verify_commands: []`.

**Root cause:** Empty list means no `pytest` invocation during phase verify. TDD promise is unenforced; capture-test-baseline skips.

**Existing test suite:** `config/scripts/orchestrator_next/tests/` contains test files (confirmed by grep of test_*.py).

**MVF sketch:** Populate `verify_commands` with a pytest invocation:
```yaml
verify_commands:
  test: pytest config/scripts/orchestrator_next/tests/ -q
```
The key name `test` should match what the verify phase step reads.

**Open question (OQ-1):** Does the verify phase step read `verify_commands.test` or a flat list? Architect should confirm the exact schema field name before this is implemented.

**Test:** Run the verify phase step; confirm pytest invocation is executed and non-zero exits block phase completion.

**Risk:** Low to medium. If existing tests are failing, this will now block the workflow. Must ensure tests pass before populating.

---

### ISSUE-6 — Agent spawns should default to `run_in_background: true`

**Location:** `skills/orchestrate/SKILL.md` lines 116–136 (dispatch loop `run_step` branch).

**Current state:** The SKILL.md dispatch loop's `run_step` branch spawns agents but does not specify `run_in_background: true`. Long-running agents (developer, architect) block the driver conversation.

**MVF sketch:** In the `run_step` dispatch branch, add `run_in_background: true` as the default for agent spawns, with an explicit carve-out for short agents (ideator, reviewer) where the result is needed immediately:
```
spawn agent(action.agent) with ... run_in_background: true
  (exceptions: ideator, reviewer — run foreground to surface result inline)
```

**Test:** Document-level: the skill prose explicitly names `run_in_background: true` as the default. Functional test is observational (driver completes parallel housekeeping while agent runs).

**Risk:** Low. Prose-only change. Behavior depends on driver following instructions.

---

### ISSUE-7 — Developer agent hand-edits state.yaml, producing malformed YAML

**Location:** `agents/developer.md` — no mention of `orchestrator record`; no prohibition on direct state.yaml edits. The developer uses Write/Edit tools to append step_history entries directly.

**Confirmed viable fix:** `record.py` is a full `orchestrator record` subcommand: accepts JSON on stdin, validates against contract outputs, appends step_history entry with correct indentation, advances `next_step`. The `Bash` tool is in the developer agent's tool list (`agents/developer.md` line 6).

**MVF sketch:** Add a constraint block to `agents/developer.md` (under "What You Don't Do" or a new "State Updates" section):
```
- MUST NOT directly edit state.yaml with Write/Edit.
- Use `orchestrator record <state.yaml> <<< '{...}'` for all step_history appends.
```
Mirror the same constraint in `agents/workflow-init.md` constraints section (already has similar language for write scope but does not address step_history append method).

**Test:** Developer agent task execution; state.yaml parses as valid YAML after each step (no `yaml.safe_load` exception); `orchestrator next` proceeds without manual repair.

**Risk:** Low. Prose-only. However, if the developer agent ignores instructions and falls back to Edit, behavior is unchanged.

---

### ISSUE-8 — Dispatcher returns `complete_workflow` when current phase is done, not when all phases are done

**Location:** `config/scripts/orchestrator_next/dispatch.py` line 288–289:
```python
# All phases complete (T-2: only single phase in fixtures, so this is "all done")
return {"action": "complete_workflow"}, 1
```

**Root cause:** The dispatcher only knows about the current phase (read from `state.phase`). When all steps in that phase complete, it returns `complete_workflow` without checking whether other phases remain in `workflow_plan`. The comment at line 288 acknowledges this is a single-phase-fixture shortcut.

**Constraint on "move into dispatcher" option:** `workflow_plan` dict keys in state.yaml are written in alphabetical order (not schema order) by the PyYAML serializer. The dispatcher cannot determine phase sequence from `workflow_plan` keys alone — it would need to re-read the schema YAML or have ordered phase information in state.yaml.

**Three fix options (all valid; design chooses):**
- **(a) Dispatcher reads schema YAML at dispatch time** to get ordered phase list. Costs a file I/O per dispatch call; avoids state.yaml changes.
- **(b) Store `phases_ordered: [specify, implement, complete]` in state.yaml at init time.** Dispatcher reads this list. Requires workflow-init to write it; state.yaml schema change.
- **(c) Strengthen orchestrate skill docs + emit a loud CLI hint** when phase is complete but is not the last phase. No code change to dispatcher; relies on driver compliance.

**MVF sketch (options b or c are simpler):** Option (c) costs only a prose edit to `skills/orchestrate/SKILL.md` section 5 "Phase transitions" + a new `WARNING` print in dispatch.py when `complete_workflow` would fire but other phases exist in `workflow_plan`. Option (b) is one additional field at init time, enables full automation, but requires schema extension.

**Test (option c):** Dispatcher returns `complete_workflow` only when state.yaml `phase` is the last phase per the ordered list (or per the skill docs reminder); option (b) test: dispatcher reads `phases_ordered` and returns correct next-phase action.

**Risk:** Medium for option (a/b); low for option (c). Option (c) still relies on driver manually advancing phase, which was the prior behavior — but with louder guidance.

---

### ISSUE-9 — `archive-completed-change` does not remove stale backlog entries

**Location:** `scripts/inline/archive-completed-change.sh` — no `rm -rf` or backlog cleanup of any kind.

**Current state (confirmed):** 5 stale backlog entries confirmed under `spec/changes/backlog/`: feature-complexity-tracking, orchestrator-doctor, per-step-allowed-tools, fix-cost-usd-and-widen-token-split, tool-calls-rename-and-preview-route-fix. All have corresponding archive entries.

The contract (`config/steps/archive-completed-change.yaml`) instruction step 5 says "Clean up the active change directory" (the `.state/<slug>/` dir) — but says nothing about `spec/changes/backlog/<slug>/`.

**MVF sketch:** Add a cleanup pass to `archive-completed-change.sh` after the archive commit:
```bash
BACKLOG_DIR="$REPO_ROOT/spec/changes/backlog/$CHANGE_ID"
if [ -d "$BACKLOG_DIR" ]; then
  rm -rf "$BACKLOG_DIR"
  git -C "$REPO_ROOT" rm -rf "$BACKLOG_DIR" 2>/dev/null || true
  git -C "$REPO_ROOT" commit -m "cleanup: remove $CHANGE_ID from backlog" 2>/dev/null || true
fi
```
Also update `config/steps/archive-completed-change.yaml` instruction step 5 to reference backlog cleanup. As a one-time action, the 5 stale entries should be manually removed in this PR (committed with the fix).

**Test:** After `archive-completed-change` runs, `spec/changes/backlog/<slug>/` does not exist; git history shows cleanup commit.

**Risk:** Low. Additive shell step; failure is non-blocking per existing contract rules.

---

### ISSUE-10.1 — USAGE CAPTURE block not executed by driver

**Location:** `skills/orchestrate/SKILL.md` lines 122–134 (USAGE CAPTURE comment block in `run_step` branch).

**Root cause:** The USAGE CAPTURE block is a comment block embedded inside the `run_step` dispatch case. The driver treated it as informational prose rather than a mandatory action step, and skipped it for every agent spawn.

**MVF sketch:** Restructure the `run_step` dispatch branch so USAGE CAPTURE is a numbered mandatory step rather than a comment:
```
      3. MANDATORY: Extract usage from task result <usage> block:
         - input_tokens, output_tokens, cache_read_input_tokens, cost_usd, duration_ms
         - tool_calls: tally {tool_name: count} from result tool_use blocks
         Include in orchestrator record payload under "usage".
```
Add a verify-style assertion after each `run_step` in the dispatch loop: "step_history[-1].usage.input_tokens is not null (must be present for agent steps)."

**Test:** After any agent step recorded via `orchestrator record`, `state.yaml step_history[-1].usage.input_tokens` is non-null.

**Risk:** Low. Prose restructure. Relies on driver compliance; no code change.

---

### ISSUE-10.2 — same as ISSUE-2 (duplicate, documented above)

---

### ISSUE-10.3 — `compute-swe-metrics` silently writes placeholder when script path is wrong

**Location:** `config/steps/compute-swe-metrics.yaml` line 25 (instruction step 2a): checks `$ORCHESTRATOR_HOME/scripts/compute-swe-metrics.sh`.

**Root cause (confirmed):** The script lives at `scripts/inline/compute-swe-metrics.sh` (confirmed present at `/Users/spidey/code/orchestrator/scripts/inline/compute-swe-metrics.sh`). `$ORCHESTRATOR_HOME/scripts/compute-swe-metrics.sh` (without `inline/`) does NOT exist. So the `if script exists` check always fails → placeholder written silently.

**Internal disagreement:** The step contract has both `run: scripts/inline/compute-swe-metrics.sh` (line 3 — the inline-step execution path) AND `instruction:` step 2a that says check `$ORCHESTRATOR_HOME/scripts/compute-swe-metrics.sh` (without `inline/`). These two paths disagree.

Since this step is `inline: true`, the `run:` path is what actually executes. The `instruction:` block is the fallback prose for non-inline execution. The path in `instruction:` step 2a should be updated to match the actual `run:` path.

**MVF sketch:** Update `config/steps/compute-swe-metrics.yaml` instruction step 2a: change the existence check path from `$ORCHESTRATOR_HOME/scripts/compute-swe-metrics.sh` to `$ORCHESTRATOR_HOME/scripts/inline/compute-swe-metrics.sh`. No script changes needed — the script itself is correct and non-blocking.

**Test:** Run the step with `ORCHESTRATOR_HOME` set to the repo root; script is found and executed (metrics block contains real data, not placeholder).

**Risk:** Low. Single path string correction in prose.

---

## Build or Reuse Decision

All 10 issues are **fixes to existing code/prose** — no net-new components needed. Each fix touches one file (prose edit or targeted code patch). No external libraries required.

---

## Approaches Considered

### Approach A — Fix all issues in one coherent PR (recommended)

All fixes in a single branch. Grouped into two tiers for task ordering. Blockers land first; polish follows. Treats the 10 issues as one coherent pass.

- **Effort:** Medium (10 distinct but small changes)
- **Pros:** One review gate; consistent; single set of tests
- **Cons:** Larger diff surface; one failing test could block unrelated fixes

### Approach B — Split blockers-only PR + polish follow-up

Land only ISSUE-1, 7, 8, 10.1, 10.2 in one PR; defer polish (3 CLOSED, 4, 5, 6, 9, 10.3) to a follow-up.

- **Effort:** Medium + Small
- **Pros:** Unblocks telemetry faster; smaller review
- **Cons:** Two PRs; polish may be deprioritized indefinitely

### Approach C — Dispatcher phase-advance automation (ISSUE-8 option b)

For ISSUE-8: store `phases_ordered` in state.yaml at init time and move phase-advance logic into the dispatcher, eliminating driver manual step entirely.

- **Effort:** Medium (requires workflow-init change + dispatcher change + state.yaml schema)
- **Pros:** Fully automated; eliminates driver error path
- **Cons:** Larger scope than the other option (c) for one issue; risk of regression in dispatcher tests

**Recommendation:** Approach A with ISSUE-8 resolved via option (c) (loud hint in CLI output + doc strengthening). Option (c) is the smallest safe fix with no schema change. Option (b) is a good follow-up if full automation is wanted. ISSUE-3 is closed.

---

## Personas

- **Autopilot driver (orchestrate skill)**: reads CLI dispatch output, spawns agents, records usage.
- **workflow-init agent**: bootstraps state.yaml with correct workflow_plan shape.
- **developer agent**: implements tasks, must use `orchestrator record` rather than hand-editing state.yaml.
- **metrics consumer**: runs `orchestrator cost --change-id <id>` to see per-step usage.

---

## Use Cases

### Happy Path

UC-1: Fresh workflow init — workflow-init agent creates state.yaml with `active:` key in workflow_plan; `orchestrator next` returns first step (not `complete_workflow`).

UC-2: Step usage captured — after agent task completes, driver records usage block; `orchestrator cost` returns non-empty events for that change.

UC-3: Phase transition — when specify phase completes, dispatcher emits a warning/hint that implement phase is next; driver advances `phase:` field and re-dispatches.

UC-4: Archive cleanup — `archive-completed-change` runs; both `.state/<slug>/` and `spec/changes/backlog/<slug>/` are removed; git history shows cleanup commit.

### Error and Edge Cases

UC-E1: Metrics DB with old otel columns — `ensure_schema` is called on a db with the old column names + blocking index; migration completes without error; new column names are present.

UC-E2: compute-swe-metrics on machine without script — step uses `run:` path (inline execution) which finds the script at `scripts/inline/`; placeholder is only written when the actual inline script exits non-zero.

UC-E3: Developer hand-edits state.yaml — not prevented by code (no guard), but developer.md explicitly forbids it; on violation, `orchestrator next` may fail YAML parse → surfaced as a workflow error, not silent.

---

## Scope

### In Scope

- Fix `agents/workflow-init.md` to name `active:` key explicitly with example YAML
- Fix `_migrate_step_events` in `upsert.py` to DROP INDEX before ALTER RENAME
- Close ISSUE-3 with grep evidence (no file change)
- Fix `preview-route.yaml` output name from phrase to `route_preview`
- Populate `spec/project.yaml verify_commands` with pytest invocation
- Update `skills/orchestrate/SKILL.md` to document background spawn default + mandatory USAGE CAPTURE
- Add `orchestrator record` requirement to `agents/developer.md` (and workflow-init.md)
- Update `skills/orchestrate/SKILL.md` section 5 with explicit phase transition reminder + dispatch.py warning
- Add backlog cleanup to `scripts/inline/archive-completed-change.sh` + contract prose
- Fix path mismatch in `config/steps/compute-swe-metrics.yaml` instruction
- Manually remove 5 stale backlog entries in this PR

### Out of Scope

- Not redesigning the metrics DB schema beyond fixing the ALTER dependency
- Not changing the feature-workflow schema or state.yaml format (unless option b for ISSUE-8 is chosen)
- Not rewriting the ideator or backlog-picking flow
- Not implementing automated phase-advance in the dispatcher (deferred to follow-up)
- Not fixing ISSUE-11 (missing backlog pre-req) or ISSUE-12 (Linear account limit)

---

## UI Direction

N/A — no UI components. All changes are in prose files, Python scripts, and bash scripts.

---

## Key Decisions

- **Design direction (chosen)**: **Approach A — single coherent PR,
  ISSUE-8 via option (c)**. Complexity: **M**. Auto-selection heuristic:
  Approach B splits same work into two PRs (complexity S+S = M total,
  higher review overhead); Approach C is L (state.yaml schema extension).
  Lowest complexity wins → A; tie-break via "higher reuse" also favors A
  (no new state.yaml field, no new script).
- **OQ-1 resolved**: `verify_commands` uses dict form
  `{test: pytest ...}`. `scripts/inline/capture-test-baseline.sh` reads
  `vc.get('test')` first (confirmed by reading the script), matching the
  tested code path.
- **OQ-2 resolved**: ISSUE-8 → option (c) (loud hint + skill doc).
  Reason: zero schema change, sufficient for solo driver use; option
  (b) is a follow-up if full automation is wanted later.
- **OQ-3 resolved**: backlog cleanup is a separate commit inside
  `archive-completed-change.sh` (matches script's existing multi-commit
  pattern; amending archive commit is forbidden by CLAUDE.md).
- **OQ-4 resolved**: `compute-swe-metrics` `run:` path (line 3,
  `scripts/inline/compute-swe-metrics.sh`) is canonical; the instruction
  prose at step 2a is updated to match.
- **Migration strategy**: `_migrate_step_events` short-circuits on the
  fast path (no renames needed) to avoid a pointless DROP/CREATE INDEX
  roundtrip on every `ensure_schema` call.

---

## Open Questions

- OQ-1: **ISSUE-5 — verify_commands key name**: Does the verify phase step read `verify_commands.test` or a flat list? Confirm the exact field name/schema before populating `spec/project.yaml`.
- OQ-2: **ISSUE-8 option choice**: Is option (c) — loud hint + doc — acceptable, or does the team want option (b) — `phases_ordered` in state.yaml + dispatcher automation? If (b), workflow-init and dispatch.py both need changes.
- OQ-3: **ISSUE-9 git commit strategy**: Should the backlog cleanup be a separate commit from the archive commit, or part of the same commit? The current script makes two separate commits; adding a third is noisy. Consider amending or combining.

---

## Technical Context

### Key files by issue

| Issue | File | Lines |
|-------|------|-------|
| ISSUE-1 | `agents/workflow-init.md` | 50–59 (section 2, workflow_plan shape) |
| ISSUE-1 (canonical) | `config/scripts/orchestrator_next/dispatch.py` | 71 (`active` key read) |
| ISSUE-2/10.2 | `config/scripts/orchestrator_next/upsert.py` | 179–187 (`_migrate_step_events`) + 197–199 (ensure_schema index order) |
| ISSUE-3 | CLOSED — stale | — |
| ISSUE-4 | `config/steps/preview-route.yaml` | 45 (outputs field) |
| ISSUE-5 | `spec/project.yaml` | 134 (`verify_commands: []`) |
| ISSUE-6 | `skills/orchestrate/SKILL.md` | 116–120 (run_step spawn block) |
| ISSUE-7 | `agents/developer.md` | "What You Don't Do" section (new constraint) |
| ISSUE-8 | `config/scripts/orchestrator_next/dispatch.py` | 288–289 (complete_workflow premature return) |
| ISSUE-8 (driver) | `skills/orchestrate/SKILL.md` | 149–164 (section 5, phase transitions) |
| ISSUE-9 | `scripts/inline/archive-completed-change.sh` | after line 38 (new backlog cleanup block) |
| ISSUE-9 | `config/steps/archive-completed-change.yaml` | instruction step 5 |
| ISSUE-10.1 | `skills/orchestrate/SKILL.md` | 122–136 (USAGE CAPTURE comment → mandatory step) |
| ISSUE-10.3 | `config/steps/compute-swe-metrics.yaml` | 25 (path: `scripts/` → `scripts/inline/`) |

### Library versions / integration points

- DuckDB: production `metrics.duckdb` has otel-prefixed column names — migration is needed
- Python `orchestrator_next` package: `upsert.py`, `dispatch.py`, `record.py` are the core modules
- `orchestrator record` CLI is confirmed viable for ISSUE-7 fix
- `spec/changes/backlog/` has 5 confirmed stale entries to remove

### Dependencies between issues

- **ISSUE-10.1 + ISSUE-10.2 must land together**: capturing usage is meaningless if the DB write is broken (and vice versa).
- **ISSUE-1 unblocks everything else**: if state.yaml `active_steps:` key persists, `orchestrator next` returns `complete_workflow` immediately and no other step runs.
- **ISSUE-7 depends on ISSUE-1 being fixed**: if dispatch never gets past init, developer agent is never spawned.
