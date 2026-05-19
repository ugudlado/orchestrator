---
feature-id: orc-59
linear-ticket: N/A
---

# Design: Rename `linear_ticket_id` → `ticket_id` in the state contract

## Context

The orchestrator state contract exposes a Linear-specific field name
(`linear_ticket_id`) in a layer that is explicitly ticketing-backend-agnostic.
Backlog.md repos carry it as `null` and fall back to slug-matching — correct at
runtime, but the name leaks the Linear brand into a neutral contract. This is a
pure mechanical rename with one verified producer (`workflow-init.sh:107`), one
verified code consumer (`mark-change-completed.sh:29`), the schema doc
(`CONVENTIONS.md:354`), three skill docs, and three test fixtures. The exact
RENAME set (13 occurrences, 8 files) and FROZEN set (append-only telemetry in
active state.yaml files) are catalogued in `diagnose.md` and confirmed by grep
at HEAD.

## Goals / Non-Goals

### Goals

- Rename `linear_ticket_id` → `ticket_id` across the producer
  (`workflow-init.sh`), the code consumer (`mark-change-completed.sh`), the
  schema doc (`CONVENTIONS.md`), three skill docs
  (`developer`/`reviewer`/`linear`), and three test fixtures
  (`test_record_validation.py`) — 13 occurrences across 8 files.
- Preserve the `change_id or ticket_id or "unknown"` archive-path fallback chain
  in `mark-change-completed.sh` byte-for-byte except the key name.
- Keep the `orchestrator_next` pytest suite green.

### Non-Goals

- **Historical `step_history` entries in active state files
  (orc-30/58/59 state.yaml) are NOT renamed.** They are append-only telemetry
  recording what `workflow-init.sh` literally wrote at execution time. Renaming
  the producer makes all *future* entries use `ticket_id`; past entries stay
  frozen — same immutability rule applied to archived files. Driver-resolved,
  binding (closes discovery OQ-1).
- **`spec/changes/archive/` is untouched** — frozen history; rewriting would
  falsify telemetry replayed by metrics consumers.
- **CONVENTIONS.md "Written By" column (`create-linear-ticket`) is NOT
  corrected.** Pre-existing doc bug (no such step contract; the linear skill
  writes the field directly), unrelated to this rename. Separate follow-up
  (closes discovery OQ-2).
- No new abstraction, indirection, or backend-detection logic — the new name is
  a plain string key, same as the old one.

## Approaches Considered

### Approach 1: Direct in-place rename of the RENAME set (XS)

Replace `linear_ticket_id` with `ticket_id` at the 13 catalogued occurrences;
leave FROZEN telemetry and archives untouched. No compatibility shim — there is
exactly one producer and one code consumer, both renamed atomically in the same
change.

Pros: Smallest possible change; no residual debt; verifiable by a single grep.
Cons: None — the producer/consumer pair is renamed together so no version skew.

### Approach 2: Dual-key compatibility (read both old and new) (S)

Have `mark-change-completed.sh` read `d.get("change_id") or d.get("ticket_id")
or d.get("linear_ticket_id") or "unknown"` to tolerate state files written by an
old producer.

Pros: Tolerates a state.yaml written before the rename.
Cons: Unnecessary — the only at-risk state files are FROZEN active telemetry
(`linear_ticket_id: null`), and `change_id` is present in every active state
file, so the fallback never reaches the ticket key in practice. Adds permanent
dead code to de-leak a name. Rejected: violates simplicity-first; the leak it
introduces (a Linear name living on in consumer code) is exactly what this
ticket removes.

### Selected Approach

**Approach 1.** The producer↔consumer relationship is fully enumerated and
verified by grep at HEAD: a single producer line writes the key, a single code
consumer line reads it, both renamed in one atomic change. No version-skew
window exists that a compatibility shim would protect against, so the shim would
be pure dead code. Lowest complexity wins per the auto-selection heuristic.

## High-Level Design

### Architecture Overview

`workflow-init.sh` emits a JSON `outputs` dict to stdout → the orchestrate
dispatch loop records it verbatim into `step_history[].evidence.outputs` →
`mark-change-completed.sh` reads `change_id`/`ticket_id` from the latest state
to compute the archive directory name. The rename touches only the string key
`linear_ticket_id` → `ticket_id` at both ends plus the doc/fixture references;
no control flow, data flow, or structure changes.

### Key Abstractions

None introduced. `ticket_id` is the same plain, optional string key the field
already was; only the name changes.

## Low-Level Design

### Components

