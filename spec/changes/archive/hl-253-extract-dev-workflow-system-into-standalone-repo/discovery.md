---
feature-id: HL-253
linear-ticket: HL-253
---

# Discovery Brief: Extract Dev Workflow System into Standalone Repo

## Feature Summary

The dev workflow system — agents, skills, step contracts, schemas, templates, and scripts — currently lives inside `~/code/shell`, a dotfiles repo. This couples workflow infrastructure to shell/dotfiles management and prevents use on machines or with tools that don't share that dotfiles repo. The goal is to extract everything workflow-related into `~/code/orchestrator`, making it a self-contained, LLM-agnostic workflow engine installable independently of any dotfiles tooling.

The orchestrator repo already exists and has been bootstrapped with `spec/project.yaml`, a Makefile, and AGENTS.md. The directory structure defined in project.yaml (`agents/`, `skills/`, `config/workflows/`, `config/steps/`) does not yet exist — it needs to be created and populated.

---

## Personas & Actors

**Mahesh (primary user):** Hands-on developer who runs `/develop` (soon `/orchestrate`) workflows daily. Expects zero friction — skill resolution, schema loading, step execution all happen transparently. Upgrades workflows by editing files in `~/code/orchestrator`.

**CI / automation runner:** Executes `/autopilot` headlessly. Needs `WORKFLOW_HOME` to resolve correctly without interactive prompts.

**Future non-Claude-Code user:** Uses Cursor, Codex CLI, Gemini CLI, or Windsurf. Needs install.sh to wire the same skills into the target tool's conventions without manual copy-paste.

---

## Use Cases

### Happy Path

**UC-1: Developer runs /orchestrate on a new feature**
A developer invokes `/orchestrate "add login page"`. The skill reads `WORKFLOW_HOME` (pointing to `~/code/orchestrator`), loads `config/workflows/feature.yaml`, resolves steps from `config/steps/`, and executes the spec-first workflow as today. No path changes visible to the user.

**UC-2: Developer sets up orchestrator on a new machine**
Developer clones `~/code/orchestrator`, runs `./install.sh`, which symlinks `agents/` → `~/.claude/agents`, `skills/` → `~/.claude/skills`, and `config/` subdirs → `~/.config/workflow/` (or equivalent). Sets `WORKFLOW_HOME` in shell profile. Running `/orchestrate` works immediately.

**UC-3: Workflow-improver updates a step contract**
Developer invokes `/workflow-improve`. The workflow-improver agent resolves `$WORKFLOW_HOME/config/steps/` and edits the YAML contract. On next run, the change is picked up automatically because the skill reads `$WORKFLOW_HOME` at runtime.

**UC-4: learn cycle routes a learned rule**
After completing a feature, `/learn` spawns the workflow-improver, which writes updated rules to `$WORKFLOW_HOME/config/steps/<step>.yaml`. The file is in `~/code/orchestrator` and therefore git-tracked in the right repo.

**UC-5: /autopilot runs headlessly**
The autopilot skill initializes `WORKFLOW_HOME` from the environment, verifies `$WORKFLOW_HOME/config/workflows/*.yaml` exist, and proceeds without any interactive prompts or path resolution errors.

### Error & Edge Cases

**UC-E1: WORKFLOW_HOME not set**
Skills fall back to `$HOME/.config/workflow` (or another agreed default). If that directory doesn't exist, the skill should emit a clear error: "WORKFLOW_HOME not set and default path does not exist. Run install.sh from ~/code/orchestrator."

**UC-E2: Partial migration — SPEC_HOME still set**
During the transition period, some installs may still have `SPEC_HOME` pointing to `~/.config/spec` (backed by shell repo symlinks). Skills that still reference `SPEC_HOME` will resolve correctly on those machines but fail on clean installs. Migration must update all 47 SPEC_HOME occurrences (23 skills, 3 agents, 11 steps, 10 schemas) to `WORKFLOW_HOME`.

**UC-E3: /develop invoked after rename**
If `/develop` is removed before an alias is in place, existing users get "command not found". The `/develop` skill must remain (as alias or full copy) until users are migrated. [ASSUMPTION: alias approach — `/develop` SKILL.md simply delegates to `/orchestrate`.]

