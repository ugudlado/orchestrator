"""`orchestrator pack publish-scenarios <pack> [--step id]` — plan phase 3.3.

Learn steps propose scenario rows into the `learn_results` DB table. A human,
or the pack's `persist-learnings` step, marks a row accepted. This command is
the one-way door from the DB back into git: accepted rows are appended to
``.orchestrator/<pack>/steps/<step_id>/scenarios/train.jsonl``.

Dedupe is by sha256 of the canonical JSON of the row, checked against the lines
already in the file, so re-running after a partial export adds nothing twice.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from orchestrator_next import judge as _judge
from orchestrator_next import state_store as ss

# ponytail: cap the semantic-dedupe comparison set rather than diffing a
# candidate against a whole train.jsonl — keeps the judge call's state small
# and bounded as a step's scenario file grows.
_SEMANTIC_DEDUPE_RECENT = 40
_SEMANTIC_DEDUPE_THRESHOLD = 0.85


def _canonical(row: dict) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def _row_hash(row: dict) -> str:
    return hashlib.sha256(_canonical(row).encode("utf-8")).hexdigest()


def existing_hashes(path: Path) -> set[str]:
    """Content hashes of the rows already in a train.jsonl."""
    out: set[str] = set()
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.add(_row_hash(json.loads(line)))
        except json.JSONDecodeError:
            # A hand-written line the engine cannot parse still counts as
            # content — hash the raw text so it is never clobbered.
            out.add(hashlib.sha256(line.encode("utf-8")).hexdigest())
    return out


def _existing_rows(path: Path) -> list[dict]:
    """Rows already in a train.jsonl, most recent last (append order)."""
    out: list[dict] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _semantic_duplicate_of(row: dict, existing_rows: list[dict]) -> str | None:
    """None if judge unavailable or no near-duplicate found; else a marker
    identifying which existing row (by its position among the recent set)
    the candidate duplicates."""
    recent = existing_rows[-_SEMANTIC_DEDUPE_RECENT:]
    if not recent:
        return None
    questions = {
        str(i): _judge.noul(
            instructions=(
                "Do `candidate` and `existing` describe the same scenario "
                "and expect the same behavior (a near-duplicate, not merely "
                "the same topic)?"
            ),
        )
        for i in range(len(recent))
    }
    state = {"candidate": row, "existing": recent}
    r = _judge.ask(state=state, questions=questions)
    if r is None:
        return None
    for i, existing_row in enumerate(recent):
        result = r.nouls.get(str(i)) if isinstance(r.nouls, dict) else None
        if result is not None and result.noul >= _SEMANTIC_DEDUPE_THRESHOLD:
            return existing_row.get("id") or f"row {i}"
    return None


def publish(pack_dir: Path, *, step: str | None = None,
            handle: str | None = None) -> dict[str, int]:
    """Append accepted learn rows to each step's scenarios/train.jsonl.

    Returns ``{step_id: rows_appended}``.
    """
    url = handle or ss.default_state_url()
    if not url:
        raise RuntimeError(
            "no state store configured — set ORCHESTRATOR_STATE_URL (learn "
            "results live in the DB, not in state.yaml)"
        )
    rows = ss.list_learn_rows(url, accepted=True)
    written: dict[str, int] = {}
    for entry in rows:
        step_id = str(entry.get("step_id") or "")
        if not step_id or (step and step_id != step):
            continue
        row = entry.get("proposed_row") or {}
        if not isinstance(row, dict) or not row:
            continue
        target = pack_dir / "steps" / step_id / "scenarios" / "train.jsonl"
        seen = existing_hashes(target)
        if _row_hash(row) in seen:
            continue
        dup_of = _semantic_duplicate_of(row, _existing_rows(target))
        if dup_of is not None:
            print(f"skipped near-duplicate of {dup_of}", file=sys.stderr)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(_canonical(row) + "\n")
        written[step_id] = written.get(step_id, 0) + 1
    return written


def publish_scenarios_cmd(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="orchestrator pack publish-scenarios",
        description="Export accepted learn rows into a pack's scenarios/train.jsonl.",
    )
    ap.add_argument("pack", help="pack name under .orchestrator/, or a pack path")
    ap.add_argument("--step", default=None, help="only this step id")
    ap.add_argument("--repo", default=None, help="consumer repo root")
    ap.add_argument("--state-url", default=None, help="state store URL override")
    args = ap.parse_args(argv)

    from orchestrator_next.config_pull import resolve_repo_root

    pack_dir = Path(args.pack)
    if not pack_dir.is_dir():
        pack_dir = resolve_repo_root(args.repo) / ".orchestrator" / args.pack
    if not pack_dir.is_dir():
        print(f"error: no pack at {pack_dir}", file=sys.stderr)
        return 1

    try:
        written = publish(pack_dir, step=args.step, handle=args.state_url)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not written:
        print("no new accepted learn rows to publish")
        return 0
    for step_id, count in sorted(written.items()):
        print(f"{step_id}: +{count} row(s) -> "
              f"{pack_dir / 'steps' / step_id / 'scenarios' / 'train.jsonl'}")
    return 0
