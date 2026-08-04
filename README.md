# orchestrator

Config-driven, LLM-agnostic workflow engine for deterministic multi-step
development workflows (design → implement → review → QA → learn).

## Get started

### 1. Install uv (if needed)

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# then open a new shell (or source your profile) so `uv` is on PATH
```

Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`  
Details: https://docs.astral.sh/uv/getting-started/installation/

### 2. Install the orchestrator CLI

```bash
uv tool install git+https://github.com/ugudlado/orchestrator.git
```

One-off without installing (same CLI):

```bash
uvx --from git+https://github.com/ugudlado/orchestrator.git orchestrator --help
```

### 3. (Optional) Add workflows to a repo

The wheel is the engine only. To run shipped schemas (`feature`, `bugfix`, …)
in a consumer git repo:

```bash
cd /path/to/your-repo
orchestrator config pull https://github.com/ugudlado/workflows.git workflows
orchestrator doctor
orchestrator feature TICKET-1
```

Skip the pull if you already vendored a pack under `.orchestrator/<pack>/`, or
if you only need the CLI for tooling.

### From this checkout (engine contributors)

```bash
git clone https://github.com/ugudlado/orchestrator.git
cd orchestrator
./install.sh          # CLI → ~/.local/bin; vendor pack; doctor
```

See `AGENTS.md` (`CLAUDE.md` is a symlink) and `docs/distribution.md` for CLI
reference, pack layout, model routing, ticketing env vars, and headless/CI
notes.
