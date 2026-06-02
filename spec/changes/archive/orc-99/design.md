---
feature-id: orc-99
linear-ticket: ORC-99
---

# Design: Bring Agent Overlay Learnings Under Rule Lifecycle

## Context

The learn cycle currently defines hit/miss updates and decay pruning for learned rules in step-contract content, while agent overlay learnings live in `.orchestrator/agents/*.md`. This split creates lifecycle drift: learned overlay guidance can accumulate without the same effectiveness checks and retirement policy. The design must preserve a single lifecycle contract, keep edits safe for mixed manual/learned overlay files, and remain compatible with partial metadata on older learned lines.

## Goals / Non-Goals

### Goals

- Extend the section 5b rule-effectiveness scan scope so learned overlay entries are updated with hits/misses using the same retry-derived signal used for step-contract rules.
- Extend the section 5b-decay to evaluate and prune ineffective learned overlay entries using the same threshold policy used for step-contract learned rules.
- Preserve a strict mutation boundary so only learned-stamped content is eligible for update/removal and manual overlay prose is untouched.
- Define verification that covers overlay hit increment, miss increment, and decay removal behavior.

### Non-Goals

- Redesigning the learn architecture or introducing a new lifecycle policy specifically for overlays.
- Changing dispatch-time overlay assembly semantics in `orchestrator_next/agent_overlay.py` or `orchestrator_next/scripts/run-workflow.sh`.
- Refactoring unrelated quality-bar adaptation logic in section 5c.

## Approaches Considered

### Approach 1: Single-Policy Path Expansion

Treat overlay files as additional lifecycle scan targets under existing section 5b and section 5b-decay rules.

Pros:
- Lowest complexity; keeps one policy surface.
- Reuses current metadata semantics (`learned`, `hits`, `misses`, `cycle`).
- Minimizes divergence risk between step-contract and overlay lifecycle behavior.

Cons:
- Requires careful wording so step-id-derived hit/miss mapping for overlays is explicit.
- Needs stronger guardrails around manual/learned mixed overlay files.

Complexity: S

### Approach 2: Overlay Normalization Adapter

Define an intermediate overlay normalization pass, then feed normalized entries into existing lifecycle scans.

Pros:
- Clear separation of extraction and lifecycle decision logic.
- Easier future extension to additional learned artifact types.

Cons:
- More moving pieces and additional cognitive overhead.
- Higher chance of contract drift between adapter and lifecycle sections.

Complexity: M

### Approach 3: Parallel Overlay Lifecycle Contract

Create a distinct overlay-only lifecycle section parallel to section 5b/section 5b-decay.

Pros:
- Very explicit overlay-specific behavior.
- Isolates overlay behavior from step-contract language.

Cons:
- Duplicates policy; likely to drift over time.
- Highest maintenance burden and review surface.

Complexity: L

### Selected Approach

Selected **Single-Policy Path Expansion** using the required heuristic: complexity mapping yields `S=2`, `M=3`, `L=4`, so Approach 1 is selected by lowest numeric complexity before tie-breakers. If tied, this approach also has the highest module reuse count because it reuses existing section 5b/5b-decay lifecycle semantics without introducing an adapter layer or duplicate policy surface. Alternatives were ruled out because they either add indirection without corresponding value (normalization adapter) or duplicate policy and increase drift risk (parallel overlay lifecycle).

## High-Level Design

### Architecture Overview

`workflow-learner` continues to derive effectiveness signals from completed feature retry data, then applies one lifecycle policy to all learned-rule targets. The target set expands from step-contract files to include `.orchestrator/agents/*.md` overlays, with learned-comment metadata as the only mutable boundary.

### Key Abstractions

- **Lifecycle target set**: union of step-contract learned-rule sources and overlay learned-rule sources.
- **Learned metadata contract**: inline `<!-- learned: ... hits: N, misses: N ... -->` comment that carries lifecycle state.
- **Safe mutation boundary**: only lines/blocks containing learned metadata are eligible for hit/miss rewrites or decay removals.

## Low-Level Design

### Components

- `skills/workflow-learner/SKILL.md` section 5b and section 5b-decay define lifecycle scan scope and mutation rules.
- Overlay files `.orchestrator/agents/*.md` are additional scan inputs for learned metadata updates/removal.
- Prose-contract tests under `orchestrator_next/tests/` assert the lifecycle contract language and safety boundaries.

### Data Flow

The learner reads recent step history and cycle count signals, computes hit/miss/decay outcomes, then applies those outcomes to all learned metadata instances found in the expanded target set. Non-learned prose in overlays remains read-only.

### State Management

Per-rule lifecycle state remains encoded in inline metadata counters (`hits`, `misses`) and `cycle` stamps. No new persistent store is introduced.

### Error Handling

- Missing counters default to `0` for backward compatibility.
- Missing overlay files produce no mutation (empty match set) rather than failure.
- Any malformed or non-learned manual text is skipped because it is outside the learned metadata boundary.

## Constraints

- Preserve one lifecycle policy across learned-rule storage locations.
- Never mutate manual overlay text lacking `<!-- learned: ... -->`.
- Keep acceptance criteria verifiable via targeted repo-root-relative test commands.

## Trade-offs

- The design favors policy consistency and low implementation complexity over introducing a richer typed representation of learned entries.
- Overlay-specific nuance is expressed through explicit contract language rather than a separate abstraction layer.

## Acceptance Criteria

- AC-1: Given a completed feature where an overlay-affecting step executes with no retries, when section 5b effectiveness update runs, then learned overlay entries in `.orchestrator/agents/*.md` are included and receive a hit increment using the same lifecycle rule as step-contract learned rules. [traces: UC-1]
- AC-2: Given decay cycle `K` that meets section 5b-decay trigger and an overlay learned entry that satisfies ineffective thresholds, when decay evaluation runs, then that learned overlay entry is flagged and removed under the same thresholds used for step-contract learned rules. [traces: UC-2]
- AC-3: Given an overlay file containing both manual prose and learned-stamped entries, when section 5b or section 5b-decay applies updates, then only `<!-- learned: ... -->`-scoped entries are touched and manual prose remains unchanged. [traces: UC-E1]
- AC-4: Given legacy learned overlay metadata with missing `hits`/`misses`, when lifecycle update or decay evaluation parses metadata, then counters default safely to `0` and processing continues without mutating unrelated manual text. [traces: UC-E2]

## Decisions

- Unify lifecycle target scope across step contracts and overlays -> avoids policy drift between learned-rule stores -> keeps review and maintenance deterministic.
- Keep metadata comment as the sole mutable anchor -> protects hand-written overlay guidance from accidental decay edits -> enforces clear safety boundary.

## Open Questions

- OQ-1: Should overlay hit/miss attribution map by originating step id, by owning agent name, or by a hybrid mapping when computing effectiveness updates?
- OQ-2: For multi-line learned overlay blocks, what exact block boundaries are canonical for safe decay removal when adjacent to manual text?
