# orchestrator

Config-driven, LLM-agnostic workflow engine for deterministic multi-step
development workflows (design → implement → review → QA → learn).

## Quickstart

### Machine install (from this checkout — repo-local)

```bash
git clone https://github.com/ugudlado/orchestrator.git
# optional sibling used as the config source (else cloned temporarily):
#   git clone https://github.com/ugudlado/workflows.git
cd orchestrator
./install.sh          # or: make onboard
```

That does **not** edit your shell profile. It:

1. **CLI** → symlink `~/.local/bin/orchestrator` (assumes `~/.local/bin` is already on PATH)
2. **Config** → vendors workflows pack into `.orchestrator/workflows/` (`config/` → that pack)
3. **Skill** → expects `skills/operator/` in this repo (workflow creator; not installed globally)
4. **`orchestrator doctor`**

Then:

```bash
orchestrator feature TICKET-1
# create new workflows with an agent in this repo: /operator
```

Refresh config later: `./install.sh --refresh-config`

### Wheel / per-repo pack (no checkout)

```bash
# 1. Install the CLI (uv keeps it isolated; pipx also works)
uv tool install git+https://github.com/ugudlado/orchestrator.git

# 2. Pull workflow config into this repo (steps own SKILL.md)
orchestrator config pull https://github.com/ugudlado/workflows.git workflows
# or: orchestrator config pull /path/to/workflows workflows --skills

# Base role prompts (extends:) — once per machine, only if packs use extends:
git clone --depth 1 https://github.com/ugudlado/prompt-packs.git ~/.orchestrator/pack

# 3. Sanity check (run inside the target repo)
orchestrator doctor

# 4. Run (workflows at .orchestrator/workflows/workflows/feature.yaml)
orchestrator feature TICKET-1
# if multiple packs share a workflow name: orchestrator workflows/feature TICKET-1
```

The wheel ships the engine only — workflows come from a repo-vendored
`.orchestrator/<pack>/` (via `config pull`) or a downloaded pack. Charters live
inside each agent step (`prompt: SKILL.md`); `--skills` optionally
symlinks them to `skills/<name>/`. No per-repo setup file is required:
repo conventions come from CLAUDE.md / AGENTS.md / README. Set
`ORCHESTRATOR_CONFIG` only to point at a different config checkout; set
`BACKLOG_URL`/`BACKLOG_TOKEN`/`BACKLOG_PROJECT` to enable ticket
integration (unset → ticket steps skip cleanly).

See `AGENTS.md` (`CLAUDE.md` is a symlink) and `docs/distribution.md` for the full CLI reference,
workflow phases, and repo-customization notes (prompts/skills overrides,
ticketing backends, headless/CI runs).
