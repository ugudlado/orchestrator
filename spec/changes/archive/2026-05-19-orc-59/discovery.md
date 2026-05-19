---
feature-id: orc-59
linear-ticket: N/A
---

# Discovery Brief: Rename `linear_ticket_id` → `ticket_id` in state.yaml

## Feature Summary

The orchestrator's state schema currently exposes a Linear-specific field name (`linear_ticket_id`) in the policy layer — a layer that is explicitly ticketing-agnostic. Repos using Backlog.md carry this field as `null` and fall back to slug-matching, which works but leaks the Linear brand into the neutral state contract. This change renames `linear_ticket_id` to `ticket_id` across all non-archived files: the schema doc (CONVENTIONS.md), the two inline scripts that emit or read it (workflow-init.sh, mark-change-completed.sh), the three skills that reference it (developer, reviewer, linear), and the three test fixtures in test_record_validation.py. Active state.yaml files (orc-30, orc-58, orc-59) also carry the field and must be updated. Archived state files under `spec/changes/archive/` are frozen history and must not be touched.

## Personas & Actors

- Workflow engine (orchestrate skill dispatch loop) — reads workflow-init outputs and writes step_history, including the `ticket_id` field in `evidence.outputs`
- Linear skill — writes `ticket_id` to state.yaml after creating a Linear issue
- Developer skill / Reviewer skill — reads `ticket_id` from state.yaml to resolve the change directory when a ticket ID is provided
- mark-change-completed.sh — reads `ticket_id` as a fallback for archive path naming when `change_id` is absent

## Use Cases

### Happy Path

UC-1: Backlog-backed workflow runs end-to-end — orchestrate skill initializes a workflow for a Backlog.md repo, workflow-init.sh emits `ticket_id: null` in its JSON output, the dispatch loop records it in step_history evidence, and mark-change-completed.sh uses `change_id` for the archive path (slug-fallback behaves identically to before the rename).

UC-2: Linear-backed workflow creates a ticket — orchestrate skill runs on a repo with a `linear:` block in project.yaml, the linear skill creates an issue and writes `ticket_id: HL-XXX` to state.yaml, and developer/reviewer skills resolve the change directory by matching `ticket_id` against the provided TICKET_ID.

UC-3: Reviewer skill resolves change dir from ticket — reviewer skill is invoked with a ticket slug, scans active state.yaml files, matches `ticket_id` (which may be null) against the slug or falls back to `change_id` matching, and proceeds to the correct artifact directory.

### Error & Edge Cases

UC-E1: Active state.yaml still has old field name — a partially migrated environment has state.yaml files with `linear_ticket_id`. mark-change-completed.sh would fail to read `ticket_id` and `change_id` is also absent, producing archive path `...unknown/`. This is the risk if active state files are not updated alongside the code.

UC-E2: Test fixture uses old field name — test_record_validation.py fixtures still carry `linear_ticket_id` after code migration; record.py's output validation may pass or fail depending on whether it reads the field name from the contract, making test results misleading.

## Scope

### In Scope

- Rename `linear_ticket_id` → `ticket_id` in CONVENTIONS.md State Field Registry (line 354)
- Rename in workflow-init.sh: comment on line 8, output doc on line 10, JSON key on line 107
- Rename in mark-change-completed.sh: fallback read on line 29
- Rename in skills/developer/SKILL.md: field reference on line 50
- Rename in skills/reviewer/SKILL.md: field reference on line 45
- Rename in skills/linear/SKILL.md: frontmatter description (line 3), step instruction (line 73), state.yaml fields table (line 111)
- Rename in test_record_validation.py: three fixture dicts (lines 68, 93, 120)
- Update active state.yaml files (orc-30, orc-58, orc-59) — these are live workflow state, not frozen history

### Out of Scope

- `spec/changes/archive/` — frozen history; renaming would corrupt metrics consumers that replay archived state
- record.py — does not reference `linear_ticket_id` at all; no change needed
- workflow-init.yaml step contract — contains no field reference; no change needed
- Any backlog/ or config/templates/ files — no occurrences found in grep
- CLAUDE.md — no occurrence found; ticket's mention of it was a false positive

## UI Direction

N/A — no UI components.

## Key Decisions

- **Direction: direct in-place rename of the 13-occurrence RENAME set (XS).**
  Selected over a dual-key compatibility shim. Grep at HEAD confirms exactly one
  producer (`workflow-init.sh:107`) and one code consumer
  (`mark-change-completed.sh:29`); both are renamed atomically in one change, so
  no version-skew window exists for a shim to protect. A shim would be permanent
  dead code that re-introduces the very Linear name this ticket removes — lowest
  complexity wins.
- **record.py: no change.** Zero occurrences at HEAD; it validates
  `workflow_plan` shape and operates on `change_id`, never this key. It neither
  allowlists nor schema-checks the field — the rename cannot break record
  validation. Stated explicitly so the developer does not chase a non-issue.
- **Consumer compat / AC-4 confirmed.** The renamed line is
  `cid = d.get("change_id") or d.get("ticket_id") or "unknown"`; every active
  state file has `change_id` set, so resolution never depends on the ticket key
  — archive-path semantics are byte-for-byte preserved.
- **Verify scope = `config/ skills/`.** Returns exactly the 13 RENAME hits at
  HEAD, must return zero after; `spec/changes/` is excluded because it holds
  FROZEN telemetry, archives, and this feature's own diagnose/discovery docs.
- OQ-1 and OQ-2 closed by binding driver decisions (recorded as Non-Goals in
  design.md): FROZEN step_history not renamed; CONVENTIONS.md "Written By"
  column inaccuracy is a separate follow-up.

## Open Questions

- OQ-1: Active state.yaml files (orc-30, orc-58, orc-59) have `linear_ticket_id: null` embedded in step_history evidence (frozen past records within live files). Should those historical records inside step_history be left as-is (since they are append-only records), or renamed too? The top-level field is the one that matters for consumers; inner step_history entries are telemetry.
- OQ-2: The CONVENTIONS.md "Written By" column for `linear_ticket_id` says `create-linear-ticket` — but that step does not appear to exist as a yaml contract; the linear skill writes the field directly. Should the "Written By" column be updated to `linear skill` on rename, or left as structural documentation?
