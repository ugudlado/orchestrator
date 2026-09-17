"""Tests for orchestrator config pull."""
from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator_next.config_pull import default_pack_name, generate_claude_plugin, pull_into_pack


def _make_source(tmp_path: Path) -> Path:
    cfg = tmp_path / "src" / "config"
    (cfg / "workflows").mkdir(parents=True)
    (cfg / "workflows" / "feature.yaml").write_text("steps:\n  - explore\n")
    (cfg / "lib").mkdir()
    (cfg / "models.yaml").write_text("models: {}\n")
    step = cfg / "steps" / "explore"
    step.mkdir(parents=True)
    (step / "contract.yaml").write_text("id: explore\nversion: 1\nprompt: SKILL.md\n")
    (step / "SKILL.md").write_text(
        "---\nname: explore\ndescription: brief\n---\n\n# Explore\n"
    )
    (step / "scenarios").mkdir()
    (step / "scenarios" / "train.jsonl").write_text(
        '{"id":"a","scenario":"s","expect":["e"]}\n'
    )
    (step / "runs").mkdir()
    (step / "runs" / "noise.json").write_text("{}\n")
    return cfg


def test_pull_lands_under_named_pack(tmp_path):
    cfg = _make_source(tmp_path)
    repo = tmp_path / "consumer"
    repo.mkdir()
    lock = pull_into_pack(
        cfg,
        repo,
        "mypack",
        export_skills=False,
        source_label=str(cfg),
        source_sha="abc",
    )
    orch = repo / ".orchestrator" / "mypack"
    assert (orch / "workflows" / "feature.yaml").is_file()
    assert (orch / "steps" / "explore" / "SKILL.md").is_file()
    assert not (orch / "steps" / "explore" / "runs").exists()
    assert not (repo / "skills").exists()
    assert lock["pack"] == "mypack"
    assert yaml.safe_load((orch / "config-lock.yaml").read_text())["source_sha"] == "abc"


def test_pull_skills_flag_symlinks(tmp_path):
    cfg = _make_source(tmp_path)
    repo = tmp_path / "consumer"
    repo.mkdir()
    lock = pull_into_pack(
        cfg,
        repo,
        "mypack",
        export_skills=True,
        source_label=str(cfg),
        source_sha=None,
    )
    link = repo / "skills" / "explore"
    assert link.is_symlink()
    assert link.resolve() == (
        repo / ".orchestrator" / "mypack" / "steps" / "explore"
    ).resolve()
    assert lock["skills"] == ["explore"]


def test_default_pack_name_from_url_and_path(tmp_path):
    assert default_pack_name("https://github.com/ugudlado/workflows.git") == (
        "workflows"
    )
    d = tmp_path / "my-config"
    d.mkdir()
    assert default_pack_name(str(d)) == "my-config"


def test_generate_claude_plugin_writes_to_default_location(tmp_path):
    cfg = _make_source(tmp_path)
    repo = tmp_path / "consumer"
    repo.mkdir()
    pull_into_pack(
        cfg, repo, "mypack", export_skills=False, source_label=str(cfg), source_sha="abc",
    )
    hint = generate_claude_plugin(repo, "mypack")
    plugin_dir = repo / ".orchestrator" / "plugins" / "mypack" / "claude"
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
    assert hint is not None
    assert "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1" in hint
    assert str(plugin_dir.resolve()) in hint


def test_generate_claude_plugin_failure_does_not_raise(tmp_path):
    """A pack with no steps/ dir can't generate a plugin — must warn, not raise."""
    repo = tmp_path / "consumer"
    (repo / ".orchestrator" / "broken").mkdir(parents=True)
    assert generate_claude_plugin(repo, "broken") is None


def test_config_pull_cli_generates_plugin_by_default(tmp_path, monkeypatch, capsys):
    from orchestrator_next.config_pull import main as pull_main

    cfg = _make_source(tmp_path)
    repo = tmp_path / "consumer"
    repo.mkdir()
    rc = pull_main([str(cfg), "mypack", "--repo", str(repo)])
    assert rc == 0
    plugin_dir = repo / ".orchestrator" / "plugins" / "mypack" / "claude"
    assert (plugin_dir / ".claude-plugin" / "plugin.json").is_file()
    out = capsys.readouterr().out
    assert "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1" in out


def test_config_pull_cli_no_plugin_skips_generation(tmp_path):
    from orchestrator_next.config_pull import main as pull_main

    cfg = _make_source(tmp_path)
    repo = tmp_path / "consumer"
    repo.mkdir()
    rc = pull_main([str(cfg), "mypack", "--repo", str(repo), "--no-plugin"])
    assert rc == 0
    plugin_dir = repo / ".orchestrator" / "plugins" / "mypack" / "claude"
    assert not plugin_dir.exists()
