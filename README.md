# orchestrator

Config-driven, LLM-agnostic workflow engine for deterministic multi-step
development workflows (design → implement → review → QA → learn).

## Quickstart

```bash
# 1. Install the CLI (uv keeps it isolated; pipx also works)
uv tool install git+https://github.com/ugudlado/orchestrator.git

# 2. Download the workflow pack (workflows, steps, skills) — once per machine
git clone --depth 1 https://github.com/ugudlado/prompt-packs.git ~/.orchestrator/pack
# update later: git -C ~/.orchestrator/pack pull

# 3. Sanity check (run inside the target repo)
orchestrator doctor

# 4. Run
orchestrator run TICKET-1 --schema feature
```

The wheel ships the engine only — workflows come from the downloaded pack
(or a repo-vendored `.orchestrator/config/`, which wins over it). No per-repo
setup file and no environment variable are required: repo conventions are
read from the repo's own docs (CLAUDE.md / AGENTS.md / README). Set
`ORCHESTRATOR_CONFIG` only to point at a different config checkout; set
`BACKLOG_URL`/`BACKLOG_TOKEN`/`BACKLOG_PROJECT` to enable ticket
integration (unset → ticket steps skip cleanly).

See `CLAUDE.md` and `docs/distribution.md` for the full CLI reference,
workflow phases, and repo-customization notes (prompts/skills overrides,
ticketing backends, headless/CI runs).
