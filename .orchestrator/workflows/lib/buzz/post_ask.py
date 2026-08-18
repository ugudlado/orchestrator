#!/usr/bin/env python3
"""Post a plain-language step ask into a buzz channel, mentioning the agent.

Thin wrapper: render the ask via render_ask.py, then shell out to the buzz
CLI. Auth/relay come from the buzz CLI's own env (BUZZ_RELAY_URL,
BUZZ_PRIVATE_KEY, optional BUZZ_AUTH_TAG).

Exact CLI syntax used (verified against buzz-cli source):
    buzz messages send --channel <UUID> --content - --mention <hex-pubkey>
Content is passed on stdin ('-') to avoid shell-quoting issues; --mention
carries the agent's pubkey explicitly so the visible @Name text is
presentation-only and still p-tags the right identity.

Prints the posted event id on stdout.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_ask import AskRenderError, render  # noqa: E402

BUZZ_BIN = "buzz"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--step", required=True, help="workflow step id (template key)")
    ap.add_argument("--pubkey", required=True, help="agent hex pubkey to mention")
    ap.add_argument("--agent-name", required=True, help="agent display name")
    ap.add_argument("--channel", required=True, help="buzz channel UUID")
    ap.add_argument("--branch", default="")
    ap.add_argument("--repo-url", default="")
    ap.add_argument("--change-id", default="")
    ap.add_argument("--context-path", default="")
    ap.add_argument(
        "--var", action="append", default=[], metavar="KEY=VALUE",
        help="extra template variable (repeatable)",
    )
    ns = ap.parse_args()

    vars: dict[str, str] = {
        "agent_name": ns.agent_name,
        "branch": ns.branch,
        "repo_url": ns.repo_url,
        "change_id": ns.change_id,
        "context_path": ns.context_path,
    }
    vars.update(dict(v.split("=", 1) for v in ns.var if "=" in v))

    try:
        content = render(ns.step, vars)
    except AskRenderError as exc:
        print(f"post_ask: {exc}", file=sys.stderr)
        return 3

    cmd = [
        BUZZ_BIN, "messages", "send",
        "--channel", ns.channel,
        "--content", "-",
        "--mention", ns.pubkey,
    ]
    try:
        proc = subprocess.run(
            cmd, input=content, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        print(
            f"post_ask: buzz CLI not found — install the '{BUZZ_BIN}' binary "
            "(from the buzz repo) and ensure it is on PATH",
            file=sys.stderr,
        )
        return 4
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        print(f"post_ask: {BUZZ_BIN} messages send failed (exit {proc.returncode})", file=sys.stderr)
        return proc.returncode or 4

    # Send output is JSON: {"event_id": "...", "accepted": true, "message": "...", ...}
    try:
        out = json.loads(proc.stdout.strip() or "{}")
        event_id = out.get("event_id") or out.get("id") or ""
    except json.JSONDecodeError:
        event_id = ""
    if not event_id:
        print(f"post_ask: could not read event id from send output: {proc.stdout!r}", file=sys.stderr)
        return 4
    print(event_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
