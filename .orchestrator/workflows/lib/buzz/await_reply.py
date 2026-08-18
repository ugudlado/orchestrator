#!/usr/bin/env python3
"""Wait for an agent's completion reply in a buzz channel.

Polls the channel for a message authored by --pubkey that contains a
```completion fenced block. The buzz CLI has no streaming/subscribe verb, so
this polls the read verb (verified against buzz-cli source):

    buzz messages get --channel <UUID> --since <unix-ts> --limit 200

Output on match: raw event JSON written to --out, the completion block's
YAML body printed to stdout, exit 0.
Timeout: message on stderr, exit 2 — the caller records the step as failed;
never hang forever (mirrors buzz's own never-fail-outward parser policy).
Fence extraction mirrors buzz-workflow completion.rs extract_completion_block.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

BUZZ_BIN = "buzz"
FENCE_OPEN = "```completion"


def extract_completion_block(content: str) -> str | None:
    """First ```completion fenced block body, as in buzz completion.rs."""
    start = content.find(FENCE_OPEN)
    if start < 0:
        return None
    after_open = start + len(FENCE_OPEN)
    nl = content.find("\n", after_open)
    if nl < 0:
        return None
    body_start = nl + 1
    close = content.find("```", body_start)
    if close < 0:
        return None
    return content[body_start:close]


def fetch_events(channel: str, since: int) -> list[dict]:
    cmd = [
        BUZZ_BIN, "messages", "get",
        "--channel", channel,
        "--since", str(since),
        "--limit", "200",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print(
            f"await_reply: buzz CLI not found — install the '{BUZZ_BIN}' binary "
            "(from the buzz repo) and ensure it is on PATH",
            file=sys.stderr,
        )
        sys.exit(4)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return []
    try:
        events = json.loads(proc.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return []
    return events if isinstance(events, list) else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", required=True, help="buzz channel UUID")
    ap.add_argument("--pubkey", required=True, help="agent hex pubkey to wait for")
    ap.add_argument("--out", required=True, help="file to write the raw event JSON to")
    ap.add_argument("--timeout", type=float, default=3600.0, help="seconds to wait")
    ap.add_argument("--poll-interval", type=float, default=15.0)
    ap.add_argument(
        "--since", type=int, default=None,
        help="unix ts to scan from (default: now, minus one poll interval)",
    )
    ns = ap.parse_args()

    pubkey = ns.pubkey.strip().lower()
    start = time.time()
    since = ns.since if ns.since is not None else int(start - ns.poll_interval)

    while True:
        for event in fetch_events(ns.channel, since):
            if str(event.get("pubkey", "")).lower() != pubkey:
                continue
            content = event.get("content") or ""
            block = extract_completion_block(content)
            if block is None:
                continue
            with open(ns.out, "w", encoding="utf-8") as fh:
                json.dump(event, fh, indent=2)
            sys.stdout.write(block)
            return 0
        if time.time() - start >= ns.timeout:
            print(
                f"await_reply: timed out after {ns.timeout:.0f}s waiting for a "
                f"```completion reply from {pubkey} in channel {ns.channel}",
                file=sys.stderr,
            )
            return 2
        time.sleep(ns.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
