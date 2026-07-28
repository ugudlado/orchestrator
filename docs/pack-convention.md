# Config Pack Convention

**Protocol version: 1**

A config pack is a directory of workflow schemas + step directories that the
orchestrator engine can dispatch. This doc is the contract a pack author
targets — it doesn't require reading engine source. It documents behavior
that already exists in code; this doc is the authoring surface, dispatch is
the enforcement.

**Start with `workflow-creator`.** It is the authoring entry point: run it
with a goal and it scaffolds the workflow schema, classifies each step as
shell or prompt, and writes the step directories. This doc is the reference
for what it emits — read it to understand, adjust, or hand-author what the
skill scaffolds, not as a substitute for running it.

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

### Inside a prompt directory

One capability is one directory. Everything the runtime and the optimizer need
about that capability lives in it:

```
skills/<name>/
  SKILL.md              # charter, with YAML frontmatter (preferred)
  prompt.md             # plain charter, no frontmatter (used only if no SKILL.md)
  learnings.md          # optional; appended to the instruction at dispatch
  metrics.md            # optional; prose rubrics an LLM judge scores against
  scenarios/
    train.jsonl         # optimizer training split; learn appends here
    dev.jsonl           # held out for validation
    holdout.jsonl       # held out for validation
  runs/
    results.jsonl       # optimizer run ledger, one row per scored run
```

Write **either** `SKILL.md` or `prompt.md`. `SKILL.md` is preferred and is what
`workflow-creator` emits; `prompt.md` exists for charters that carry no
metadata. If both are present `SKILL.md` wins and `prompt.md` is ignored.
Frontmatter is stripped from `SKILL.md` before dispatch; `prompt.md` is passed
through verbatim, so it must not carry a frontmatter block.

`scenarios/` and `metrics.md` are the eval bank, colocated on purpose: the
directory the executor dispatches is the same directory `prompt-eval --pack`
consumes. A skill with no bank still runs — it just has no feedback signal.

**Frontmatter (`SKILL.md`).** `name` and `description` are the skill's identity;
`user-invocable: true` exposes it as a standalone skill. `extends` names the
base role this charter inherits from:

```yaml
---
name: design
description: "Produce design.md and tasks.yaml from discovery. Use when ..."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#architect
---
```

The shape is `git+<git-url>@<ref>#<role>`. The ref **must be a full commit
SHA** — never a tag or branch name. The optimizer's clone cache never
refreshes once populated, so a moved tag keeps serving the base it first
fetched while still looking pinned. A SHA is the only ref that can't drift out
from under you.

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

### `create-worktree` is optional

The shipped coding workflows open with `create-worktree`, but nothing in the
engine requires it. A workflow may start with any step — an intake step, or
the first prompt step — and run to completion with no branch and no worktree.

What changes is where artifacts land. Steps write under
`$ORCHESTRATOR_WORKTREE_ARTIFACT_DIR`, which the engine derives per run:

| `worktree_path` in state  | `ORCHESTRATOR_WORKTREE_ARTIFACT_DIR` |
| ------------------------- | ------------------------------------ |
| set (create-worktree ran) | `<worktree_path>/spec/changes`       |
| absent (omitted)          | `$REPO_ROOT/spec/changes`            |

Both are the same relative path under a different base, so a step that writes
to `$ORCHESTRATOR_WORKTREE_ARTIFACT_DIR/<change_id>/` works either way. Do not
hardcode a worktree path. The engine's only hard requirement is that the
project is a git repo containing `spec/project.yaml` — a branch is not part of
that.

## 4. `contract.yaml` keys

- `id` — must match the step's directory name.
- `version` — integer, bumped on behavior change.
- Exactly one of:
  - `run: script.sh` — **shell**
  - `prompt: <dir>` + `model: <alias>` — **prompt** (directory via skill search)

Optional: `state_mutating`, `default_outputs`. Any other key is ignored by
the engine, with one exception: `skill:` is **rejected** with a contract error.
It was the old way to name a charter and is gone — use `prompt: <dir>`. The
directory is resolved through the skill search dirs below, and the charter
inside it is `SKILL.md` if present, else `prompt.md`. There is no second field
for skill-vs-prompt; the directory's contents decide.

Skill search order: `$ORCHESTRATOR_SKILLS_PREPEND` (os.pathsep-separated, put
in front of the normal path) → `$ORCHESTRATOR_SKILLS_TEST_OVERRIDE` (tests,
replaces the rest) → `<repo>/skills` → engine checkout `skills/` →
`~/.claude/skills` → `~/.codex/skills` → `~/.agents/skills` → Pi skills dir.

`pack add` uses the prepend during install-time validation: the pack's own
`skills/` goes first, then the search path the installing repo will really use
at runtime. So a pack may reference a skill it doesn't ship (validation passes
where that skill resolves, fails where it doesn't), and a shipped copy always
shadows the repo's.

`<repo>` is derived from the active config root: `<repo>/.orchestrator/config`
(vendored pack) and `<repo>/config` (engine checkout) both resolve to `<repo>`.
Receipt resolution and skill search share that derivation — they must agree, or
vendored skills become unresolvable.

Agent steps get two env vars naming resolved prompt directories:

- `ORCHESTRATOR_PROMPT_DIR` — the directory this step's own charter came from.
- `ORCHESTRATOR_PROMPT_DIRS` — a JSON object mapping `step_id` → absolute
  prompt dir for **every** agent step in the workflow, so a step can write
  beside another step's charter.

`learn` uses the second one: it looks the target step up in the map and
appends the scenario to `<dir>/scenarios/train.jsonl`. Train split only — it
never writes `dev.jsonl` or `holdout.jsonl`, which stay held out for
validation. A step id absent from the map has no prompt directory to write
beside and is skipped. There is no blessed `pack/` location; colocation beside
the charter is the whole rule.

### Installing

`orchestrator pack add <path|git-url>` is the install step: point it at the
pack directory (or a git URL) and it copies workflows, steps, and skills into
the target repo. Run it in a repo whose config root is untracked — it refuses
tracked git config roots by design.

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
