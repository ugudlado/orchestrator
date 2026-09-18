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

## First run

```bash
orchestrator init
```

Asks a short set of questions on a TTY (state store, concurrency, headless
backend and budget, backlog sync, trust list) — Enter keeps the shown
default — and writes only the keys you changed to `~/.orchestrator/orchestrator.toml`
(`--repo` writes `.orchestrator/orchestrator.toml` in the current repo
instead). It then offers to pull a workflow pack if the repo has none yet,
using the trust list it just wrote. Off a TTY, or with `--yes`, it writes the
all-default template and skips the questions — safe for scripts and CI.
`orchestrator config init` is kept as an alias for `init --yes`.

Skipped `orchestrator init`? Every other verb prints a one-line reminder to
stderr the first time it runs with no settings file anywhere in the layer
chain; it never blocks. `orchestrator doctor` reports the same thing as a
WARN. See [Settings](#settings) for what each key does and the full
precedence chain.

## Trust a pack source

A pulled pack carries shell scripts and agent charters that run against your
repo, so remote pulls must be allow-listed first in the `[trust]` section of
`~/.orchestrator/orchestrator.toml`:

```toml
[trust]
allow = ["https://github.com/ugudlado/*"]
require_signed = false
```

Or in one command:

```bash
orchestrator config set trust.allow "https://github.com/ugudlado/*" --global
```

Local paths are always allowed — trust only governs network pulls. The older
`~/.orchestrator/trust.toml` is still read, with a deprecation warning.

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

`orchestrator config pull` generates the Claude plugin automatically (pass
`--no-plugin` to skip). It lands at the stable default location,
`.orchestrator/plugins/<pack>/claude/`, and the pull prints the exact command
to load it:

```bash
orchestrator config pull https://github.com/ugudlado/workflows.git workflows
# ... prints: CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude --plugin-dir /abs/path/.orchestrator/plugins/workflows/claude
```

To regenerate it later (after hand-editing a step, or with `--no-plugin`
pulls), run `orchestrator pack` directly — no `--out` needed, it writes to the
same default location and prints the same hint:

```bash
orchestrator pack --target claude
CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude --plugin-dir .orchestrator/plugins/workflows/claude
```

`--out <dir>` still overrides the location for a one-off export.

In the session, ask for e.g. "use orchestrator run with recipe feature slug
TICKET-1". If function hooks aren't enabled, the plugin still installs a
fallback skill that drives the same workflow. See
[`docs/claude-mod-api-notes.md`](docs/claude-mod-api-notes.md) for how the
generated hooks map pack steps to Claude Code agents/tools.

The generated plugin dir is not gitignored by default — a team may choose to
commit it so every clone gets a ready-to-load plugin without regenerating.
`orchestrator doctor` reports whether it exists and is still fresh against the
pulled pack.

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
Pin one with `--backend claude-cli|anthropic` or `headless.backend`:

```bash
orchestrator run --headless design my-slug --backend claude-cli
```

`claude-cli` maps the step contract's `tools:` to Claude Code's own tools and
asks for the declared outputs via `--json-schema`. Set
`headless.step_budget_usd` to cap the spend of each step. See
[`docs/cloud-environment.md`](docs/cloud-environment.md) for Slack `@Claude`
/ cloud-sandbox setup (secrets, network access, MCP).

## Reporting

```bash
orchestrator status <run-id> --json
orchestrator events <run-id>
orchestrator report --state <path> --json
```

Run state lives in the local RunStore (SQLite at `~/.orchestrator/orchestrator.db`
by default); set `state.url` for a shared store.

## Settings

Engine settings live in `orchestrator.toml`, not in a pile of environment
variables. Later layers win, and `orchestrator config show` prints the source
of every key:

```text
defaults < ~/.orchestrator/orchestrator.toml < <repo>/.orchestrator/orchestrator.toml
        < ORCHESTRATOR_* env var < CLI flag
```

```bash
orchestrator config init            # commented template in this repo
orchestrator config set run.max_parallel 2
orchestrator config show --json
```

```toml
[state]
url = "postgresql://user@host/orch"   # shared store; unset = local SQLite
backend = "sqlite"                    # or "file" for one YAML per run
tenant = "default"

[run]
max_parallel = 1                      # 1 = serial
stale_after_hours = 24.0
disable_worktree_lock = false

[headless]
backend = "claude-cli"                # or "anthropic"
step_budget_usd = 0.50
claude_bin = "claude"

[backlog]
url = "https://backlog.example"
project = "ORC"
token_env = "BACKLOG_TOKEN"           # the env var NAME — never the token

[trust]
allow = ["https://github.com/ugudlado/*"]
require_signed = false
trust_all = false

[models]
config = "/path/to/models.yaml"
route_overrides = { designer = { model_id = "claude-opus-5" } }
```

Every key keeps its old environment variable as an override, so nothing breaks
for an existing setup:

| Env override                         | Setting                     |
| ------------------------------------ | --------------------------- |
| `ORCHESTRATOR_STATE_URL`             | `state.url`                 |
| `ORCHESTRATOR_STATE_BACKEND`         | `state.backend`             |
| `ORCHESTRATOR_TENANT`                | `state.tenant`              |
| `ORCHESTRATOR_MAX_PARALLEL`          | `run.max_parallel`          |
| `ORCHESTRATOR_STALE_AFTER_HOURS`     | `run.stale_after_hours`     |
| `ORCHESTRATOR_DISABLE_WORKTREE_LOCK` | `run.disable_worktree_lock` |
| `ORCHESTRATOR_HEADLESS_BACKEND`      | `headless.backend`          |
| `ORCHESTRATOR_STEP_BUDGET_USD`       | `headless.step_budget_usd`  |
| `ORCHESTRATOR_CLAUDE_BIN`            | `headless.claude_bin`       |
| `BACKLOG_URL` / `BACKLOG_PROJECT`    | `backlog.url` / `.project`  |
| `ORCHESTRATOR_TRUST_ALL`             | `trust.trust_all`           |
| `ORCHESTRATOR_MODELS_CONFIG`         | `models.config`             |
| `ORCHESTRATOR_MODEL_ROUTE_OVERRIDES` | `models.route_overrides`    |

`ORCHESTRATOR_CONFIG` is deliberately **not** a setting: the config root comes
from the pack layout, and that env var stays its one explicit override.

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
