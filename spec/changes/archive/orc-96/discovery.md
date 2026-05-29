---
feature-id: orc-96
linear-ticket: ORC-96
---

# Discovery Brief: Inject project.yaml learnings into agent step_context at spawn

## Feature Summary

The self-improving loop currently only closes on what an agent happens to read: `project.yaml` accumulates `learnings[]` (rules distilled by the `learn` phase), but nothing guarantees a spawning agent ever reads them. This feature makes `dispatch.py` select the learnings relevant to the agent being spawned and inject them into the agent-path action as a structured `learnings` block, so accumulated repo knowledge reaches agent BEHAVIOR deterministically rather than by chance. The one real decision is the relevance policy (`_relevant_learnings`): how much of the repo's learnings each agent sees, and how informational reference data is excluded.

## Personas & Actors

- **Dispatcher (`dispatch.py`)** — system actor that builds the agent-path action dict at spawn time; the seam where injection happens.
- **Spawned agent (discoverer, architect, developer, reviewer, etc.)** — consumer of the injected `learnings` block; the loop closes on its behavior.
- **`learn` phase / `workflow-learner` agent** — producer that appends `learnings[]` entries to `project.yaml` (the upstream of this feature; not modified here).
- **Feature author / maintainer** — must decide whether to build on the corrupt main-branch WIP or start clean in the worktree (see OQ-1).

## Use Cases

### Happy Path

UC-1: Inject behavioral learnings — the dispatcher wants to attach repo `learnings[]` to the agent-path action so that a spawned agent acts on accumulated repo knowledge without having to read `project.yaml` itself.
UC-2: Exclude reference data — the dispatcher wants to filter out `kind: informational` learnings so that agents receive behavioral guidance only, not reference links that dilute attention.

### Error & Edge Cases

UC-E1: No project.yaml — what happens when `_project_yaml_path` resolves to `None`: `_load_learnings` returns `[]`, no `learnings` key is added to the action, dispatch proceeds normally.
UC-E2: Empty / malformed learnings — what happens when `learnings:` is absent, not a list, or YAML is unreadable: load returns `[]`, no key added, a warning may be emitted to stderr (mirrors `_max_spawn_failures`).
UC-E3: Inline (run:) path — what happens on the `run:` dispatch branch: NO `learnings` key is injected (inline scripts are not agents; AC-3).

## Scope

### In Scope

- Implement `_relevant_learnings(learnings, agent_name, phase)` relevance policy (the one real decision; currently raises `NotImplementedError`).
- Exclude `kind: informational` entries (AC-2).
- Inject only on the `agent:` dispatch path, never the `run:` path (AC-3).
- Tests covering: no `project.yaml`, empty learnings, informational exclusion, and tag matching (AC-4); full suite green (AC-5).
- Repair the corrupt in-progress plumbing when porting it into the worktree (see Key Decisions / OQ-1) — the helpers and injection must land as well-formed, single-copy code.

### Out of Scope

- Modifying the `learn` phase / `workflow-learner` to emit `agents:`/`phases:` tags — the writer is upstream; this feature must work with today's untagged learnings. (Tagging emission is a separate concern; OQ-2.)
- Cross-repo learning merge — `project.yaml` is already repo-scoped; the file IS the repo (documented in `_load_learnings`).
- Changing the learning schema in `project.yaml` — rationale: schema (`id`/`rule`/`kind`/`source`/optional `refs`) is fixed by the existing `learn` writer; adding `agents:`/`phases:` is optional and back-compatible, not required.
- Injecting learnings into the agent's actual prompt rendering — `dispatch.py` only attaches the structured block to the action dict; how the driver/agent surfaces it is downstream.

## UI Direction

N/A — no UI components.

## Key Decisions

- **Build location — start clean in the worktree, treat main WIP as a reference sketch (recommended, needs sign-off):** The plumbing described in the ticket as "landed" exists ONLY on `main` as **uncommitted, structurally corrupt** code: `_load_learnings` and `_relevant_learnings` are mis-indented *inside* `_persist_node_status`, and the agent-path injection block (`learnings = _relevant_learnings(...)`) is **duplicated 7+ times** with out-of-order line numbers (signs of a botched edit / merge damage). The orc-96 worktree's `dispatch.py` is CLEAN and contains none of it. Recommendation: implement orc-96 fresh in the worktree, porting only the *idea* of the seams (`_project_yaml_path` already exists clean in the worktree's `_max_spawn_failures`; add well-formed `_load_learnings` + `_relevant_learnings` + single injection). The corrupt main working-tree `dispatch.py` should NOT be committed as-is and should be reverted on main. This matches the repo's known `engine-self-modification-hazard` learning. (Confirm via OQ-1.)
- **Relevance policy shape (hint for design step, not resolved here):** Schema today is `id`/`rule`/`kind`/`source` plus optional `refs`; NO learning currently carries an `agents:` or `phases:` tag. AC-1 frames the choice as inject-all vs. tag-and-filter with untagged=universal. Because nothing emits tags today, tag-and-filter degenerates to inject-all for every existing entry — the only filter that bites in practice is the `kind: informational` exclusion (AC-2). The design step (`create-or-refresh-artifacts`) owns the final policy; explore records the trade-off only.
- **Resolution helper reuse:** `_project_yaml_path` (worktree-first then repo_root) already exists and is shared by `_max_spawn_failures`; `_load_learnings` must reuse it (mirrors orc-85 resolution and the `worktree-state-dir-path` learning).
- **CHOSEN DIRECTION (design step):** Relevance policy = **tag-and-filter, untagged = universal** (design.md Approach 2). Rationale: AC-2's `kind: informational` exclusion already forces per-item iteration, so honoring optional `agents:`/`phases:` tags is the same XS work and is forward-compatible — it degrades to inject-all today (no learning is tagged, per OQ-2) and tightens automatically when the learner starts tagging, with no breaking policy rewrite. Inject-all (Approach 1) was ruled out on the forward-compatibility constraint despite the alpha tiebreak favoring it. Consequence: `_relevant_learnings` iterates, excludes `informational`, applies optional tag filters, preserves order. Implementation already landed clean in this worktree's `dispatch.py`; remaining work is test coverage of the seams (main-branch corrupt WIP abandoned per OQ-1).

## Open Questions

- OQ-1: Should orc-96 be implemented clean in the worktree (recommended) with the corrupt, uncommitted main-branch `dispatch.py` WIP reverted — or should the feature attempt to salvage/commit the main WIP? This is a sign-off-worthy decision because it determines whether uncommitted main changes are abandoned. (Recommendation: start clean; revert main.)
- OQ-2: Does any current writer emit `agents:`/`phases:` tags on learnings, or are all learnings untagged today? Evidence: the only writers are `agents/workflow-learner.md` and `skills/learn/SKILL.md`; the latter documents the append schema (`project_learning` → `spec/project.yaml` `learnings[]`) with no `agents:`/`phases:` field, and every existing `project.yaml` `learnings[]` entry carries only `id`/`learned`/`rule` (some add `summary`/`evidence`/`kind`) — i.e. untagged. CONFIRMED: `kind: informational` is real and used exactly once today (`external-benchmark-references`, project.yaml ~L205), which is the canonical AC-2 exclusion case. If untagged is confirmed for tags, the policy can default to inject-all-minus-informational with tag-filtering as a forward-compatible no-op.
- OQ-3: Should `_relevant_learnings` drop the heavy `refs`/internal fields before injection (send only `id`/`rule`), or inject entries verbatim? (Affects token cost in the action payload; design-step concern.)
