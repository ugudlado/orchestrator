"""T-1: RED prose-contract tests for overlay lifecycle parity in workflow-learner."""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_LEARNER_SKILL = os.path.join(_REPO_ROOT, "skills", "workflow-learner", "SKILL.md")


def _read_learner_skill() -> str:
    with open(_LEARNER_SKILL, "r", encoding="utf-8") as f:
        return f.read()


def _section(content: str, heading: str, next_heading: str | None = None) -> str:
    start = content.find(heading)
    assert start != -1, f"workflow-learner SKILL.md must contain {heading}"
    if next_heading is None:
        end = len(content)
    else:
        end = content.find(next_heading, start)
        if end == -1:
            end = len(content)
    return content[start:end]


def test_rule_effectiveness_scan_includes_agent_overlay_files() -> None:
    section = _section(
        _read_learner_skill(),
        "### 5b. Rule Effectiveness Update (every invocation)",
        "### 5b-decay. Rule Decay Evaluation (every 5th invocation)",
    )
    assert ".orchestrator/agents/*.md" in section, (
        "section 5b must include .orchestrator/agents/*.md in effectiveness scan targets"
    )


def test_rule_decay_scan_applies_same_thresholds_to_overlay_rules() -> None:
    section = _section(
        _read_learner_skill(),
        "### 5b-decay. Rule Decay Evaluation (every 5th invocation)",
        "### 5c. Adaptive Quality Bar (every invocation)",
    )
    assert ".orchestrator/agents/*.md" in section, (
        "section 5b-decay must scan .orchestrator/agents/*.md with existing ineffective thresholds"
    )
    assert "hits == 0 AND (K - cycle) > 5" in section, (
        "section 5b-decay must keep existing ineffective threshold rules for overlays"
    )
    assert "misses / (hits + misses) > 0.7" in section, (
        "section 5b-decay must keep existing miss-rate threshold for overlays"
    )


def test_overlay_mutation_scope_is_learned_comment_only() -> None:
    section_5b = _section(
        _read_learner_skill(),
        "### 5b. Rule Effectiveness Update (every invocation)",
        "### 5b-decay. Rule Decay Evaluation (every 5th invocation)",
    )
    section_decay = _section(
        _read_learner_skill(),
        "### 5b-decay. Rule Decay Evaluation (every 5th invocation)",
        "### 5c. Adaptive Quality Bar (every invocation)",
    )
    combined = f"{section_5b}\n{section_decay}"
    assert re.search(r"ONLY remove.*<!-- learned:", combined), (
        "lifecycle contract must explicitly limit removals to learned-stamped overlay entries"
    )
    assert re.search(r"manual overlay prose|never touch manual", combined, re.IGNORECASE), (
        "lifecycle contract must explicitly protect manual overlay prose from mutation"
    )


def test_overlay_metadata_missing_hits_and_misses_defaults_to_zero() -> None:
    section = _section(
        _read_learner_skill(),
        "### 5b. Rule Effectiveness Update (every invocation)",
        "### 5c. Adaptive Quality Bar (every invocation)",
    )
    assert re.search(r"hits.*default 0", section, re.IGNORECASE), (
        "overlay metadata parsing must default missing hits to 0"
    )
    assert re.search(r"misses.*default 0", section, re.IGNORECASE), (
        "overlay metadata parsing must default missing misses to 0"
    )
