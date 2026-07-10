# Cloud environment — running the orchestrator from Slack `@Claude`

Configures a Claude Code on the web environment so a Slack `@Claude` session (or any
cloud session for this repo) can install the orchestrator, reach the backlog backend
via MCP, and drive a workflow per [`DRIVE.md`](../DRIVE.md).

Installation lives in a repo SessionStart hook (section 1), so it travels with the clone.
The only things you configure in the **environment settings dialog** at claude.ai/code are
the **secrets** (section 2) and **network access** — leave the environment's Setup script
field empty.

---

## ⚠️ Network prerequisite — read first

The backlog MCP server runs in **remote mode**: it proxies to your backlog backend's REST
API over `BACKLOG_URL`. Today that URL is **Tailscale-only**:

```
backlog_url: http://vmi3254961.tail4397c5.ts.net:6420   (~/.config/backlog/config.yml)
```

**The Anthropic cloud sandbox is not on your tailnet, so it cannot reach a `*.ts.net`
URL.** Before any of this works in the cloud, the backend must be reachable from the
public internet. Pick one:

- **Tailscale Funnel** in front of the backend → gives a public `https://...ts.net` URL.
- **Public HTTPS host** for the backend, protected by `BACKLOG_TOKEN`.

Then allowlist that host under **Network access → Custom → Allowed domains**.

If you don't expose the backend, the cloud session can't do MCP ticketing — it can still
run the workflow and report status as plain text in the Slack thread (no ticket sync).

---

## 1. Install — a repo SessionStart hook, NOT the environment setup script

**Leave the environment's Setup script field empty.** Installing from there fails: the
environment setup script runs from `/home/user`, but the repo clones into a
_subdirectory_, so `pip install -e .` finds no `pyproject.toml` (exit 1). It also wouldn't
run if a Slack session picked a different environment.

Instead, installation lives in the repo and travels with the clone:

- [`.claude/cloud-setup.sh`](../.claude/cloud-setup.sh) — runs `pip install -e .` and the
  backlog binary.
- [`.claude/settings.json`](../.claude/settings.json) — a `SessionStart` hook that runs it.

Why this works where the setup script didn't:

- SessionStart hooks run with **the repo as the working directory**, so `pip install -e .`
  finds `pyproject.toml` — no path guessing.
- The script is **guarded by `CLAUDE_CODE_REMOTE=true`**, so it's a no-op on your laptop and
  only installs in cloud sessions.
- It's **idempotent** (skips work already done), so session resume stays fast.
- It runs **regardless of which environment** the session lands in — important because a
  Slack `@Claude` session can't choose its environment (see "Environment binding" below).

Trade-off vs. the setup script: SessionStart hooks run on every session start/resume and
don't benefit from environment caching, so keep the script fast (the idempotency guards do
this). The first cloud session may prompt to **trust the hook** before it runs.

## 2. Environment variables (Environment → Environment variables field, `.env` format)

```
ORCHESTRATOR_SKIP_USAGE_CHECK=1
BACKLOG_URL=https://<your-public-backlog-host>
BACKLOG_TOKEN=<your-backlog-token>
```

- `ORCHESTRATOR_SKIP_USAGE_CHECK=1` — the session has no subprocess token count; this
  lets `orchestrator done` accept agent steps without real usage (cost records as $0 for
  those steps — known limitation).
- `BACKLOG_URL` / `BACKLOG_TOKEN` — backlog remote mode reads these env vars **before**
  falling back to `~/.config/backlog/config.yml` (which doesn't exist in the cloud). This
  is how the cloud session reaches the _same_ backend your laptop does.

> **Secrets caveat:** Claude Code on the web has no secrets store yet. Env vars are stored
> in the environment config in plaintext, visible to anyone who can edit the environment.
> `BACKLOG_TOKEN` lives there with that visibility in mind.

## 3. MCP server — committed to the repo

[`.mcp.json`](../.mcp.json) (project scope) declares the backlog MCP server. Repo
`.mcp.json` servers carry over to cloud sessions automatically (part of the clone). The
`${BACKLOG_URL}` / `${BACKLOG_TOKEN}` placeholders resolve from the env vars above.

```json
{
  "mcpServers": {
    "backlog": {
      "command": "backlog",
      "args": ["mcp", "start"],
      "env": {
        "BACKLOG_URL": "${BACKLOG_URL}",
        "BACKLOG_TOKEN": "${BACKLOG_TOKEN}"
      }
    }
  }
}
```

Note this is a **stdio** server (the only transport backlog.md supports), so the `backlog`
binary must be installed in the session — that's what `.claude/cloud-setup.sh` (section 1)
does. It is **not** an Anthropic-hosted connector.

---

## Environment binding — how a Slack session reaches this config

A cloud environment is **not** bound to a repo; it's a separate per-session choice. A Slack
`@Claude` mention gives the session a repo but provides no way to pick an environment, so it
uses your **default** environment. To make this reliable:

- Keep **one** cloud environment (edit the default rather than creating a second), so there's
  nothing else for Slack to land on.
- Put everything that _can_ live in the repo there — `.mcp.json`, `DRIVE.md`,
  `.claude/settings.json` + `cloud-setup.sh` all clone in automatically, independent of
  environment. Only the **secrets** (`BACKLOG_URL`, `BACKLOG_TOKEN`,
  `ORCHESTRATOR_SKIP_USAGE_CHECK`) must live in the environment.
- **Verify the binding** in a throwaway `@Claude` session: have it run
  `env | grep -E 'BACKLOG_URL|ORCHESTRATOR'`. If the vars appear, Slack is using your
  environment. If not, you have more than one environment and Slack picked the wrong default.

---

## Verification (run once before trusting the setup)

In a throwaway `@Claude` session for this repo:

```bash
env | grep -E 'ORCHESTRATOR_SKIP_USAGE_CHECK|BACKLOG_URL'   # env vars present?
curl -fsS -H "Authorization: Bearer $BACKLOG_TOKEN" \
  "$BACKLOG_URL/api/tasks?limit=1" | head                    # backend reachable?
orchestrator --help                              # orchestrator runnable?
```

If the curl fails, the network prerequisite isn't met — the backend isn't reachable
from the sandbox. Fix that before relying on MCP or engine ticket sync.

Also confirm the Slack session inherits this environment: have it print
`env | grep ORCHESTRATOR` — if the var shows, the Slack → environment binding is confirmed.
