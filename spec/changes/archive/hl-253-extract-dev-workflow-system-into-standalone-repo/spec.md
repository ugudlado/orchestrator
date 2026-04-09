---
feature-id: HL-253
linear-ticket: HL-253
---

# Specification: Extract Dev Workflow System into Standalone Repo

## Motivation

The dev workflow system (agents, skills, step contracts, schemas, templates, scripts) currently lives inside `~/code/shell`, a dotfiles repo. This creates tight coupling between workflow infrastructure and shell/dotfiles management. It prevents using the workflow engine on machines or with tools that don't share that dotfiles repo.

Extracting everything workflow-related into `~/code/orchestrator` makes it a self-contained, LLM-agnostic workflow engine, installable independently. This is the foundational move that enables future multi-tool support (Cursor, Codex CLI, Gemini CLI) and decouples workflow evolution from dotfiles maintenance.

## What Changes

1. **Directory creation** -- orchestrator repo gets `agents/`, `skills/`, `config/workflows/`, `config/steps/`, `config/templates/`, `config/scripts/`
2. **File migration** -- 11 agent files, 21 skill directories, 36 step contracts (35 YAML + CONVENTIONS.md), 5 schema YAMLs, 5 template directories, 1 script, 1 grammar file copied from shell repo to orchestrator
3. **Environment variable rename** -- all 47 `SPEC_HOME` references across 16 files become `WORKFLOW_HOME`
4. **Path structure update** -- `$SPEC_HOME/schemas/` becomes `$WORKFLOW_HOME/config/workflows/`, `$SPEC_HOME/steps/` becomes `$WORKFLOW_HOME/config/steps/`, `$SPEC_HOME/templates/` becomes `$WORKFLOW_HOME/config/templates/`
5. **New install.sh** -- exports `WORKFLOW_HOME=~/code/orchestrator` in shell profile, symlinks `agents/` and `skills/` to `~/.claude/`
6. **New guidelines.yaml** -- semantic workflow selection replacing hardcoded keyword matching
7. **Skill rename** -- `/develop` becomes `/orchestrate` (primary entry point); `/develop` kept as delegation alias

## Requirements

### Functional

**FR-1: Directory structure**
Create `agents/`, `skills/`, `config/workflows/`, `config/steps/`, `config/templates/`, `config/scripts/` in the orchestrator repo root.

**FR-2: Agent file migration**
Copy all 11 agent markdown files from `~/code/shell/src/claude/agents/` to `~/code/orchestrator/agents/`. Files: architect.md, debugger.md, developer.md, discoverer.md, haiku-agent.md, humanizer.md, ideator.md, reviewer.md, sonnet-agent.md, ux-reviewer.md, workflow-improver.md.

**FR-3: Skill directory migration**
Copy 21 skill directories from `~/code/shell/src/claude/skills/` to `~/code/orchestrator/skills/`. All directories except `linear/`, which stays in the shell repo.

**FR-4: Step contract migration**
Copy 35 step contract YAML files and CONVENTIONS.md from `~/code/shell/src/spec/steps/` to `~/code/orchestrator/config/steps/`.

**FR-5: Schema migration**
Copy 5 schema YAML files (feature.yaml, bugfix.yaml, chore.yaml, spike.yaml, bootstrap.yaml) from `~/code/shell/src/spec/schemas/` to `~/code/orchestrator/config/workflows/`.

**FR-6: Template migration**
Copy 5 template directories (feature, bugfix, chore, spike, bootstrap) from `~/code/shell/src/spec/templates/` to `~/code/orchestrator/config/templates/`.

**FR-7: Script migration**
Copy `compute-swe-metrics.sh` from `~/code/shell/src/spec/scripts/` to `~/code/orchestrator/config/scripts/`.

**FR-8: Grammar migration**
Copy `grammar.yaml` from `~/code/shell/src/spec/` to `~/code/orchestrator/config/grammar.yaml`.

**FR-9: SPEC_HOME to WORKFLOW_HOME rename**
Replace all `SPEC_HOME` references with `WORKFLOW_HOME` across all copied files. All 47 occurrences in 16 files must be updated.

