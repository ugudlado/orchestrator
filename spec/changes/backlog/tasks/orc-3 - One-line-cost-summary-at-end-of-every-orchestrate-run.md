---
id: ORC-3
title: One-line cost summary at end of every /orchestrate run
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-cost-tail-on-orchestrate-complete
  - feature
  - score-6.5
  - recurrence-1
dependencies:
  - ORC-1
  - ORC-2
references:
  - 'Ideation session 2026-05-03 with the user — idea #5 from a 5-idea ranking.'
  - >-
    Reframed 2026-05-03 after autopilot collapse (commit fa6112d) removed
    `autopilot-iterate.yaml`; mechanism moved to the orchestrate complete-phase.
priority: low
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: cost-tail-on-orchestrate-complete -->

**Original score:** 6.5 | **Recurrence:** 1

## Idea

After any `/orchestrate` run completes, emit one line to the transcript summarizing the just-shipped feature:

```
[fix-archive-backlog-cleanup-tests] $0.74 / 12m / 84k tokens / 2.1× median
```

The orchestrate skill already runs `scripts/cost-report.sh --change-id $CHANGE_ID` at `complete_workflow` time and includes its stdout in the final message. This entry asks for an additional `--tail` mode of that script (or a new named query) that emits a single compact line, so terminal-tailers and `/autopilot` consumers see at-a-glance cost without grepping the multi-line report.

## Why Now

- Builds on `cost-summary-on-archive` (uses the same query) and `cost-delta-baseline` (the median ratio).
- Tiny — one query mode + one log line in `orchestrate` complete-phase prose.
- Especially valuable when `/autopilot` chains via shell loops or remote-agent runners: the tail is the only signal the wrapper sees.

## Scope

1. New `--tail` mode (or new named query `cost-tail <change_id>`) in `metrics-query.sh` returning a single formatted line.
2. Edit `skills/orchestrate/SKILL.md` complete-phase block: after the cost-report stdout, also emit the tail line on its own.
3. Test: archive a small completed feature and assert the tail line is present + matches the expected shape.

## Out of scope

- Cost dashboards / time-series rendering.
- Mid-feature cost emits.
- Stopping work on cost (covered by `metrics-regression-detection`).

## Dependencies

- Hard: `cost-summary-on-archive` (uses its query).
- Soft: `cost-delta-baseline` (the "Nx median" segment — degrades gracefully if delta isn't computed yet).

## Priority

- User value: 7/10 (one-line at-a-glance cost after every shipped feature)
- Strategic fit: 7/10
- Technical leverage: 8/10 (~10 lines: one query mode + one log line)
- Effort: extra-small
- **Score: 6.5**

## Source

- Ideation session 2026-05-03 with the user — idea #5 from a 5-idea ranking.
- Reframed 2026-05-03 after autopilot collapse (commit fa6112d) removed `autopilot-iterate.yaml`; mechanism moved to the orchestrate complete-phase.

---
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 New `--tail` mode (or new named query `cost-tail <change_id>`) in `metrics-query.sh` returning a single formatted line.
- [ ] #2 Edit `skills/orchestrate/SKILL.md` complete-phase block: after the cost-report stdout, also emit the tail line on its own.
- [ ] #3 Test: archive a small completed feature and assert the tail line is present + matches the expected shape.
<!-- AC:END -->
