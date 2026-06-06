"""Repo-level project.yaml path resolution for learnings injection."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def project_yaml_path(state_raw: dict[str, Any]) -> Path | None:
    """Resolve spec/project.yaml, worktree-first then repo_root."""
    worktree = state_raw.get("worktree_path")
    if isinstance(worktree, str) and worktree:
        wt = Path(os.path.expanduser(worktree))
        if wt.is_dir():
            candidate = wt / "spec" / "project.yaml"
            if candidate.is_file():
                return candidate
    repo_root = state_raw.get("repo_root")
    if isinstance(repo_root, str) and repo_root:
        candidate = Path(os.path.expanduser(repo_root)) / "spec" / "project.yaml"
        if candidate.is_file():
            return candidate
    return None
