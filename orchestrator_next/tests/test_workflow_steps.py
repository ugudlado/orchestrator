"""Tests for workflow step entry helpers."""

from orchestrator_next.workflow_steps import normalize_step_entry, step_id_of


def test_step_id_from_string():
    assert step_id_of("explore") == "explore"
    assert step_id_of("explore if flag") == "explore"


def test_step_id_from_prompt_entry():
    assert step_id_of({"prompt": "ux-critique"}) == "ux-critique"
    assert step_id_of({"id": "review", "prompt": "review"}) == "review"
    assert step_id_of({"id": "one-off", "prompt": "custom-role"}) == "one-off"


def test_normalize_adds_id_from_prompt():
    assert normalize_step_entry({"prompt": "design"}) == {
        "id": "design",
        "prompt": "design",
    }
