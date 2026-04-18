# Proposed additions to spec/project.yaml — learnings section

These blocks are ready to append under the `learnings:` key in
`/Users/spidey/code/orchestrator/spec/project.yaml`.
Apply them manually or via a follow-up commit after signoff.

Check each ID against the existing list first (current IDs as of 2026-04-18:
workflow-plan-upfront, file-level-symlinks, autopilot-must-complete,
metrics-db-derived, design-divergence-from-backlog).
None of the IDs below duplicate an existing entry.

---

```yaml
  - id: composite-pk-audit-trail
    learned: 2026-04-18
    source: subprocess-per-step-observability
    rule: >
      When an audit table may legitimately have multiple terminal rows per
      logical attempt (e.g. escalation then completion), include the status
      column in the composite primary key. Pure (entity, attempt) keys silently
      overwrite the escalation row.

  - id: inline-steps-are-tokenless
    learned: 2026-04-18
    source: subprocess-per-step-observability
    rule: >
      Do not attempt per-step token capture for inline-executed steps in a
      Claude Code session — the parent-context token counter is not exposed to
      the running conversation. Per-step attribution requires subprocess-per-step
      execution.

  - id: cross-artifact-drift-requires-atomic-edits
    learned: 2026-04-18
    source: subprocess-per-step-observability
    rule: >
      When editing a name or identifier that appears in multiple prose artifacts
      (spec.md, design.md, tasks.md), grep all artifacts for every variant before
      committing any single edit. Prose artifacts duplicate structural facts;
      editing one without checking the others is a latent drift source.

  - id: bash-fragility-prefer-python-for-new-code
    learned: 2026-04-18
    source: subprocess-per-step-observability
    rule: >
      For new YAML/JSON manipulation, state-machine logic, or arithmetic in this
      repo, write Python; reserve bash only for shell-native wrappers (PATH wiring,
      env var forwarding, process exec). Bash quoting and associative-array
      limitations (declare -A on bash 3.2) are a recurring bug source.

  - id: reviewer-retry-cost-is-atomic-vs-design
    learned: 2026-04-18
    source: subprocess-per-step-observability
    rule: >
      Scope review-gate fixes so each is resolvable in a single atomic commit.
      If a finding requires design revisit, surface it as a blocker before the
      review step rather than absorbing it mid-retry. Retries are cheap when
      findings are mechanically fixable; expensive when they expose architectural
      drift.

  - id: orchestrator-should-dispatch-not-execute
    learned: 2026-04-18
    source: subprocess-per-step-observability
    rule: >
      The orchestrator skill must be a pure dispatcher — it reads state and
      returns an action; cognitive or compute work runs in a named worker process,
      never inline in the orchestrator's own context. Steps that mix dispatch and
      execution are refactor candidates.
```

Also update `context.tech_stack` to add `python`:

```yaml
context:
  tech_stack: [bash, zsh, yaml, duckdb, yq, python]
```