**UC-E4: Worktree path divergence**
The worktree is at `~/code/feature_worktrees/hl-253-...`, not `~/code/orchestrator`. The install.sh and directory creation work must target `~/code/orchestrator` (the main repo), not the worktree, since worktrees share the git object store but not the working tree path.

---

## Scope

### In Scope

- Create directory structure in orchestrator: `agents/`, `skills/`, `config/workflows/`, `config/steps/`, `config/templates/`, `config/scripts/`
- Move (copy then verify, not `git mv` across repos) all 11 agent files from `~/code/shell/src/claude/agents/`
- Move all 22 skill directories from `~/code/shell/src/claude/skills/`
- Move 35 step contract YAML files + CONVENTIONS.md from `~/code/shell/src/spec/steps/`
- Move 5 schema YAML files from `~/code/shell/src/spec/schemas/` → `config/workflows/`
- Move 5 template directories (feature, bugfix, chore, spike, bootstrap) from `~/code/shell/src/spec/templates/`
- Move `scripts/compute-swe-metrics.sh` from `~/code/shell/src/spec/scripts/`
- Move `grammar.yaml` from `~/code/shell/src/spec/` → `config/`
- Rename all `SPEC_HOME` references → `WORKFLOW_HOME` across moved files (47 total occurrences)
- Update schema path references: `$SPEC_HOME/schemas/` → `$WORKFLOW_HOME/config/workflows/`
- Create `install.sh` in orchestrator that replaces the `configure_claude_code` spec-wiring logic from `~/code/shell/scripts/setup-common.sh`
- Create `guidelines.yaml` (replaces hardcoded keyword matching in `/develop` for schema detection)
- Rename `/develop` skill to `/orchestrate`; keep `/develop` as alias
- Update `~/code/shell/scripts/setup-common.sh` to remove spec-wiring logic (or delegate to orchestrator's install.sh)
- Set `WORKFLOW_HOME` default in install.sh (export pointing to `~/.config/workflow` or `$ORCHESTRATOR_HOME`)

### Out of Scope

- `~/code/shell/src/home/` (dotfiles) — stays in shell repo
- `~/code/shell/src/claude/settings.json` — stays in shell repo
- `~/code/shell/src/claude/hooks/` — stays in shell repo
- `~/code/shell/src/hooksmith/` — stays in shell repo
- `~/code/shell/src/linear/` — stays in shell repo
- Cross-tool wiring beyond Claude Code (Cursor, Codex CLI, Gemini CLI) — install.sh stubs for future, but only Claude Code is fully wired in this ticket
- Workflow schema changes or new workflow types
- `/learn` cycle improvements
- Deleting source files from shell repo (leave in place; post-migration cleanup is a separate ticket)

---

## UI Direction

N/A — no UI components.

---

## Key Decisions

**KD-1: WORKFLOW_HOME = ~/code/orchestrator (no symlink layer)**
Skills read directly from the repo. `install.sh` sets `WORKFLOW_HOME=~/code/orchestrator` in the shell profile. No `~/.config/workflow/` directory needed. Editing skills/steps directly edits the git-tracked source.

**KD-2: guidelines.yaml uses semantic descriptions (LLM-driven selection)**
Each schema has a natural-language description. The LLM picks the best match based on semantic similarity to the user's description — no keyword list. Format: `schema-name: "<description of when to use this schema>"`.

---

## Open Questions

1. **WORKFLOW_HOME default path:** Should the default fallback be `$HOME/.config/workflow` (clean break from SPEC_HOME) or stay at `$HOME/.config/spec` during a transition period to avoid breaking existing installs? Using a new path is cleaner but requires all users to run `install.sh` before the first `/orchestrate` invocation.

2. **Transition strategy for SPEC_HOME:** Should the skills emit a deprecation warning when `SPEC_HOME` is set but `WORKFLOW_HOME` is not, and auto-derive `WORKFLOW_HOME=$SPEC_HOME`? Or hard-cut immediately? Given there is only one user (Mahesh), a hard-cut after running `install.sh` is probably fine — but this needs confirmation.

3. **Shell repo cleanup timing:** When should `~/code/shell/src/spec/` and `~/code/shell/src/claude/agents|skills` be removed? This ticket moves files; deletion should be gated on verifying the orchestrator install works end-to-end. Likely a follow-up chore.

4. **install.sh target for config:** Current shell setup symlinks `config/{schemas,steps,templates,scripts}` → `~/.config/spec/{schemas,steps,templates,scripts}`. The new orchestrator's install.sh needs to decide where to symlink — `~/.config/workflow/` is a reasonable target, but it means changing the `WORKFLOW_HOME` default. Alternatively, install.sh could accept a `--target` flag.

5. **guidelines.yaml format:** The ticket says "keyword matching → guidelines.yaml (LLM-driven workflow selection)". What should the YAML format be? The current keyword matching in `/develop` is inline in the SKILL.md. The grammar.yaml in shell repo defines structural grammar, not workflow-selection logic. [ASSUMPTION: guidelines.yaml maps intent signals to schema names using natural-language descriptions rather than keywords, allowing the LLM to match by semantic similarity.]

6. **haiku-agent.md status:** The shell repo has `haiku-agent.md` in agents, but it is not listed in the Linear ticket's "what moves" list. Does it move? [ASSUMPTION: it moves — it is a model-routing agent, not Claude-Code-specific.]

7. **`linear` skill exclusion:** The skills list in the ticket does not include `linear` (it stays in shell repo per "What stays"). But `~/code/shell/src/claude/skills/linear/` exists. Confirm this is intentional — the linear skill depends on shell-specific config (`~/.config/linear/config.yaml`) which stays in shell.

---

## Technical Context

### Current Wiring (shell repo)

`~/code/shell/scripts/setup-common.sh` `configure_claude_code()` function (lines ~349–415) does two things:

1. Symlinks `~/code/shell/src/claude/{agents,hooks,skills,templates,config}` → `~/.claude/{agents,hooks,skills,templates,config}`
2. Symlinks `~/code/shell/src/spec/{schemas,steps,templates,scripts}` → `~/.config/spec/{schemas,steps,templates,scripts}`

The `~/.claude/skills` and `~/.claude/agents` directories are therefore directory-level symlinks to the shell repo, not file-level symlinks. This means editing any file in `~/.claude/skills/develop/SKILL.md` actually edits the shell repo source.

### SPEC_HOME Resolution

`SPEC_HOME` is not exported in any shell profile (grep confirmed zero hits in `.zshrc`, `.zprofile`, `.zshenv`). It defaults at runtime inside each skill: `SPEC_HOME=${SPEC_HOME:-$HOME/.config/spec}`. The `~/.config/spec/` directory currently contains four symlinks to the shell repo plus a `changes/` directory.

### SPEC_HOME Occurrence Count

| Location | Files | Occurrences |
|---|---|---|
| Skills (develop, learn, autopilot) | 3 | 23 |
| Agents (ideator, workflow-improver) | 2 | 3 |
| Step contracts | 6 | 11 |
| Schemas (all 5) | 5 | 10 |
| **Total** | **16** | **47** |

### Schema Path Pattern

Schemas reference `$SPEC_HOME/steps/` and `$SPEC_HOME/templates/<schema>/`. After move:
- `$SPEC_HOME/schemas/` → `$WORKFLOW_HOME/config/workflows/`
- `$SPEC_HOME/steps/` → `$WORKFLOW_HOME/config/steps/`
- `$SPEC_HOME/templates/` → `$WORKFLOW_HOME/config/templates/`

### Orchestrator Repo Current State

`~/code/orchestrator/` contains: `AGENTS.md`, `CLAUDE.md`, `Makefile`, `spec/project.yaml`. The Makefile's `doctor` target already checks for `config/workflows`, `config/steps`, `agents/`, `skills/` — confirming the expected directory structure. No `install.sh` exists yet.

### Files That Move (Summary)

| Source path (shell repo) | Destination (orchestrator) |
|---|---|
| `src/claude/agents/*.md` (11 files) | `agents/` |
| `src/claude/skills/*/` (22 dirs, minus `linear`) | `skills/` |
| `src/spec/schemas/*.yaml` (5 files) | `config/workflows/` |
| `src/spec/steps/*.yaml` + `CONVENTIONS.md` (36 files) | `config/steps/` |
| `src/spec/templates/*/` (5 dirs) | `config/templates/` |
| `src/spec/scripts/compute-swe-metrics.sh` | `config/scripts/` |
| `src/spec/grammar.yaml` | `config/grammar.yaml` |
