---
feature-id: orc-59
type: refactor
---

# Diagnosis: Rename `linear_ticket_id` → `ticket_id`

## Symptom

The orchestrator's state contract exposes a Linear-specific field name (`linear_ticket_id`) in the backend-agnostic policy layer. Files that are explicitly ticketing-neutral — `mark-change-completed.sh`, `skills/developer/SKILL.md`, `skills/reviewer/SKILL.md` — read this field by a name that leaks the Linear backend. Repos using Backlog.md carry the field as `null` and work correctly, but the name itself couples the neutral policy layer to one specific ticketing provider. This is an abstraction leak in the state contract, not a runtime failure.

## Reproduction (Catalog Command)

```
grep -rn "linear_ticket_id" /Users/spidey/code/orchestrator \
  --include='*.md' --include='*.sh' --include='*.py' --include='*.yaml' \
  | grep -v spec/changes/archive
```

### Actual output (17 hits)

```
/Users/spidey/code/orchestrator/config/steps/CONVENTIONS.md:354:| `linear_ticket_id` | string | create-linear-ticket | Linear issue ID (e.g., `HL-123`). Also stored in `.spec.yaml`. |
/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/tests/test_record_validation.py:68:                "linear_ticket_id": None,
/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/tests/test_record_validation.py:93:                "linear_ticket_id": None,
/Users/spidey/code/orchestrator/config/scripts/orchestrator_next/tests/test_record_validation.py:120:                "linear_ticket_id": None,
/Users/spidey/code/orchestrator/config/scripts/inline/mark-change-completed.sh:29:cid = d.get("change_id") or d.get("linear_ticket_id") or "unknown"
/Users/spidey/code/orchestrator/config/scripts/inline/workflow-init.sh:8:#              FLAGS_LINEAR       — "true" | "false" (skipped; linear_ticket_id always null)
/Users/spidey/code/orchestrator/config/scripts/inline/workflow-init.sh:10:#   {worktree_path, branch, linear_ticket_id, workflow_plan, resolved_flags, plan_yaml_path}
/Users/spidey/code/orchestrator/config/scripts/inline/workflow-init.sh:107:    "linear_ticket_id": None,
/Users/spidey/code/orchestrator/spec/changes/orc-58/state.yaml:50:      linear_ticket_id: null
/Users/spidey/code/orchestrator/spec/changes/orc-30/state.yaml:50:      linear_ticket_id: null
/Users/spidey/code/orchestrator/skills/developer/SKILL.md:50:matches `TICKET_ID` (case-insensitive). `linear_ticket_id` may be `null` —
/Users/spidey/code/orchestrator/spec/changes/orc-59/state.yaml:55:      linear_ticket_id: null
/Users/spidey/code/orchestrator/spec/changes/orc-59/state.yaml:294:    discrepancies: 3 active state.yaml files (orc-30/58/59) carry linear_ticket_id
/Users/spidey/code/orchestrator/skills/linear/SKILL.md:3:description: This skill should be used when the user asks to "create a Linear issue", "file a ticket", "open a Linear ticket", "update a Linear issue", "check a Linear ticket", "add a label to a ticket", "assign an issue", "close a ticket", or any time a workflow step needs to create/read a Linear ticket. Also use when reading or writing linear_ticket_id in state.yaml.
/Users/spidey/code/orchestrator/skills/linear/SKILL.md:73:3. Update `$WORKFLOW_STATE_DIR/<feature>/state.yaml` field `linear_ticket_id: HL-XXX`.
/Users/spidey/code/orchestrator/skills/linear/SKILL.md:111:| `linear_ticket_id` | Primary issue ID (e.g. `HL-134`) |
/Users/spidey/code/orchestrator/skills/reviewer/SKILL.md:45:matches `TICKET_ID` (case-insensitive). `linear_ticket_id` may be `null` —
```

### Hit classification

