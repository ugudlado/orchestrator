---
id: ORC-23
title: State YAML Schema Validation
status: To Do
assignee: []
created_date: '2026-05-03 10:56'
updated_date: '2026-05-03 11:00'
labels:
  - slug-state-yaml-schema-validation
  - feature
  - score-3.5
  - recurrence-1
dependencies: []
priority: low
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: state-yaml-schema-validation -->

**Original score:** 3.5 | **Recurrence:** 1

## Idea

Create a validation step (or pre-check in the dispatch loop) that validates `state.yaml` against the grammar defined in `grammar.yaml` before each step execution. The grammar already defines required fields (`schema`, `status`, `phase`, `step_id`, `flags`, `started_at`, `updated_at`) and valid enum values (`active|completed|paused`), but nothing enforces them at runtime. When an LLM writes a malformed state.yaml (wrong field name, missing required field, invalid enum), the error surfaces much later as a cryptic failure in a downstream step. Early validation with a clear error message ("state.yaml missing required field 'flags'") would catch corruption immediately.

## Why Now

The `state_contract` section in `project.yaml` already declares `required: [schema, flags]` and `merge_precedence`, but the orchestrate skill doesn't validate against it. The grammar file defines the full schema. The gap between "defined" and "enforced" is the problem. With the recent consolidation of state into `WORKFLOW_STATE_DIR`, there's now a single canonical path to validate.

## Prototype

No visual prototype. Implementation: add a validation check at the top of the dispatch loop (orchestrate SKILL.md section 4) that reads `state.yaml`, checks all required fields from `grammar.yaml state.required`, validates enum values, and reports specific violations before executing any step.

## Priority

- User value: 7/10
- Strategic fit: 7/10
- Technical leverage: 7/10
- Effort: medium
- **Score: 3.5**

---
<!-- SECTION:DESCRIPTION:END -->
