---
feature-id: orc-116
linear-ticket: ORC-116
---

# Design: Persist and surface a step "briefing"

## Context

When an agent step terminates, `record._build_history_entry` copies a fixed
whitelist of optional keys (`_OPTIONAL_STEP_HISTORY_KEYS`,
`orchestrator_next/record.py:50-60`) from the done payload into
`step_history[-1]`. Neither `reason` (already emitted on abandon and validated
for routing at `record.py:556-558`) nor any freeform "why" field is in that
list, so agent reasoning is dropped before persistence. Correspondingly,
`workflow_report_step.py` (`config/steps/workflow-report/`) renders only
mechanical columns — status/attempts/tokens/cost/duration/model — so a post-run
reader cannot see *why* a step reached its terminal status without re-reading
raw agent stdout.

The `_COMPLETION_CONTRACT` string (`orchestrator_next/run_loop.py:56-75`) is
injected into every agent prompt in `build_prompt()` (`:114-126`), which makes
it the single leverage point for a global "always emit X" instruction across
all agent steps.

## Verified System Boundaries

- `_COMPLETION_CONTRACT` is used by exactly one caller — `build_prompt` at
  `run_loop.py:125` — so editing the constant reaches all agent steps.
- `_OPTIONAL_STEP_HISTORY_KEYS` is consumed by exactly one loop —
  `for key in _OPTIONAL_STEP_HISTORY_KEYS` at `record.py:569` — which copies
  keys from the done payload into the history entry. Adding a key is
  passthrough only; no schema or validator changes.
- `workflow_report_step.py:60-197` is a single `build_workflow_report`
  function. Stdout structured output is the dict returned at line 175; stderr
  table is written inline via `sys.stderr.write`. Both surfaces live in one
  file.
- Truncation to 120 chars already exists in the same file at line 169 for
  the `detail` field of the workflow-issues table — same ceiling reused.
- Shell/script steps do not run through `build_prompt` (they invoke
  `script.sh` directly), so they never see the contract and never emit
  briefing; the whitelist copy tolerates absence (missing key = skip). No
  extra guard needed.

## Goals / Non-Goals

### Goals

- Agents emit a one-line `briefing` in every terminal COMPLETION outputs
  block via the shared contract.
- `briefing` and `reason` persist into `step_history[]` entries.
- Workflow report renders `briefing` per step (stderr table) truncated to
  120 chars, and exposes it in the stdout structured JSON.
- Shell/script steps and agent steps that omit `briefing` render as `—` and
  route normally — no regression.

### Non-Goals

- No per-prompt edits to individual step `prompt.md` files.
- No new schema fields, no parallel reasoning channel, no schema-level
  validation of briefing content.
- No backfill of existing `step_history[]` entries in on-disk state files.
- No dashboard / UI work — stderr table + stdout JSON only.
- No change to `reason` routing semantics; only its persistence changes.

## Approaches Considered

### Approach 1: Single `briefing` field via shared contract (Selected)

Add `briefing` as an always-emit field in `_COMPLETION_CONTRACT`; add
`briefing` and `reason` to `_OPTIONAL_STEP_HISTORY_KEYS`; render one new
column in the stderr table and one new key in the stdout JSON.

- Pros: one leverage point per surface (contract, whitelist, report);
  reuses existing passthrough machinery; matches ponytail direction from
  ticket (no new channel).
- Cons: agents may occasionally omit the field — tolerated by design.
- Complexity: **S**.

### Approach 2: Reuse `reason` on both paths

Extend `reason` to be always-emitted on success too; skip the new field.

- Pros: one fewer field name.
- Cons: overloads a routing-signal field with UX text; existing prompts
  and agents treat `reason` as abandonment-specific; higher blast radius on
  routing logic that inspects `reason`.
- Complexity: **M**.

### Approach 3: New per-step contract machinery

Introduce a typed `briefing` output validated by contract.yaml per step.

- Pros: enforceable.
- Cons: new machinery for one string field; contradicts the
  "no new schema/contract machinery" scope note; touches 21 contracts.
- Complexity: **L**.

### Selected Approach

**Approach 1**. Lowest complexity (S). Aligns with the ticket's explicit
"no parallel reasoning channel" direction. Approach 2 conflates routing
with UX. Approach 3 is over-engineering rejected by scope.

## High-Level Design

### Architecture Overview

Three touchpoints, each already a single-owner surface:

1. **Contract emission** (`run_loop.py:_COMPLETION_CONTRACT`) — instruct
   every agent to emit `briefing` in the outputs block on both success and
   abandon forms.
