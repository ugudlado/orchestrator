from pathlib import Path
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from sync_pi_agents import build_pi_frontmatter, convert_tools, normalize_tools, split_markdown
CONFIG_PATH = REPO_ROOT / "config" / "pi-agents.yaml"
DEVELOPER_PATH = REPO_ROOT / "agents" / "developer.md"


def test_convert_tools_maps_claude_builtins():
    mapped = convert_tools(
        ["Read", "Write", "Edit", "Grep", "Glob", "Bash", "Skill"],
        tool_map={"Read": "read", "Write": "write", "Edit": "edit", "Grep": "grep", "Glob": "find", "Bash": "bash"},
        skip_tools={"Skill"},
        mcp_tool_map={},
    )
    assert mapped == ["read", "write", "edit", "grep", "find", "bash"]


def test_developer_frontmatter_converts_for_pi():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    source_frontmatter, body = split_markdown(DEVELOPER_PATH)
    pi_frontmatter = build_pi_frontmatter(source_frontmatter, config, "developer")

    assert pi_frontmatter["name"] == "developer"
    assert pi_frontmatter["tools"][:6] == ["read", "write", "edit", "grep", "find", "bash"]
    assert "web_search" in pi_frontmatter["tools"]
    assert pi_frontmatter["inheritProjectContext"] is True
    assert "Implements all tasks" in pi_frontmatter["description"]
    assert body.lstrip().startswith("# Developer Agent")


def test_normalize_tools_accepts_json_array_string():
    assert normalize_tools('["Read", "Bash"]') == ["Read", "Bash"]
