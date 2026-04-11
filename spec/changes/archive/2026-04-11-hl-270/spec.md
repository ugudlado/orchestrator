---
feature-id: use-sonnet-for-simplifier-step
linear-ticket: HL-270
---

# Chore: Use Sonnet for Simplifier step

## What

Add a `model: sonnet` field to `run-simplify.yaml` step contract so the dispatch
loop passes an explicit model override when spawning the simplifier agent. Document
the step-level `model:` field convention in CONVENTIONS.md so other steps can use it.

Files affected:
- `config/steps/run-simplify.yaml` -- add `model: sonnet` field
- `config/steps/CONVENTIONS.md` -- document step-level model override convention

## Why

The simplifier step is a non-critical quality pass (code cleanup, not correctness
verification). Using Sonnet explicitly ensures cost efficiency -- Sonnet is cheaper
and faster than Opus while being sufficient for simplification tasks. Adding a
step-level `model:` field also establishes the convention for future cost
optimization of other non-critical steps.

## Acceptance Criteria

- [ ] AC-1: `run-simplify.yaml` has a `model: sonnet` field at the top level.
- [ ] AC-2: CONVENTIONS.md documents the step-level `model:` field with semantics:
  "When present, the dispatch loop passes this as the `model` parameter to the
  Agent tool, overriding the agent definition's model frontmatter."
- [ ] AC-3: No other step contracts are modified (scope: simplifier only).
