"""Tests for orchestrator_next.packs — pack add/remove/list (ORC-119).

pack add/remove always target <cwd>/.orchestrator/config/ — vendoring into
wherever the command was run from is the only mode (no --into flag, no
tracked-root guard: vendoring into a tracked repo is the point).
"""
from __future__ import annotations

import json
import os
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


def _init_git_repo(path: Path) -> None:
    git_env = packs._clean_git_env()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, env=git_env)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True, env=git_env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, env=git_env)


@pytest.fixture
def cwd_repo(tmp_path, monkeypatch):
    """A tmp git repo, chdir'd into — pack add/remove vendor into
    <cwd>/.orchestrator/config/, i.e. this repo."""
    repo = tmp_path / "consumer_repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "README.md").write_text("hi\n")
    git_env = packs._clean_git_env()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=git_env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, env=git_env)

    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_WORKFLOW_DIR", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", raising=False)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def vendored_root(cwd_repo):
    return cwd_repo / ".orchestrator" / "config"


# --------------------------------------------------------------------------
# add / list / remove round-trip
# --------------------------------------------------------------------------

def test_add_list_remove_round_trip(tmp_path, cwd_repo, vendored_root):
    src = tmp_path / "src_pack"
    _make_minimal_pack(src)

    name = packs.pack_add(str(src), repo_root=str(tmp_path))
    assert name == "widgets"

    # files landed under <cwd>/.orchestrator/config/
    assert (vendored_root / "workflows" / "widgets.yaml").is_file()
    assert (vendored_root / "steps" / "do-thing" / "contract.yaml").is_file()
    assert (vendored_root / "steps" / "do-thing-agent" / "prompt.md").is_file()

    # receipt written, with commit=None for a plain local-path source
    receipts = json.loads((vendored_root / ".packs.json").read_text())
    assert "widgets" in receipts
    assert receipts["widgets"]["version"] == "1.0.0"
    assert receipts["widgets"]["protocol"] == 1
    assert receipts["widgets"]["commit"] is None
    assert len(receipts["widgets"]["files"]) >= 3

    # list surfaces it (config_root() resolves here via the repo-local fallback)
    os.environ["REPO_ROOT"] = str(cwd_repo)
    try:
        rows = packs.pack_list()
    finally:
        os.environ.pop("REPO_ROOT", None)
    names = {r["name"] for r in rows}
    assert "widgets" in names
    row = next(r for r in rows if r["name"] == "widgets")
    assert row["version"] == "1.0.0"
    assert row["protocol"] == 1

    # remove deletes exactly the receipt-listed files
    packs.pack_remove("widgets")
    assert not (vendored_root / "workflows" / "widgets.yaml").is_file()
    assert not (vendored_root / "steps" / "do-thing").exists()
    assert not (vendored_root / "steps" / "do-thing-agent").exists()
    # steps/ parent survives (other packs could live there)
    assert (vendored_root / "steps").is_dir()

    receipts_after = json.loads((vendored_root / ".packs.json").read_text())
    assert "widgets" not in receipts_after


def test_remove_unknown_pack_refuses(cwd_repo):
    with pytest.raises(packs.PackError, match="no installed pack"):
        packs.pack_remove("nope")


# --------------------------------------------------------------------------
# gitignore warning
# --------------------------------------------------------------------------

def test_warns_when_gitignored(tmp_path, cwd_repo, capsys):
    (cwd_repo / ".gitignore").write_text(".orchestrator/\n")
    git_env = packs._clean_git_env()
    subprocess.run(["git", "add", "-A"], cwd=cwd_repo, check=True, env=git_env)
    subprocess.run(["git", "commit", "-q", "-m", "add gitignore"], cwd=cwd_repo, check=True, env=git_env)

    src = tmp_path / "src_pack"
    _make_minimal_pack(src, name="ignoretest")
    packs.pack_add(str(src), repo_root=str(tmp_path))

    err = capsys.readouterr().err
    assert "gitignore" in err
    assert "git add -f" in err


