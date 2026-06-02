---
feature-id: orc-99
linear-ticket: ORC-99
---

# Discovery Brief: Bring Agent Overlay Edits Under Hit/Miss + Decay Lifecycle

## Feature Summary

ORC-99 closes a self-improvement lifecycle gap: learned guidance written to repo-scoped agent overlays under `.orchestrator/agents/*.md` is currently outside the explicit rule-effectiveness (`hits`/`misses`) and decay scans described in the workflow-learner pipeline, while step-contract learned rules already carry this lifecycle. Without parity, overlay learnings can accumulate indefinitely even when ineffective, increasing prompt bloat and drift risk in repeated runs.

## Personas & Actors

- **workflow-learner agent (`skills/workflow-learner/SKILL.md`)**: defines learn-cycle routing, hit/miss updates, and decay policy
- **Orchestrator maintainers**: rely on self-improving behavior to stay bounded rather than accretive
- **Dispatch/runtime path (`orchestrator_next/scripts/run-workflow.sh`, `orchestrator_next/agent_overlay.py`)**: consumes `.orchestrator/agents/<agent>.md` overlays during agent-step prompt assembly
- **Feature developers/reviewers**: need deterministic rules for what lines may be decayed versus preserved

## Use Cases

### Happy Path

UC-1: Overlay effectiveness tracking — workflow-learner wants to include learned overlay rules in hit/miss accounting so that overlay guidance follows the same effectiveness lifecycle as learned step-contract rules.
UC-2: Overlay decay pruning — workflow-learner wants to remove ineffective learned overlay entries on decay cycles so that stale agent-specific guidance does not grow unbounded.

### Error & Edge Cases

UC-E1: Mixed manual + learned overlay content — what happens when `.orchestrator/agents/<agent>.md` contains both hand-written guidance and learned stamped blocks, and decay must ensure only `<!-- learned: ... -->` entries are touched.
UC-E2: Legacy or partial metadata in overlay entries — what happens when an overlay learned block is missing `hits`/`misses` fields and lifecycle logic must default safely without corrupting manual text.

## Scope

### In Scope

- Inventory current lifecycle behavior in `skills/workflow-learner/SKILL.md` sections `5b` and `5b-decay` and identify where overlay files are excluded.
- Identify concrete integration points between overlay storage (`.orchestrator/agents/*.md`) and prompt consumption (`orchestrator_next/scripts/run-workflow.sh` + `orchestrator_next/agent_overlay.py`).
- Capture constraints for safe text mutation so only learned-stamped overlay entries are eligible for hit/miss updates or decay removal.
- Identify test-surface expectations for overlay hit increment, miss increment, and decay removal coverage.

### Out of Scope

- Redesigning the broader learn-cycle architecture (agent vs deterministic script ownership) — this ticket focuses on lifecycle parity for overlays, not full pipeline redesign.
- Changing overlay load semantics at dispatch time (`agent_overlay.py` and prompt concatenation) — these are existing integration surfaces, not the behavior under change.
- Refactoring unrelated step-contract learned-rule policies or quality-bar adaptation logic — no direct requirement from ORC-99 acceptance criteria.

## UI Direction

N/A — no UI components.

## Key Decisions

- **Build-or-reuse**: Reuse and extend the existing learned-rule lifecycle (hit/miss + decay) rather than creating a separate overlay-specific policy, to preserve one contract for learned guidance regardless of storage location.
- **Safety boundary**: Learned metadata comments (`<!-- learned: ... -->`) are the sole mutation boundary; hand-written overlay prose is treated as immutable by lifecycle operations.
- **Design selection (auto-heuristic)**:
  - Candidate A — **Single-policy path expansion**: extend §5b/§5b-decay scan targets from step-contract files to a unified set that also includes `.orchestrator/agents/*.md`; complexity **S (2)**; module reuse count **3** (`workflow-learner` lifecycle logic, existing metadata parser behavior, existing prose-contract test pattern).
  - Candidate B — **Overlay adapter then lifecycle pass**: add an intermediate normalized overlay index before running §5b/§5b-decay; complexity **M (3)**; module reuse count **2**.
  - Candidate C — **Dual independent overlay lifecycle**: define separate overlay-only lifecycle instructions parallel to §5b/§5b-decay; complexity **L (4)**; module reuse count **1**.
  - Selection criteria applied: complexity first (`S=2 < M=3 < L=4`), tie-breaks not needed.
  - **Selected direction: Single-policy path expansion** -> lowest complexity while preserving one lifecycle contract and minimizing divergence risk between step-contract and overlay behavior.

## Open Questions

- OQ-1: The workflow-learner contract text still references scanning `$ORCHESTRATOR_HOME/config/steps/*.yaml`, while active learned metadata appears in step `prompt.md` files under `config/steps/<step>/prompt.md`; which path is canonical for lifecycle scans in current implementation?
- OQ-2: Is lifecycle enforcement for `5b`/`5b-decay` currently purely prompt-instructional (agent-executed) or backed by deterministic script logic that should also be extended to `.orchestrator/agents/*.md`?
- OQ-3: For overlay decay removal, what exact block granularity is canonical (single line with metadata comment, or multi-line learned block anchored by metadata) so edits can safely avoid adjacent manual text?
- OQ-4: Should overlay lifecycle scans include all agent overlay files in `.orchestrator/agents/*.md`, or only overlays for agents observed in the just-completed feature's `step_history`?
