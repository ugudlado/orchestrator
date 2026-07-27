---
name: workflow-creator
description: >-
  Create orchestrator workflows from any user goal. Web-searches how similar
  real-world workflows are structured, breaks them into steps, classifies each step
  as shell (script) or skill (SKILL.md), then scaffolds config/workflows and
  config/steps. Use when the user asks to create a workflow, design a pipeline,
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
user ask → scan existing → improve or create → web search → I/O contract → steps → shell|skill → scaffold → hand-off
```

---

## Process

### 1. Parse the ask

Extract: goal/deliverable, schema name (kebab-case), artifact root (default `spec/changes/<slug>/`). Ask only if schema name or deliverable is unclear.

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
- Are there gaps (missing phases, wrong classifications, no intake step, no `workflow-improve`)?
- Does its I/O contract match what the user needs?

Present a gap analysis:

```
Existing workflow: <schema>
Description: <its description>

Gaps for your use case:
  - Missing: <step or phase>
  - Misclassified: <step> should be shell/prompt because <reason>
  - No intake step — <id> is never resolved to context
  - No workflow-improve step

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

- Files written to `spec/changes/<slug>/` (reports, packages, configs)
- External state changes (ticket closed, record updated, email sent, deployment live)

Write this as a brief contract block — it drives the intake step design and sets expectations for the user.

### 3. Web search the workflow

Search how practitioners actually structure this process — phase names, handoffs, typical artifacts. Prefer industry sources over generic AI posts. Cite 1–3 sources in the proposal.

### 4. Break into atomic steps — always starting with intake

The **first step is always an intake shell step** (`intake-<schema>`). It translates the `<id>` the user passes into structured context files that all downstream steps read from:

```bash
# intake-<schema>/script.sh — reads $CHANGE_ID, writes context to $CHANGE_DIR
# Examples:
#   Pull Linear ticket → write spec/changes/<slug>/ticket.md
#   Fetch CTMS record  → write spec/changes/<slug>/protocol.json
#   Validate PDF path  → copy to spec/changes/<slug>/input.pdf
#   Parse config file  → write spec/changes/<slug>/config.yaml
```

The **last step should reuse `learn`** when the workflow should reflect and propose improvements (`ls "$ORCH_CONFIG/steps/learn/"`). Prefer the shared skill name so every workflow feeds the same improvement loop.

Middle steps: one clear outcome each, skill-noun or shell verb-object ids, split at natural artifact boundaries.

### 5. Classify each step — shell | skill | prompt

The orchestrator dispatches **three** kinds:

**Shell (deterministic)** — `run: script.sh`

**Skill (reusable)** — `model:` + `skill: <name>`; charter in `skills/<name>/SKILL.md`

**Prompt (local)** — `model:` + `prompt: <file>`; charter file under the step dir

When unsure: reusable outside workflows → skill; one-off → prompt; no LLM → shell.
See [references/classification.md](references/classification.md).

**Reuse:** scan `skills/` and `$ORCH_CONFIG/steps/` before creating. Never clone a
skill charter into `config/steps/`.

### 6. Propose and confirm — BEFORE writing any files

| #   | Step id         | Route        | Agent role (if skill) | Rationale       | Inputs     | Outputs |
| --- | --------------- | ------------ | --------------------- | --------------- | ---------- | ------- |
| 1   | intake-<schema> | shell        | —                     | Translates id   | $CHANGE_ID | …       |
| …   | …               | skill/prompt | <id>-er               | …               | …          | …       |
| N   | learn           | skill        | learner               | Reflects on run | …          | …       |

Workflow entries for skills:

```yaml
steps:
  - skill: explore
  - id: design-review
    skill: design-review
    on_failure: design
```

Apply edits; then scaffold.

### 7. Scaffold

**Workflow** (`$ORCH_CONFIG/workflows/<schema>.yaml`):

```yaml
description: >-
  <One sentence intent>
steps:
  - intake-<schema>
  - skill: <capability>
  - learn
```

**Shell step** — `contract.yaml` with `run: script.sh` + `script.sh`.

**Skill step** — thin `config/steps/<id>/contract.yaml`:

```yaml
id: <id>
version: 1
skill: <id>
model: sonnet
```

Charter in `skills/<id>/SKILL.md` (frontmatter + body). Optional optimizer pack
files (`pack.yaml`, `metrics.md`, `scenarios/`) live beside the skill.

**Prompt step** — `contract.yaml` with `prompt: <file>` + `model:`; markdown file
under the step dir (any name).

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
  N. learn reflects on the run and logs improvement proposals

Output artifacts:
  spec/changes/<id>/<key-artifact>
  spec/changes/<id>/workflow-improvements.md
```

---

## Rules

- Flat `steps:` list — order is execution order
- No LLM tool names in YAML or contracts (agent-agnostic)
- Never scaffold before the user confirms the I/O contract and step table
- Classify **shell | skill | prompt**
- Never put probabilistic work in shell or deterministic work in skill/prompt
- Skills live under `skills/`; step contracts for skills are thin (`skill:` + `model:`)
- Every workflow starts with `intake-<schema>` and preferably ends with `learn`
