# Explore

**Intent:** Survey the problem space — constraints, patterns, and open questions.

## Inputs

- `spec/project.yaml` and codebase source for context.
- Ticket body at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md`
  (`spec/changes/<slug>/ticket-context.md`) when present — written by
  `load-ticket-context`. Source of truth for scope; do not invent a different
  feature from the codebase.

## Outputs

- `discovery_result` — COMPLETION output handle: `{path: "discovery.md"}` (or
  `{already_completed: true, archive_path: "...", path: "discovery.md"}` on rerun-guard hit).
- Artifact: `discovery.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md`.

## Instructions

0. **Rerun guard (do this first):** Under `$REPO_ROOT/spec/changes/archive/`, check whether
   this change already completed (`status: completed` or `mark-change-completed` in
   archived `state.yaml` for the same `change_id` / ticket). If yes, write a short
   `discovery.md` noting the prior `archive_path`, then return COMPLETION with
   `outputs.discovery_result: {already_completed: true, archive_path: "...", path: "discovery.md"}`
   and `artifacts: [discovery.md]`. Do not redo codebase survey.
1. Search the codebase for files, patterns, and modules relevant to the description.
   First read `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/ticket-context.md` (same as
   `spec/changes/<slug>/ticket-context.md`) when it exists — that file is the
   ticket body (title, description, ACs). Treat it as the source of truth for
   scope; do not invent a different feature from the codebase.
   Read architecture from spec/project.yaml and directly related source files.
   Do NOT web-search unless the description explicitly references external technology.
2. Identify existing codebase conventions that constrain the solution space.
3. Identify key constraints, integration points, and affected components.
4. List unresolved questions that will inform design choices.
5. Write discovery brief to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md, using the
   template at skills/explore/templates/$SCHEMA/discovery.md as
   structural guide. All required sections must be populated (use "N/A" for irrelevant
   sections). Required sections, in order: Frontmatter (`feature-id`, `linear-ticket`),
   Feature Summary, Personas & Actors, Use Cases (Happy Path + Error & Edge Cases),
   Scope (In Scope + Out of Scope), UI Direction, Key Decisions, Open Questions.
   **Producing discovery.md? First Read `skills/explore/reference/discovery-format.md`**
   for the exact per-section format, field rules, and identifier conventions.
6. Return COMPLETION:
   ```
   COMPLETION:
     status: completed
     outputs:
       discovery_result: {path: "discovery.md"}
     artifacts: [discovery.md]
   ```
   Do not return the brief as chat prose — the file is the artifact.

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
