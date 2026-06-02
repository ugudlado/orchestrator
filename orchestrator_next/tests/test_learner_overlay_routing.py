"""T-5: prose-contract tests for workflow-learner agent_improvement overlay routing."""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_LEARNER_SKILL = os.path.join(_REPO_ROOT, "skills", "workflow-learner", "SKILL.md")


def _read_learner_skill() -> str:
    with open(_LEARNER_SKILL, "r", encoding="utf-8") as f:
        return f.read()


def _agent_improvement_section(content: str) -> str:
    """Extract §4a agent_improvement routing prose (table row + after-classification note)."""
    start = content.find("#### 4a. Classifier")
    assert start != -1, "workflow-learner SKILL.md must contain §4a classifier section"
    end = content.find("**Routing decision tree:**", start)
    if end == -1:
        end = len(content)
    return content[start:end]


def test_agent_improvement_routes_to_orchestrator_agents_overlay() -> None:
    section = _agent_improvement_section(_read_learner_skill())
    assert ".orchestrator/agents/" in section, (
        "agent_improvement must route to .orchestrator/agents/<name>.md overlay"
    )


def test_agent_improvement_requires_learned_stamp() -> None:
    section = _agent_improvement_section(_read_learner_skill())
    assert "<!-- learned:" in section, (
        "agent_improvement overlay entries must carry <!-- learned: ... --> metadata"
    )
    assert not re.search(
        r"agent_improvement.*No metadata comment, no stamp",
        section,
        re.IGNORECASE | re.DOTALL,
    ), "agent_improvement must not instruct 'No metadata comment, no stamp'"


def test_agent_improvement_validates_base_skill_exists() -> None:
    section = _agent_improvement_section(_read_learner_skill())
    assert re.search(r"skills/<name>/SKILL\.md.*exist", section, re.IGNORECASE | re.DOTALL), (
        "agent_improvement must validate skills/<name>/SKILL.md exists before scaffolding overlay"
    )


def test_agent_improvement_does_not_write_skills_skill_md() -> None:
    section = _agent_improvement_section(_read_learner_skill())
    table_row = re.search(
        r"\|\s*`agent_improvement`[^|]*\|\s*([^|]+)\|\s*([^|]+)\|",
        section,
    )
    assert table_row is not None, "agent_improvement row must exist in classifier table"
    target_col = table_row.group(2)
    assert ".orchestrator/agents/" in target_col, (
        "agent_improvement table target must be the repo overlay path"
    )
    assert "`skills/<name>/SKILL.md`" not in target_col.split("—")[0], (
        "agent_improvement table target column must not name skills/<name>/SKILL.md as the write target"
    )
    after = re.search(
        r"`agent_improvement`.*?→.*?edit `skills/<name>/SKILL\.md`",
        section,
        re.IGNORECASE | re.DOTALL,
    )
    assert after is None, (
        "agent_improvement after-classification prose must not instruct editing skills/<name>/SKILL.md"
    )
