---
id: ORC-14
title: Harden the Orchestrate Dispatch Loop
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-orchestrate-dispatch-loop-hardening
  - feature
  - score-7.5
  - recurrence-2
dependencies: []
priority: medium
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: orchestrate-dispatch-loop-hardening -->

**Original score:** 7.5 | **Recurrence:** 2

## Idea

The orchestrate skill's dispatch loop (SKILL.md section 4) describes the core execution engine but has several fragile points: (1) The "READ step contract" instruction does not specify what to do if the YAML file is missing or malformed -- the agent will just fail mid-workflow. (2) The "READ agent definition" instruction has no fallback if the `.md` file is missing. (3) The "AFTER step completes" section writes `next_step` but does not handle the case where state.yaml is corrupted or locked. (4) There is no timeout or circuit-breaker for agent spawns that hang. Add explicit error handling clauses to the dispatch loop: file-not-found checks before each READ, agent spawn timeout guidance, and state.yaml write-after-verify pattern.

## Why Now

As the orchestrator runs longer autonomous sessions (autopilot with multiple iterations), the probability of hitting these edge cases increases. A single missing step contract YAML (perhaps due to a rename that was not propagated) can crash an entire autopilot session with no recovery path. The recent rename of SPEC_CHANGES_DIR to WORKFLOW_STATE_DIR is exactly the kind of change that could leave stale references.

## Priority

- User value: 7/10
- Strategic fit: 8/10
- Technical leverage: 7/10
- Effort: small
- **Score: 7.0**

---
<!-- SECTION:DESCRIPTION:END -->
