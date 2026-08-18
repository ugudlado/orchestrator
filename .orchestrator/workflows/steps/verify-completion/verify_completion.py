"""verify-completion step logic.

Env contract (driver-injected, see orchestrator_next/step_env.py):
  BUZZ_REPLY_EVENT   (required) path to the remote agent's reply event JSON
  ORCHESTRATOR_ROSTER (required) roster.yaml pinning step -> role -> pubkey
  BUZZ_VERIFIED_STEP  (optional) step id being verified; when unset, derived
                      from state step_history: last entry whose step_id has a
                      role in the roster's steps mapping
  STATE_YAML_PATH / ORCHESTRATOR_STATE_YAML_PATH (used only for derivation)
  BRANCH              (optional) run branch; best-effort git ls-remote check
  REPO_ROOT           cwd for the git check

Checks: (a) verify_from_roster (id + BIP-340 sig + roster pubkey),
(b) the fenced ```completion block parses as YAML with a status field,
(c) when BRANCH is set, git ls-remote shows the branch on origin (warn-only).

Exit 0 with a done-payload JSON on stdout on pass; exit 1 with a failed
payload and a clear stderr reason on fail.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_STEP_DIR = Path(__file__).resolve().parent
_LIB_BUZZ = _STEP_DIR.parent.parent / "lib" / "buzz"


def _load_verify_event():
    spec = importlib.util.spec_from_file_location(
        "buzz_verify_event", _LIB_BUZZ / "verify_event.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extract_completion_block(content: str) -> "str | None":
    """First ```completion fenced block body, or None (mirrors buzz's parser)."""
    fence = "```completion"
    start = content.find(fence)
    if start == -1:
        return None
    after_open = start + len(fence)
    nl = content.find("\n", after_open)
    if nl == -1:
        return None
    body_start = nl + 1
    close = content.find("```", body_start)
    if close == -1:
        return None
    return content[body_start:close]


def parse_completion(content: str) -> dict:
    """Parse the reply content's completion fence; raise ValueError on problems."""
    block = extract_completion_block(content)
    if block is None:
        raise ValueError("reply content has no fenced ```completion block")
    import yaml

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise ValueError(f"completion block is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("completion block must be a YAML mapping")
    status = data.get("status")
    if not isinstance(status, str) or status not in ("success", "failed"):
        raise ValueError(
            f"completion block status must be 'success' or 'failed' (got {status!r})"
        )
    return data


def _derive_verified_step(roster_steps: dict) -> "str | None":
    """Last step_history entry whose step_id maps to a roster role."""
    state_path = os.environ.get("ORCHESTRATOR_STATE_YAML_PATH") or os.environ.get(
        "STATE_YAML_PATH"
    )
    if not state_path or not os.path.isfile(state_path):
        return None
    import yaml

    try:
        raw = yaml.safe_load(open(state_path, encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    history = raw.get("step_history") or []
    for entry in reversed(history):
        if isinstance(entry, dict) and entry.get("step_id") in roster_steps:
            return str(entry["step_id"])
    return None


def _check_branch_advanced() -> "str | None":
    """Best-effort: warn (never fail) when BRANCH isn't visible on a remote."""
    branch = os.environ.get("BRANCH", "").strip()
    if not branch:
        return None
    cwd = os.environ.get("REPO_ROOT") or os.getcwd()
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--heads", "origin", branch],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"WARN verify-completion: git ls-remote failed: {exc}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(
            "WARN verify-completion: no reachable remote 'origin' "
            f"(git ls-remote rc={proc.returncode}): {proc.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    line = proc.stdout.strip()
    if not line:
        print(
            f"WARN verify-completion: branch '{branch}' not found on origin — "
            "remote agent may not have pushed",
            file=sys.stderr,
        )
        return None
    sha = line.split()[0]
    print(f"verify-completion: origin/{branch} at {sha}", file=sys.stderr)
    return sha


def _fail(reason: str) -> "int":
    print(f"ERROR verify-completion: {reason}", file=sys.stderr)
    print(
        json.dumps(
            {
                "status": "failed",
                "outputs": {"verified": "no", "reason": reason},
                "evidence": {"summary": reason},
            }
        )
    )
    return 1


def main() -> int:
    event_path = os.environ.get("BUZZ_REPLY_EVENT", "").strip()
    roster_path = os.environ.get("ORCHESTRATOR_ROSTER", "").strip()
    if not event_path:
        return _fail("BUZZ_REPLY_EVENT is required (path to reply event JSON)")
    if not os.path.isfile(event_path):
        return _fail(f"BUZZ_REPLY_EVENT file not found: {event_path}")
    if not roster_path:
        return _fail("ORCHESTRATOR_ROSTER is required (path to roster.yaml)")

    try:
        with open(event_path, encoding="utf-8") as f:
            event = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(f"reply event is not readable JSON: {exc}")

    ve = _load_verify_event()

    verified_step = os.environ.get("BUZZ_VERIFIED_STEP", "").strip()
    if not verified_step:
        try:
            roster = ve._load_roster(roster_path)
        except ValueError as exc:
            return _fail(str(exc))
        roster_steps = roster.get("steps") if isinstance(roster.get("steps"), dict) else {}
        verified_step = _derive_verified_step(roster_steps)
        if not verified_step:
            return _fail(
                "cannot determine step to verify: set BUZZ_VERIFIED_STEP or run "
                "with state step_history containing a roster-mapped agent step"
            )

    # (a) signature + roster pubkey pinning
    try:
        role = ve.verify_from_roster(event, roster_path, verified_step)
    except ValueError as exc:
        return _fail(f"event verification failed for step '{verified_step}': {exc}")

    # (b) completion fence parses with a status field
    try:
        completion = parse_completion(event.get("content", ""))
    except ValueError as exc:
        return _fail(f"completion block invalid for step '{verified_step}': {exc}")

    # (c) best-effort branch check (warn-only)
    remote_sha = _check_branch_advanced()

    outputs = {
        "verified": "yes",
        "verified_step": verified_step,
        "role": role,
        "event_id": event["id"].lower(),
        "completion_status": completion["status"],
    }
    if remote_sha:
        outputs["remote_sha"] = remote_sha
    print(
        f"verify-completion: event {event['id']} verified for step "
        f"'{verified_step}' (role '{role}', completion status "
        f"'{completion['status']}')",
        file=sys.stderr,
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "outputs": outputs,
                "evidence": {
                    "summary": f"verified signed completion for {verified_step}"
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
