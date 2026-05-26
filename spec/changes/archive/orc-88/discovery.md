---
feature-id: orc-88
linear-ticket: ORC-88
---

# Discovery Brief: Lock in routes.yaml as canonical model source; remove unused per-step model: override

## Feature Summary

The repo currently documents two competing models for how a step picks an LLM:
(a) `scripts/routes.yaml` maps `agent → {model tier, subprocess}` and is read at
runtime by `pricing.py`, `estimate-cost.sh`, and `dashboard/server.py`; (b)
`config/steps/CONVENTIONS.md § Step-Level Model Override` describes an optional
top-level `model:` key on a step contract that the dispatch loop would forward
to the Agent tool. The override is documented but unused — no step contract sets
`model:` and no dispatch code reads it. The earlier ORC-58 ticket claimed
`routes.yaml` was deleted, yet the file is alive and was extended by ORC-83/84.
This ticket eliminates the contradiction by locking in the simpler shipped
model (`step → agent (in contract) → model (in routes.yaml)`), deleting the
unused override doc, and reconciling ORC-58's archive note.

## Personas & Actors

- **Contributor reading step contracts** — needs one unambiguous answer to "how
  does this step pick its model?" before adding/editing a contract.
- **Workflow-improver / `/learn`** — reads CONVENTIONS.md when proposing rule
  additions or new contracts; a phantom field invites incorrect future use.
- **Cost-attribution tooling** (`pricing.py`, `estimate-cost.sh`,
  `dashboard/server.py`) — already consumes `scripts/routes.yaml` as the agent→
  model map; the header should declare this status so future refactors don't
  treat the file as scratch config.

## Use Cases

### Happy Path

UC-1: Read source of truth — A contributor opens CONVENTIONS.md, sees a single
"Agent → Model Routing" section pointing to `scripts/routes.yaml`, and writes a
new step contract with only an `agent:` field.

UC-2: Cost lookup wiring — `pricing.py` / `estimate-cost.sh` /
`dashboard/server.py` continue to load `scripts/routes.yaml` as today; the file
header documents them as the consumers so the wiring is self-describing.

UC-3: ORC-58 reconciliation — A future reader of the ORC-58 archive finds a
note (or reopened/re-closed history) that points to ORC-88 explaining that
`routes.yaml` was not actually deleted and is the canonical map.

### Error & Edge Cases

UC-E1: Contributor copies the deleted override section from git history — Once
the section is gone from CONVENTIONS.md and the new section explicitly states
"steps declare only `agent:`", a future contributor who resurrects the field
from git history has a clear, opposing rule to violate; AC-3's grep guard makes
the violation detectable.

UC-E2: A future need for per-step model selection arises — Out of scope for
this ticket. If/when needed, the design will revisit whether the route lives in
the contract or in `routes.yaml` keyed on `(agent, step)`; this ticket
deliberately removes the half-built path so the next design starts clean rather
than inheriting an undocumented stub.

## Scope

### In Scope

- Remove the "Step-Level Model Override" section from
  `config/steps/CONVENTIONS.md`.
- Add a short "Agent → Model Routing" section to CONVENTIONS.md (or
  `spec/project.yaml` — pick one home; CONVENTIONS.md is the contributor-facing
  doc) naming `scripts/routes.yaml` as the single source of truth and
  instructing contracts to declare only `agent:`.
- Add a header comment block to `scripts/routes.yaml` declaring it canonical
  and listing consumers (`config/scripts/orchestrator_next/pricing.py`,
  `config/scripts/estimate-cost.sh`, `scripts/dashboard/server.py`).
- Repo-wide verification: zero step contracts contain a top-level `model:`
  key; zero dispatch code paths read a step-level `model:`.
- Reconcile ORC-58: either add a note to its archived `state.yaml` /
  `tasks.md` pointing to ORC-88 as the corrected resolution, or reopen and
  re-close per backlog policy.

### Out of Scope

- Implementing a real per-step model override. Removing the phantom
  documentation does not block future work — when a use case appears, the
  design can start from a clean slate. Rationale: AC asks only to remove the
  unused doc, not to ship the feature.
- Changing the shape of `scripts/routes.yaml` (key names, model tiers,
  subprocess list). Pricing and dashboard tooling already depends on the
  current shape; reshaping is a separate ticket.
- Migrating any consumer that reads `routes.yaml` to a different location
  (e.g., `config/routes.yaml`). Path stability matters for the three callers
  listed above.
- Removing or renaming `scripts/lib/agent-routes.sh` (the shell loader). It is
  the load path; this ticket only annotates the YAML it loads.
- Cost-attribution refactor (ORC-8 follow-up). Mentioned by the ticket as
  downstream-blocked, but solving it is not in this ticket's AC.

## UI Direction

N/A — no UI components.

## Key Decisions

- **Design direction (selected by design-and-draft-artifacts)**: "Routing
  section in CONVENTIONS.md" (Approach 1 in design.md). Selected via the
  auto-selection heuristic — complexity XS (1) vs Approach 2's S (2). Rationale:
  CONVENTIONS.md is the file contributors already open when authoring step
  contracts, so the new rule sits where the inverse rule used to live;
  architecture-level readers are covered by the routes.yaml header (AC-5).
- OQ-1 resolved → CONVENTIONS.md (per above).
- OQ-2 resolved → append forward pointer to ORC-58 archive (lighter than
  re-open/re-close; honors "archive is immutable history" loosely while
  remaining grep-discoverable).
- OQ-3 resolved → doc-only; no test guard against re-introducing `model:`.
  Over-engineering for a one-line YAML field the dispatcher doesn't read;
  AC-3 grep is the durable signal.

## Open Questions

- OQ-1: Should the new "Agent → Model Routing" section live in
  `config/steps/CONVENTIONS.md` (closer to step-contract authors) or in
  `spec/project.yaml § architecture` (closer to the project-wide source-of-truth
  doc)? Ticket AC-2 names both as acceptable; design phase picks one.
- OQ-2: For AC-4, is the preferred reconciliation form (a) a one-line note
  appended to the ORC-58 archive `state.yaml` / `tasks.md`, or (b) a
  re-open/re-close cycle via the backlog CLI? The first is cheaper and
  honors the "archive is immutable history" convention loosely; the second is
  more traceable in the backlog tool. Defer to design / user preference.
- OQ-3: The phantom `model:` field is documented in CONVENTIONS.md only — no
  dispatch code reads it (verified via grep for `step.*model`, `contract.*model`,
  `model_override` — zero hits in `config/scripts/` or dispatch code). Is a
  test fixture or guard needed to *prevent* future reintroduction, or is the
  doc-removal + AC-3 grep sufficient? Recommend doc-only; a test guard is
  over-engineering for a one-line YAML key.
