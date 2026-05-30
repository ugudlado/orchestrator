"""
Tests for orchestrator_next.resolver — load_agent_tools().

T-1: RED tests — verify load_agent_tools behavior.
These tests initially fail (ModuleNotFoundError on resolver module)
until T-2 creates resolver.py.
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def orch_home(tmp_path):
    """Create a minimal ORCHESTRATOR_HOME tree with a skills/ dir."""
    (tmp_path / "skills").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def user_home(tmp_path):
    """Create a fake ~/.claude/skills/ directory."""
    home = tmp_path / "fake_home"
    (home / ".claude" / "skills").mkdir(parents=True)
    return home


def _write_agent_file(skills_dir, agent_name: str, frontmatter: dict | None = None, tools_list=None, bad_yaml: bool = False):
    """Write a skill SKILL.md file with optional YAML frontmatter."""
    skill_dir = skills_dir / agent_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    if bad_yaml:
        path.write_text("---\nbad: yaml: {unclosed\n---\n# Agent\n")
        return
    if frontmatter is None:
        path.write_text("# Agent — no frontmatter\n")
        return
    fm = dict(frontmatter)
    if tools_list is not None:
        fm["tools"] = tools_list
    path.write_text(f"---\n{yaml.dump(fm)}---\n# Agent\n")


# ---------------------------------------------------------------------------
# T-1: load_agent_tools
# ---------------------------------------------------------------------------

class TestLoadAgentTools:

    def test_always_returns_none(self):
        """Tools are documented in agents.yaml comments, not enforced at dispatch time."""
        from orchestrator_next.resolver import load_agent_tools
        assert load_agent_tools("developer") is None
        assert load_agent_tools("architect") is None
        assert load_agent_tools("nonexistent-agent") is None
