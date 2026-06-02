# Design Review — ORC-99

Verdict: **needs_work**

## Scores

- completeness: **9/10**
- ac_coverage: **8/10**
- task_quality: **8/10**
- feasibility: **4/10** (capped due to critical finding)
- scope_control: **8/10**
- overall (minimum dimension): **4/10**

## Findings

### Critical

1. **Feasibility gap: unresolved implementation locus can invalidate the task plan**
   - References: `design.md` (`Open Questions` OQ-1, OQ-2), `discovery.md` (OQ-1, OQ-2), `tasks.yaml` (T-1..T-3 file scope)
   - Problem: The artifacts leave unresolved whether lifecycle behavior is prompt-instructional vs deterministic script-backed, and which file paths are canonical for lifecycle scanning. Despite that, `tasks.yaml` only plans edits to `skills/workflow-learner/SKILL.md` plus prose-contract tests. If actual enforcement is implemented outside this skill text or against different canonical paths, the planned changes can pass local tests but still miss ORC-99 acceptance behavior in real runs.
   - Why this blocks implementation: Acceptance criteria require lifecycle parity behavior, not only wording parity. Without resolving implementation locus/path canon first, execution can target the wrong system boundary.
   - Required fix:
     - Resolve OQ-1 and OQ-2 in `design.md` before implementation starts.
     - Update `Selected Approach` / low-level component section to state the authoritative enforcement surface.
     - Update `tasks.yaml` file lists and verify commands to include whichever concrete runtime surfaces enforce lifecycle behavior (if any beyond `SKILL.md`).

### Important

1. **AC traceability could be sharper at task granularity**
   - References: `tasks.yaml` T-1, T-2, T-3
   - Problem: Every task lists all ACs (`AC-1..AC-4`) in `why`, including T-3 which is a phase-gate/cleanup task. This technically traces to ACs but blurs direct accountability for which task is the primary implementation owner per AC.
   - Impact: Increases review ambiguity and makes failure triage less deterministic when one AC regresses.
   - Recommended fix:
     - Keep broad phase-gate trace if desired, but assign primary AC ownership per implementation task (e.g., T-2 primary for behavior ACs, T-3 primary for integration confirmation).

## Guidance For Architect

1. Close feasibility-critical open questions (OQ-1/OQ-2) and convert answers into explicit design decisions.
2. Reconcile `tasks.yaml` with those decisions so task file scopes match the true enforcement boundary.
3. Retain the existing RED/xfail -> GREEN marker-removal pattern; this part is structurally sound.
4. Keep non-goal boundaries, but add one sentence clarifying how ORC-97 dispatch dependency affects ORC-99 completion gating (prerequisite vs parallel track) to avoid execution dead-ends.
