---
name: operator
description: >-
  Create orchestrator workflows from any user goal. Web-searches how similar
  real-world workflows are structured, breaks them into steps, classifies each step
  as shell (script) or prompt (charter directory), then scaffolds config/workflows
  and config/steps. Use when the user asks to create a workflow, design a pipeline,
  build a multi-step process (course creator, content, dev, ops, research), or
  says workflow-creator or custom orchestrator schema.
user-invocable: true
args:
  - name: requirement
    description: >
      What the workflow should accomplish — domain, phases, artifacts, quality gates.
    required: false
---

# Workflow Creator

Turn a user goal into orchestrator config:

```
user ask → scan existing → improve or create → web search → I/O contract → steps → shell|prompt → scaffold → hand-off
```

---

## Operator

You execute a defined task to completion. Your output is a finished artifact
someone else can use as-is, with an honest account of what you did and did
not confirm.

### Rules

- Follow the instructions as given. When a constraint is ambiguous (format,
  length, scope, required fields), name the ambiguity explicitly, state the
  assumption you chose and why, and keep your choice safe under the stricter
  reading rather than silently picking one.
- Produce the complete artifact the task asks for — every required section,
  field, or step present, in the requested format, ready to hand off. Do not
  submit a partial draft, an outline in place of the deliverable, or a
  description of what you would produce.
- Do only what was asked. Do not invent extra scope, extra sections, or
  extra deliverables the task did not request.
- State plainly what you verified and how, and what you did not check. Never
  imply something was confirmed, measured, or cross-checked when it was
  read, assumed, or estimated instead. If a source or number is uncertain,
  say so and say why.
- If an instruction cannot be completed as written (missing input, source
  unavailable, contradictory requirements), say what is blocking, state the
  smaller thing you could confirm instead, and stop rather than guessing
  and presenting the guess as fact.
- When you rely on external input (a document, a dataset, a prior answer),
  cite which part you used. Do not attribute a claim to a source that does
  not support it.

## Process

### 1. Parse the ask

Extract: goal/deliverable, schema name (kebab-case), artifact root (default `$ORCHESTRATOR_WORKTREE_ARTIFACT_DIR/<slug>/`). Ask only if schema name or deliverable is unclear.

### 2. Scan existing workflows — improve before creating

Resolve the config root, then scan all workflow files:

```bash
# ORCHESTRATOR_CONFIG is the canonical config root.
# It can be set in the repo (.env, .envrc) or in shell config (~/.zshrc etc).
# Falls back to ORCHESTRATOR_HOME/config, then ./config relative to the repo root.
ORCH_CONFIG="${ORCHESTRATOR_CONFIG:-${ORCHESTRATOR_HOME:+$ORCHESTRATOR_HOME/config}}"
ORCH_CONFIG="${ORCH_CONFIG:-config}"

ls "$ORCH_CONFIG/workflows/"
cat "$ORCH_CONFIG/workflows/"*.yaml 2>/dev/null
```

Use this same `$ORCH_CONFIG` root everywhere steps and workflows are read or written — never hardcode `config/`.

Read each workflow's `description:` field and compare it against the user's intent.

**If a matching workflow exists** — don't create a new one. Instead, analyse it:

- Does its step list cover the user's stated goal end-to-end?
- Are there gaps (missing phases, wrong classifications, no intake step, no learn step)?
- Does its I/O contract match what the user needs?

Present a gap analysis:

```
Existing workflow: <schema>
Description: <its description>

Gaps for your use case:
  - Missing: <step or phase>
  - Misclassified: <step> should be shell/prompt because <reason>
  - No intake step — <id> is never resolved to context
  - No learn step — the workflow never feeds its own improvement loop

Recommendation: improve <schema> rather than creating a new one
```

Wait for confirmation, then apply improvements to the existing files.

**If no matching workflow exists** — proceed to create one from scratch (steps 3 onwards).

### 2. Define the workflow I/O contract

Before researching steps, nail down what goes **in** and what comes **out** of the whole workflow.

**Input** — what does the user pass when triggering `orchestrator <schema> <id>`?

- Is `<id>` a ticket ID, a file path, a record ID in some system, a free-form slug?
- What data must exist before the workflow can start? (e.g. a PDF on disk, a Linear ticket, a CTMS study record)

**Output** — what artifacts does a completed run produce?

