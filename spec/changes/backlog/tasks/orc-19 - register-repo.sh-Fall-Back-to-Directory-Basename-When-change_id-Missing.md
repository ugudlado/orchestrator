---
id: ORC-19
title: 'register-repo.sh: Fall Back to Directory Basename When change_id Missing'
status: In Progress
assignee:
  - spidey
created_date: '2026-05-03 10:55'
updated_date: '2026-05-09 22:46'
labels:
  - slug-register-repo-changeid-fallback
  - feature
  - score-6.0
  - recurrence-1
dependencies: []
priority: low
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: register-repo-changeid-fallback -->

**Original score:** 6.0 | **Recurrence:** 1

## Idea

`register-repo.sh` currently skips `state.yaml` files that lack a `change_id`
field, logging `skip:` and continuing. This is correct per FR-6 but loses
ingestable data: 3 of 10 archives in the orchestrator repo
(`hl-253-extract-dev-workflow-system-into-standalone-repo`,
`quality-gates-phase2`, `reliability-phase1`) are fully populated state.yaml
files from before the `change_id` field was added.

These archives have `slug` and other useful fields but skip silently.

## Why Now

Surfaced during implement-phase review of `cross-repo-metrics-duckdb`
(reviewer Important Finding I1). The fix is ~3 lines and recovers 30% of
this repo's archive history. Should ship before any consumer (e.g.,
`/learn` querying DuckDB) runs analytics that would notice the gap.

## Scope

When `change_id` is empty after `yq` extraction, fall back to:

```bash
change_id=$(basename "$(dirname "$state_file")")
```

Then proceed through the existing slug guard
(`^[a-z0-9._-]+$`) and ingest. Existing slug-validation defense remains intact.
Log: `warn: change_id absent, using dirname fallback: <slug>`.

## Out of scope

- Backfilling `change_id` into the legacy state.yaml files themselves
- Changing the schema (PK still `(repo_root, change_id)` — fallback just
  populates the value at ingest time)

## Priority

- User value: 5/10
- Strategic fit: 6/10
- Technical leverage: 8/10 (3-line fix, 30% data recovery)
- Effort: extra-small (--light feature)
- **Score: 6.0**

---
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All three legacy state.yaml files have explicit change_id field
- [ ] #2 Dirname fallback branch added to register-repo.sh lines 163-166
- [ ] #3 Warn message emitted when fallback is used
- [ ] #4 Test case covers missing change_id path with safe dirname
<!-- AC:END -->
