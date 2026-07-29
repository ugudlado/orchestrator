"""Validate, append, and commit the eval scenarios learn proposed this run.

learn is an LLM step, so it stages candidate rows in
``<state dir>/proposed-scenarios.jsonl`` instead of editing prompt packs
directly. This step is the deterministic gate between that staging file and
each pack's ``scenarios/train.jsonl``.

The gate exists because a bad row poisons a whole pack, not just itself:
``prompt_optimizer.run._load_scenario_banks`` raises on the first malformed
line, so one pretty-printed or duplicate-id row makes every scenario in that
pack unevaluable. The validation here mirrors that loader's contract exactly —
one line of JSON per row, keys exactly {id, scenario, expect}, ids unique
across train/dev/holdout.

Rows that fail are dropped with a recorded reason; the step always exits 0.
Learning is best-effort and must never fail the workflow (a nonzero exit from a
script step aborts the run — see run_loop.run_script_step).

Env read (beyond the standard ORCHESTRATOR_* block):
  ORCHESTRATOR_STATE_YAML_PATH   staging file lives beside state.yaml
  ORCHESTRATOR_PROMPT_DIRS       JSON step_id -> prompt dir (the append target)
  ORCHESTRATOR_PROMPT_PATH       allowed prompt roots (append confinement)
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SPLITS = ("train", "dev", "holdout")
STAGING_NAME = "proposed-scenarios.jsonl"
SCENARIO_KEYS = {"id", "scenario", "expect"}


def _log(msg: str) -> None:
    print(f"persist-learnings: {msg}", file=sys.stderr)


def _emit(
    summary: str,
    persisted: list[dict[str, Any]] | None = None,
    skipped: list[dict[str, Any]] | None = None,
    commits: list[dict[str, Any]] | None = None,
) -> None:
    print(json.dumps({
        "status": "completed",
        "outputs": {
            "persist_learnings": {
                "persisted": persisted or [],
                "skipped": skipped or [],
                "commits": commits or [],
            }
        },
        "evidence": {"summary": summary},
    }))


# ---------------------------------------------------------------------------
# Staging file discovery
# ---------------------------------------------------------------------------

def staging_files() -> list[Path]:
    """Staging files to consume, in read order.

    The state dir (parent of state.yaml) is the canonical location and is
    always set. ORCHESTRATOR_WORKFLOW_DIR is also checked because it is the
    variable step prompts historically name — but it is empty on non-worktree
    runs (parser.load_state derives it from worktree_path), so it cannot be the
    primary.
    """
    found: list[Path] = []
    state_path = (
        os.environ.get("ORCHESTRATOR_STATE_YAML_PATH")
        or os.environ.get("STATE_YAML_PATH")
        or ""
    )
    candidates = []
    if state_path:
        candidates.append(Path(state_path).parent / STAGING_NAME)
    workflow_dir = os.environ.get("ORCHESTRATOR_WORKFLOW_DIR") or ""
    if workflow_dir:
        candidates.append(Path(workflow_dir) / STAGING_NAME)
    for candidate in candidates:
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
    return found


# ---------------------------------------------------------------------------
# Row parsing and validation (mirrors prompt_optimizer.run._load_scenario_banks)
# ---------------------------------------------------------------------------

class RowError(ValueError):
    """A staged row cannot be persisted; the message is the recorded reason."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise RowError(f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def parse_line(text: str) -> dict[str, Any]:
    """Parse one physical line into a staged-row object.

    A pretty-printed row fails here by construction: its first line is not
    valid JSON on its own. That is the multiline rejection — the optimizer's
    bank format is one row per line and nothing else can be appended.
    """
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise RowError(
            f"row must be one single line of valid JSON, "
            f"pretty-printed/multiline rows are rejected ({exc.msg})"
        ) from exc
    if not isinstance(obj, dict):
        raise RowError("row must be a JSON object")
    return obj