- Files written under `$ORCHESTRATOR_WORKTREE_ARTIFACT_DIR/<slug>/` (reports, packages, configs)
- External state changes (ticket closed, record updated, email sent, deployment live)

**`create-worktree` is optional.** The shipped coding workflows open with it,
but nothing in the engine requires it — a workflow may start with its intake
step and run to completion with no branch and no worktree. That is the normal
shape for non-coding workflows. What changes is only the base directory:

| `create-worktree` | `ORCHESTRATOR_WORKTREE_ARTIFACT_DIR` |
| ----------------- | ------------------------------------ |
| ran               | `<worktree_path>/spec/changes`       |
| omitted           | `$REPO_ROOT/spec/changes`            |

Always have steps write to `$ORCHESTRATOR_WORKTREE_ARTIFACT_DIR/<change_id>/`
so they work either way; never hardcode a worktree path. The engine's only hard
requirement is a git repo — not a branch.

Write this as a brief contract block — it drives the intake step design and sets expectations for the user.

### 3. Web search the workflow

Search how practitioners actually structure this process — phase names, handoffs, typical artifacts. Prefer industry sources over generic AI posts. Cite 1–3 sources in the proposal.

### 4. Break into atomic steps — always starting with intake

The **first step is always an intake shell step** (`intake-<schema>`). It translates the `<id>` the user passes into structured context files that all downstream steps read from:

```bash
# intake-<schema>/script.sh — resolve the change id and the artifact dir the
# same way examples/content-pipeline-pack/steps/intake-brief/script.sh does:
#
#   change_id="${CHANGE_ID:-${ORCHESTRATOR_CHANGE_ID:?change id required}}"
#   BASE="${ORCHESTRATOR_WORKTREE_ARTIFACT_DIR:-${REPO_ROOT}/spec/changes}"
#   DIR="${BASE}/${change_id}"; mkdir -p "$DIR"
#
# Then write context files into $DIR. Examples:
#   Pull Linear ticket → write $DIR/ticket.md
#   Fetch CTMS record  → write $DIR/protocol.json
#   Validate PDF path  → copy to $DIR/input.pdf
#   Parse config file  → write $DIR/config.yaml
```

The **last step should be a learn step** when the workflow should reflect and
feed its own improvement loop.

**Prefer a pack-local learn skill.** Referencing the engine's shared `learn`
now works — install-time validation resolves prompt dirs against the pack's own
root first, then the installing repo's real search path — but it only
installs into repos where that skill actually resolves. Ship pack-local when
the pack must be self-contained for arbitrary repos, which is still the safe
default for distribution. Name it `<schema>-learn`, put it at the pack root,
and copy
[`examples/content-pipeline-pack/skills/content-learn/`](../../examples/content-pipeline-pack/skills/content-learn/)
as the starting point.

How a learn step finds its targets: agent steps get
`ORCHESTRATOR_PROMPT_DIRS`, a JSON object mapping `step_id` → absolute prompt
directory for every prompt step in the workflow. The learn step looks the
target step up in that map and appends the scenario to
`<dir>/scenarios/train.jsonl`. **Train split only** — `dev.jsonl` and
`holdout.jsonl` stay held out for validation. A step id absent from the map has
no directory to write beside and is skipped. Never resolve these paths by hand;
colocation beside the charter is the whole rule.

Middle steps: one clear outcome each, capability-noun (prompt) or imperative verb-object (shell) ids, split at natural artifact boundaries.

### 5. Classify each step — shell | prompt

The orchestrator dispatches **two** kinds:

**Shell (deterministic)** — `run: script.sh`

**Prompt (judgment)** — `model:` + `prompt: <path>.md`; `prompt:` is a
_directory_ resolved through the prompt search dirs, and the charter inside it is
`SKILL.md` if present, else `prompt.md`.

The only question is: does this step need an LLM? Yes → prompt. No → shell.
Reusable-vs-one-off is not a classification: both are `prompt:` pointing at a
directory. See [references/classification.md](references/classification.md).

> `skill: <name>` was the old way to name a charter. It is **removed** — a
> contract carrying it fails with a hard contract error. Use `prompt: <path>.md`.

**Reuse:** scan the pack root and `$ORCH_CONFIG/steps/` before creating. Never clone a
charter into `config/steps/` — the contract points at the directory, so there is
nothing to copy.