2. **Persistence** (`record.py:_OPTIONAL_STEP_HISTORY_KEYS`) — extend the
   whitelist with `briefing` and `reason`.
3. **Surfacing** (`workflow_report_step.py:build_workflow_report`) — pull
   `briefing` from each history entry, truncate to 120 chars for the
   stderr table, include the raw string in each per-step dict in the
   stdout JSON.

### Key Abstractions

None new. The three existing extension points are the abstraction.

## Low-Level Design

### Components

- **`_COMPLETION_CONTRACT`** (`run_loop.py:56-75`): add `briefing:
  "<one-line summary>"` to both the success and abandoned forms shown in
  the contract, plus a one-line rule stating it should be present on
  every terminal status.
- **`_OPTIONAL_STEP_HISTORY_KEYS`** (`record.py:50-60`): append
  `"briefing"` and `"reason"` to the tuple.
- **`build_workflow_report`** (`workflow_report_step.py:60-197`):
  - When collapsing entries into `rows`, capture the last non-empty
    `briefing` seen for a step_id (parallel to how `model` is handled at
    `:110-112`).
  - Add a `Briefing` column to the header/separator and per-row printout.
    Truncate to 120 chars with ellipsis using the same `[:120]` pattern as
    the issues `detail` field at `:169`.
  - Add `"briefing": r["briefing"] or None` to each step dict in the
    returned structured output at `:180-186`.

### Data Flow

Agent stdout → `parse_completion` (existing) → done payload dict → 
`record._build_history_entry` copies whitelisted keys → written to
`step_history[-1]` in `state.yaml` → `workflow_report_step.py` reads
`step_history[]` at report time → truncated for stderr table, raw for JSON.

### State Management

`step_history[]` entries gain up to two new optional string keys
(`briefing`, `reason`). Entries without them read as before.

### Error Handling

- Missing `briefing`: `dict.get("briefing")` returns `None`; report
  renders `—`. No error path.
- Multi-line briefing: `.replace("\n", " ")` before truncation (same as
  existing `detail`).
- Colon in briefing: agent contract already requires YAML-quoting values
  containing colons (existing rule at `run_loop.py:60`); no new
  instruction needed.

## Constraints

None beyond standard project conventions.

## Trade-offs

- Agents may omit the briefing occasionally; we tolerate absence rather
  than escalating to a hard contract failure. Acceptable — the field is
  UX, not routing. If omission rate proves high in practice, hardening is
  a follow-up.
- Table width grows by one column (~120 chars max) — the workflow report
  is already wide; adding a trailing column at the end minimizes reflow
  damage compared to inserting mid-row.

## Acceptance Criteria

- AC-1: Every agent step's assembled prompt contains a `briefing:`
  instruction in the shared completion contract (verify: unit test on the
  contract string constant). [traces: UC-1, UC-2]
- AC-2: A done payload carrying `briefing` (success form) results in
  `state.yaml`'s `step_history[-1]["briefing"]` equal to the emitted
  value. [traces: UC-1]
- AC-3: A done payload carrying `reason` (abandoned form) results in
  `state.yaml`'s `step_history[-1]["reason"]` equal to the emitted value.
  [traces: UC-2]
- AC-4: `workflow_report_step.build_workflow_report()` returns a `steps`
  list whose entries include a `briefing` key (string or None), and the
  stderr table contains a `Briefing` column with entries truncated to
  ≤120 characters. [traces: UC-1, UC-2, UC-E3]
- AC-5: A `step_history[]` containing entries with no `briefing` key
  (shell/script or omitting agent) produces no error and renders `—` in
  the table and `null` in the JSON. [traces: UC-E1, UC-E2]
- AC-6: Existing routing/validation behavior is preserved — an
  abandoned-form done payload still routes as abandoned regardless of
  whether `briefing` is present; a completed-form still routes as
  completed. (verify: existing record tests still pass.) [traces: UC-1,
  UC-2]

## Decisions

- Single `briefing` field, not overloading `reason` → keeps routing signal
  separate from UX text → both fields persist; `briefing` is the primary
  render column.
- Contract-side global injection, not per-prompt edits → 1 edit, ~21
  prompts benefit → future prompts inherit automatically.
- Truncation ceiling 120 chars → matches existing `detail` field
  behavior → consistent readability.
- Append `Briefing` at the end of the stderr table → minimizes column
  reflow → existing tests parsing table by prefix keep working.

## Open Questions

(none — OQ-1 from discovery resolved: yes, `briefing` appears per-step in
the structured JSON. AC-4 encodes this.)
