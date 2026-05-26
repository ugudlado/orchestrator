---
feature-id: orc-88
linear-ticket: ORC-88
---

# Design: Lock in routes.yaml as canonical model source; remove unused per-step model override

## Context

Two competing descriptions of "how a step picks a model" coexist in the repo:

1. **Shipped path** — `scripts/routes.yaml` maps `agent → {model, subprocess}`.
   It is loaded at runtime by `config/scripts/orchestrator_next/pricing.py`,
   `scripts/estimate-cost.sh` (via `config/scripts/estimate-cost.sh`), and
   `scripts/dashboard/server.py`. It was extended by ORC-83/84.
2. **Phantom path** — `config/steps/CONVENTIONS.md § Step-Level Model Override`
   documents an optional top-level `model:` key on a step contract. Repo-wide
   grep confirms zero step contracts set `model:` and zero dispatch code paths
   read one (`grep -rn "^model:" config/steps/` → empty; `grep -rn
   "step.*model\|contract.*model\|model_override"` outside DB-column code →
   empty).

ORC-58 was archived declaring `routes.yaml` deleted, but the file is alive and
load-bearing. The contradiction confuses new contributors and blocks downstream
cost-attribution work (ORC-8). This design removes the phantom doc, declares
`routes.yaml` canonical at its source, and reconciles the ORC-58 archive.

## Goals / Non-Goals

### Goals

- Make `scripts/routes.yaml` the single, self-describing source of truth for
  agent → model routing.
- Eliminate the "Step-Level Model Override" doc so future contributors don't
  ship against a half-built field.
- Leave the ORC-58 archive discoverable as an obsolete claim by linking forward
  to ORC-88.

### Non-Goals

- Implementing a real per-step model override. Removing the phantom doc is the
  full scope; if a use case appears later, that ticket starts from a clean
  slate.
- Changing the shape, key names, or path of `scripts/routes.yaml`. Pricing and
  dashboard code already depends on the current shape.
- Removing or renaming `scripts/lib/agent-routes.sh` (the loader).
- Adding a test or pre-commit guard against re-introducing `model:` in a
  contract. The grep in AC-3 is sufficient evidence and avoids over-engineering
  for a one-line YAML field.

## Approaches Considered

### Approach 1: Routing section in CONVENTIONS.md

Add a short "Agent → Model Routing" section to `config/steps/CONVENTIONS.md`
naming `scripts/routes.yaml` as canonical and stating that step contracts
declare only `agent:`. Remove the existing "Step-Level Model Override" section
in the same edit.

- **Pros**: CONVENTIONS.md is the file contributors already open when authoring
  or editing a step contract. The rule sits next to the related conventions
  (frontmatter, approach field, learned-rules routing). One file changes; one
  doc tells the story.
- **Cons**: Project-level architecture readers who only browse `spec/project.yaml`
  won't see it there. Mitigated by routes.yaml's own header comment (AC-5).
- **Complexity**: XS.

### Approach 2: Routing section in spec/project.yaml § architecture

Add the routing rule to `spec/project.yaml § architecture` and leave
CONVENTIONS.md to delete the override section only.

- **Pros**: Architecture-level facts live with other architecture-level facts.
- **Cons**: Contributors editing step contracts rarely open `project.yaml`. The
  rule is contract-authoring guidance, not architecture; splitting "delete here,
  state rule there" risks the new section drifting out of sync with contract
  practice. Adds a second touched file.
- **Complexity**: S.

### Selected Approach

**Approach 1.** Lowest complexity (XS vs S) and aligns the rule with the file
contributors actually read when writing step contracts. The routing section
lives where the inverse rule used to live, so anyone arriving via the old
"Step-Level Model Override" anchor or muscle memory immediately reads the
correct rule. The companion header on `routes.yaml` covers the architecture-
level audience.

## High-Level Design

### Architecture Overview

This is a documentation and metadata change — no runtime behavior changes. The
agent → model lookup remains `scripts/routes.yaml` loaded by
`scripts/lib/agent-routes.sh` and the three Python consumers. After this
change, the docs match the code:

```
step contract (declares `agent:` only)
        │
        ▼
scripts/routes.yaml  ◀── canonical agent → {model, subprocess} map
        │
        ├── scripts/lib/agent-routes.sh         (shell loader)
        ├── config/scripts/orchestrator_next/pricing.py
        ├── config/scripts/estimate-cost.sh
        └── scripts/dashboard/server.py
```

### Key Abstractions

No new abstractions. The change formalizes an existing one: routes.yaml is the
single agent → model lookup table, and step contracts contribute only the agent
identity.

## Low-Level Design

### Components

This is a doc-only change. Four files are touched:

