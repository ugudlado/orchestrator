# Step classification: shell | skill | prompt

Use this when a step is ambiguous during workflow design.

The orchestrator executes **three** kinds of steps.

## Deterministic → shell (`run: script.sh`)

Same inputs → same action. File ops, fixed CLI, git, webhooks, ticket status.

**Naming:** imperative verb-object — `create-worktree`, `ticket-start`.

## Reusable capability → skill (`model:` + `skill: <name>`)

Judgment work that should also work standalone (`/ux-critique`, `/implement`).

- Charter lives in `skills/<name>/SKILL.md` (installable)
- Workflow: `- skill: ux-critique` or `- id: ux-critique` with contract `skill:`
- Agent role (mental model): skill + `-er` → ux-critiquer, implementer, reviewer

**Test:** Would you invoke this outside a workflow? → skill.

## One-off / pack-local → prompt (`model:` + `prompt: <file>`)

Judgment work that is not a reusable installable skill — charter is a markdown
file under the step dir (any name; prompt-optimizer keys off `pack.yaml`
`prompt:`).

**Test:** Only meaningful inside this workflow/pack? → prompt.

## Edge cases

| Step                               | Route  | Why                 |
| ---------------------------------- | ------ | ------------------- |
| Convert md → PDF via pandoc        | shell  | Fixed command       |
| UX critique                        | skill  | Reusable            |
| One-off schema-specific summarizer | prompt | Not a product skill |
| Ticket → In Progress               | shell  | Fixed API call      |

## Reuse

```bash
ls skills/
ls "$ORCH_CONFIG/steps/"
```

Prefer an existing `skills/<name>` before scaffolding a new prompt step. Never
duplicate a skill charter under `config/steps/`.
