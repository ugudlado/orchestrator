# Workflow Override Contract

Defines how a repository can ship its own workflow definitions that take
precedence over the global orchestrator workflow.

---

## Motivation

The orchestrator ships deterministic workflow machinery (schemas, step
contracts, templates) that should work for any repo. But some repos have
genuine structural reasons to diverge — a different set of phases, a
custom step, a stricter verify block. These are **workflow concerns**,
distinct from **repo learnings** (tech stack, domain, conventions) which
live in `spec/project.yaml`.

This contract governs the workflow axis only. For repo learnings, see
`spec/project.yaml` `learnings:` and the rule-merge contract.

---

## Resolution Rule

Every dispatch-loop read of workflow configuration uses this rule:

```
RESOLVE_WORKFLOW_FILE(relative_path):
  repo_override = $REPO_ROOT/.orchestrator/<relative_path>
  IF exists(repo_override):
    RETURN repo_override
  RETURN $ORCHESTRATOR_HOME/config/<relative_path>
```

Applies to:

| Relative path                        | Purpose                               |
|--------------------------------------|---------------------------------------|
| `workflows/<schema>.yaml`            | Workflow schema (phases, steps)       |
| `workflows/_<include>.yaml`          | Shared phase includes                 |
| `steps/<step_id>.yaml`               | Individual step contract              |
| `steps/CONVENTIONS.md`               | Step authoring conventions            |
| `templates/<kind>/...`               | Output templates                      |
| `guidelines.yaml`                    | Workflow selection guidelines         |

**Does NOT apply to (universal invariants — global only):**

| Path                                          | Why                           |
|-----------------------------------------------|-------------------------------|
| `steps/contracts/error-recovery.md`           | Retry protocol is universal   |
| `steps/contracts/resume-token.md`             | Resume format is universal    |
| `steps/contracts/rule-merge.md`               | Merge algorithm is universal  |
| `steps/contracts/metrics-schema.md`           | Telemetry schema is universal |
| `steps/contracts/workflow-override.md` (self) | This contract                 |

Rationale: protocol contracts keep the orchestrator pluggable across repos
and hosts. Overriding them would break cross-repo metrics, resume, and
rule semantics. A repo that needs to diverge on protocol should propose a
change to global, not fork.

---

## File-level Replacement (no merge)

When a repo ships an override, it **fully replaces** the global file. There
is no YAML-level merge of schemas or step contracts. This is deliberate:

- Merging would require a precedence policy per field (already complex for
  rules; worse for phases and verify blocks).
- Whole-file replacement makes the repo's workflow readable in one place.
- Repos that want to diverge slightly can copy the global file and edit.

The trade-off: drift. A repo-overridden schema stops receiving upstream
improvements automatically. Repos that override should audit their
overrides periodically against upstream.

---

## What Belongs Here vs. `project.yaml`

| Concern                                                | Home                                  |
|--------------------------------------------------------|---------------------------------------|
| "This repo has a different phase ordering"             | `.orchestrator/workflows/<schema>.yaml` |
| "This repo needs an extra gating step"                 | `.orchestrator/workflows/<schema>.yaml` |
| "This repo's verify must run `pnpm typecheck`"         | `spec/project.yaml` learnings[]       |
| "This repo uses pytest with shadow DB"                 | `spec/project.yaml` learnings[]       |
| "This repo prefers conventional commits"               | `spec/project.yaml` rules[]           |
| "Auth module is legacy — don't refactor"               | `spec/project.yaml` learnings[]       |

**Rule of thumb:** if the knowledge is about *how the workflow runs*
(phases, steps, gates, contracts), it's a workflow override. If it's
about *what the repo is* (stack, commands, domain, conventions), it's a
learning. Agents read both; the workflow shapes execution, learnings
shape decisions within execution.

---

## Interaction with Rule-Merge

The rule-merge contract (`rule-merge.md`) already filters learned rules
by repo scope via the `<!-- ... repo: X -->` metadata. That mechanism is
orthogonal to this contract:

- **Workflow override**: replaces whole files (schemas, step contracts).
- **Rule-merge repo scope**: filters individual learned rules inside a
  (possibly global) step contract.

A repo can use either, both, or neither. Most repos should use neither —
`project.yaml` learnings + global workflow is sufficient for the majority
of divergence needs.

---

## When to Reach for a Workflow Override

Prefer `project.yaml` learnings first. Only add a `.orchestrator/`
override when:

1. The divergence is about workflow **shape** (phases, steps, gates), not
   workflow **inputs** (commands, conventions, domain facts).
2. Encoding the divergence as a learning would require the agent to
   interpret prose where a YAML contract would be deterministic.
3. The divergence is stable — not a one-off for the current feature.

Signs you're reaching too far:

- Copying a step contract just to add one rule → write a learning instead.
- Overriding `guidelines.yaml` for keyword tweaks → propose a global change.
- Forking a schema to rename phases → propose a global change.
