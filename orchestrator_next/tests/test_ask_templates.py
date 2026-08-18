"""Tests for the buzz ask templates + renderer (.orchestrator/workflows/lib/buzz)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUZZ_LIB = REPO_ROOT / ".orchestrator" / "workflows" / "lib" / "buzz"

# Rostered step ids (roster.yaml steps: mapping in the buzz workspace).
ROSTERED_STEPS = [
    "explore", "diagnose", "ux-design", "ux-critique", "design",
    "design-review", "implement", "code-review", "learn",
]

ALL_VARS = {
    "agent_name": "Implementer",
    "branch": "run/orc-1",
    "repo_url": "https://github.com/ugudlado/orchestrator.git",
    "change_id": "ORC-1",
    "context_path": "spec/changes/orc-1/ticket-context.md",
}


def _load_render_ask():
    spec = importlib.util.spec_from_file_location("render_ask", BUZZ_LIB / "render_ask.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["render_ask"] = mod
    spec.loader.exec_module(mod)
    return mod


render_ask = _load_render_ask()


def test_every_rostered_step_has_a_template():
    templates = render_ask.load_templates()
    for step in ROSTERED_STEPS:
        assert step in templates, f"asks.yaml missing template for rostered step '{step}'"


@pytest.mark.parametrize("step", ROSTERED_STEPS)
def test_render_fills_all_placeholders(step):
    text = render_ask.render(step, ALL_VARS)
    for value in ALL_VARS.values():
        assert value in text
    assert "{" not in text and "}" not in text, f"unfilled placeholder in '{step}' ask"


@pytest.mark.parametrize("step", ROSTERED_STEPS)
def test_rendered_ask_carries_standing_instructions(step):
    text = render_ask.render(step, ALL_VARS)
    assert "push" in text.lower(), f"'{step}' ask must instruct pushing before replying"
    assert "```completion" in text, f"'{step}' ask must instruct ending with a ```completion block"
    assert f"branch {ALL_VARS['branch']}" in text


def test_unknown_placeholder_errors_clearly():
    missing = {k: v for k, v in ALL_VARS.items() if k != "context_path"}
    with pytest.raises(render_ask.AskRenderError, match="context_path"):
        render_ask.render("implement", missing)


def test_unknown_step_uses_generic_fallback():
    text = render_ask.render("some-unrostered-step", ALL_VARS)
    assert ALL_VARS["agent_name"] in text
    assert "```completion" in text
    assert "push" in text.lower()


def test_fallback_parser_matches_yaml_parser():
    """The stdlib block-scalar parser must agree with PyYAML on asks.yaml."""
    yaml = pytest.importorskip("yaml")
    text = (BUZZ_LIB / "asks.yaml").read_text(encoding="utf-8")
    via_yaml = {k: str(v).strip("\n") for k, v in (yaml.safe_load(text) or {}).items()}
    via_fallback = {k: v.strip("\n") for k, v in render_ask._parse_block_scalars(text).items()}
    assert via_fallback == via_yaml
