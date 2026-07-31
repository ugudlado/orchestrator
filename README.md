# orchestrator

Config-driven, LLM-agnostic workflow engine for deterministic multi-step
development workflows (design → implement → review → QA → learn).

## Quickstart

```bash
# 1. Install the CLI (uv keeps it isolated; pipx also works)
uv tool install git+https://github.com/ugudlado/orchestrator.git

# 2. Sanity check (run inside the target repo)
orchestrator doctor

# 3. Run
orchestrator run TICKET-1 --schema feature
```

No per-repo setup file and no environment variable are required for the
common case — the CLI falls back to its bundled workflow config, and repo
conventions are read from the repo's own docs (CLAUDE.md / AGENTS.md /
README). Set `ORCHESTRATOR_CONFIG` only to point at a different config
checkout; set `BACKLOG_URL`/`BACKLOG_TOKEN`/`BACKLOG_PROJECT` to enable
ticket integration (unset → ticket steps skip cleanly).

See `CLAUDE.md` and `docs/distribution.md` for the full CLI reference,
workflow phases, and repo-customization notes (prompts/skills overrides,
ticketing backends, headless/CI runs).
