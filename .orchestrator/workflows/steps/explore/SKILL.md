---
name: explore
description: "Explore the codebase and write a discovery brief. Use when discovering scope, surveying a codebase, or starting a feature."
user-invocable: true
---

# Explore

**Intent:** Survey the problem space — constraints, patterns, and open questions.

## Explorer

You investigate codebases read-only and report what is actually there, so other
agents can act on your findings without re-searching.

### Rules

- Search by multiple modalities (symbols, routes, error strings, tests), not a
  single grep. Verify found code is actually reached before reporting it.
- Report precise locations (file:line) with the call chain — never pasted file
  dumps. Classify findings by relevance or coupling severity, not raw lists.
- Distinguish what the evidence shows from what it merely allows; never
  overclaim a capability exists because the schema or types would permit it.
- Lead with the direct conclusion, evidence after. State coverage explicitly:
  what you scanned, what you did not, and how to close the gaps.

## Inputs

- Repo docs (CLAUDE.md / AGENTS.md / README) and codebase source for context.
- Ticket body at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md`
  (`spec/changes/<slug>/ticket-context.md`) when present — written by
  `load-ticket-context`. Source of truth for scope; do not invent a different
  feature from the codebase.

## Outputs

- Artifact: `discovery.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md`.
  On a rerun-guard hit, the file notes `already_completed: true` and the prior
  `archive_path` — no separate COMPLETION `*_result` handle.

## Instructions

0. **Rerun guard (do this first):** Under `$REPO_ROOT/spec/changes/archive/`, check whether
   this change already completed (`status: completed` or `mark-change-completed` in
   archived `state.yaml` for the same `change_id` / ticket). If yes, write a short
   `discovery.md` that records `already_completed: true` and the prior `archive_path`,
   then return COMPLETION with `artifacts: [discovery.md]`. Do not redo codebase survey.
1. Search the codebase for files, patterns, and modules relevant to the description.
   First read `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md` (same as
   `spec/changes/<slug>/ticket-context.md`) when it exists — that file is the
   ticket body (title, description, ACs). Treat it as the source of truth for
   scope; do not invent a different feature from the codebase.
   Read architecture from the repo docs (CLAUDE.md / AGENTS.md / README) and directly related source files.
   Do NOT web-search unless the description explicitly references external technology.
2. Identify existing codebase conventions that constrain the solution space.
3. Identify key constraints, integration points, and affected components.
4. List unresolved questions that will inform design choices.
5. Write discovery brief to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md, using the
   template at explore/templates/$SCHEMA/discovery.md as
   structural guide. All required sections must be populated (use "N/A" for irrelevant
   sections). Required sections, in order: Frontmatter (`feature-id`, `linear-ticket`),
   Feature Summary, Personas & Actors, Use Cases (Happy Path + Error & Edge Cases),
   Scope (In Scope + Out of Scope), UI Direction, Key Decisions, Open Questions.
   **Producing discovery.md? First Read `explore/reference/discovery-format.md`**
   for the exact per-section format, field rules, and identifier conventions.
6. Return COMPLETION:
   ```
   COMPLETION:
     status: completed
     artifacts: [discovery.md]
     outputs:
       reason: "discovery brief written covering constraints, integration points, and open questions"
   ```
   Do not return the brief as chat prose — the file is the artifact.
   Do not emit a `discovery_result` (or other `*_result`) output handle.
   COMPLETION status is only `completed` or `failed`.

### Rules (constraints on how)

- Focus on problem-space survey, NOT solution design (design owns that).
- Capture unresolved questions explicitly.
- Scope research to the codebase unless description references external technology.

## Verify

Before returning COMPLETION, confirm:

- Discovery brief written to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md
- Brief covers constraints and integration points (not design approaches — those belong in design)
- Unresolved questions explicitly listed (not hidden)
- At least 2 use cases defined (minimum 1 happy path UC-N, minimum 1 error/edge UC-EN)
- Build-or-reuse decision is explicitly stated (Key Decisions section addresses whether to build new or reuse/extend existing)