**FR-10: Schema path updates**
Update internal path references in schemas and step contracts:
- `$SPEC_HOME/schemas/` or `schemas/` context references become `$WORKFLOW_HOME/config/workflows/`
- `$SPEC_HOME/steps/` becomes `$WORKFLOW_HOME/config/steps/`
- `$SPEC_HOME/templates/` becomes `$WORKFLOW_HOME/config/templates/`

**FR-11: install.sh**
Create `install.sh` in orchestrator repo root that:
- Exports `WORKFLOW_HOME=~/code/orchestrator` into the user's shell profile (~/.zshrc or ~/.bashrc)
- Symlinks `~/code/orchestrator/agents` to `~/.claude/agents`
- Symlinks `~/code/orchestrator/skills` to `~/.claude/skills`
- Is idempotent (safe to run multiple times)

**FR-12: guidelines.yaml**
Create `~/code/orchestrator/config/guidelines.yaml` with semantic descriptions for each workflow schema (feature, bugfix, chore, spike, bootstrap). Format: each schema name maps to a natural-language description of when to use it. The LLM selects the best match by semantic similarity.

**FR-13: /orchestrate skill**
Create `~/code/orchestrator/skills/orchestrate/` as the primary workflow entry point. This is a renamed copy of `/develop` with `SPEC_HOME` replaced by `WORKFLOW_HOME` and path references updated.

**FR-14: /develop alias**
Update `~/code/orchestrator/skills/develop/SKILL.md` to delegate to `/orchestrate` rather than containing the full workflow logic. This preserves backward compatibility for existing muscle memory.

### Non-Functional

**NFR-1: No shell repo changes**
This ticket does not modify or delete any files in `~/code/shell`. Source files remain in place for a separate cleanup ticket.

**NFR-2: Idempotent install**
Running `install.sh` multiple times produces the same result without errors or duplicated config entries.

**NFR-3: Zero-friction upgrade path**
After running `install.sh`, invoking `/orchestrate` or `/develop` must work with no additional setup steps.

**NFR-4: Git-tracked source of truth**
All workflow files live in `~/code/orchestrator` and are git-tracked. Symlinks point into the repo, not the other way around.

## Architecture

The architecture is a direct filesystem layout with symlink-based installation:

```
~/code/orchestrator/                    # WORKFLOW_HOME
├── agents/                             # 11 agent .md files
├── skills/                             # 21 skill directories + orchestrate/
│   ├── orchestrate/SKILL.md            # Primary entry point (renamed develop)
│   ├── develop/SKILL.md                # Alias → delegates to /orchestrate
│   ├── autopilot/SKILL.md
│   └── ...
├── config/
│   ├── workflows/                      # 5 schema YAMLs (was schemas/)
│   ├── steps/                          # 35 step YAMLs + CONVENTIONS.md
│   ├── templates/                      # 5 template dirs
│   ├── scripts/                        # compute-swe-metrics.sh
│   ├── grammar.yaml
│   └── guidelines.yaml                 # Semantic workflow selection
├── install.sh                          # Wires symlinks + WORKFLOW_HOME
├── Makefile
└── spec/project.yaml
```

**Runtime resolution:** Skills read `WORKFLOW_HOME` (defaulting to `$HOME/.config/spec` during transition, but install.sh sets the canonical value). Schemas reference `$WORKFLOW_HOME/config/steps/` and `$WORKFLOW_HOME/config/templates/<schema>/` for step and template resolution.

**Installation wiring:**
```
~/.claude/agents → ~/code/orchestrator/agents
~/.claude/skills → ~/code/orchestrator/skills
WORKFLOW_HOME=~/code/orchestrator (in shell profile)
```

## Test Strategy

N/A -- this is a config/YAML migration with no runtime code. Verification is file-existence checks, grep for stale references, and manual invocation of `/orchestrate`.

## Acceptance Criteria

**AC-1: WORKFLOW_HOME resolves correctly** [traces: UC-1, UC-5]
Running `echo $WORKFLOW_HOME` after sourcing shell profile returns `$HOME/code/orchestrator`. The directory exists and contains `config/workflows/feature.yaml`.

