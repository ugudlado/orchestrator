# orchestrator

Config-driven, LLM-agnostic workflow engine for deterministic multi-step
development workflows (design → implement → review → QA → learn).

> The CLI speaks protocol v2: `start` / `step` / `done`, plus `--headless`
> when the engine should drive the model itself. The v1 `next` / `done`
> exit-code protocol and the self-driving `orchestrator run` are removed. See
> [`docs/protocol-v2.md`](docs/protocol-v2.md).

## Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # if you don't have uv
uv tool install git+https://github.com/ugudlado/orchestrator.git
orchestrator doctor
```

Upgrade: `uv tool upgrade orchestrator`.

## Trust a pack source

A pulled pack carries shell scripts and agent charters that run against your
repo, so remote pulls must be allow-listed first in
`~/.orchestrator/trust.toml`:

```toml
[[allow]]
source = "https://github.com/ugudlado/*"
```

Local paths are always allowed — trust only governs network pulls. See
`orchestrator_next/trust.py` for the full format (signing keys, `require_signed`).

## Pull a workflow pack into a repo

```bash
cd /path/to/your-repo
orchestrator config pull https://github.com/ugudlado/workflows.git workflows
orchestrator doctor
orchestrator feature TICKET-1
# same name in two packs: orchestrator mypack/feature TICKET-1
```

See [`docs/pack-convention.md`](docs/pack-convention.md) for pack layout and
config resolution order.

## Run from Claude Code

Generate a plugin from the pulled pack, then point Claude Code at it:

```bash
orchestrator pack --target claude --out .tmp/plugin-claude
CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude --plugin-dir .tmp/plugin-claude
```

In the session, ask for e.g. "use orchestrator run with recipe feature slug
TICKET-1". If function hooks aren't enabled, the plugin still installs a
fallback skill that drives the same workflow. See
[`docs/claude-mod-api-notes.md`](docs/claude-mod-api-notes.md) for how the
generated hooks map pack steps to Claude Code agents/tools.

## Run from Codex

```bash
orchestrator pack --target codex --out .tmp/plugin-codex
```

Points Codex at the generated agent/tool definitions the same way. The
`.codex-plugin/` manifest format is unverified against Codex's actual plugin
loader — check the generator's own warning output before relying on it.

## Run headless (CI / cloud)

```bash
orchestrator run --headless <recipe> <slug>
orchestrator headless <run-id>   # resume
```

The engine walks `step`/`done` in-process and runs each judgment step itself —
no driver script needed.

Two backends run those steps:

| Backend      | How it runs                               | Credential                                   |
| ------------ | ----------------------------------------- | -------------------------------------------- |
| `claude-cli` | `claude -p` (Claude Code non-interactive) | the machine's Claude Code login              |
| `anthropic`  | Anthropic Messages API via the SDK        | `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` |

The default is `claude-cli` unless an API credential is already in the
environment, so a workstation with Claude Code signed in needs no API key.
Pin one with `--backend claude-cli|anthropic` or
`ORCHESTRATOR_HEADLESS_BACKEND`:

```bash
orchestrator run --headless design my-slug --backend claude-cli
```

`claude-cli` maps the step contract's `tools:` to Claude Code's own tools and
asks for the declared outputs via `--json-schema`. Set
`ORCHESTRATOR_STEP_BUDGET_USD` to cap the spend of each step. See
[`docs/cloud-environment.md`](docs/cloud-environment.md) for Slack `@Claude`
/ cloud-sandbox setup (secrets, network access, MCP).

## Reporting

```bash
orchestrator status <run-id> --json
orchestrator events <run-id>
orchestrator report --state <path> --json
```

Run state lives in the local RunStore (SQLite at `~/.orchestrator/orchestrator.db`
by default); set `ORCHESTRATOR_STATE_URL` for a shared store.

## From this checkout (engine contributors)

```bash
git clone https://github.com/ugudlado/orchestrator.git
cd orchestrator
uv sync --extra dev
.venv/bin/python -m pytest orchestrator_next/tests -q   # or: make test
```

Use `.venv/bin/python -m pytest` (or `make test`) — a bare `pytest` on PATH
may resolve to a system interpreter without the dev extras installed, which
silently fakes failures.

See `AGENTS.md` (`CLAUDE.md` is a symlink) and
[`docs/distribution.md`](docs/distribution.md) for CLI reference, model
routing, ticketing env vars, and the engine/pack split.
