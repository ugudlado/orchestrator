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
    """Create a minimal ORCHESTRATOR_HOME tree with an agents/ dir."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def user_home(tmp_path):
    """Create a fake ~/.claude/agents/ directory."""
    home = tmp_path / "fake_home"
    (home / ".claude" / "agents").mkdir(parents=True)
    return home


def _write_agent_file(agents_dir, agent_name: str, frontmatter: dict | None = None, tools_list=None, bad_yaml: bool = False):
    """Write an agent .md file with optional YAML frontmatter."""
    path = agents_dir / f"{agent_name}.md"
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

    def test_returns_set_for_valid_agent_file(self, orch_home, monkeypatch):
        """Agent file with valid tools: frontmatter returns a set[str]."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        _write_agent_file(orch_home / "agents", "developer", frontmatter={}, tools_list=["Read", "Grep", "Glob"])
        from orchestrator_next.resolver import load_agent_tools
        result = load_agent_tools("developer")
        assert result == {"Read", "Grep", "Glob"}

    def test_missing_file_returns_none(self, orch_home, tmp_path, monkeypatch):
        """Missing agent file returns None (not exception)."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        home = tmp_path / "empty_home"
        (home / ".claude" / "agents").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        from orchestrator_next.resolver import load_agent_tools
        result = load_agent_tools("nonexistent-agent")
        assert result is None

    def test_frontmatter_without_tools_key_returns_none(self, orch_home, monkeypatch):
        """Agent file with frontmatter but no tools: key returns None."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        _write_agent_file(orch_home / "agents", "no-tools", frontmatter={"name": "no-tools"})
        from orchestrator_next.resolver import load_agent_tools
        result = load_agent_tools("no-tools")
        assert result is None

    def test_no_frontmatter_returns_none(self, orch_home, monkeypatch):
        """Agent file without frontmatter block returns None."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        _write_agent_file(orch_home / "agents", "no-fm")
        from orchestrator_next.resolver import load_agent_tools
        result = load_agent_tools("no-fm")
        assert result is None

    def test_bad_yaml_returns_none(self, orch_home, monkeypatch):
        """Agent file with unparseable YAML returns None (no exception)."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        _write_agent_file(orch_home / "agents", "bad-yaml", bad_yaml=True)
        from orchestrator_next.resolver import load_agent_tools
        result = load_agent_tools("bad-yaml")
        assert result is None

    def test_orch_home_agents_wins_over_user_home(self, tmp_path, monkeypatch):
        """Search order: $ORCHESTRATOR_HOME/agents/ wins over ~/.claude/agents/."""
        orch = tmp_path / "orch"
        (orch / "agents").mkdir(parents=True)
        home = tmp_path / "home"
        (home / ".claude" / "agents").mkdir(parents=True)
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch))
        monkeypatch.setenv("HOME", str(home))

        # Write different tools in each location
        (orch / "agents" / "myagent.md").write_text(
            "---\ntools:\n- ReadFromOrchHome\n---\n# myagent\n"
        )
        (home / ".claude" / "agents" / "myagent.md").write_text(
            "---\ntools:\n- ReadFromUserHome\n---\n# myagent\n"
        )

        from orchestrator_next.resolver import load_agent_tools
        # Force reimport to avoid module-level cache issues
        import importlib
        import orchestrator_next.resolver as resolver_mod
        importlib.reload(resolver_mod)
        result = resolver_mod.load_agent_tools("myagent")
        assert result == {"ReadFromOrchHome"}

    def test_falls_back_to_user_home_when_orch_home_missing(self, tmp_path, monkeypatch):
        """Falls back to ~/.claude/agents/ when file absent from ORCHESTRATOR_HOME."""
        orch = tmp_path / "orch"
        (orch / "agents").mkdir(parents=True)
        home = tmp_path / "home"
        (home / ".claude" / "agents").mkdir(parents=True)
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch))
        monkeypatch.setenv("HOME", str(home))

        # Only write in user home
        (home / ".claude" / "agents" / "fallback-agent.md").write_text(
            "---\ntools:\n- FallbackTool\n---\n# fallback-agent\n"
        )

        from orchestrator_next.resolver import load_agent_tools
        import importlib
        import orchestrator_next.resolver as resolver_mod
        importlib.reload(resolver_mod)
        result = resolver_mod.load_agent_tools("fallback-agent")
        assert result == {"FallbackTool"}

    def test_non_list_tools_value_returns_none(self, orch_home, monkeypatch):
        """Agent file where tools: is a string (non-list) returns None."""
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch_home))
        path = orch_home / "agents" / "string-tools.md"
        path.write_text("---\ntools: 'Read,Grep'\n---\n# agent\n")
        from orchestrator_next.resolver import load_agent_tools
        result = load_agent_tools("string-tools")
        assert result is None

    def test_oserror_on_read_continues_to_next_location(self, tmp_path, monkeypatch):
        """OSError when reading an agent file: skips to next search location."""
        orch = tmp_path / "orch"
        (orch / "agents").mkdir(parents=True)
        home = tmp_path / "home"
        (home / ".claude" / "agents").mkdir(parents=True)
        monkeypatch.setenv("ORCHESTRATOR_HOME", str(orch))
        monkeypatch.setenv("HOME", str(home))

        # Create file in orch_home location that exists (passes isfile) but raises OSError on read
        agent_path = orch / "agents" / "oserror-agent.md"
        agent_path.write_text("placeholder")

        # Create valid file in user home fallback
        (home / ".claude" / "agents" / "oserror-agent.md").write_text(
            "---\ntools:\n- FallbackRead\n---\n# oserror-agent\n"
        )

        original_open = open

        def mock_open(path, *args, **kwargs):
            if str(path) == str(agent_path):
                raise OSError("permission denied")
            return original_open(path, *args, **kwargs)

        import builtins
        import importlib
        monkeypatch.setattr(builtins, "open", mock_open)

        import orchestrator_next.resolver as resolver_mod
        importlib.reload(resolver_mod)
        result = resolver_mod.load_agent_tools("oserror-agent")
        # After OSError, should fall through to user home fallback
        assert result == {"FallbackRead"}
