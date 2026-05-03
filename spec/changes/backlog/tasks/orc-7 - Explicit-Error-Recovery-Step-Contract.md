---
id: ORC-7
title: Explicit Error Recovery Step Contract
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-error-recovery-contract-step
  - feature
  - score-8.0
  - recurrence-1
dependencies: []
priority: medium
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: error-recovery-contract-step -->

**Original score:** 8.0 | **Recurrence:** 1

## Idea

The orchestrate SKILL.md dispatch loop says "Follow Error Recovery Contract (CONVENTIONS.md) for all failures" but CONVENTIONS.md does not contain an explicit Error Recovery Contract section. The `execute-next-task.yaml` step contract has inline retry logic (steps 7a-7f), `run-phase-review.yaml` has its own retry pattern (step 7), and `phase-signoff.yaml` has a rejection-fix loop (step 5). These three retry/recovery patterns are defined independently with slightly different semantics. There should be a single `error-recovery.yaml` step contract (or a CONVENTIONS.md section) that defines the canonical retry/escalation pattern: (1) diagnose failure, (2) attempt scoped fix, (3) re-verify, (4) increment retry counter, (5) escalate at max_retries. Then the three existing steps reference it instead of each defining their own variant.

## Why Now

The orchestrator references an Error Recovery Contract that does not exist. This is a concrete gap -- any agent following the dispatch loop instructions will hit a broken reference. Additionally, inconsistent retry semantics across steps mean that `/learn`'s cross-feature retry analysis (step 2b) is comparing apples to oranges when aggregating retry data across different step types.

## Priority

- User value: 6/10
- Strategic fit: 9/10
- Technical leverage: 9/10
- Effort: medium
- **Score: 8.0**

---
<!-- SECTION:DESCRIPTION:END -->
