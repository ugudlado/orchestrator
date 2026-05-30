---
name: workflow-creator
description: >-
  Create orchestrator workflows from any user goal. Web-searches how similar
  real-world workflows are structured, breaks them into steps, classifies each step
  as deterministic (shell script) or probabilistic (agent prompt), then scaffolds
  config/workflows and config/steps. Use when the user asks to create a workflow,
  design a pipeline, build a multi-step process (course creator, content, dev,
  ops, research), or says workflow-creator or custom orchestrator schema.
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
user ask → web search → steps → deterministic? → shell : prompt → workflow YAML + step contracts
```

A workflow is `config/workflows/<schema>.yaml` with an ordered `steps:` list. Each
step is `config/steps/<step-id>/` with a contract + either `script.sh` or `prompt.md`.

Adding a workflow file automatically adds `orchestrator <schema> <id>` (ORC-108).

---

## Process

### 1. Parse the ask

From `$ARGUMENTS` or conversation, extract:

- Goal and deliverable (what “done” looks like)
- Schema name (kebab-case → CLI subcommand)
- Artifact root (default: `spec/changes/<slug>/` or user preference)
- Optional gates (review, sign-off, export)

Ask only if schema name or deliverable is unclear.

### 2. Web search the workflow

Search for how practitioners structure this process — frameworks, phase names,
handoffs, typical artifacts. Prefer industry sources over generic AI blog posts.

Example queries (adapt):

- `"<domain> workflow phases steps best practices"`
- `"ADDIE instructional design process"` (course)
- `"editorial workflow draft review publish"`

Capture: phase order, deliverables per phase, where review happens, what is usually
automated vs judgment-heavy.

Cite 1–3 sources in the proposal.

### 3. Break into steps

Decompose the researched workflow into **atomic steps** — one clear outcome each:

- Verb-led kebab-case ids (`research-topic`, `package-export`, `archive-run`)
- Explicit inputs/outputs per step
- One checkpoint per step (easier resume, clearer metrics)

Avoid mega-steps (“research and write everything”). Split at natural artifact
boundaries.

### 4. Classify each step: deterministic vs probabilistic

For **every** proposed step, decide route:

| Route | When | Orchestrator shape |
|-------|------|-------------------|
| **Shell (deterministic)** | Same inputs → same action every time; no judgment | `kind: script`, `run: script.sh` |
| **Prompt (probabilistic)** | Judgment, synthesis, creativity, research, review | `agent: <name>` + `prompt.md` |

**Deterministic — use shell:**

- Move/archive/copy files; mkdir; templated paths
- Format conversion with fixed rules (pandoc flags, zip bundle)
- Run linter/test/build; parse exit code
- Ticket/backlog status sync; webhook POST with fixed payload
- Metrics rollup, checksum, deterministic JSON transform
- Git operations with fixed args (commit message template)

**Probabilistic — use prompt:**

- Research, summarize, compare alternatives
- Write or edit prose/code/design from requirements
- Review against rubric; critique quality
- Plan, outline, prioritize with tradeoffs
- Anything where reasonable people would differ on output

When unsure: if you can write a bash script **without** calling an LLM, it is
deterministic. If the step needs “read context and decide,” it is probabilistic.

See [references/classification.md](references/classification.md) for edge cases.

**Reuse:** If an existing step under `config/steps/` already matches (same I/O and
behavior), reference it in the workflow instead of duplicating. Check with:

```bash
ls config/steps/
```

### 5. Propose and refine

Present a table **before** writing files:

| # | Step id | Route | Rationale | Inputs | Outputs |
|---|---------|-------|-----------|--------|---------|
| 1 | … | shell / prompt | why | … | … |

Include researched framework name and sources.

Ask the user to adjust or confirm. Apply edits; then scaffold.

### 6. Scaffold

**Workflow:**

```yaml
# <Title> — <purpose>
# CLI: orchestrator <schema> <slug>
steps:
  - step-one
  - step-two
```

**Shell step** (`config/steps/<id>/`):

```yaml
# contract.yaml
id: <id>
version: 1
kind: script
run: script.sh
```

```bash
# script.sh — set -uo pipefail; read env; emit JSON on stdout
printf '%s\n' '{"status": "completed", "outputs": {...}}'
```

Follow `config/steps/ticket-start/script.sh` for env vars and JSON shape.

**Prompt step** (`config/steps/<id>/`):

```yaml
# contract.yaml
id: <id>
version: 1
agent: discoverer   # or architect, reviewer, ideator — closest platform agent
```

```markdown
<!-- prompt.md — ORC-104: all instruction here -->
# <Step title>

## Inputs
## Outputs
## Instructions
## Verify

Return COMPLETION with status + output paths.
```

Pick `agent:` for routing/model config in `config/agents.yaml`; put step-specific
instructions in `prompt.md`. Add a new skill under `skills/<agent>/` only when the
same agent role is reused across many workflows.

**Optional:** `skills/<schema>/SKILL.md` entry point → `orchestrator <schema> <slug>`.

### 7. Validate

```bash
bash skills/workflow-creator/scripts/validate-workflow.sh <schema>
pytest config/steps/__tests__/test_all_contracts_have_agent_or_run.py -q
orchestrator doctor
```

### 8. Hand off

```bash
orchestrator <schema> <slug>
```

---

## Orchestrator rules

- Flat `steps:` list — order is execution order (single `main` phase)
- No LLM tool names in workflow YAML or contracts (agent-agnostic)
- Script steps: exit 0 + JSON stdout; driver records via `orchestrator done`
- Agent steps: driver spawns agent; COMPLETION block required
- Repo override: `.orchestrator/workflows/<schema>.yaml` replaces global file

---

## Anti-patterns

- Skipping web search and inventing phases from memory
- Putting probabilistic work in shell (heredoc prompts to curl LLM APIs)
- Putting deterministic transforms in agent steps (wastes tokens, drifts)
- Scaffolding before user sees the classified step table
- One step that mixes unrelated deterministic + probabilistic work

---

## Reference

- [references/classification.md](references/classification.md) — deterministic vs probabilistic edge cases
