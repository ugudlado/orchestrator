# Discovery Brief Format Contract

The `discovery.md` file is a structural contract between `explore` (producer) and
`design` / `review` (consumers). Both producer and
consumer steps MUST use this exact format.

## Format

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

## Field rules

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

## Identifier conventions

- Use case IDs: `UC-1`, `UC-2`, ... for happy path; `UC-E1`, `UC-E2`, ... for error/edge
- IDs are sequential within their category with no gaps
- Open question IDs: `OQ-1`, `OQ-2`, ... sequential with no gaps

## Consumers

- `design` — reads UC-N identifiers for design.md AC traceability and scope/use cases for task derivation
- `review` — verifies structural compliance
