---
feature-id: orc-96
linear-ticket: ORC-96
---

# Design: Inject project.yaml learnings into agent step_context at spawn

## Context

The self-improving loop only closes when an agent happens to read `project.yaml`.
The `learn` phase distills rules into `project.yaml`'s `learnings[]`, but nothing
guarantees a spawning agent ever sees them — accumulated repo knowledge reaches
agent behavior by chance, not by construction.

`dispatch.py` builds the agent-path action dict at spawn time (`dispatch()`),
which is the deterministic seam where injection belongs. The dispatcher already
resolves `project.yaml` worktree-first then `repo_root` via `_project_yaml_path`
(shared with `_max_spawn_failures`, orc-85). The one genuine decision is the
**relevance policy**: how much of the repo's `learnings[]` each agent sees, and
how reference data is excluded so it does not dilute behavioral guidance.

The implementation already landed clean in this worktree's `dispatch.py` during
the investigation that unblocked this workflow (the corrupt main-branch WIP noted
in discovery OQ-1 was abandoned, not salvaged — matching the
`engine-self-modification-hazard` learning). This design documents the as-built
seams and the relevance policy; the remaining work is **test coverage** of those
seams.

## Goals / Non-Goals

### Goals

- Inject a structured `learnings` block into the **agent-path** action dict so
  spawned agents act on accumulated repo knowledge deterministically.
- Apply a relevance policy that excludes `kind: informational` reference data and
  honors optional `agents:`/`phases:` tags (untagged = universal).
- Make injection best-effort: any failure degrades to no learnings, never blocks
  a spawn or resume.
- Cover the seams with tests: no project.yaml, empty/malformed learnings,
  informational exclusion, tag matching, and a green full suite.

### Non-Goals

- Modifying the `learn` phase / `workflow-learner` to emit `agents:`/`phases:`
  tags — the writer is upstream and untouched here.
- Injecting learnings on the **inline `run:` path** — inline scripts are not
  agents.
- Rendering learnings into the agent's actual prompt — `dispatch.py` only attaches
  the structured block to the action dict; surfacing it is downstream.
- Changing the `learnings[]` schema in `project.yaml` or adding cross-repo merge —
  the file is already repo-scoped.

## Approaches Considered

### Approach 1: Inject-all (minus informational)

Load `learnings[]`, drop `kind: informational`, inject the rest verbatim for
every agent on the agent path.

- **Pros**: Minimal logic; nothing to mis-tag; every agent sees every behavioral
  rule.
- **Cons**: No path to per-agent or per-phase targeting as the corpus grows;
  every agent pays the full token cost of the whole corpus. Adding targeting
  later is a breaking change to the policy, not an additive one.
- **Complexity**: XS

### Approach 2: Tag-and-filter, untagged = universal

Iterate `learnings[]`. Exclude `kind: informational`. If an entry carries an
optional `agents:` list, include it only when the spawning agent is in that list;
likewise for `phases:`. Entries with neither tag are universal (injected for
every agent).

- **Pros**: Honors AC-2's exclusion with the same per-item iteration; adds
  per-agent/per-phase targeting as a **forward-compatible no-op** (no learning is
  tagged today, so it degrades to inject-all now and tightens automatically when
  the learner starts tagging — no future policy rewrite).
- **Cons**: Marginally more branching than inject-all (two optional-tag checks).
- **Complexity**: XS

### Selected Approach

**Approach 2 (tag-and-filter, untagged = universal).** AC-2 already requires
per-item iteration to drop `informational` entries, so the optional `agents:`/
`phases:` checks are the *same* XS unit of work — both approaches are XS. On the
complexity tie the heuristic prefers higher module reuse (both reuse
`_project_yaml_path` identically → still tied), then breaks alphabetically, which
would favor "Inject-all". The deciding constraint that rules out Approach 1 is
**forward-compatibility**: discovery OQ-2 confirms no learning is tagged today, so
Approach 2 behaves identically to Approach 1 right now while leaving a non-breaking
path to targeting. Choosing inject-all would force a breaking policy rewrite the
moment the learner emits a tag. Same cost, strictly more headroom.

