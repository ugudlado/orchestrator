"""Chat-driver slug helper: must match ~/.claude/projects/<slug>/ on disk.

``jsonl_usage._repo_slug`` is chat-driver-only (orc-111). The dashboard mirrors
the same normalization in ``ui/dashboard/server.py::_repo_slug`` without importing
this module — update both if the rules change.
"""
from __future__ import annotations

from orchestrator_next.jsonl_usage import _repo_slug


def test_repo_slug_normalizes_underscores_to_hyphens():
  path = "/Users/spidey/code/feature_worktrees/orc-86"
  assert _repo_slug(path) == "-Users-spidey-code-feature-worktrees-orc-86"


def test_repo_slug_main_repo_unchanged():
  path = "/Users/spidey/code/orchestrator"
  assert _repo_slug(path) == "-Users-spidey-code-orchestrator"