def scenario_of(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract the scenario object from a staged row.

    Canonical shape is ``{"step_id": ..., "row": {...}}``; ``scenario`` as the
    wrapper key and the flat form (the three keys inline beside step_id) are
    accepted too, because learn writes this file from a prompt.
    """
    for key in ("row", "scenario"):
        value = raw.get(key)
        if isinstance(value, dict):
            return value
    return {k: v for k, v in raw.items() if k != "step_id"}


def validate_scenario(scenario: dict[str, Any]) -> None:
    """Raise RowError unless the scenario satisfies the optimizer's contract."""
    if set(scenario) != SCENARIO_KEYS:
        details = []
        missing = SCENARIO_KEYS - set(scenario)
        unknown = set(scenario) - SCENARIO_KEYS
        if missing:
            details.append(f"missing {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"unknown {', '.join(sorted(unknown))}")
        raise RowError(f"invalid scenario shape ({'; '.join(details)})")
    if not isinstance(scenario["id"], str) or not scenario["id"].strip():
        raise RowError("id must be a non-empty string")
    if not isinstance(scenario["scenario"], str) or not scenario["scenario"].strip():
        raise RowError("scenario must be a non-empty string")
    expect = scenario["expect"]
    if (
        not isinstance(expect, list)
        or not expect
        or not all(isinstance(item, str) and item.strip() for item in expect)
    ):
        raise RowError("expect must be a non-empty list of non-empty strings")


def existing_ids(pack: Path) -> set[str]:
    """Scenario ids already present in the pack's train/dev/holdout banks.

    Unparseable existing lines are ignored rather than fatal: this step's job
    is to not make a bank worse, and a pack that is already broken is the
    optimizer's error to report.
    """
    ids: set[str] = set()
    for split in SPLITS:
        path = pack / "scenarios" / f"{split}.jsonl"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                ids.add(row["id"])
    return ids


# ---------------------------------------------------------------------------
# Append target resolution
# ---------------------------------------------------------------------------

def prompt_dirs() -> dict[str, str]:
    raw = os.environ.get("ORCHESTRATOR_PROMPT_DIRS") or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        _log("WARN ORCHESTRATOR_PROMPT_DIRS is not valid JSON — no append targets")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if isinstance(v, str)}


def prompt_roots() -> list[Path]:
    raw = os.environ.get("ORCHESTRATOR_PROMPT_PATH") or os.environ.get(
        "ORCHESTRATOR_SKILLS_TEST_OVERRIDE"
    ) or ""
    return [Path(p) for p in raw.split(os.pathsep) if p]


def _variants(path: Path) -> set[Path]:
    """A path in both literal-absolute and symlink-resolved form."""
    forms = {Path(os.path.abspath(path))}
    try:
        forms.add(path.resolve())
    except OSError:
        pass
    return forms


def is_confined(path: Path, roots: list[Path]) -> bool:
    """True when path sits under one of the allowed prompt roots.

    Compared in both literal and resolved form: skill dirs are commonly
    symlinks into another checkout, so resolving only one side of the
    comparison would reject a legitimate pack.
    """
    for root in roots:
        for root_form in _variants(root):
            for path_form in _variants(path):
                if path_form == root_form or root_form in path_form.parents:
                    return True
    return False


# ---------------------------------------------------------------------------
# Append
# ---------------------------------------------------------------------------

def append_rows(target: Path, scenarios: list[dict[str, Any]]) -> None:
    """Append scenarios to target as one JSON line each, under an exclusive lock.

    json.dumps escapes newlines, so a row can never break the one-row-per-line
    format no matter what the scenario text contains.
    """
    payload = "".join(
        json.dumps(scenario, sort_keys=True) + "\n" for scenario in scenarios
    ).encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "ab+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size:
                handle.seek(size - 1)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    # Strip GIT_* so an ambient GIT_DIR (leaked from a pre-commit hook) cannot
    # redirect these calls at another repo — same guard as record.autocommit_state.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, env=env
    )


def git_root(path: Path) -> Path | None:
    proc = _git(path, "rev-parse", "--show-toplevel")
    top = proc.stdout.strip()
    if proc.returncode != 0 or not top:
        return None
    return Path(top)


def is_dirty(root: Path, target: Path) -> bool:
    """True when target already carries uncommitted changes.

    A pre-existing dirty target means someone else's edit is in flight; the
    commit below would sweep it into a `learn scenarios` commit.
    """
    proc = _git(root, "status", "--porcelain", "--", str(target))
    return proc.returncode != 0 or bool(proc.stdout.strip())


