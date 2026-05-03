---
id: ORC-31
title: Live retro.md capture + backlog dedup/recurrence sync
status: To Do
assignee: []
created_date: '2026-05-03 10:56'
updated_date: '2026-05-03 11:00'
labels:
  - slug-retro-capture-and-backlog-sync
  - bug
  - score-7.8
  - recurrence-1
dependencies: []
priority: medium
ordinal: 30000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: retro-capture-and-backlog-sync -->

**Original score:** 7.8 | **Recurrence:** 1

## Idea

Three tightly-coupled workflow changes so issues observed during a feature run are captured once, presented to the user, and synced to the backlog without duplication:

1. **Capture at surface time.** Extend dispatcher + step contracts to emit a
   `workflow_issues` payload to `orchestrator record` when something unexpected
   happens (a script exits non-zero on a "never fail" step, a driver has to
   manually advance phase, a sandbox block triggers, an inline usage block is
   empty, etc.). record.py already accepts this payload (memory S4411); the
   gap is in the emit side — drivers/scripts/agents don't produce it
   consistently. Each issue gets: `{id, category, severity, surfaced_at,
   detail, workaround, fix_direction, dedup_key}`. Appended live to
   `$WORKFLOW_STATE_DIR/$CHANGE_ID/retro.md`.

2. **Present retro.md in the final report.** At the end of the complete
   phase (or at autopilot iteration end), render retro.md as a Markdown
   table to the user alongside the cost report. For autopilot under
   `--auto`, render-and-proceed (not render-and-pause). This is the
   user-facing visibility surface — no more "what broke during this run?"
   guessing.

3. **Sync retro → backlog with dedup + recurrence counter.**
   - Each backlog entry grows a `**Recurrence:** N` line and a `sources:`
     list (feature_id / ISSUE-N that contributed). The Summary table
     gains a `Rec.` column.
   - When archiving a feature, a `sync-retro-to-backlog` step (new) walks
     the retro's issues. For each issue:
     - Compute `dedup_key` = slug if retro specifies a `backlog_entry:`
       field, else a hash of `(category, fix_direction)` normalized.
     - If `dedup_key` matches an existing entry's slug OR matches any
       existing entry's `sources:` hash: bump that entry's recurrence
       counter and append the new source. Do NOT add a duplicate H2.
     - If no match: create a new H2 entry with `Recurrence: 1`.
   - Ideator prioritization uses `effective_score = base_score + 0.5 *
     (recurrence - 1)`, so an issue hit 3× floats above a higher-scored
     one-time annoyance.

## Why Now

The user requested this live during autopilot-2026-04-20-001: 6
issues surfaced in retro.md post-hoc, and 3 of them had existing-entry
overlap that a human had to reason about. This won't scale across
autonomous runs — the backlog will fill with near-duplicates, and
ideator prioritization will miss recurring pain. Also, retro.md today
is **only backfilled after the fact** (see the backfill note on
`2026-04-19-live-telemetry-and-repeat-until-enforcement/retro.md`),
meaning issues are lost if no human runs the backfill.

## Prototype

```yaml
# new step: sync-retro-to-backlog (runs in complete phase, after archive-completed-change)
inputs: [retro_md_path, backlog_md_path]
outputs:
  - new_entries: [slug, ...]
  - bumped_entries: [{slug, new_recurrence}]
  - skipped_entries: [{issue_id, reason}]
```

## Source

spec/changes/archive/2026-04-19-fix-inline-scripts-tmpdir/retro.md (user request 2026-04-20 after autopilot-2026-04-20-001)

---

## Open questions for spec

- Dedup key definition — slug-first, fallback to category+fix_direction
  hash, or something else? (Bias toward slug since retros already emit
  `backlog_entry:` slugs.)
- When to increment the counter: at retro-write time (live) or at
  sync-retro-to-backlog time (archive)? Archive-time is safer
  (idempotent, can re-run).
- Should closed/shipped items decay out of ideator's score or stay
  forever? If shipped, a future recurrence is a regression and should
  be counted loudly.
- Schema migration for existing backlog.md: today's consolidation
  marked every entry as `Recurrence: 1`, with 3 manually bumped this
  round. A one-shot migration script could scan all existing retro.md
  files for already-backfilled recurrences.
<!-- SECTION:DESCRIPTION:END -->
