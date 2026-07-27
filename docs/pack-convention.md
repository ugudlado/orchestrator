# Config Pack Convention

**Protocol version: 1**

A config pack is a directory of workflow schemas + step directories that the
orchestrator engine can dispatch. This doc is the contract a pack author
targets — it doesn't require reading engine source. It documents behavior
that already exists in code; this doc is the authoring surface, dispatch is
the enforcement.

## 1. Layout

```
<pack-root>/
  pack.yaml              # name, version, description, protocol: 1
  workflows/*.yaml        # workflow schema definitions
  steps/<id>/
    contract.yaml         # id, version, run|skill|prompt (+ model)
    script.sh             # shell steps only
  steps/lib/              # optional, shared shell helpers (this pack only)

# Installable skills live outside the pack (repo skills/ + agent skill dirs):
skills/<name>/
  SKILL.md                # charter (any filename ok for prompt-optimizer via pack.yaml)
  pack.yaml               # optional — prompt-optimizer pack (prompt: SKILL.md)
  metrics.md / scenarios/ # optional eval banks
```

A pack may ship its own `steps/lib/`. Depending on another pack's `lib/`
(including the bundled `core` pack's) is undocumented and unsupported.

## 2. Dispatch kinds (shell | skill | prompt)

The orchestrator executes **three** kinds of steps:

| Kind       | Contract                    | Charter source                     | Role                                        |
| ---------- | --------------------------- | ---------------------------------- | ------------------------------------------- |
| **Shell**  | `run: script.sh`            | —                                  | Deterministic plumbing                      |
| **Skill**  | `model:` + `skill: <name>`  | installed `skills/<name>/SKILL.md` | Reusable capability (workflow + standalone) |
| **Prompt** | `model:` + `prompt: <file>` | markdown file under the step dir   | One-off / pack-local procedure              |

Prompt-optimizer does **not** care what the charter file is named — it optimizes
whatever path `pack.yaml` `prompt:` points at (body only when YAML frontmatter
is present).

### Naming

| Kind                      | Pattern                | Examples                             |
| ------------------------- | ---------------------- | ------------------------------------ |
| Skill id / name           | capability noun/verb   | `ux-critique`, `implement`, `review` |
| Agent role (mental model) | skill + `-er`/`-or`    | ux-critiquer, implementer, reviewer  |
| Shell id                  | imperative verb-object | `create-worktree`, `ticket-start`    |
| Prompt step id            | kebab-case outcome     | `workflow-report-summary`            |

**One capability → one skill directory** under `skills/`. Step contracts for
skills are thin (`skill:` + `model:`). Install links `skills/*` into agent
skill dirs.

## 3. Workflow step entries

`workflows/<schema>.yaml` `steps:` may use:

```yaml
steps:
  - create-worktree # shell (contract run:)
  - skill: explore # id defaults to skill name
  - id: design
    skill: design
    model: opus # optional; contract model wins if omitted here
  - id: design-review
    skill: design-review
    on_failure: design
  - id: one-off
    prompt: pack/charter.md # local prompt file (contract still required today)
    model: sonnet
  - ticket-start
```

Plain string ids still work and resolve via `steps/<id>/contract.yaml`.

## 4. `contract.yaml` keys

- `id` — must match the step's directory name.
- `version` — integer, bumped on behavior change.
- Exactly one of:
  - `run: script.sh` — **shell**
  - `skill: <name>` + `model: <alias>` — **skill** (name resolves via skill search path)
  - `prompt: <relpath>` + `model: <alias>` — **prompt** (path relative to the step dir; no `..`)

Optional: `state_mutating`, `default_outputs`. Any other key is ignored by
the engine.

Skill search order: `$ORCHESTRATOR_SKILLS_TEST_OVERRIDE` (tests) →
`<repo>/skills` → engine checkout `skills/` → `~/.claude/skills` →
`~/.codex/skills` → `~/.agents/skills` → Pi skills dir.

## 5. Step protocol

**Shell steps** — exit 0 success; last stdout line JSON object → outputs.
`state_mutating` caveat unchanged (see prior docs).

**Skill / prompt steps** — instruction is the charter body (frontmatter
stripped for `SKILL.md`). Agent must end with `COMPLETION:` YAML when run
inside the orchestrator. Standalone skill invocation may omit it.

**Exit codes**: `1` complete, `2` blocked, `3` error.

## 6. Aliases

Packs speak in capability-tier aliases (`opus`, `sonnet`, `composer`, …),
never concrete model ids. Unroutable alias → exit 4.

## 7. Protocol versioning

`pack.yaml` declares `protocol: 1`. Bump only on breaking changes to contract
keys or step protocol semantics.
