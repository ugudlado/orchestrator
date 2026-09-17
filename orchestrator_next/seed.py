"""Seeding a new run: initial state.yaml + generated plan.

`orchestrator start` (protocol.py) is the only caller. Everything here is
pure state authorship — no dispatch, no execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from orchestrator_next.execute import _log


def _pack_sha_for(schema: str, repo_root: str) -> str:
    """Identify the pack this run was seeded from (best effort)."""
    from orchestrator_next.paths import (
        ConfigRootError,
        WorkflowRefError,
        config_root,
        pack_sha,
        resolve_workflow_ref,
    )

    try:
        _pack, _wf, cfg = resolve_workflow_ref(
            schema, Path(repo_root) if repo_root else None
        )
    except (WorkflowRefError, OSError):
        try:
            cfg = config_root()
        except (ConfigRootError, OSError):
            return ""
    try:
        return pack_sha(cfg)
    except OSError:
        return ""


def _recipe_artifacts_root_for(schema: str, repo_root: str = "") -> str:
    """The recipe's ``artifacts_root`` template, or "" when it declares none.

    Best-effort: a recipe that cannot be read here is not a reason to refuse to
    seed the run, since the default artifacts base still works.
    """
    from orchestrator_next.parser import load_recipe

    try:
        return load_recipe(schema, repo_root).artifacts_root
    except Exception:  # noqa: BLE001 — an unreadable recipe falls back to the default
        return ""


def _schema_active_steps(schema: str, repo_root: str = "") -> list[str]:
    """Load step ids for a workflow schema (pack-aware when possible)."""
    from orchestrator_next.paths import WorkflowRefError, config_root, resolve_workflow_ref
    from orchestrator_next.workflow_steps import step_id_of

    schema_yaml: Path | None = None
    try:
        root = Path(repo_root) if repo_root else None
        _, wf, cfg = resolve_workflow_ref(schema, root)
        cand = cfg / "workflows" / f"{wf}.yaml"
        if cand.is_file():
            schema_yaml = cand
    except WorkflowRefError:
        pass
    if schema_yaml is None:
        schema_yaml = config_root() / "workflows" / f"{schema}.yaml"
    if not schema_yaml.is_file():
        raise FileNotFoundError(f"schema '{schema}' not found: {schema_yaml}")
    schema_doc = yaml.safe_load(schema_yaml.read_text(encoding="utf-8")) or {}
    active = [
        sid
        for entry in schema_doc.get("steps", [])
        if (sid := step_id_of(entry))
    ]
    if not active:
        raise ValueError(f"schema '{schema}' declares no steps")
    return active


def _write_initial_state(
    state_yaml: Path, *, slug: str, schema: str, repo_root: str,
    active: list[str], prior_path: str, config_pack: str = "",
    worktree_path: str = "",
    user_input: str = "",
    ticket_id: str = "",
    run_id: str = "",
) -> None:
    """Write the initial state.yaml, carrying identity fields from the most
    recent prior state file when provided.

    ``slug`` / ``change_id`` are the run identity (UUID for new opaque-input runs).
    ``user_input`` is opaque text for the workflow (ticket id or brief) — never
    used as identity. ``ticket_id`` is only set when explicitly provided (or
    carried from prior); it is not defaulted from slug.
    """
    prior_context: dict = {}
    if prior_path:
        try:
            prior_raw = yaml.safe_load(Path(prior_path).read_text()) or {}
            for key in ("worktree_path", "branch", "repo_root", "change_id", "slug",
                        "ticket_id", "config_pack", "user_input"):
                if prior_raw.get(key):
                    prior_context[key] = prior_raw[key]
        except (OSError, yaml.YAMLError):
            pass  # prior unreadable — start fresh

    repo_root = prior_context.get("repo_root") or repo_root
    config_pack = config_pack or prior_context.get("config_pack") or ""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = prior_context.get("slug") or slug
    state: dict[str, Any] = {
        "change_id": prior_context.get("change_id") or slug,
        "slug": slug,
        "run_id": run_id or _new_run_id(),
        "schema": schema,
        "recipe": schema,
        "status": "active",
        "repo_root": repo_root,
        "workflow_plan": {"main": {"active": active, "filtered": []}},
        "phase": "main",
        "next_step": {"phase": "main", "step_id": active[0]},
        "step_history": [],
        "created_at": now,
        "started_at": now,
    }
    tid = (ticket_id or prior_context.get("ticket_id") or "").strip()
    if tid:
        state["ticket_id"] = tid
    ui = (user_input or prior_context.get("user_input") or "").strip()
    if ui:
        state["user_input"] = ui
    if config_pack:
        state["config_pack"] = config_pack
    sha = _pack_sha_for(schema, repo_root)
    if sha:
        state["pack_sha"] = sha
    # Pin the recipe's artifacts_root now, while the pack is still resolvable
    # from this repo_root. Later verbs run from a worktree (or any cwd) where
    # the pack cannot be found, and a silent miss sends artifacts to the
    # engine default instead of where the steps actually write them.
    artifacts_root = _recipe_artifacts_root_for(schema, repo_root)
    if artifacts_root:
        state["artifacts_root"] = artifacts_root
    wt = worktree_path or prior_context.get("worktree_path") or ""
    if wt:
        state["worktree_path"] = wt
    if prior_context.get("branch"):
        state["branch"] = prior_context["branch"]

    state_yaml.write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))
    _log(f"seeded: {state_yaml}")


def _new_run_id() -> str:
    from orchestrator_next.paths import new_run_id

    return new_run_id()


def seed_state_file(
    state_yaml: Path,
    *,
    slug: str,
    schema: str,
    repo_root: str,
    worktree_path: str = "",
    config_pack: str = "",
    prior_path: str = "",
    user_input: str = "",
    ticket_id: str = "",
    run_id: str = "",
) -> None:
    """Seed ``state_yaml`` and run generate_plan."""
    active = _schema_active_steps(schema, repo_root)
    state_yaml.parent.mkdir(parents=True, exist_ok=True)
    _write_initial_state(
        state_yaml,
        slug=slug,
        schema=schema,
        repo_root=repo_root,
        active=active,
        prior_path=prior_path,
        config_pack=config_pack,
        worktree_path=worktree_path,
        user_input=user_input,
        ticket_id=ticket_id,
        run_id=run_id,
    )
    from orchestrator_next import generate_plan as _gp
    _gp.generate_plan(str(state_yaml))
