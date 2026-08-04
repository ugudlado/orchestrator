# Step classification: shell | prompt

Use this when a step is ambiguous during workflow design.

The orchestrator executes **two** kinds of steps. There is no third kind — the
old `skill:` contract field is removed and is now a hard contract error.

## Deterministic → shell (`run: script.sh`)

Same inputs → same action. File ops, fixed CLI, git, webhooks, ticket status.

**Naming:** imperative verb-object — `create-worktree`, `ticket-start`,
`intake-brief`.

## Judgment → prompt (`model:` + `prompt: <path>.md`)

Anything requiring LLM reasoning: summarizing, critiquing, deciding relevance,
writing prose, choosing between plausible options.

- `prompt:` is a relative path to a **`.md` file**. Prefer the step-local
  form `<id>/SKILL.md` where `config/steps/<id>/<id>` symlinks to
  `skills/<id>` at the pack root. Legacy skills-search refs
  (`<name>/SKILL.md` without a step-local symlink) still resolve via
  `<repo>/skills` then `<pack>/skills`.
- The charter inside the skill dir is `SKILL.md` if present, else `prompt.md`.
- Workflow entry: `- prompt: ux-critique`, or `- id: ux-critique` with
  `prompt: ux-critique` when you need `on_failure`/`max_retries`.
- Agent role (mental model): capability + `-er`/`-or` → ux-critiquer,
  implementer, reviewer.

**Test:** does the step need an LLM? → prompt. Otherwise → shell.

Reusable-vs-one-off is **not** a classification question. Both cases are
`prompt:` pointing at a directory; whether that directory is a polished
installable skill or a pack-local charter is a matter of what you put in it,
not of which contract field you write.

## Edge cases

| Step                               | Route  | Why                          |
| ---------------------------------- | ------ | ---------------------------- |
| Convert md → PDF via pandoc        | shell  | Fixed command                |
| Format JSON into a fixed md table  | shell  | Template-driven, no judgment |
| UX critique                        | prompt | Requires judgment            |
| One-off schema-specific summarizer | prompt | Summarizing is LLM work      |
| Ticket → In Progress               | shell  | Fixed API call               |

A step that mixes both — run a linter, then decide which failures block — is
either split into a shell step and a prompt step, or classified `prompt`
because judgment is present. It is never `shell`.

## Reuse

```bash
ls .
ls "$ORCH_CONFIG/steps/"
```

Prefer an existing pack-root `<name>/` before scaffolding a new prompt directory.
Never duplicate a charter under `config/steps/` — the contract points at the
directory, so there is nothing to copy.
