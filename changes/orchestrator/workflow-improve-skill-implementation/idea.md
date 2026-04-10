# Implement /workflow-improve Skill

## Idea
The `/workflow-improve` skill at `skills/workflow-improve/SKILL.md` is a 3-line stub with no real logic ("Analyze metrics and identify improvements to workflow infrastructure"). Similarly, the `/telemetry` skill is a 2-line stub. The `/workflow-improve` skill should be the user-facing command that validates the full workflow graph: checks every schema's step references resolve to actual step contract YAMLs, checks every step contract's `agent:` field resolves to an agent `.md`, checks `flags_read` references exist in schema `defaults`, and validates template references. This overlaps with the existing `doctor-deep-check` backlog item but is runtime-invocable rather than a Makefile target, and focuses on structural integrity of the workflow graph rather than symlink health.

## Why Now
The orchestrator has 38 step contracts, 6 schemas, and 11 agents. As this grows, silent reference breakage (a schema referencing a step that was renamed, a step referencing an agent that was deleted) will become a real maintenance burden. The recent refactoring wave (renaming SPEC_CHANGES_DIR, moving config paths) is exactly the kind of change that creates these breakages.

## Priority
- User value: 7/10
- Strategic fit: 9/10
- Technical leverage: 8/10
- Effort: medium
- **Score: 7.5**
