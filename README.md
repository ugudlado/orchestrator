# orchestrator

Config-driven, LLM-agnostic workflow engine for deterministic multi-step
development workflows (design → implement → review → QA → learn).

## Quickstart

```bash
# 1. Install the CLI (uv keeps it isolated; pipx also works)
uv tool install git+https://github.com/ugudlado/orchestrator.git

# 2. Pull workflow config into this repo (steps own SKILL.md)
orchestrator config pull https://github.com/ugudlado/workflow-config.git mypack
# or: orchestrator config pull /path/to/workflow-config mypack --skills

# Base role prompts (extends:) — once per machine
git clone --depth 1 https://github.com/ugudlado/prompt-packs.git ~/.orchestrator/pack

# 3. Sanity check (run inside the target repo)
orchestrator doctor

# 4. Run (workflows at .orchestrator/mypack/workflows/feature.yaml)
orchestrator feature TICKET-1
# if multiple packs share a workflow name: orchestrator mypack/feature TICKET-1
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