Auto-selection heuristic record:

| Approach | Complexity | Numeric | Module reuse |
|----------|-----------|---------|--------------|
| Inject-all | XS | 1 | 1 (`_project_yaml_path`) |
| Tag-and-filter | XS | 1 | 1 (`_project_yaml_path`) |

- (a) Complexity map: XS=1 for both.
- (b) Lowest numeric: tie at 1.
- (c) Reuse tiebreak: tie at 1.
- (d) Alpha tiebreak would pick "Inject-all", **overridden by the stated
  forward-compatibility constraint** — Approach 1 is ruled out because it cannot
  honor future tags without a breaking change, so the genuine selection is
  Approach 2. (The heuristic's alpha step only applies among approaches that
  satisfy all hard constraints; forward-compat is a hard constraint here.)

## High-Level Design

### Architecture Overview

```
dispatch(state, state_yaml_path)
  ├─ agent path (contract.agent set)
  │    learnings = _relevant_learnings(_load_learnings(state.raw),
  │                                    contract.agent, state.phase)   # best-effort
  │    action["learnings"] = learnings
  ├─ resume path (any in_progress step retry)
  │    same call, action["learnings"] = _resume_learnings             # best-effort
  │    NOTE: resume has no agent-vs-run guard (see Open Questions)
  └─ inline run path (fresh dispatch, contract.run set)
       NO learnings key                                               # AC-3
```

`_load_learnings` reuses `_project_yaml_path` (worktree-first, then `repo_root`)
to find `project.yaml`, reads `learnings[]`, and JSON-flattens the list (so
YAML-parsed `date` scalars survive the action payload, which must be JSON-
serializable). `_relevant_learnings` applies the relevance policy.

### Key Abstractions

- `_load_learnings(state_raw) -> list[dict]` — raw loader. Empty list on missing
  file, unreadable YAML, or non-list `learnings:`. JSON round-trips via
  `json.dumps(..., default=str)` to coerce `date`/non-JSON scalars to strings.
- `_relevant_learnings(learnings, agent_name, phase) -> list[dict]` — relevance
  filter implementing Approach 2. Order-preserving.

## Low-Level Design

### Components

| Component | Responsibility | Inputs | Outputs |
|-----------|---------------|--------|---------|
| `_project_yaml_path` | Resolve project.yaml (pre-existing, orc-85) | `state_raw` | `Path \| None` |
| `_load_learnings` | Read & JSON-flatten `learnings[]` | `state_raw` | `list[dict]` |
| `_relevant_learnings` | Filter by policy | `learnings`, `agent_name`, `phase` | `list[dict]` |
| `dispatch` agent branch | Attach `learnings` to action | resolved contract | action dict |
| `dispatch` resume branch | Attach `learnings` to resume action | resolved contract | action dict |

### Data Flow

`project.yaml` → `_load_learnings` (load + JSON-flatten) → `_relevant_learnings`
(exclude informational, apply tags) → `action["learnings"]` (agent / resume
paths only) → emitted JSON → driver → spawned agent context.

### State Management

No new persistent state. The `learnings` block is computed at dispatch time and
lives only in the in-flight action dict. `project.yaml` is read-only here.

### Error Handling

Injection is **best-effort** at both injection sites. `_load_learnings` returns
`[]` and writes a stderr warning on missing/unreadable/malformed input (mirrors
`_max_spawn_failures`). Both injection sites wrap the
`_relevant_learnings(_load_learnings(...))` call in `try/except Exception`,
degrading to `learnings = []` and a stderr warning rather than failing dispatch.
Rationale: `dispatch()` is a hot path; a learnings fault must never take down a
spawn or block a resume.

## Constraints

- The action payload must be JSON-serializable — forces the `json.dumps(...,
  default=str)` flatten in `_load_learnings` (YAML `learned: 2026-04-09` parses to
  a `date`).
- Verify commands in tasks must be repo-root-relative (no absolute paths, no
  `cd`).
- Must not reference any specific LLM tool (agent-agnostic rule).

## Trade-offs

