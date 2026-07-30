# orchestrator

Config-driven, LLM-agnostic workflow engine for deterministic multi-step
development workflows (design → implement → review → QA → learn).

## Quickstart

```bash
# 1. Install the CLI (uv keeps it isolated; pipx also works)
uv tool install git+https://github.com/ugudlado/orchestrator.git

# 2. In the target repo: scaffold spec/project.yaml
orchestrator init

# 3. Sanity check
orchestrator doctor

# 4. Run
orchestrator run TICKET-1 --schema feature
```

No environment variable is required for the common case — the CLI falls
back to its bundled workflow config automatically. Set `ORCHESTRATOR_CONFIG`
only to point at a different config checkout (e.g. developing the engine
itself, or a config-repo split).

See `CLAUDE.md` and `docs/distribution.md` for the full CLI reference,
workflow phases, and repo-customization notes (prompts/skills overrides,
ticketing backends, headless/CI runs).