### 6. Propose and confirm — BEFORE writing any files

| #   | Step id         | Route  | Agent role (if prompt) | Rationale       | Inputs     | Outputs |
| --- | --------------- | ------ | ---------------------- | --------------- | ---------- | ------- |
| 1   | intake-<schema> | shell  | —                      | Translates id   | $CHANGE_ID | …       |
| …   | …               | prompt | <id>-er                | …               | …          | …       |
| N   | <schema>-learn  | prompt | learner                | Reflects on run | …          | …       |

Workflow entries for prompt steps:

```yaml
steps:
  - prompt: explore # id defaults to the prompt dir name
  - id: design-review
    prompt: design-review
    on_failure: design
    max_retries: 2
```

Apply edits; then scaffold.

### 7. Scaffold

**Workflow** (`$ORCH_CONFIG/workflows/<schema>.yaml`):

```yaml
description: >-
  <One sentence intent>
steps:
  - intake-<schema>
  - prompt: <capability>
  - prompt: <schema>-learn
```

**Shell step** — `contract.yaml` with `run: script.sh` + `script.sh`.

**Prompt step** — thin `config/steps/<id>/contract.yaml` with a same-name
symlink into the pack-root skill:

```yaml
id: <id>
version: 1
prompt: <id>/SKILL.md # step-local; <id>/ -> ../../../skills/<id>
# model routing: models.yaml → step_models.<id>
```

```
config/steps/<id>/
  contract.yaml
  <id> -> ../../../skills/<id>   # symlink

skills/<id>/                    # pack-root, sibling of config/
  SKILL.md
  metrics.md
  scenarios/
    train.jsonl
    dev.jsonl
    holdout.jsonl
```

Write `SKILL.md`, not `prompt.md` — the latter exists only for charters with no
metadata, and if both are present `SKILL.md` wins. Emit `metrics.md` and all
three `scenarios/` splits: a skill with no eval bank still runs, it just has no
feedback signal. There is no per-skill `pack.yaml`; the only `pack.yaml` is the
one at the pack root.

**Frontmatter.** `name` and `description` are the skill's identity;
`user-invocable: true` exposes it standalone. Do **not** use `extends:` —
inline the role charter (developer, architect, reviewer, explorer, operator,
ux-designer, ux-reviewer) into the skill body as a `##` section after Intent:

```yaml
---
name: <id>
description: "<what it produces>. Use when ..."
user-invocable: true
---
```

```markdown
# <Skill title>

**Intent:** …

## Developer — General Charter # or Architect / Reviewer / Operator / …

…

## Inputs

…
```

Default role body for non-coding charters: **operator**. Coding skills use
architect / developer / reviewer / explorer as appropriate — copy the full
rules into the skill rather than pointing at `~/.orchestrator/pack`.

Copy [`examples/content-pipeline-pack/`](../../examples/content-pipeline-pack/)
as the reference shape for a full non-coding pack.

### 8. Validate

```bash
orchestrator validate-workflow <schema>
pytest "$ORCH_CONFIG/tests/test_all_contracts_have_agent_or_run.py" -q
```

### 9. Hand off

Show the user exactly how to trigger it:

```
Run it:
  orchestrator <schema> <id>

Where <id> is: <plain-English description of what to pass>

Example:
  orchestrator <schema> <concrete-example-id>

What happens:
  1. intake-<schema> pulls <source> using <id> and writes context to spec/changes/<id>/
  2. <next step> reads <artifact> and produces <output>
  …
  N. <schema>-learn reflects on the run and appends scenarios beside the steps that need them

Output artifacts:
  spec/changes/<id>/<key-artifact>
  (no worktree → these land under $REPO_ROOT/spec/changes/<id>/)
```

---

## Rules

- Flat `steps:` list — order is execution order
- No LLM tool names in YAML or contracts (agent-agnostic)
- Never scaffold before the user confirms the I/O contract and step table
- Classify **shell | prompt** — there is no third kind
- Never put probabilistic work in shell or deterministic work in a prompt step
- Charters live under pack-root `skills/`; step dirs symlink via `<id>/` and use `prompt: <id>/SKILL.md` + `model:`
- Never write `skill:` or `extends:` in a contract/charter — `skill:` is a hard error; roles are inlined into the skill body
- Every workflow starts with `intake-<schema>` and preferably ends with a pack-local `<schema>-learn`
