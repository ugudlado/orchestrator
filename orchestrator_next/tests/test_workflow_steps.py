"""Tests for workflow step entry helpers."""

from orchestrator_next.workflow_steps import normalize_step_entry, step_id_of


def test_step_id_from_string():
    assert step_id_of("explore") == "explore"
    assert step_id_of("explore if flag") == "explore"


def test_step_id_from_skill_entry():
    assert step_id_of({"skill": "ux-critique"}) == "ux-critique"
    assert step_id_of({"id": "review", "skill": "review"}) == "review"


def test_step_id_from_prompt_entry():
    assert step_id_of({"id": "one-off", "prompt": "charter.md"}) == "one-off"
    assert step_id_of({"prompt": "charter.md"}) is None  # id required for prompt-only


def test_normalize_adds_id_from_skill():
    assert normalize_step_entry({"skill": "design"}) == {"id": "design", "skill": "design"}