| File | Edit |
|---|---|
| `config/steps/CONVENTIONS.md` | Delete `## Step-Level Model Override` section (existing lines ~181–191). Add a new section `## Agent → Model Routing` in its place, stating routes.yaml is canonical and step contracts declare only `agent:`. |
| `scripts/routes.yaml` | Expand the existing header comment block (lines 1–3) to declare the file canonical and list its three consumers by path. Keep existing `# model:` / `# subprocess:` field comments. |
| `spec/changes/archive/2026-05-25-orc-58/state.yaml` | Append a short top-level note (e.g., `superseded_by: orc-88` plus a one-line explanation) pointing forward. State file is YAML — append a new top-level key rather than editing existing structure. |
| `spec/changes/archive/2026-05-25-orc-58/tasks.md` | Append a final "## Reconciliation" section pointing to ORC-88 as the corrected resolution. |

### Data Flow

N/A — no runtime data flow change. The doc edits describe the existing flow
accurately for the first time.

### State Management

The only state change is a one-time edit to the ORC-58 archive. The convention
across the repo treats archive directories as immutable history; we are
appending forward-pointers (not rewriting history), which is the lighter of the
two reconciliation options in OQ-2. This honors the convention loosely while
still leaving a discoverable note. Backlog re-open / re-close was considered
and rejected as heavier ceremony for the same outcome.

### Error Handling

Verification is grep-based. The verify commands in tasks.yaml must:

- Confirm `^model:` appears in zero step contracts (i.e., contributors haven't
  reintroduced the field).
- Confirm the deleted section header is gone from CONVENTIONS.md.
- Confirm the new "Agent → Model Routing" header is present in CONVENTIONS.md.
- Confirm the routes.yaml header lists the three consumer paths.
- Confirm the ORC-58 archive contains a forward pointer to orc-88.

If any grep returns the wrong count, the task fails and the developer agent
re-runs the edit.

## Constraints

- Path stability of `scripts/routes.yaml`: three consumers reference this exact
  path. The header edit must not change the filename or relocate the file.
- The YAML schema of `routes.yaml` (key names, model tiers, subprocess values)
  must remain byte-compatible with the existing loaders. Only comments change.
- Archive convention: do not rewrite ORC-58's existing fields; only append.

## Trade-offs

- **Append-to-archive vs re-open/re-close** (OQ-2): chose append. Lighter, no
  backlog churn, still discoverable via grep on `orc-88` in the archive tree.
  Sacrifices the tidier audit trail of a full re-open/re-close cycle.
- **No code-level guard against re-introducing `model:`** (OQ-3): chose
  doc-only. A test guard for a one-line YAML key the dispatch loop doesn't even
  read is over-engineering; the AC-3 grep is the durable signal.

## Acceptance Criteria

- AC-1: Given a contributor opens `config/steps/CONVENTIONS.md`, when they
  search for "Step-Level Model Override", then no section by that name exists.
  [traces: UC-1, UC-E1]
- AC-2: Given a contributor opens `config/steps/CONVENTIONS.md`, when they
  scan top-level sections, then a section "Agent → Model Routing" exists that
  names `scripts/routes.yaml` as the single source of truth and instructs
  step contracts to declare only `agent:`. [traces: UC-1, UC-E1]
- AC-3: Given the repo at HEAD after this change, when `grep -rn "^model:"
  config/steps/` is run, then it returns zero matches; and when `grep -rn
  "step.*model\|contract.*model\|model_override" config/scripts/ scripts/` is
  run (excluding DB-column references for the recorded `model` field on
  step_events), then no dispatch code path reads a step-level `model:`.
  [traces: UC-1, UC-E1]
- AC-4: Given a future reader opens `spec/changes/archive/2026-05-25-orc-58/`,
  when they read `state.yaml` or `tasks.md`, then they find a forward pointer
  naming orc-88 as the corrected resolution and explaining that
  `scripts/routes.yaml` was not actually deleted. [traces: UC-3]
- AC-5: Given a contributor opens `scripts/routes.yaml`, when they read the
  header comment block, then it states the file is canonical for agent→model
  routing and lists its consumers:
  `config/scripts/orchestrator_next/pricing.py`,
  `config/scripts/estimate-cost.sh`, and `scripts/dashboard/server.py`.
  [traces: UC-2]

## Decisions

- "Agent → Model Routing" section lives in CONVENTIONS.md, not project.yaml →
  contributors edit contracts via CONVENTIONS.md; placing the rule next to the
  removed override avoids drift → architecture-level readers are covered by the
  routes.yaml header.
- ORC-58 reconciled by appending a forward pointer rather than re-opening →
  matches the "archive is immutable history" convention and avoids backlog
  churn → audit trail is grep-discoverable rather than CLI-discoverable.
- No code-level guard against re-introducing `model:` → AC-3 grep is sufficient
  and a guard would over-engineer a non-existent code path → if the field is
  ever wanted for real, that ticket designs the guard alongside it.

## Open Questions

- None. All three discovery OQs are resolved above (OQ-1 → CONVENTIONS.md,
  OQ-2 → append to archive, OQ-3 → doc-only).
