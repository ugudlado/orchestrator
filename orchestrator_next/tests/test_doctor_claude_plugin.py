"""Tests for doctor.check_claude_plugin — Claude plugin dir presence/freshness."""
from __future__ import annotations

from pathlib import Path

from orchestrator_next.config_pull import generate_claude_plugin, pull_into_pack
from orchestrator_next.doctor import check_claude_plugin


def _make_source(tmp_path: Path) -> Path:
    cfg = tmp_path / "src" / "config"
    (cfg / "workflows").mkdir(parents=True)
    (cfg / "workflows" / "feature.yaml").write_text("steps:\n  - explore\n")
    step = cfg / "steps" / "explore"
    step.mkdir(parents=True)
    (step / "contract.yaml").write_text("id: explore\nversion: 1\nprompt: SKILL.md\n")
    (step / "SKILL.md").write_text(
        "---\nname: explore\ndescription: brief\n---\n\n# Explore\n"
    )
    return cfg


def _pull(tmp_path: Path, repo: Path) -> Path:
    cfg = _make_source(tmp_path)
    pull_into_pack(
        cfg, repo, "mypack", export_skills=False, source_label=str(cfg), source_sha="abc",
    )
    return repo / ".orchestrator" / "mypack"


def test_check_claude_plugin_passes_with_no_packs(tmp_path):
    result = check_claude_plugin(tmp_path)
    assert result.status == "PASS"


def test_check_claude_plugin_passes_when_ungenerated(tmp_path):
    """No plugin dir is a valid choice (--no-plugin) — not a WARN."""
    _pull(tmp_path, tmp_path)
    result = check_claude_plugin(tmp_path)
    assert result.status == "PASS"
    assert "no generated plugin" in result.detail


def test_check_claude_plugin_passes_when_fresh(tmp_path):
    _pull(tmp_path, tmp_path)
    generate_claude_plugin(tmp_path, "mypack")
    result = check_claude_plugin(tmp_path)
    assert result.status == "PASS"
    assert "fresh" in result.detail


def test_check_claude_plugin_warns_when_stale(tmp_path):
    pack_dir = _pull(tmp_path, tmp_path)
    generate_claude_plugin(tmp_path, "mypack")
    # Hand-edit the pack after generating — the plugin is now stale.
    (pack_dir / "steps" / "explore" / "SKILL.md").write_text(
        "---\nname: explore\ndescription: changed\n---\n\n# Explore v2\n"
    )
    result = check_claude_plugin(tmp_path)
    assert result.status == "WARN"
    assert "stale" in result.detail