**AC-2: All files present in correct locations** [traces: UC-1, UC-3, UC-4]
- `~/code/orchestrator/agents/` contains exactly 11 .md files
- `~/code/orchestrator/skills/` contains 22 directories (21 migrated + orchestrate)
- `~/code/orchestrator/config/workflows/` contains 5 .yaml files
- `~/code/orchestrator/config/steps/` contains 35 .yaml files + CONVENTIONS.md
- `~/code/orchestrator/config/templates/` contains 5 directories
- `~/code/orchestrator/config/scripts/compute-swe-metrics.sh` exists
- `~/code/orchestrator/config/grammar.yaml` exists

**AC-3: install.sh works on a fresh setup** [traces: UC-2]
Running `make setup` (or `./install.sh`) from `~/code/orchestrator`:
- Creates symlink `~/.claude/agents` pointing to `~/code/orchestrator/agents`
- Creates symlink `~/.claude/skills` pointing to `~/code/orchestrator/skills`
- Adds `export WORKFLOW_HOME=~/code/orchestrator` to `~/.zshrc` (idempotent — no duplicate lines on re-run)
- After `source ~/.zshrc`, `echo $WORKFLOW_HOME` returns the repo path

**AC-4: /orchestrate skill works end-to-end** [traces: UC-1]
`~/code/orchestrator/skills/orchestrate/SKILL.md` exists, references `WORKFLOW_HOME` (not `SPEC_HOME`), and resolves `$WORKFLOW_HOME/config/workflows/` for schema loading.

**AC-5: /develop alias works** [traces: UC-E3]
`~/code/orchestrator/skills/develop/SKILL.md` exists and delegates to `/orchestrate`. It does not contain the full workflow logic inline.

**AC-6: guidelines.yaml present and correct format** [traces: UC-1]
`~/code/orchestrator/config/guidelines.yaml` exists, contains entries for all 5 schemas (feature, bugfix, chore, spike, bootstrap), each with a natural-language description string.

**AC-7: No stale SPEC_HOME references** [traces: UC-E2]
`grep -r SPEC_HOME ~/code/orchestrator/` returns zero matches (excluding .git/ and spec/project.yaml's `$SPEC_CHANGES_DIR` which is a different variable).

**AC-8: Schema path references updated** [traces: UC-1, UC-3]
All schema files in `config/workflows/` reference `$WORKFLOW_HOME/config/steps/` and `$WORKFLOW_HOME/config/templates/` (not the old `$SPEC_HOME/steps/` or `$SPEC_HOME/templates/` paths).

**AC-9: WORKFLOW_HOME fallback on missing env var** [traces: UC-E1]
Skills that reference `WORKFLOW_HOME` include a default fallback: `WORKFLOW_HOME=${WORKFLOW_HOME:-$HOME/.config/spec}`.

**AC-10: make doctor passes** [traces: UC-2]
Running `make doctor` in orchestrator repo shows all green checks for config/workflows, config/steps, agents/, skills/, install.sh.

## Alternatives Considered

1. **Symlink layer (WORKFLOW_HOME = ~/.config/workflow)** -- install.sh would create `~/.config/workflow/` with symlinks into the repo. Rejected: adds indirection without benefit. Direct repo path is simpler.

2. **Monorepo approach (keep in shell repo, add exports)** -- workflow files stay in shell but are exported via install.sh. Rejected: doesn't decouple repos, blocks future multi-tool support.

3. **git submodule** -- orchestrator as a submodule of shell. Rejected: adds git complexity, doesn't simplify independent installation.

## Impact

- **Shell repo:** No changes in this ticket. Source files remain until separate cleanup.
- **User workflow:** After running `install.sh`, `/orchestrate` replaces `/develop` as the canonical command. `/develop` continues to work as alias.
- **CI/automation:** `WORKFLOW_HOME` must be set in CI environment for `/autopilot` to work.

## Decisions

| ID | Decision | Rationale |
|---|---|---|
| KD-1 | WORKFLOW_HOME = ~/code/orchestrator | Direct repo path; no symlink indirection needed |
| KD-2 | guidelines.yaml uses semantic descriptions | LLM picks best schema match by semantic similarity, not keyword lists |
| KD-3 | Hard-cut from SPEC_HOME (no transition) | Single user; install.sh sets WORKFLOW_HOME immediately |
| KD-4 | linear skill excluded from migration | Depends on shell-specific config (~/.config/linear/) |
| KD-5 | /develop kept as alias, not deleted | Backward compatibility for muscle memory |
