"""Tests for orchestrator_next.packs — pack add/remove/list (ORC-119)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator_next import packs


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False))


def _make_minimal_pack(root: Path, *, name: str = "widgets", protocol: int = 1,
                        step_id: str = "do-thing", model: str = "sonnet") -> Path:
    """A pack with one script step + one agent step + one workflow referencing both."""
    _write_yaml(root / "pack.yaml", {
        "name": name, "version": "1.0.0", "protocol": protocol,
        "description": "test pack",
    })

    # script step
    script_dir = root / "steps" / step_id
    script_dir.mkdir(parents=True)
    _write_yaml(script_dir / "contract.yaml", {"id": step_id, "version": 1, "run": "script.sh"})
    (script_dir / "script.sh").write_text("#!/usr/bin/env bash\necho '{}'\n")
    (script_dir / "script.sh").chmod(0o755)

    # agent step
    agent_step_id = f"{step_id}-agent"
    agent_dir = root / "steps" / agent_step_id
    agent_dir.mkdir(parents=True)
    _write_yaml(agent_dir / "contract.yaml", {"id": agent_step_id, "version": 1, "model": model})
    (agent_dir / "prompt.md").write_text("Do the thing.\n")

    # workflow referencing both steps
    _write_yaml(root / "workflows" / f"{name}.yaml", {
        "steps": [step_id, agent_step_id],
    })

    return root


@pytest.fixture
def untracked_config_root(tmp_path, monkeypatch):
    """A tmp config root that is NOT git-tracked (pytest tmp_path is never
    inside a git work tree with tracked files by default)."""
    root = tmp_path / "config_root"
    root.mkdir()
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(root))
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_WORKFLOW_DIR", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", raising=False)
    return root


# --------------------------------------------------------------------------
# add / list / remove round-trip
# --------------------------------------------------------------------------

def test_add_list_remove_round_trip(tmp_path, untracked_config_root):
    src = tmp_path / "src_pack"
    _make_minimal_pack(src)

    name = packs.pack_add(str(src), repo_root=str(tmp_path))
    assert name == "widgets"

    # files landed
    assert (untracked_config_root / "workflows" / "widgets.yaml").is_file()
    assert (untracked_config_root / "steps" / "do-thing" / "contract.yaml").is_file()
    assert (untracked_config_root / "steps" / "do-thing-agent" / "prompt.md").is_file()

    # receipt written
    receipts = json.loads((untracked_config_root / ".packs.json").read_text())
    assert "widgets" in receipts
    assert receipts["widgets"]["version"] == "1.0.0"
    assert receipts["widgets"]["protocol"] == 1
    assert len(receipts["widgets"]["files"]) >= 3

    # list surfaces it
    rows = packs.pack_list()
    names = {r["name"] for r in rows}
    assert "widgets" in names
    row = next(r for r in rows if r["name"] == "widgets")
    assert row["version"] == "1.0.0"
    assert row["protocol"] == 1

    # remove deletes exactly the receipt-listed files
    packs.pack_remove("widgets")
    assert not (untracked_config_root / "workflows" / "widgets.yaml").is_file()
    assert not (untracked_config_root / "steps" / "do-thing").exists()
    assert not (untracked_config_root / "steps" / "do-thing-agent").exists()
    # steps/ parent survives (other packs could live there)
    assert (untracked_config_root / "steps").is_dir()

    receipts_after = json.loads((untracked_config_root / ".packs.json").read_text())
    assert "widgets" not in receipts_after


def test_remove_unknown_pack_refuses(untracked_config_root):
    with pytest.raises(packs.PackError, match="no installed pack"):
        packs.pack_remove("nope")


# --------------------------------------------------------------------------
# collision refusal
# --------------------------------------------------------------------------

def test_add_refuses_on_step_id_collision(tmp_path, untracked_config_root):
    src1 = tmp_path / "src1"
    _make_minimal_pack(src1, name="pack-a", step_id="shared-step")
    packs.pack_add(str(src1), repo_root=str(tmp_path))

    src2 = tmp_path / "src2"
    _make_minimal_pack(src2, name="pack-b", step_id="shared-step")

    with pytest.raises(packs.PackError, match="conflicts"):
        packs.pack_add(str(src2), repo_root=str(tmp_path))

    # no partial copy from the failed pack-b install
    rows = {r["name"] for r in packs.pack_list()}
    assert "pack-b" not in rows


def test_add_refuses_when_already_installed(tmp_path, untracked_config_root):
    src = tmp_path / "src_pack"
    _make_minimal_pack(src)
    packs.pack_add(str(src), repo_root=str(tmp_path))

    with pytest.raises(packs.PackError, match="already installed"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


# --------------------------------------------------------------------------
# invalid-pack refusal
# --------------------------------------------------------------------------

def test_add_refuses_bad_pack_yaml(tmp_path, untracked_config_root):
    src = tmp_path / "bad_pack"
    src.mkdir()
    (src / "pack.yaml").write_text("not: valid: yaml: [")

    with pytest.raises(packs.PackError):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_add_refuses_missing_required_keys(tmp_path, untracked_config_root):
    src = tmp_path / "incomplete_pack"
    src.mkdir()
    _write_yaml(src / "pack.yaml", {"name": "incomplete"})  # no version/protocol

    with pytest.raises(packs.PackError, match="version|protocol"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_add_refuses_unsupported_protocol(tmp_path, untracked_config_root):
    src = tmp_path / "future_pack"
    _make_minimal_pack(src, name="future", protocol=99)

    with pytest.raises(packs.PackError, match="protocol"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_add_refuses_contract_id_mismatch(tmp_path, untracked_config_root):
    src = tmp_path / "mismatch_pack"
    _write_yaml(src / "pack.yaml", {"name": "mismatch", "version": "1.0.0", "protocol": 1})
    step_dir = src / "steps" / "real-name"
    step_dir.mkdir(parents=True)
    _write_yaml(step_dir / "contract.yaml", {"id": "wrong-name", "version": 1, "run": "script.sh"})
    (step_dir / "script.sh").write_text("#!/usr/bin/env bash\necho '{}'\n")

    with pytest.raises(packs.PackError, match="does not match"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_add_refuses_bad_contract_yaml(tmp_path, untracked_config_root):
    src = tmp_path / "bad_contract_pack"
    _write_yaml(src / "pack.yaml", {"name": "badcontract", "version": "1.0.0", "protocol": 1})
    step_dir = src / "steps" / "broken-step"
    step_dir.mkdir(parents=True)
    (step_dir / "contract.yaml").write_text("id: [unterminated")

    with pytest.raises(packs.PackError, match="invalid YAML"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_model_alias_lint_warns_not_fails(tmp_path, untracked_config_root, capsys):
    src = tmp_path / "concrete_model_pack"
    _make_minimal_pack(src, name="concrete", model="claude-opus-4-7")

    name = packs.pack_add(str(src), repo_root=str(tmp_path))
    assert name == "concrete"  # install succeeds despite the lint warning

    err = capsys.readouterr().err
    assert "looks like a concrete model id" in err


# --------------------------------------------------------------------------
# git-URL source (local file:// clone, no network)
# --------------------------------------------------------------------------

def test_add_from_git_url(tmp_path, untracked_config_root):
    src = tmp_path / "git_src"
    _make_minimal_pack(src, name="gitpack")

    git_env = packs._clean_git_env()
    subprocess.run(["git", "init", "-q"], cwd=src, check=True, env=git_env)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=src, check=True, env=git_env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True, env=git_env)
    subprocess.run(["git", "add", "-A"], cwd=src, check=True, env=git_env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=src, check=True, env=git_env)

    url = f"file://{src}"
    name = packs.pack_add(url, repo_root=str(tmp_path))
    assert name == "gitpack"

    receipts = json.loads((untracked_config_root / ".packs.json").read_text())
    assert receipts["gitpack"]["source"] == url
    assert (untracked_config_root / "workflows" / "gitpack.yaml").is_file()


# --------------------------------------------------------------------------
# untracked-root safety check
# --------------------------------------------------------------------------

def test_add_refuses_against_tracked_config_root(tmp_path, monkeypatch):
    tracked_root = tmp_path / "tracked_config"
    tracked_root.mkdir()
    (tracked_root / "workflows").mkdir()
    (tracked_root / "placeholder.yaml").write_text("x: 1\n")

    git_env = packs._clean_git_env()
    subprocess.run(["git", "init", "-q"], cwd=tracked_root, check=True, env=git_env)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tracked_root, check=True, env=git_env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tracked_root, check=True, env=git_env)
    subprocess.run(["git", "add", "-A"], cwd=tracked_root, check=True, env=git_env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tracked_root, check=True, env=git_env)

    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(tracked_root))
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)

    src = tmp_path / "src_pack"
    _make_minimal_pack(src)

    with pytest.raises(packs.PackError, match="tracked"):
        packs.pack_add(str(src), repo_root=str(tmp_path))

    with pytest.raises(packs.PackError, match="tracked"):
        packs.pack_remove("anything")


def test_remove_refuses_against_tracked_config_root_this_checkout():
    """Sanity: this checkout's own git-tracked config/ must refuse pack ops."""
    import os

    checkout_config = Path(__file__).resolve().parents[2] / "config"
    assert packs._is_git_tracked_dir(checkout_config)

    old = os.environ.get("ORCHESTRATOR_CONFIG")
    os.environ["ORCHESTRATOR_CONFIG"] = str(checkout_config)
    try:
        with pytest.raises(packs.PackError, match="tracked"):
            packs.pack_add(str(checkout_config), repo_root=str(checkout_config))
    finally:
        if old is None:
            os.environ.pop("ORCHESTRATOR_CONFIG", None)
        else:
            os.environ["ORCHESTRATOR_CONFIG"] = old


def test_list_works_against_tracked_config_root(monkeypatch):
    """pack list has no untracked-root restriction — works read-only anywhere."""
    checkout_config = Path(__file__).resolve().parents[2] / "config"
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(checkout_config))
    monkeypatch.delenv("ORCHESTRATOR_HOME", raising=False)

    rows = packs.pack_list()
    names = {r["name"] for r in rows}
    assert "core" in names  # config/pack.yaml synthesized row


# --------------------------------------------------------------------------
# CLI main()
# --------------------------------------------------------------------------

def test_cli_main_add_list_remove(tmp_path, untracked_config_root, capsys):
    src = tmp_path / "cli_pack"
    _make_minimal_pack(src, name="clipack")

    assert packs.main(["add", str(src)]) == 0
    assert "installed pack 'clipack'" in capsys.readouterr().out

    assert packs.main(["list"]) == 0
    assert "clipack" in capsys.readouterr().out

    assert packs.main(["remove", "clipack"]) == 0
    assert "removed pack 'clipack'" in capsys.readouterr().out


def test_cli_main_unknown_subcommand(untracked_config_root, capsys):
    assert packs.main(["bogus"]) == 1
    assert "unknown pack subcommand" in capsys.readouterr().err