- **Whole-corpus injection today** vs. per-agent targeting: accepted because no
  learning is tagged yet, so targeting would be inert; Approach 2 keeps the cheap
  path open without paying for it now.
- **Verbatim entries** (including `refs`/`source`) vs. trimming to `id`/`rule`:
  injected verbatim (discovery OQ-3). Accepted — current corpus is small; trimming
  is a downstream token-cost optimization, not a correctness concern, and the
  `informational` exclusion already removes the heaviest `refs`-bearing entries.

## Acceptance Criteria

- AC-1: Given a state on the agent dispatch path with a readable `project.yaml`
  containing behavioral `learnings[]`, When `dispatch()` builds the action, Then
  the action dict contains a `learnings` key whose value is the policy-filtered
  list. [traces: UC-1]
- AC-2: Given `learnings[]` containing an entry with `kind: informational`, When
  `_relevant_learnings` runs, Then that entry is excluded from the result while
  behavioral entries are retained. [traces: UC-2]
- AC-3: Given a step dispatched on the **fresh** inline `run:` path, When
  `dispatch()` builds the action, Then the action dict contains **no** `learnings`
  key. (Scope note: the resume path has no agent-vs-run guard and sets `learnings`
  unconditionally — see Open Questions OQ-A. AC-3 pins the fresh `run:` branch,
  which is the canonical inline path; the resume edge is documented and tested for
  its as-built behavior, not asserted clean.) [traces: UC-E3]
- AC-4: Given `_project_yaml_path` resolves to `None` (no project.yaml), When
  `_load_learnings` runs, Then it returns `[]`, the agent action carries
  `learnings: []`, and dispatch proceeds normally (exit 0). [traces: UC-E1]
- AC-5: Given `learnings:` is absent, not a list, or the YAML is unreadable, When
  `_load_learnings` runs, Then it returns `[]` (a stderr warning may be emitted)
  and dispatch does not crash. [traces: UC-E2]
- AC-6: Given a learning carrying `agents: [X]` (or `phases: [P]`), When
  `_relevant_learnings` runs for agent Y≠X (or phase Q≠P), Then the tagged entry
  is excluded; and given an untagged entry, Then it is included for every agent.
  [traces: UC-1]
- AC-7: Given the full dispatch test suite, When run from repo root via pytest,
  Then it passes (no regressions from the learnings tests). [traces: UC-E2]

## Decisions

- Tag-and-filter over inject-all → AC-2 forces per-item iteration so tag-honoring
  is free and forward-compatible → no breaking policy rewrite when the learner
  starts tagging.
- JSON round-trip in `_load_learnings` (`default=str`) → YAML `date` scalars are
  not JSON-serializable but the action payload must be → dates flatten to strings
  in the injected block.
- Best-effort `try/except` at both injection sites → dispatch is a hot path → a
  learnings fault degrades to `[]`, never blocks a spawn/resume.
- Inject on the resume path too (mirrors fresh dispatch) → an in_progress retry
  must see the same learnings a fresh spawn would → consistent agent context
  across attempts.

## Open Questions

- OQ-A (non-blocking, scope note): The resume branch (`dispatch.py:471-527`)
  fires for **any** `in_progress` step — agent or inline — and sets
  `"learnings": _resume_learnings` unconditionally, lacking the
  `if contract.agent / elif contract.run` guard that the fresh-dispatch path has.
  So an inline `run:` step left `in_progress` (e.g. a crash mid-execution) would
  carry a `learnings` key on re-dispatch, which is inconsistent with AC-3's intent
  for the inline path. This is a rare edge (inline steps normally complete
  atomically) and the value is harmless (a `run:` step ignores the key). Decision:
  **document and test the as-built behavior here, do not fix it in this
  test-coverage feature** (a fix would edit the clean `dispatch.py`, expanding
  scope). If the inconsistency matters, file a follow-up chore to add the
  agent-vs-run guard to the resume branch.
- Discovery OQ-1 resolved: clean worktree implementation, main WIP abandoned.
  OQ-2 confirmed: no learning is tagged today. OQ-3 resolved: inject verbatim.
