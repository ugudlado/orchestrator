"""Tests for the optional prompt-optimizer doctor check."""
from __future__ import annotations

from orchestrator_next.doctor import check_prompt_optimizer, run_all


def test_prompt_optimizer_warns_when_unset(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_PROMPT_OPTIMIZER_DIR", raising=False)

    result = check_prompt_optimizer()

    assert result.name == "prompt optimizer"
    assert result.status == "WARN"
    assert "not set" in result.detail


def test_prompt_optimizer_warns_when_directory_is_missing(tmp_path, monkeypatch):
    missing = tmp_path / "prompt-optimizer"
    monkeypatch.setenv("ORCHESTRATOR_PROMPT_OPTIMIZER_DIR", str(missing))

    result = check_prompt_optimizer()

    assert result.status == "WARN"
    assert "not a directory" in result.detail


def test_prompt_optimizer_warns_when_uv_is_missing(tmp_path, monkeypatch):
    optimizer_dir = tmp_path / "prompt-optimizer"
    optimizer_dir.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_PROMPT_OPTIMIZER_DIR", str(optimizer_dir))
    monkeypatch.setenv("PATH", "")

    result = check_prompt_optimizer()

    assert result.status == "WARN"
    assert "uv is not on PATH" in result.detail


def test_prompt_optimizer_passes_when_directory_and_uv_exist(tmp_path, monkeypatch):
    optimizer_dir = tmp_path / "prompt-optimizer"
    optimizer_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(0o755)
    monkeypatch.setenv("ORCHESTRATOR_PROMPT_OPTIMIZER_DIR", str(optimizer_dir))
    monkeypatch.setenv("PATH", str(bin_dir))

    result = check_prompt_optimizer()

    assert result.status == "PASS"
    assert str(optimizer_dir) in result.detail


def test_run_all_includes_prompt_optimizer_check(tmp_path, monkeypatch, capsys):
    config_root = tmp_path / "config"
    (config_root / "workflows").mkdir(parents=True)
    (config_root / "steps").mkdir()
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(config_root))
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_PROMPT_OPTIMIZER_DIR", raising=False)

    result = run_all()

    assert result == 0
    assert "prompt optimizer" in capsys.readouterr().out
