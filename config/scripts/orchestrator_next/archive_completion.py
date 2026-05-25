"""Detect completed feature archives and short-circuit stale reruns.

When `orchestrator run <ticket>` is invoked for a feature that already finished
(archived under spec/changes/archive/<date>-<slug>/), drivers should not re-seed
or respawn discoverer/architect. This module finds prior completions and either
blocks seeding (probe) or finalizes an active stale state.yaml (handle).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.record import record

_ARCHIVE_STATE_GLOB = "spec/changes/archive/*/state.yaml"
_DATE_SLUG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _parse_archive_state(path: Path) -> dict[str, Any] | None:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _is_completed_archive_state(raw: dict[str, Any]) -> bool:
    if raw.get("status") == "completed":
        return True
    for entry in reversed(raw.get("step_history") or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("step_id") == "mark-change-completed" and entry.get("status") == "completed":
            return True
    return bool(raw.get("archive_path") and raw.get("completed_at"))


def _archive_sort_key(path: Path, raw: dict[str, Any]) -> tuple[str, float]:
    completed = raw.get("completed_at")
    if isinstance(completed, str) and completed:
        return (completed, path.stat().st_mtime)
    m = _DATE_SLUG_RE.match(path.parent.name)
    if m:
        return (m.group(1), path.stat().st_mtime)
    return ("", path.stat().st_mtime)


def find_completed_archive(
    repo_root: str | Path,
    *,
    slug: str | None = None,
    ticket_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the newest completed archive row for slug or ticket_id, else None."""
    root = Path(repo_root)
    archive_dir = root / "spec" / "changes" / "archive"
    if not archive_dir.is_dir():
        return None

    want_slug = _norm(slug)
    want_ticket = _norm(ticket_id)
    if not want_slug and not want_ticket:
        return None

    matches: list[tuple[tuple[str, float], dict[str, Any]]] = []
    for state_path in archive_dir.glob("*/state.yaml"):
        raw = _parse_archive_state(state_path)
        if raw is None or not _is_completed_archive_state(raw):
            continue
        cid = _norm(str(raw.get("change_id") or raw.get("slug") or ""))
        tid = _norm(str(raw.get("ticket_id") or ""))
        dir_match = _DATE_SLUG_RE.match(state_path.parent.name)
        dir_slug = _norm(dir_match.group(2)) if dir_match else _norm(state_path.parent.name)
        slug_ok = not want_slug or cid == want_slug or dir_slug == want_slug
        ticket_ok = not want_ticket or tid == want_ticket
        if not (slug_ok or ticket_ok):
            continue

        archive_path = raw.get("archive_path")
        if not archive_path:
            archive_path = f"spec/changes/archive/{state_path.parent.name}/"
        info = {
            "archive_path": str(archive_path),
            "completed_at": raw.get("completed_at"),
            "change_id": raw.get("change_id") or raw.get("slug"),
            "ticket_id": raw.get("ticket_id"),
            "archive_state_path": str(state_path),
        }
        matches.append((_archive_sort_key(state_path, raw), info))

    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def _artifact_dir(state_raw: dict[str, Any]) -> Path:
    slug = str(state_raw.get("change_id") or state_raw.get("slug") or "")
    worktree = state_raw.get("worktree_path")
    repo = Path(str(state_raw.get("repo_root") or "."))
    if worktree:
        return Path(str(worktree)) / "spec" / "changes" / slug
    return repo / "spec" / "changes" / slug


def _step_completed(state_raw: dict[str, Any], step_id: str, phase: str = "main") -> bool:
    for entry in reversed(state_raw.get("step_history") or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("step_id") == step_id and entry.get("phase") == phase:
            return entry.get("status") == "completed"
    return False


_DISCOVERY_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "templates" / "already-completed-discovery.md"
)


