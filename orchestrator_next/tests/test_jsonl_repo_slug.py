"""Claude Code project slug must match ~/.claude/projects/<slug>/ on disk."""
from __future__ import annotations

from orchestrator_next.jsonl_usage import _repo_slug


def test_repo_slug_normalizes_underscores_to_hyphens():
  path = "/Users/spidey/code/feature_worktrees/orc-86"
  assert _repo_slug(path) == "-Users-spidey-code-feature-worktrees-orc-86"


def test_repo_slug_main_repo_unchanged():
  path = "/Users/spidey/code/orchestrator"
  assert _repo_slug(path) == "-Users-spidey-code-orchestrator"
