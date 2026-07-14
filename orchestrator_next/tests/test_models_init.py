"""Tests for `orchestrator models init` (D2)."""
from __future__ import annotations

from pathlib import Path

import yaml

from orchestrator_next.model_routes import resolve_route
from orchestrator_next.models_init import main


def _fake_bin(tmp_path: Path, *names: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        f = bin_dir / name
        f.write_text("#!/bin/sh\nexit 0\n")
        f.chmod(0o755)
    return bin_dir


def _no_env_overrides(monkeypatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", raising=False)


def test_init_writes_chains_from_bundled_seed_when_no_layer_has_tools(monkeypatch, tmp_path, capsys):
    """Fresh machine: no config root set, no ~/.orchestrator/models.yaml yet
    → seeds from the bundled config/models.example.yaml."""
    home = tmp_path / "home"
    bin_dir = _fake_bin(tmp_path, "claude", "cursor-agent")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    _no_env_overrides(monkeypatch)

    rc = main([])
    out = capsys.readouterr().out

    assert rc == 0
    out_path = home / ".orchestrator" / "models.yaml"
    assert out_path.is_file()
    data = yaml.safe_load(out_path.read_text())
    assert set(data["models"].keys()) == {"opus", "sonnet", "haiku", "composer"}
    # composer chain: cursor available -> first candidate is cursor
    assert data["models"]["composer"][0]["subprocess"] == "cursor"
    assert "composer -> cursor" in out


def test_init_keeps_only_available_candidates_in_chain(monkeypatch, tmp_path):
    """composer's chain is [cursor, claude] in the seed; if only claude is on
    PATH, the written chain should drop the unavailable cursor candidate."""
    home = tmp_path / "home"
    bin_dir = _fake_bin(tmp_path, "claude")  # no cursor-agent
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    _no_env_overrides(monkeypatch)

    rc = main([])
    assert rc == 0

    out_path = home / ".orchestrator" / "models.yaml"
    data = yaml.safe_load(out_path.read_text())
    composer_chain = data["models"]["composer"]
    assert len(composer_chain) == 1
    assert composer_chain[0]["subprocess"] == "claude"


def test_init_refuses_to_overwrite_without_force(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    (home / ".orchestrator").mkdir(parents=True)
    existing = home / ".orchestrator" / "models.yaml"
    existing.write_text("models: {}\n")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(_fake_bin(tmp_path, "claude")))
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    _no_env_overrides(monkeypatch)

    rc = main([])
    err = capsys.readouterr().err

    assert rc != 0
    assert "--force" in err
    assert existing.read_text() == "models: {}\n"  # untouched


def test_init_force_overwrites_existing_file(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".orchestrator").mkdir(parents=True)
    existing = home / ".orchestrator" / "models.yaml"
    existing.write_text("models: {}\n")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(_fake_bin(tmp_path, "claude")))
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    _no_env_overrides(monkeypatch)

    rc = main(["--force"])
    assert rc == 0
    data = yaml.safe_load(existing.read_text())
    assert "opus" in data["models"]


def test_init_written_file_round_trips_through_resolve_route(monkeypatch, tmp_path):
    """The file models init writes must be directly usable by the D3
    resolver — round-trip check, not just a structural assertion. The home
    file is self-sufficient (carries its own tools: block), so this must
    resolve correctly even with NO config-root layer at all."""
    home = tmp_path / "home"
    bin_dir = _fake_bin(tmp_path, "claude", "cursor-agent")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    _no_env_overrides(monkeypatch)

    assert main([]) == 0

    # No config_root layer at all — user_home is the only layer with data.
    route = resolve_route("composer", None)
    assert route["subprocess"] == "cursor"
    assert route["model_id"] == "composer-2.5"
    assert route["is_fallback"] is False

    data = yaml.safe_load((home / ".orchestrator" / "models.yaml").read_text())
    assert data["tools"]["cursor"]["binary"] == "cursor-agent"
    assert "args_template" in data["tools"]["cursor"]  # full entry, not partial (D1 wholesale-wins)


def test_init_binds_alias_to_cursor_when_cursor_agent_present(monkeypatch, tmp_path):
    """Plan example: composer -> cursor when cursor-agent is present."""
    home = tmp_path / "home"
    bin_dir = _fake_bin(tmp_path, "cursor-agent")  # cursor only, no claude
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    _no_env_overrides(monkeypatch)

    assert main([]) == 0
    data = yaml.safe_load((home / ".orchestrator" / "models.yaml").read_text())
    assert data["models"]["composer"][0]["subprocess"] == "cursor"
    # opus/sonnet/haiku have no available candidate (claude absent) -> dropped entirely
    assert "opus" not in data["models"] or not data["models"]["opus"]
