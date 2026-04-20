#!/usr/bin/env python3
"""ingest-subagents-auto.py — Auto-invoke orchestrator ingest-subagents at complete phase.

Resolves driver session_id (TMPDIR UUID or JSONL scan) and calls
`orchestrator ingest-subagents`. Exits 0 always (fail-soft per design).

Usage:  python ingest-subagents-auto.py <state_yaml_path>
"""
from __future__ import annotations
import datetime as dt, json, os, re, subprocess, sys
from pathlib import Path
import yaml

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


def _slug(repo_root: str) -> str:
    return repo_root.replace("/", "-")


def _projects_root(home: str | None = None) -> Path:
    return (Path(home) if home else Path.home()) / ".claude" / "projects"


def _parse_iso(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _from_tmpdir() -> str | None:
    """Primary: extract UUID directory component from $TMPDIR."""
    for part in Path(os.environ.get("TMPDIR", "")).parts:
        if _UUID_RE.fullmatch(part):
            return part
    return None


def _from_scan(repo_root: str, t0: dt.datetime | None, t1: dt.datetime | None,
               home: str | None = None) -> str | None:
    """Fallback: newest *.jsonl in ~/.claude/projects/<slug>/ within [t0, t1]."""
    slug_dir = _projects_root(home) / _slug(repo_root)
    if not slug_dir.exists():
        return None
    candidates = list(slug_dir.glob("*.jsonl"))
    if not candidates:
        return None
    if t0 and t1:
        in_win = [
            p for p in candidates
            if t0 <= dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.timezone.utc) <= t1
        ]
        if in_win:
            candidates = in_win
    return max(candidates, key=lambda p: p.stat().st_mtime).stem


def _warn(msg: str) -> None:
    print(f"[ingest-subagents-auto] WARNING: {msg}", file=sys.stderr)


def main() -> int:
    if len(sys.argv) < 2:
        _warn("Usage: ingest-subagents-auto.py <state_yaml_path>")
        print(json.dumps({"skipped": True, "reason": "no state_yaml_path"}))
        return 0
    try:
        state = yaml.safe_load(open(sys.argv[1]))
    except Exception as exc:
        _warn(f"cannot read state.yaml: {exc}")
        print(json.dumps({"skipped": True, "reason": "state.yaml unreadable"}))
        return 0

    change_id = state.get("change_id", "")
    repo_root = state.get("repo_root", "")
    if not change_id or not repo_root:
        _warn("state.yaml missing change_id or repo_root")
        print(json.dumps({"skipped": True, "reason": "missing change_id or repo_root"}))
        return 0

    t0 = _parse_iso(state.get("started_at") or "")
    t1 = _parse_iso(state.get("completed_at") or "")
    home = os.environ.get("HOME")

    session_id = _from_tmpdir()
    path = "tmpdir"
    if not session_id:
        session_id = _from_scan(repo_root, t0, t1, home=home)
        path = "scan"
    if not session_id:
        _warn(f"session_id unresolvable for change_id={change_id}; skipping ingest-subagents")
        print(json.dumps({"skipped": True, "reason": "session_id unresolvable"}))
        return 0

    orch_home = os.environ.get("ORCHESTRATOR_HOME", "")
    bin_path = os.path.join(orch_home, "bin", "orchestrator") if orch_home else "orchestrator"
    cmd = [bin_path, "ingest-subagents", "--change-id", change_id, "--session-id", session_id]
    env = dict(os.environ)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_root, env=env)
    except Exception as exc:
        _warn(f"subprocess error: {exc}")
        print(json.dumps({"skipped": True, "reason": f"subprocess error: {exc}"}))
        return 0

    if r.returncode != 0:
        _warn(f"ingest-subagents exited {r.returncode}: {r.stderr.strip()}")
        print(json.dumps({"skipped": True, "reason": f"ingest-subagents exit {r.returncode}"}))
        return 0

    try:
        ingested = json.loads(r.stdout)
    except json.JSONDecodeError:
        ingested = {"raw": r.stdout.strip()}

    print(json.dumps({"ingest_subagents_result": {**ingested, "session_id": session_id, "resolution_path": path}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