def _write_already_completed_discovery(
    artifact_dir: Path,
    archive_info: dict[str, Any],
    *,
    flagged_by: str,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    template = _DISCOVERY_TEMPLATE_PATH.read_text(encoding="utf-8")
    body = template.format(
        change_id=archive_info.get("change_id") or artifact_dir.name,
        ticket=archive_info.get("ticket_id") or "N/A",
        archive_path=archive_info.get("archive_path") or "",
        completed_at=archive_info.get("completed_at") or "unknown",
        flagged_by=flagged_by,
    )
    (artifact_dir / "discovery.md").write_text(body, encoding="utf-8")


def _finalize_plan_completed(state_raw: dict[str, Any], phase: str = "main") -> None:
    phase_plan = (state_raw.get("workflow_plan") or {}).get(phase)
    if not isinstance(phase_plan, dict):
        return
    nodes = phase_plan.get("nodes")
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if isinstance(node, dict) and node.get("id"):
            node["status"] = "completed"


def _strip_in_progress_tail(state_raw: dict[str, Any], step_id: str, phase: str) -> None:
    history = state_raw.get("step_history")
    if not isinstance(history, list):
        return
    while history:
        last = history[-1]
        if (
            isinstance(last, dict)
            and last.get("step_id") == step_id
            and last.get("phase") == phase
            and last.get("status") == "in_progress"
        ):
            history.pop()
        else:
            break


def _write_state_yaml(path: Path, state_raw: dict[str, Any]) -> None:
    pre = path.read_bytes()
    try:
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(state_raw, f, sort_keys=False, default_flow_style=False)
        with path.open(encoding="utf-8") as f:
            yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        path.write_bytes(pre)
        raise


def finalize_already_completed_rerun(state_yaml_path: str, archive_info: dict[str, Any]) -> dict[str, Any]:
    """Record a flag step and mark the workflow completed without respawning agents."""
    path = Path(state_yaml_path)
    state_raw = yaml.safe_load(path.read_text()) or {}
    phase = str(state_raw.get("phase") or "main")
    slug = str(state_raw.get("change_id") or state_raw.get("slug") or "")
    archive_path = str(archive_info.get("archive_path") or "")

    if state_raw.get("status") == "completed" and state_raw.get("archive_path") == archive_path:
        return {
            "action": "halt_complete",
            "flagged_by": "workflow",
            "message": (
                f"Feature {slug} is already completed "
                f"(archive: {archive_path}). No work to do."
            ),
            "archive_path": archive_path,
        }

    artifact_dir = _artifact_dir(state_raw)
    flagged_by = "discoverer"
    flag_step = "explore"
    skip_flag_record = False

    if _step_completed(state_raw, "explore", phase):
        flagged_by = "architect"
        flag_step = "design-and-draft-artifacts"
        if _step_completed(state_raw, flag_step, phase):
            skip_flag_record = True
    else:
        _write_already_completed_discovery(artifact_dir, archive_info, flagged_by=flagged_by)

    if skip_flag_record:
        _finalize_plan_completed(state_raw, phase)
        state_raw["status"] = "completed"
        state_raw["archive_path"] = archive_path
        if archive_info.get("completed_at"):
            state_raw["completed_at"] = archive_info["completed_at"]
        state_raw.pop("next_step", None)
        _write_state_yaml(path, state_raw)
        ticket = archive_info.get("ticket_id") or slug
        return {
            "action": "halt_complete",
            "flagged_by": "workflow",
            "message": (
                f"Feature {ticket} was already completed. "
                f"Archived at {archive_path}. "
                "Workflow state synced to completed without respawning agents."
            ),
            "archive_path": archive_path,
            "completed_at": archive_info.get("completed_at"),
        }

    if flag_step == "explore":
        payload: dict[str, Any] = {
            "step_id": "explore",
            "phase": phase,
            "status": "completed",
            "agent": "discoverer",
            "outputs": {
                "discovery_result": {
                    "already_completed": True,
                    "archive_path": archive_path,
                    "completed_at": archive_info.get("completed_at"),
                    "path": "discovery.md",
                },
            },
            "artifacts": ["discovery.md"],
            "usage": {"input_tokens": 1, "output_tokens": 1, "model": "archive-completion"},
            "evidence": {
                "summary": (
                    "Rerun detected prior archived completion; "
                    "skipped discoverer respawn."
                ),
            },
        }
    else:
        _strip_in_progress_tail(state_raw, flag_step, phase)
        rel_design = f"spec/changes/{slug}/design.md"
        rel_tasks = f"spec/changes/{slug}/tasks.yaml"
        payload = {
            "step_id": "design-and-draft-artifacts",
            "phase": phase,
            "status": "completed",
            "agent": "architect",
            "outputs": {
                "design.md": rel_design,
                "tasks.yaml": rel_tasks,
                "design_direction": "already_completed",
                "complexity": "S",
            },
            "artifacts": ["design.md", "tasks.yaml"],
            "usage": {"input_tokens": 1, "output_tokens": 1, "model": "archive-completion"},
            "evidence": {
                "summary": (
                    "Rerun detected prior archived completion; "
                    "skipped architect respawn."
                ),
            },
        }

    result, code = record(str(path), payload)
    if code != 0:
        return {
            "action": "error",
            "message": f"Failed to record {flag_step} short-circuit: {result}",
        }

    state_raw = yaml.safe_load(path.read_text()) or {}
    _finalize_plan_completed(state_raw, phase)
    state_raw["status"] = "completed"
    state_raw["archive_path"] = archive_path
    if archive_info.get("completed_at"):
        state_raw["completed_at"] = archive_info["completed_at"]
    state_raw.pop("next_step", None)
    _write_state_yaml(path, state_raw)

    ticket = archive_info.get("ticket_id") or slug
    return {
        "action": "halt_complete",
        "flagged_by": flagged_by,
        "message": (
            f"Feature {ticket} was already completed. "
            f"Archived at {archive_path}. "
            f"Flagged by {flagged_by}; workflow closed without redoing implementation."
        ),
        "archive_path": archive_path,
        "completed_at": archive_info.get("completed_at"),
    }


def probe(repo_root: str, slug: str, ticket_id: str) -> dict[str, Any]:
    info = find_completed_archive(repo_root, slug=slug, ticket_id=ticket_id)
    if info is None:
        return {"action": "continue"}
    ticket = ticket_id or slug
    return {
        "action": "halt_complete",
        "message": (
            f"Feature {ticket} is already completed "
            f"(archive: {info['archive_path']}). "
            "Skipping seed and workflow."
        ),
        "archive_path": info["archive_path"],
        "completed_at": info.get("completed_at"),
    }


def handle(state_yaml_path: str) -> dict[str, Any]:
    path = Path(state_yaml_path)
    if not path.is_file():
        return {"action": "continue"}
    try:
        state_raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"action": "error", "message": str(exc)}

    repo_root = str(state_raw.get("repo_root") or "")
    slug = str(state_raw.get("change_id") or state_raw.get("slug") or "")
    ticket_id = str(state_raw.get("ticket_id") or "")

    info = find_completed_archive(repo_root, slug=slug, ticket_id=ticket_id or None)
    if info is None:
        return {"action": "continue"}

    if state_raw.get("status") == "completed":
        return {
            "action": "halt_complete",
            "flagged_by": "workflow",
            "message": (
                f"Feature {slug} is already completed "
                f"(archive: {info['archive_path']})."
            ),
            "archive_path": info["archive_path"],
        }

    return finalize_already_completed_rerun(str(path), info)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print("usage: archive_completion.py probe <repo_root> <slug> <ticket_id>", file=sys.stderr)
        print("       archive_completion.py handle <state.yaml>", file=sys.stderr)
        return 7

    cmd = args[0]
    if cmd == "probe":
        if len(args) != 4:
            return 7
        out = probe(args[1], args[2], args[3])
    elif cmd == "handle":
        if len(args) != 2:
            return 7
        out = handle(args[1])
    else:
        return 7

    print(json.dumps(out, sort_keys=True))
    # Exit 0 for halt_complete and continue — shell drivers parse JSON.action.
    # Non-zero exit made `cmd || fallback` treat a successful short-circuit as failure.
    if out.get("action") == "error":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