| Component | File | Change |
|---|---|---|
| Producer | `config/scripts/inline/workflow-init.sh` | `linear_ticket_id` → `ticket_id` on the JSON key (~L107) and the two doc-comment lines (~L8, ~L10) |
| Code consumer | `config/scripts/inline/mark-change-completed.sh` | `d.get("linear_ticket_id")` → `d.get("ticket_id")` (~L29); fallback chain otherwise unchanged |
| Schema doc | `config/steps/CONVENTIONS.md` | Field name in State Field Registry row (~L354); "Written By" column left as-is (out of scope) |
| Doc consumer | `skills/developer/SKILL.md` | Field reference in ticket-resolution instruction (~L44–50) |
| Doc consumer | `skills/reviewer/SKILL.md` | Field reference in ticket-resolution instruction (~L44–50) |
| Skill (writer) | `skills/linear/SKILL.md` | Frontmatter `description:` (~L3), write instruction (~L73), state-fields table row (~L111) |
| Test fixtures | `config/scripts/orchestrator_next/tests/test_record_validation.py` | Three `outputs` dict keys (~L68, L93, L120) |

Line numbers are approximate (the worktree has uncommitted skill edits shifting
some lines). Edit by content match, not line number.

### Data Flow

Unchanged. The key's value (`null` for Backlog repos, `HL-XXX`/`ORC-XX` when the
linear skill writes it) and every read/write site are preserved; only the key
string changes.

### State Management

`record.py` has **zero** occurrences of `linear_ticket_id` (verified by grep at
HEAD). It validates `workflow_plan` shape and operates on `change_id`; it
neither allowlists nor schema-checks this key. The rename therefore needs **no**
`record.py` change and cannot break record validation — explicitly stated so the
developer does not chase a non-issue.

The three `test_record_validation.py` fixtures carry `linear_ticket_id` only as
a plain dict key inside the `outputs` block that mirrors workflow-init's JSON
shape. Because record.py does not validate this key, the suite passes with
*either* name; the fixture rename is for fidelity with the renamed producer and
serves as a regression guard, not a behavioral assertion.

### Error Handling

The archive-path fallback `change_id or ticket_id or "unknown"` is preserved
exactly with the new key name. In every active state file `change_id` is
present, so the chain resolves at `change_id` and never depends on the ticket
key — the rename cannot change archive-path resolution for any current or
backlog-backed run (AC-4). FROZEN telemetry lines reading `linear_ticket_id:
null` are inside historical `step_history` entries that no consumer reads for
path resolution, so leaving them is safe.

## Constraints

- Direct `state.yaml` edits are forbidden (CLAUDE.md). The active state.yaml
  FROZEN lines are therefore both out of scope *and* must not be touched by any
  means.
- Edits identified by content, not line number (uncommitted worktree edits have
  shifted skill-doc lines).

## Trade-offs

Pure mechanical rename: there is no meaningful TDD RED step. record.py validates
`workflow_plan` shape, not this key, so no test can be made to fail on the key
name. The fixture update is therefore a fidelity-with-producer change plus a
regression guard, sequenced before the rename per repo TDD convention; its
verify is "suite stays green," not "a test goes red." Calling it a RED step
would be theatre — the simplicity principle backs the honest framing.

## Acceptance Criteria

- AC-1: Given the RENAME set, when the rename is applied, then
  `grep -rn "linear_ticket_id" config/ skills/ --include='*.py' --include='*.sh'
  --include='*.md' --include='*.yaml'` returns **zero** lines (it returns
  exactly 13 at HEAD). [traces: UC-1, UC-2, UC-3]
- AC-2: Given the renamed test fixtures, when `python -m pytest
  tests/test_record_validation.py -q` is run from
  `config/scripts/orchestrator_next/`, then it exits 0 with all tests passing.
  [traces: UC-E2]
- AC-3: Given the FROZEN set, when the change is complete, then
  `spec/changes/orc-58/state.yaml:50`, `spec/changes/orc-30/state.yaml:50`,
  `spec/changes/orc-59/state.yaml:55,294`, and everything under
  `spec/changes/archive/` still contain `linear_ticket_id` unchanged
  (`git diff --stat` shows no state.yaml or archive file modified). [traces: UC-1]
- AC-4: Given a backlog-backed run whose latest state has `change_id` set and
  the ticket key `null`, when `mark-change-completed.sh` computes the archive
  dir, then it resolves via `change_id` exactly as before — the renamed line
  reads `cid = d.get("change_id") or d.get("ticket_id") or "unknown"`. [traces: UC-1]
- AC-5: Given the producer `workflow-init.sh`, when it emits its JSON `outputs`,
  then the key is `ticket_id` (not `linear_ticket_id`) and the consumer
  `mark-change-completed.sh` reads the same key — producer/consumer key names
  match. [traces: UC-1, UC-2]

## Decisions

- One producer, one code consumer, both renamed atomically → no version-skew
  window → no compatibility shim → zero residual Linear-name debt.
- `record.py` untouched (zero occurrences; validates `workflow_plan` not this
  key) → developer must not add record.py changes or chase validation failures.
- Verify scope is `config/ skills/` (exactly the 8 RENAME files' directories);
  `spec/changes/` excluded because it holds FROZEN telemetry, archives, and this
  feature's own diagnose/discovery docs that legitimately name the from-field.

## Open Questions

- None. Discovery OQ-1 and OQ-2 closed by binding driver decisions (see
  Non-Goals).
</content>
