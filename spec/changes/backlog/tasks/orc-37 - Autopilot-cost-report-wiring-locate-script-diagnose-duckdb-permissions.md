---
id: ORC-37
title: 'Autopilot cost-report wiring: locate script + diagnose duckdb permissions'
status: Done
assignee: []
created_date: '2026-05-03 20:29'
updated_date: '2026-05-08 15:30'
labels:
  - bug
  - orc-36-followup
  - autopilot
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
At end of /autopilot ORC-36 wrap-up, no cost summary was emitted. Two issues:

1. Wrap-up step looked for cost-report.sh under ~/.config/orchestrator/scripts/ but it lives in repo at scripts/cost-report.sh. The wrap-up wiring is hardcoded to the wrong path.

2. metrics.duckdb at ~/.config/orchestrator/metrics.duckdb is unwritable: 'Operation not permitted' on macOS (likely sandboxed I/O restriction or stale lock). Even if the script were located, write access would fail.

Manual run of /Users/spidey/code/orchestrator/scripts/cost-report.sh --change-id orc-36 worked fine and produced a complete report ($17.50 total, 24 steps, per-agent + per-model breakdown). So the script and DB are both functional from CLI — the autopilot harness is the broken link.

Impact: every autopilot run silently misses its cost summary at the most important moment (post-completion review). Cost data still lands in DuckDB via step-event ingestion, but operators don't see it unless they run the report manually.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Autopilot wrap-up locates cost-report.sh via repo path ($REPO_ROOT/scripts/) not ~/.config/orchestrator/
- [ ] #2 Diagnose root cause of metrics.duckdb 'Operation not permitted' on macOS — fix or document the workaround
- [ ] #3 Cost summary appears at end of /autopilot run (verified by running /autopilot on a follow-up ticket)
- [ ] #4 Regression test: wrap-up step exits non-zero if cost-report.sh is missing or DB unwritable, instead of silently skipping
- [x] #5 install.sh symlinks $REPO_ROOT/scripts/ to $ORCHESTRATOR_HOME/scripts/ (mirroring how it already symlinks config/), so ~/.config/orchestrator/scripts/cost-report.sh resolves
- [ ] #6 install.sh creates ~/.config/orchestrator/ with proper ownership and pre-initializes metrics.duckdb (empty schema or touch) so first write doesn't hit permission/sandbox issues
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Autopilot cost-report wiring fixed. Scripts symlinked into ORCHESTRATOR_HOME, SKILL.md updated to use relative scripts/cost-report.sh path, fail-loud on non-zero exit, metrics.duckdb pre-created by install.sh.

All 4 tasks committed (d048dc0 → 9d4083e) and archived at spec/changes/archive/2026-05-04-orc-37/.
<!-- SECTION:FINAL_SUMMARY:END -->
