"""A project's own MCP servers, for whichever agent runs its steps.

A project is a folder (a git repo). Its tools come from `<project>/.mcp.json`,
the file Claude Code already reads, in the same shape for every agent:

    {"mcpServers": {"linear": {"command": "npx", "args": ["-y", "linear-mcp"],
                               "env": {"LINEAR_API_KEY": "${LINEAR_API_KEY}"}},
                    "docs": {"type": "http", "url": "https://example.com/mcp"}}}

`${VAR}` (and `${VAR:-default}`) is filled from `<project>/.orchestrator/mcp.env`
(KEY=value lines; keep it out of git) and then from the worker's environment. Step
agents never see these values in their own environment: the step env drops
secret-looking names on purpose, so only the MCP servers get them.

Claude gets the resolved servers through a private `--mcp-config` file that the
agent's Read and Bash are denied, plus `--allowedTools mcp__<name>` for each, since
a headless run can't ask. Codex gets `-c mcp_servers.<name>.…` overrides; those are
visible in `ps` to the worker's own user, which is why the Codex worker should run as
a user of its own.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def servers(project: str | os.PathLike | None) -> dict[str, dict]:
    """The project's servers as written (unresolved); {} without a .mcp.json."""
    if not project:
        return {}
    p = Path(project) / ".mcp.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    found = data.get("mcpServers") if isinstance(data, dict) else None
    return {k: v for k, v in (found or {}).items() if isinstance(v, dict) and re.fullmatch(r"[A-Za-z0-9_-]+", k)}


def env_file(project: str | os.PathLike) -> dict[str, str]:
    p = Path(project) / ".orchestrator" / "mcp.env"
    out: dict[str, str] = {}
    try:
        lines = p.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
    return out


def needed(srv: dict) -> set[str]:
    """Variable names a server's config refers to."""
    return {m.group(1) for m in VAR.finditer(json.dumps(srv)) if m.group(2) is None}


def resolve(project: str | os.PathLike) -> dict[str, dict]:
    values = {**os.environ, **env_file(project)}

    def fill(v):
        if isinstance(v, str):
            return VAR.sub(lambda m: values.get(m.group(1), m.group(2) or ""), v)
        if isinstance(v, list):
            return [fill(x) for x in v]
        if isinstance(v, dict):
            return {k: fill(x) for k, x in v.items()}
        return v

    return {name: fill(srv) for name, srv in servers(project).items()}


def ready(project: str | os.PathLike | None) -> dict[str, dict]:
    """Resolved servers whose variables are all set; one missing a key is left out, not started blank."""
    if not project:
        return {}
    have = {**os.environ, **env_file(project)}
    raw = servers(project)
    return {n: srv for n, srv in resolve(project).items() if needed(raw[n]) <= have.keys()}


def summary(project: str | os.PathLike) -> list[dict]:
    """What Studio shows: names, kind and which variables are missing. Never values."""
    have = set(env_file(project))
    out = []
    for name, srv in servers(project).items():
        kind = srv.get("type") or ("http" if srv.get("url") else "stdio")
        out.append(
            {
                "name": name,
                "type": kind,
                "what": str(srv.get("url", "")).split("?")[0]  # a key in the query string stays hidden
                if kind != "stdio"
                else " ".join([srv.get("command", ""), *srv.get("args", [])])[:120],
                "missing": sorted(v for v in needed(srv) if v not in have and v not in os.environ),
            }
        )
    return out


def _toml(v) -> str:
    """A TOML inline value. JSON strings, numbers, booleans and arrays of them are valid TOML."""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {_toml(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    return json.dumps(v)


def codex_args(project: str | os.PathLike | None) -> list[str]:
    if not project:
        return []
    args: list[str] = []
    for name, srv in ready(project).items():
        if srv.get("url"):
            fields = {"url": srv["url"]}
            if srv.get("headers"):
                fields["http_headers"] = srv["headers"]
        else:
            fields = {"command": srv.get("command", "")}
            for k in ("args", "env"):
                if srv.get(k):
                    fields[k] = srv[k]
        for k, v in fields.items():
            args += ["-c", f"mcp_servers.{name}.{k}={_toml(v)}"]
    return args
