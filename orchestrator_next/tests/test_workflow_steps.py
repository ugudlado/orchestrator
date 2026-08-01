"""Tests for workflow step entry helpers."""

from orchestrator_next.workflow_steps import normalize_step_entry, step_id_of


def test_step_id_from_string():
    assert step_id_of("explore") == "explore"
    assert step_id_of("explore if flag") == "explore"


def test_step_id_from_prompt_entry():
    # Legacy prompt: fallback still resolves an id.
    assert step_id_of({"prompt": "ux-critique"}) == "ux-critique"
    assert step_id_of({"id": "review", "prompt": "review"}) == "review"
    assert step_id_of({"id": "one-off", "prompt": "custom-role"}) == "one-off"


def test_step_id_from_id_entry_without_prompt():
    # Canonical form: id + routing, no prompt:.
    assert step_id_of({"id": "design-review", "on_failure": "design", "max_retries": 3}) == "design-review"
    assert step_id_of({"id": "implement", "on_failure": "design"}) == "implement"
    assert step_id_of({"id": "explore"}) == "explore"


def test_normalize_adds_id_from_prompt():
    assert normalize_step_entry({"prompt": "design"}) == {
        "id": "design",
        "prompt": "design",
    }


def test_normalize_preserves_routing_without_prompt():
    entry = {"id": "design-review", "on_failure": "design", "max_retries": 3}
    assert normalize_step_entry(entry) == entry
    assert normalize_step_entry("explore") == {"id": "explore"}