def commit_group(root: Path, paths: list[Path], change_id: str) -> dict[str, Any]:
    """Commit the given train.jsonl paths in one repo. Never switches branch."""
    record: dict[str, Any] = {"git_root": str(root), "paths": [str(p) for p in paths]}
    args = [str(p) for p in paths]
    add = _git(root, "add", "--", *args)
    if add.returncode != 0:
        record["committed"] = False
        record["reason"] = f"git add failed: {add.stderr.strip()}"
        return record
    if _git(root, "diff", "--cached", "--quiet", "--", *args).returncode == 0:
        record["committed"] = False
        record["reason"] = "nothing staged after append"
        return record
    message = f"chore({change_id}): learn scenarios"
    commit = _git(root, "commit", "-m", message, "--", *args)
    if commit.returncode != 0:
        record["committed"] = False
        record["reason"] = f"git commit failed: {commit.stderr.strip()}"
        return record
    record["committed"] = True
    record["message"] = message
    record["commit_sha"] = _git(root, "rev-parse", "HEAD").stdout.strip()
    return record


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _accept_rows(
    sources: list[Path],
    targets: dict[str, str],
    roots: list[Path],
) -> tuple[dict[Path, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Split staged rows into per-pack accepted scenarios and skip records."""
    accepted: dict[Path, list[dict[str, Any]]] = {}
    skipped: list[dict[str, Any]] = []
    known_ids: dict[Path, set[str]] = {}

    for source in sources:
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            skipped.append({"source": str(source), "reason": f"unreadable: {exc}"})
            continue
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            where = {"source": f"{source}:{number}"}
            try:
                raw = parse_line(line)
            except RowError as exc:
                skipped.append({**where, "reason": str(exc)})
                continue

            step_id = raw.get("step_id")
            if not isinstance(step_id, str) or not step_id.strip():
                skipped.append({**where, "reason": "step_id must be a non-empty string"})
                continue
            where["step_id"] = step_id

            pack_raw = targets.get(step_id)
            if not pack_raw:
                skipped.append({
                    **where,
                    "reason": "step_id has no prompt dir in ORCHESTRATOR_PROMPT_DIRS",
                })
                continue
            pack = Path(pack_raw)
            if not pack.is_dir():
                skipped.append({**where, "reason": f"prompt dir does not exist: {pack}"})
                continue
            if not is_confined(pack, roots):
                skipped.append({
                    **where,
                    "reason": f"prompt dir is outside the allowed prompt roots: {pack}",
                })
                continue

            scenario = scenario_of(raw)
            try:
                validate_scenario(scenario)
            except RowError as exc:
                skipped.append({**where, "reason": str(exc)})
                continue

            seen = known_ids.setdefault(pack, existing_ids(pack))
            scenario_id = scenario["id"]
            if scenario_id in seen:
                skipped.append({
                    **where,
                    "id": scenario_id,
                    "reason": f"duplicate scenario id {scenario_id!r}",
                })
                continue
            seen.add(scenario_id)
            accepted.setdefault(pack, []).append(scenario)

    return accepted, skipped


def _persist(
    accepted: dict[Path, list[dict[str, Any]]],
    skipped: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[Path | None, list[Path]]]:
    """Append accepted scenarios, refusing targets that are already dirty."""
    persisted: list[dict[str, Any]] = []
    by_repo: dict[Path | None, list[Path]] = {}

    for pack in sorted(accepted, key=str):
        scenarios = accepted[pack]
        target = pack / "scenarios" / "train.jsonl"
        root = git_root(pack)
        if root is not None and is_dirty(root, target):
            for scenario in scenarios:
                skipped.append({
                    "id": scenario["id"],
                    "reason": f"append target already has uncommitted changes: {target}",
                })
            continue
        try:
            append_rows(target, scenarios)
        except OSError as exc:
            for scenario in scenarios:
                skipped.append({"id": scenario["id"], "reason": f"append failed: {exc}"})
            continue
        persisted.append({
            "pack": str(pack),
            "path": str(target),
            "ids": [scenario["id"] for scenario in scenarios],
        })
        by_repo.setdefault(root, []).append(target)

    return persisted, by_repo


def run() -> int:
    sources = staging_files()
    if not sources:
        _log("no proposed-scenarios.jsonl staged — nothing to persist")
        _emit("no proposed scenarios staged")
        return 0

    accepted, skipped = _accept_rows(sources, prompt_dirs(), prompt_roots())
    persisted, by_repo = _persist(accepted, skipped)

    change_id = (
        os.environ.get("ORCHESTRATOR_CHANGE_ID")
        or os.environ.get("CHANGE_ID")
        or "learn"
    )
    commits: list[dict[str, Any]] = []
    for root, paths in by_repo.items():
        if root is None:
            commits.append({
                "git_root": None,
                "paths": [str(p) for p in paths],
                "committed": False,
                "reason": "prompt dir is not inside a git repository",
            })
            continue
        commits.append(commit_group(root, paths, change_id))

    # Consume the staging file so a re-run of this step cannot re-apply rows.
    # The skip reasons above are the audit trail; they land in step_history.
    for source in sources:
        try:
            source.unlink()
        except OSError as exc:
            _log(f"WARN could not remove staging file {source}: {exc}")

    total = sum(len(entry["ids"]) for entry in persisted)
    summary = (
        f"persisted {total} scenario(s) to {len(persisted)} pack(s); "
        f"{len(skipped)} row(s) skipped; {sum(1 for c in commits if c.get('committed'))} commit(s)"
    )
    _log(summary)
    for entry in skipped:
        _log(f"  skipped {entry.get('source') or entry.get('id')}: {entry['reason']}")
    _emit(summary, persisted, skipped, commits)
    return 0


def main() -> int:
    try:
        return run()
    except Exception as exc:  # noqa: BLE001 — learning must never fail the run
        _log(f"WARN unexpected failure, persisting nothing: {exc}")
        _emit(f"persist skipped after unexpected failure: {exc}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