# --------------------------------------------------------------------------
# collision refusal
# --------------------------------------------------------------------------

def test_add_refuses_on_step_id_collision(tmp_path, cwd_repo):
    src1 = tmp_path / "src1"
    _make_minimal_pack(src1, name="pack-a", step_id="shared-step")
    packs.pack_add(str(src1), repo_root=str(tmp_path))

    src2 = tmp_path / "src2"
    _make_minimal_pack(src2, name="pack-b", step_id="shared-step")

    with pytest.raises(packs.PackError, match="conflicts"):
        packs.pack_add(str(src2), repo_root=str(tmp_path))


def test_add_refuses_when_already_installed(tmp_path, cwd_repo):
    src = tmp_path / "src_pack"
    _make_minimal_pack(src)
    packs.pack_add(str(src), repo_root=str(tmp_path))

    with pytest.raises(packs.PackError, match="already installed"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


# --------------------------------------------------------------------------
# --force upgrade
# --------------------------------------------------------------------------

def test_force_upgrades_in_place(tmp_path, cwd_repo, vendored_root):
    src = tmp_path / "src_pack"
    _make_minimal_pack(src, name="upgradeable")
    packs.pack_add(str(src), repo_root=str(tmp_path))

    with pytest.raises(packs.PackError, match="already installed"):
        packs.pack_add(str(src), repo_root=str(tmp_path))

    (src / "steps" / "do-thing" / "script.sh").write_text("#!/usr/bin/env bash\necho '{\"v\":2}'\n")
    name = packs.pack_add(str(src), repo_root=str(tmp_path), force=True)
    assert name == "upgradeable"
    assert "v\":2" in (vendored_root / "steps" / "do-thing" / "script.sh").read_text()


# --------------------------------------------------------------------------
# invalid-pack refusal
# --------------------------------------------------------------------------

def test_add_refuses_bad_pack_yaml(tmp_path, cwd_repo):
    src = tmp_path / "bad_pack"
    src.mkdir()
    (src / "pack.yaml").write_text("not: valid: yaml: [")

    with pytest.raises(packs.PackError):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_add_refuses_missing_required_keys(tmp_path, cwd_repo):
    src = tmp_path / "incomplete_pack"
    src.mkdir()
    _write_yaml(src / "pack.yaml", {"name": "incomplete"})  # no version/protocol

    with pytest.raises(packs.PackError, match="version|protocol"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_add_refuses_unsupported_protocol(tmp_path, cwd_repo):
    src = tmp_path / "future_pack"
    _make_minimal_pack(src, name="future", protocol=99)

    with pytest.raises(packs.PackError, match="protocol"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_add_refuses_contract_id_mismatch(tmp_path, cwd_repo):
    src = tmp_path / "mismatch_pack"
    _write_yaml(src / "pack.yaml", {"name": "mismatch", "version": "1.0.0", "protocol": 1})
    step_dir = src / "steps" / "real-name"
    step_dir.mkdir(parents=True)
    _write_yaml(step_dir / "contract.yaml", {"id": "wrong-name", "version": 1, "run": "script.sh"})
    (step_dir / "script.sh").write_text("#!/usr/bin/env bash\necho '{}'\n")

    with pytest.raises(packs.PackError, match="does not match"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_add_refuses_bad_contract_yaml(tmp_path, cwd_repo):
    src = tmp_path / "bad_contract_pack"
    _write_yaml(src / "pack.yaml", {"name": "badcontract", "version": "1.0.0", "protocol": 1})
    step_dir = src / "steps" / "broken-step"
    step_dir.mkdir(parents=True)
    (step_dir / "contract.yaml").write_text("id: [unterminated")

    with pytest.raises(packs.PackError, match="invalid YAML"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_model_alias_lint_warns_not_fails(tmp_path, cwd_repo, capsys):
    src = tmp_path / "concrete_model_pack"
    _make_minimal_pack(src, name="concrete", model="claude-opus-4-7")

    name = packs.pack_add(str(src), repo_root=str(tmp_path))
    assert name == "concrete"  # install succeeds despite the lint warning

    err = capsys.readouterr().err
    assert "looks like a concrete model id" in err


# --------------------------------------------------------------------------
# git-URL source (local file:// clone, no network) — records commit sha
# --------------------------------------------------------------------------

def test_add_from_git_url(tmp_path, cwd_repo, vendored_root):
    src = tmp_path / "git_src"
    _make_minimal_pack(src, name="gitpack")
    _init_git_repo(src)
    git_env = packs._clean_git_env()
    subprocess.run(["git", "add", "-A"], cwd=src, check=True, env=git_env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=src, check=True, env=git_env)
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True, env=git_env,
    ).stdout.strip()

    url = f"file://{src}"
    name = packs.pack_add(url, repo_root=str(tmp_path))
    assert name == "gitpack"

    receipts = json.loads((vendored_root / ".packs.json").read_text())
    assert receipts["gitpack"]["source"] == url
    assert receipts["gitpack"]["commit"] == expected_sha
    assert (vendored_root / "workflows" / "gitpack.yaml").is_file()


# --------------------------------------------------------------------------
# models.yaml: copied only if absent
# --------------------------------------------------------------------------

def test_models_yaml_copied_only_if_absent(tmp_path, cwd_repo, vendored_root):
    src = tmp_path / "models_src"
    _make_minimal_pack(src, name="modelspack")
    _write_yaml(src / "models.yaml", {"models": {"sonnet": {"model_id": "claude-sonnet-5"}}})

    packs.pack_add(str(src), repo_root=str(tmp_path))
    assert (vendored_root / "models.yaml").is_file()

    src2 = tmp_path / "models_src2"
    _make_minimal_pack(src2, name="modelspack2", step_id="do-other-thing")
    _write_yaml(src2 / "models.yaml", {"models": {"sonnet": {"model_id": "claude-other"}}})
    packs.pack_add(str(src2), repo_root=str(tmp_path))

    data = yaml.safe_load((vendored_root / "models.yaml").read_text())
    assert data["models"]["sonnet"]["model_id"] == "claude-sonnet-5"


# --------------------------------------------------------------------------
# pack list — read-only, works against any config root incl. tracked ones
# --------------------------------------------------------------------------

def test_list_works_against_tracked_config_root(monkeypatch):
    """pack list has no restriction — works read-only against this checkout's
    own git-tracked config/."""
    checkout_config = Path(__file__).resolve().parents[2] / "config"
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(checkout_config))

    rows = packs.pack_list()
    names = {r["name"] for r in rows}
    assert "core" in names  # config/pack.yaml synthesized row


# --------------------------------------------------------------------------
# CLI main()
# --------------------------------------------------------------------------

def test_cli_main_add_list_remove(tmp_path, cwd_repo, capsys):
    src = tmp_path / "cli_pack"
    _make_minimal_pack(src, name="clipack")

    assert packs.main(["add", str(src)]) == 0
    assert "installed pack 'clipack'" in capsys.readouterr().out

    os.environ["REPO_ROOT"] = str(cwd_repo)
    try:
        assert packs.main(["list"]) == 0
        assert "clipack" in capsys.readouterr().out
    finally:
        os.environ.pop("REPO_ROOT", None)

    assert packs.main(["remove", "clipack"]) == 0
    assert "removed pack 'clipack'" in capsys.readouterr().out


def test_cli_main_add_force(tmp_path, cwd_repo, capsys):
    src = tmp_path / "cli_pack2"
    _make_minimal_pack(src, name="clipack2")

    assert packs.main(["add", str(src)]) == 0
    capsys.readouterr()
    assert packs.main(["add", str(src), "--force"]) == 0
    assert "installed pack 'clipack2'" in capsys.readouterr().out


def test_cli_main_unknown_subcommand(cwd_repo, capsys):
    assert packs.main(["bogus"]) == 1
    assert "unknown pack subcommand" in capsys.readouterr().err
