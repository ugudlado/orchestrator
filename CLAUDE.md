# CLAUDE.md

Read `spec/project.yaml` for all project context — vision, architecture, tech stack, quality bars, rules, gotchas, and learnings.

## File Operations

Never delete, rename, or move files without explicit permission. When converting formats (e.g., .md → .mdx), keep the original until the user confirms the new one works.

## Workflow

Feature work must go through the formal `/autopilot` or `/orchestrate` workflow — session directory, phase gating, checkpoints. Never jump straight to implementation and never skip the complete phase.

## Approach Before Implementation

Before starting any implementation step (writing code, running destructive commands, or creating multi-file artifacts), state the approach in 3 bullets:
1. **Files**: which files will be created or modified
2. **Approach**: the specific change in one sentence — not the goal, the mechanism
3. **Not doing**: what's deliberately out of scope for this step

Skip this only for trivial single-file edits under ~10 lines. Under `--auto`, emit the bullets and proceed; a human can intervene if the approach is wrong. Under interactive mode, wait for confirmation unless the user has pre-approved the step.

## Minimal Fixes

When making changes, do not introduce unnecessary fallbacks, abstractions, or generalizations. Prefer the smallest targeted fix. If a refactor starts feeling generic or "while I'm here," stop and confirm direction.

## Root-Cause Debugging

When a bug is reported, fix the structural root cause — don't layer more rules or guardrails on top. If the root cause is unclear, ask rather than patching symptoms.
