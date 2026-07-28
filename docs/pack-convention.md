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
    contract.yaml         # id, version, run|prompt (+ model)
    script.sh             # shell steps only
  steps/lib/              # optional, shared shell helpers (this pack only)
  skills/<name>/          # vendored prompt dirs (SKILL.md or prompt.md + eval)
    SKILL.md              # charter (frontmatter may carry extends)
    metrics.md / scenarios/  # optional eval banks (colocated)
```

`skills/` may contain **only skill directories** — a stray file directly under
it (e.g. `skills/README.md`) fails validation, since vendoring would drop it
into `<repo>/skills/` outside conflict detection.

Prompt directories also resolve from the repo/engine skill search path when not
shipped inside a pack.

## 2. Dispatch kinds (shell | prompt)

The orchestrator executes **two** kinds of steps:

| Kind       | Contract                   | Charter source                                     | Role                   |
| ---------- | -------------------------- | -------------------------------------------------- | ---------------------- |
| **Shell**  | `run: script.sh`           | —                                                  | Deterministic plumbing |
| **Prompt** | `model:` + `prompt: <dir>` | `SKILL.md` if present else `prompt.md` in that dir | LLM procedure          |

`prompt:` names a **directory** resolved via skill search dirs (not a file under
the step dir). The same directory is what `prompt-eval --pack` takes — runtime
and optimizer share one unit. Skill vs plain-prompt is an optimizer concern
(directory contents), not an executor field.

### Naming

| Kind                      | Pattern                  | Examples                             |
| ------------------------- | ------------------------ | ------------------------------------ |
| Prompt dir / step id      | capability noun/verb     | `ux-critique`, `implement`, `review` |
| Agent role (mental model) | capability + `-er`/`-or` | ux-critiquer, implementer, reviewer  |
| Shell id                  | imperative verb-object   | `create-worktree`, `ticket-start`    |

**One capability → one prompt directory** under `skills/`. Step contracts for
LLM steps are thin (`prompt:` + `model:`).

## 3. Workflow step entries

`workflows/<schema>.yaml` `steps:` may use:

```yaml
steps:
  - create-worktree # shell (contract run:)
  - prompt: explore # id defaults to prompt dir name
  - id: design
    prompt: design
    model: opus # optional; contract model wins if omitted here
  - id: design-review
    prompt: design-review
    on_failure: design
  - ticket-start
```

Plain string ids still work and resolve via `steps/<id>/contract.yaml`.

## 4. `contract.yaml` keys

- `id` — must match the step's directory name.
- `version` — integer, bumped on behavior change.
- Exactly one of:
  - `run: script.sh` — **shell**
  - `prompt: <dir>` + `model: <alias>` — **prompt** (directory via skill search)

Optional: `state_mutating`, `default_outputs`. Any other key is ignored by
the engine.

Skill search order: `$ORCHESTRATOR_SKILLS_TEST_OVERRIDE` (tests) →
`<repo>/skills` → engine checkout `skills/` → `~/.claude/skills` →
`~/.codex/skills` → `~/.agents/skills` → Pi skills dir.

`<repo>` is derived from the active config root: `<repo>/.orchestrator/config`
(vendored pack) and `<repo>/config` (engine checkout) both resolve to `<repo>`.
Receipt resolution and skill search share that derivation — they must agree, or
vendored skills become unresolvable.

Agent steps export `ORCHESTRATOR_PROMPT_DIR` to the resolved prompt directory
so learn can append `scenarios/train.jsonl` by colocation.

`orchestrator pack add` vendors pack `skills/<name>/` into `<repo>/skills/<name>`
(not under `.orchestrator/config/`). Receipts record those paths as
`@repo/skills/...` so `pack remove` deletes exactly what was installed.

**Distribution note:** `install.sh` still symlinks the engine checkout's
`skills/*` into `~/.claude/skills`, `~/.codex/skills`, and Pi. That remains the
machine-global fallback when a repo has no vendored skills; per-repo
`<repo>/skills` wins in resolution order, and install.sh's global symlinks
resolve later in the search order (engine checkout `skills/`, then
`~/.claude/skills` and friends). Do not treat the two as competing —
vendoring is plug-and-play for packs; install.sh covers the engine checkout.

## 5. Step protocol

**Shell steps** — exit 0 success; last stdout line JSON object → outputs.
`state_mutating` caveat unchanged (see prior docs).

**Prompt steps** — instruction is the charter body (frontmatter stripped for
`SKILL.md`). Agent must end with `COMPLETION:` YAML when run inside the
orchestrator. Standalone skill invocation may omit it.

**Exit codes**: `1` complete, `2` blocked, `3` error.

## 6. Aliases

Packs speak in capability-tier aliases (`opus`, `sonnet`, `composer`, …),
never concrete model ids. Unroutable alias → exit 4.

## 7. Protocol versioning

`pack.yaml` declares `protocol: 1`. Bump only on breaking changes to contract
keys or step protocol semantics.