| # | File | Line | Classification | Reason |
|---|------|------|----------------|--------|
| 1 | `config/steps/CONVENTIONS.md` | 354 | **RENAME** (schema doc) | State Field Registry — authoritative contract definition |
| 2 | `config/scripts/orchestrator_next/tests/test_record_validation.py` | 68 | **RENAME** (test fixture) | Fixture dict key in a test; must match post-rename field name |
| 3 | `config/scripts/orchestrator_next/tests/test_record_validation.py` | 93 | **RENAME** (test fixture) | Same as above |
| 4 | `config/scripts/orchestrator_next/tests/test_record_validation.py` | 120 | **RENAME** (test fixture) | Same as above |
| 5 | `config/scripts/inline/mark-change-completed.sh` | 29 | **RENAME** (consumer) | Reads field from state.yaml; must match producer output |
| 6 | `config/scripts/inline/workflow-init.sh` | 8 | **RENAME** (doc comment) | Env-var description comment in producer script |
| 7 | `config/scripts/inline/workflow-init.sh` | 10 | **RENAME** (doc comment) | Outputs doc comment in producer script |
| 8 | `config/scripts/inline/workflow-init.sh` | 107 | **RENAME** (producer) | JSON key emitted to stdout; this is the canonical producer line |
| 9 | `spec/changes/orc-58/state.yaml` | 50 | **FROZEN** (step_history telemetry) | Inside `step_history[].evidence.outputs`; append-only historical record |
| 10 | `spec/changes/orc-30/state.yaml` | 50 | **FROZEN** (step_history telemetry) | Inside `step_history[].evidence.outputs`; append-only historical record |
| 11 | `skills/developer/SKILL.md` | 50 | **RENAME** (consumer doc) | Skill instruction references field by name |
| 12 | `spec/changes/orc-59/state.yaml` | 55 | **FROZEN** (step_history telemetry) | Inside `step_history[].evidence.outputs`; append-only historical record |
| 13 | `spec/changes/orc-59/state.yaml` | 294 | **FROZEN** (step_history note) | Discovery step evidence note; append-only historical record |
| 14 | `skills/linear/SKILL.md` | 3 | **RENAME** (skill doc — description) | Frontmatter `description:` field references `linear_ticket_id` by name |
| 15 | `skills/linear/SKILL.md` | 73 | **RENAME** (skill doc — instruction) | Step instruction directs agent to write `linear_ticket_id` to state.yaml |
| 16 | `skills/linear/SKILL.md` | 111 | **RENAME** (skill doc — table) | State fields table in linear skill |
| 17 | `skills/reviewer/SKILL.md` | 45 | **RENAME** (consumer doc) | Skill instruction references field by name |

**Total hits: 17. RENAME: 13. FROZEN: 4.**

## Root Cause

The field was named `linear_ticket_id` when the Linear integration was the only ticketing backend. It was embedded directly in the state schema contract (CONVENTIONS.md) and the two inline scripts that produce and consume it. Because the policy layer (`mark-change-completed.sh`, developer/reviewer skills) is ticketing-agnostic, the field name leaks a backend-specific brand into a neutral contract.

**Exact producer line:**
`config/scripts/inline/workflow-init.sh:107` — `"linear_ticket_id": None,` — this is the single point where the field is written into workflow-init's JSON output, which the orchestrate dispatch loop records in `step_history[].evidence.outputs`.

**Exact consumer line:**
`config/scripts/inline/mark-change-completed.sh:29` — `cid = d.get("change_id") or d.get("linear_ticket_id") or "unknown"` — reads the field from state.yaml as a fallback for archive path naming.

(Developer and reviewer skills read it by name in their instructions; those are doc consumers, not code consumers.)

## Impact

### RENAME set (13 occurrences across 8 files)

| File | Lines | Change |
|------|-------|--------|
| `config/steps/CONVENTIONS.md` | 354 | Field name in State Field Registry table |
| `config/scripts/inline/workflow-init.sh` | 8, 10, 107 | Comment lines + JSON key |
| `config/scripts/inline/mark-change-completed.sh` | 29 | `d.get("linear_ticket_id")` → `d.get("ticket_id")` |
| `config/scripts/orchestrator_next/tests/test_record_validation.py` | 68, 93, 120 | Fixture dict keys |
| `skills/developer/SKILL.md` | 50 | Field reference in instruction |
| `skills/reviewer/SKILL.md` | 45 | Field reference in instruction |
| `skills/linear/SKILL.md` | 3, 73, 111 | Frontmatter description + instruction + state fields table |

### FROZEN set (4 occurrences across 3 files — DO NOT CHANGE)

| File | Lines | Reason |
|------|-------|--------|
| `spec/changes/orc-58/state.yaml` | 50 | `step_history[].evidence.outputs` — append-only telemetry written by workflow-init at workflow start |
| `spec/changes/orc-30/state.yaml` | 50 | Same — append-only telemetry |
| `spec/changes/orc-59/state.yaml` | 55, 294 | Line 55: append-only telemetry; Line 294: discovery step evidence note |

These entries record what `workflow-init.sh` literally wrote at the time of execution. They are historical records. Rewriting them would falsify the telemetry and risk state corruption (direct state.yaml edits are forbidden per CLAUDE.md).

### Scope decisions (driver-resolved, binding)

1. **Historical step_history entries in active state files (orc-30/58/59) will NOT be renamed.** The fix renames the producer so all future entries use `ticket_id`; past entries stay frozen — same immutability principle applied to archived files.

2. **The CONVENTIONS.md "Written By" column inaccuracy (`create-linear-ticket` → should be `linear skill`) is OUT OF SCOPE.** Pre-existing doc bug unrelated to this rename. Note as follow-up only.

## Proposed Approach

Rename the contract field name from `linear_ticket_id` to `ticket_id` across the producer (`workflow-init.sh`), the consumer (`mark-change-completed.sh`), the schema doc (`CONVENTIONS.md`), three skill docs (`developer`, `reviewer`, `linear`), and three test fixtures (`test_record_validation.py`); leave all append-only `step_history` entries in active state files frozen.

## Unresolved Questions

None — scope resolved by driver (see Scope Decisions above). OQ-1 and OQ-2 from the discovery brief are both closed.

---

**Follow-up (out of scope for ORC-59):** `CONVENTIONS.md:354` "Written By" column currently reads `create-linear-ticket`, which is not an existing step contract; the `linear` skill writes this field directly. Correct in a separate, targeted doc fix.
