# Tasks -- Use Sonnet for Simplifier step

- [x] T-1: Add model field to run-simplify.yaml and document convention in CONVENTIONS.md
  Add `model: sonnet` to run-simplify.yaml top-level fields. Add a "Step-Level Model Override"
  section to CONVENTIONS.md explaining that when a step contract has a `model:` field, the
  dispatch loop passes it as the `model` parameter to the Agent tool, overriding the agent
  definition's model frontmatter.
  Verify: run-simplify.yaml contains `model: sonnet`; CONVENTIONS.md has a section documenting
  step-level model override; no other step contracts were modified.
