---
name: workflow-creator
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
requirement is a git repo containing `spec/project.yaml` — not a branch.

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
`skills/` first, then the installing repo's real search path — but it only
installs into repos where that skill actually resolves. Ship pack-local when
the pack must be self-contained for arbitrary repos, which is still the safe
default for distribution. Name it `<schema>-learn`, put it in the pack's
`skills/`, and copy
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

**Reuse:** scan `skills/` and `$ORCH_CONFIG/steps/` before creating. Never clone a
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

**Prompt step** — thin `config/steps/<id>/contract.yaml`:

```yaml
id: <id>
version: 1
prompt: <id>/SKILL.md # or <id>/prompt.md for a plain (non-skill) charter
model: sonnet
```

The charter file lives in the prompt directory `skills/<id>/`. One capability
is one directory, and everything the runtime and the optimizer need about it lives
inside:

```
skills/<id>/
  SKILL.md              # charter with YAML frontmatter (preferred — emit this)
  metrics.md            # prose rubrics an LLM judge scores against
  scenarios/
    train.jsonl         # optimizer training split; the learn step appends here
    dev.jsonl           # held out for validation
    holdout.jsonl       # held out for validation
```

Write `SKILL.md`, not `prompt.md` — the latter exists only for charters with no
metadata, and if both are present `SKILL.md` wins. Emit `metrics.md` and all
three `scenarios/` splits: a skill with no eval bank still runs, it just has no
feedback signal. There is no per-skill `pack.yaml`; the only `pack.yaml` is the
one at the pack root.

**Frontmatter.** `name` and `description` are the skill's identity;
`user-invocable: true` exposes it standalone; `extends` names the base role the
charter inherits:

```yaml
---
name: <id>
description: "<what it produces>. Use when ..."
user-invocable: true
extends: git+git@github.com:ugudlado/prompt-packs.git@302b87dcc7c8b6a83d249194f3e47e98d3214794#operator
---
```

The shape is `git+<git-url>@<ref>#<role>`. The ref **must be a full commit
SHA** — never a tag or branch. The optimizer's clone cache never refreshes once
populated, so a moved tag keeps serving the base it first fetched while still
looking pinned; a SHA is the only ref that cannot drift out from under you.

Use the neutral **`operator`** base by default for non-coding roles. The
coding-specific bases (`architect`, `developer`, `reviewer`, `explorer`) carry
assumptions about repos, diffs, and tests that mislead a content or ops charter.

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
- Charters live in prompt directories under `skills/`; step contracts are thin (`prompt: <path>.md` + `model:`)
- Never write `skill:` in a contract — it is removed and raises a contract error
- `extends` pins a full commit SHA, never a tag or branch
- Every workflow starts with `intake-<schema>` and preferably ends with a pack-local `<schema>-learn`
