# Explore

**Intent:** Survey the problem space — constraints, patterns, and open questions.

## Inputs

None. (Reads `spec/project.yaml` and codebase source for context.)

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
   Read architecture from spec/project.yaml and directly related source files.
   Do NOT web-search unless the description explicitly references external technology.
2. Identify existing codebase conventions that constrain the solution space.
3. Identify key constraints, integration points, and affected components.
4. List unresolved questions that will inform design choices.
5. Write discovery brief to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md per the
   Discovery Brief Format Contract below,
   using the template at $ORCHESTRATOR_HOME/config/steps/explore/templates/$SCHEMA/discovery.md as structural guide.
   All required sections must be populated (use "N/A" for irrelevant sections).
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

- Focus on problem-space survey, NOT solution design (design-and-draft-artifacts owns that).
- Capture unresolved questions explicitly.
- Scope research to the codebase unless description references external technology.

## Verify

Before returning COMPLETION, confirm:

- Discovery brief written to $WORKTREE_ARTIFACT_DIR/$CHANGE_ID/discovery.md
- Brief covers constraints and integration points (not design approaches — those belong in design-and-draft-artifacts)
- Unresolved questions explicitly listed (not hidden)
- At least 2 use cases defined (minimum 1 happy path UC-N, minimum 1 error/edge UC-EN)
- Build-or-reuse decision is explicitly stated (Key Decisions section addresses whether to build new or reuse/extend existing)

---

## Discovery Brief Format Contract

The `discovery.md` file is a structural contract between `explore` (producer) and
`create-or-refresh-artifacts` / `run-phase-review` (consumers). Both producer and
consumer steps MUST use this exact format.

### Format

```markdown
---
feature-id: FEATURE-ID
linear-ticket: HL-XXX
---

# Discovery Brief: {title}

## Feature Summary

{One paragraph: what this feature does and why it matters.}

## Personas & Actors

{Who interacts with this feature — user roles, system actors, external services.}

## Use Cases

### Happy Path

UC-1: {title} — {actor} wants to {action} so that {outcome}.
UC-2: {title} — {actor} wants to {action} so that {outcome}.

### Error & Edge Cases

UC-E1: {title} — what happens when {error condition}.

## Scope

### In Scope

- {explicit list items}

### Out of Scope

- {explicit list items with rationale}

## UI Direction

{For UI features: playground description. For non-UI: "N/A — no UI components."}

## Key Decisions

- {Decision}: {rationale}

## Open Questions

- OQ-N: {question}
```

### Field rules

| Field                | Required   | Format                                                                     |
| -------------------- | ---------- | -------------------------------------------------------------------------- |
| Frontmatter          | Yes        | YAML block with `feature-id` and `linear-ticket`                           |
| Feature Summary      | Yes        | Single paragraph, no bullet lists                                          |
| Personas & Actors    | Yes        | At least one actor identified                                              |
| Happy Path Use Cases | Yes        | Minimum 2, format: `UC-<N>: title — actor wants to action so that outcome` |
| Error & Edge Cases   | Yes        | Minimum 1, format: `UC-E<N>: title — what happens when condition`          |
| In Scope             | Yes        | Bulleted list, at least one item                                           |
| Out of Scope         | Yes        | Bulleted list with rationale per item                                      |
| UI Direction         | Yes        | "N/A — no UI components" if non-UI                                         |
| Key Decisions        | Contextual | Populated by design-exploration step if design=true                        |
| Open Questions       | Yes        | Empty section means no blockers. Format: `OQ-<N>: question`                |

### Identifier conventions

- Use case IDs: `UC-1`, `UC-2`, ... for happy path; `UC-E1`, `UC-E2`, ... for error/edge
- IDs are sequential within their category with no gaps
- Open question IDs: `OQ-1`, `OQ-2`, ... sequential with no gaps

### Consumers

- `create-or-refresh-artifacts` — reads UC-N identifiers for design.md AC traceability and scope/use cases for task derivation
- `run-phase-review` — verifies structural compliance
