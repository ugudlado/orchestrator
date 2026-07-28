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
                        step_id: str = "do-thing", model: str = "sonnet",
                        with_skill: bool = True) -> Path:
    """A pack with one script step + one agent step + one workflow referencing both.

    Agent steps use ``prompt: <dir>`` resolved via pack ``skills/<dir>/``.
    """
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

    steps = [step_id]
    if with_skill:
        # agent step + colocated prompt dir under skills/
        agent_step_id = f"{step_id}-agent"
        agent_dir = root / "steps" / agent_step_id
        agent_dir.mkdir(parents=True)
        _write_yaml(agent_dir / "contract.yaml", {
            "id": agent_step_id, "version": 1, "model": model, "prompt": f"{agent_step_id}/prompt.md",
        })
        skill_dir = root / "skills" / agent_step_id
        skill_dir.mkdir(parents=True)
        (skill_dir / "prompt.md").write_text("Do the thing.\n")
        scenarios = skill_dir / "scenarios"
        scenarios.mkdir()
        (scenarios / "train.jsonl").write_text(
            '{"id":"happy","scenario":"ok","expect":["works"]}\n'
        )
        steps.append(agent_step_id)

    # workflow referencing steps
    _write_yaml(root / "workflows" / f"{name}.yaml", {
        "steps": steps,
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
    # skills vendor to <repo>/skills/, not under the config root
    assert (cwd_repo / "skills" / "do-thing-agent" / "prompt.md").is_file()
    assert (cwd_repo / "skills" / "do-thing-agent" / "scenarios" / "train.jsonl").is_file()

    # receipt written, with commit=None for a plain local-path source
    receipts = json.loads((vendored_root / ".packs.json").read_text())
    assert "widgets" in receipts
    assert receipts["widgets"]["version"] == "1.0.0"
    assert receipts["widgets"]["protocol"] == 1
    assert receipts["widgets"]["commit"] is None
    assert len(receipts["widgets"]["files"]) >= 3
    skill_receipts = [
        f for f in receipts["widgets"]["files"] if f.startswith("@repo/skills/")
    ]
    assert skill_receipts, "skills must be receipted with @repo/ prefix"

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

    # remove deletes exactly the receipt-listed files (config + repo skills)
    packs.pack_remove("widgets")
    assert not (vendored_root / "workflows" / "widgets.yaml").is_file()
    assert not (vendored_root / "steps" / "do-thing").exists()
    assert not (cwd_repo / "skills" / "do-thing-agent").exists()
    # steps/ parent survives (other packs could live there)
    assert (vendored_root / "steps").is_dir()

    receipts_after = json.loads((vendored_root / ".packs.json").read_text())
    assert "widgets" not in receipts_after


def test_remove_unknown_pack_refuses(cwd_repo):
    with pytest.raises(packs.PackError, match="no installed pack"):
        packs.pack_remove("nope")


def test_add_refuses_on_skill_name_collision(tmp_path, cwd_repo):
    src1 = tmp_path / "src1"
    _make_minimal_pack(src1, name="pack-a", step_id="alpha")
    packs.pack_add(str(src1), repo_root=str(tmp_path))

    src2 = tmp_path / "src2"
    # same skill dir name (alpha-agent) via same step_id suffix
    _make_minimal_pack(src2, name="pack-b", step_id="alpha")

    with pytest.raises(packs.PackError, match="conflicts"):
        packs.pack_add(str(src2), repo_root=str(tmp_path))


def test_validate_pack_rejects_skill_without_charter(tmp_path, cwd_repo):
    src = tmp_path / "bad_skill_pack"
    _make_minimal_pack(src, name="badskill", with_skill=False)
    empty = src / "skills" / "orphan"
    empty.mkdir(parents=True)
    (empty / "metrics.md").write_text("# metrics\n")

    with pytest.raises(packs.PackError, match="SKILL.md or prompt.md"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


def test_validate_pack_rejects_bad_scenario_jsonl(tmp_path, cwd_repo):
    src = tmp_path / "bad_jsonl_pack"
    _make_minimal_pack(src, name="badjsonl")
    train = src / "skills" / "do-thing-agent" / "scenarios" / "train.jsonl"
    train.write_text("{not-json\n")

    with pytest.raises(packs.PackError, match="invalid JSON"):
        packs.pack_add(str(src), repo_root=str(tmp_path))


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


def test_force_refuses_hand_authored_skill(tmp_path, cwd_repo):
    """--force exempts only skills this pack installed, never a user's own."""
    src = tmp_path / "src_pack"
    _make_minimal_pack(src, name="forcepack", step_id="alpha")
    packs.pack_add(str(src), repo_root=str(tmp_path))

    # A second pack ships a skill name the user hand-authored (unreceipted).
    hand = cwd_repo / "skills" / "beta-agent"
    hand.mkdir(parents=True)
    (hand / "SKILL.md").write_text("Mine, not a pack's.\n")

    src2 = tmp_path / "src_pack2"
    _make_minimal_pack(src2, name="forcepack", step_id="beta")
    with pytest.raises(packs.PackError, match="conflicts"):
        packs.pack_add(str(src2), repo_root=str(tmp_path), force=True)

    assert (hand / "SKILL.md").read_text() == "Mine, not a pack's.\n"


def test_force_refuses_hand_authored_step(tmp_path, cwd_repo, vendored_root):
    """--force exempts only steps this pack's receipts claim."""
    src = tmp_path / "step_src"
    _make_minimal_pack(src, name="steppack", step_id="alpha")
    packs.pack_add(str(src), repo_root=str(tmp_path))

    hand = vendored_root / "steps" / "beta"
    hand.mkdir(parents=True)
    _write_yaml(hand / "contract.yaml", {"id": "beta", "version": 1, "run": "script.sh"})
    (hand / "script.sh").write_text("#!/usr/bin/env bash\necho mine\n")

    src2 = tmp_path / "step_src2"
    _make_minimal_pack(src2, name="steppack", step_id="beta")
    with pytest.raises(packs.PackError, match="conflicts"):
        packs.pack_add(str(src2), repo_root=str(tmp_path), force=True)

    assert "echo mine" in (hand / "script.sh").read_text()


def test_force_refuses_hand_authored_workflow(tmp_path, cwd_repo, vendored_root):
    """--force exempts only workflows this pack's receipts claim."""
    src = tmp_path / "wf_src"
    _make_minimal_pack(src, name="wfpack", step_id="alpha")
    packs.pack_add(str(src), repo_root=str(tmp_path))

    hand = vendored_root / "workflows" / "handmade.yaml"
    hand.parent.mkdir(parents=True, exist_ok=True)
    hand.write_text("steps: [mine]\n")

    # Same pack, upgraded to also ship a workflow the user hand-wrote. Its
    # receipt claims wfpack.yaml only, so handmade.yaml stays off-limits.
    src2 = tmp_path / "wf_src2"
    _make_minimal_pack(src2, name="wfpack", step_id="alpha")
    _write_yaml(src2 / "workflows" / "handmade.yaml", {"steps": ["alpha"]})
    with pytest.raises(packs.PackError, match="conflicts"):
        packs.pack_add(str(src2), repo_root=str(tmp_path), force=True)

    assert hand.read_text() == "steps: [mine]\n"


def test_add_refuses_stray_file_under_pack_skills(tmp_path, cwd_repo):
    """Non-directory entries under skills/ would be copied outside conflict
    detection and deleted by a later pack remove."""
    src = tmp_path / "stray_pack"
    _make_minimal_pack(src, name="straypack")
    (src / "skills" / "README.md").write_text("pack docs\n")

    user_readme = cwd_repo / "skills" / "README.md"
    user_readme.parent.mkdir(parents=True, exist_ok=True)
    user_readme.write_text("user's own notes\n")

    with pytest.raises(packs.PackError, match="only skill directories"):
        packs.pack_add(str(src), repo_root=str(tmp_path))

    assert user_readme.read_text() == "user's own notes\n"


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


# --------------------------------------------------------------------------
# End-to-end resolution of a vendored skill — no ORCHESTRATOR_SKILLS_TEST_OVERRIDE.
#
# The override short-circuits skill_search_dirs() before the <repo>/skills
# branch, so every override-based test passes even when repo-root derivation
# is wrong. These two exercise the real path under both config-discovery
# routes.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Install-time validation resolves shared skills the pack doesn't ship.
#
# Validation must search the pack's skills/ first, then the search path the
# *target repo* really uses at runtime — a pack referencing a shared skill
# must not fail install-time on a skill that would resolve fine.
# --------------------------------------------------------------------------

def _make_pack_referencing_skill(root: Path, *, name: str, prompt: str) -> Path:
    """A pack whose only agent step names ``prompt`` but ships no skills/."""
    _write_yaml(root / "pack.yaml", {
        "name": name, "version": "1.0.0", "protocol": 1, "description": "test pack",
    })
    step_dir = root / "steps" / "shared-ref"
    step_dir.mkdir(parents=True)
    _write_yaml(step_dir / "contract.yaml", {
        "id": "shared-ref", "version": 1, "model": "sonnet", "prompt": prompt,
    })
    _write_yaml(root / "workflows" / f"{name}.yaml", {"steps": ["shared-ref"]})
    return root


def _write_skill(dir_: Path, text: str = "Shared charter.\n") -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "SKILL.md").write_text(text)
    return dir_


def test_validate_resolves_skill_from_target_repo(tmp_path, cwd_repo):
    """A skill present only in the installing repo satisfies validation.

    This is the discriminating case: the engine checkout's own skills/ is
    always on the search path, so referencing e.g. `learn` would pass even
    with broken repo-root threading. Only a repo-local skill proves it.
    """
    _write_skill(cwd_repo / "skills" / "repo-only-charter")

    src = tmp_path / "shared_src"
    _make_pack_referencing_skill(src, name="sharedpack", prompt="repo-only-charter/SKILL.md")

    result = packs.validate_pack(src, str(cwd_repo))
    assert result.ok, result.errors


def test_validate_fails_when_skill_absent_everywhere(tmp_path, cwd_repo):
    """Negative half of the pair above: identical pack, skill not written.

    Asserted against the positive case rather than a message match — the only
    error validate_pack appends names the workflow file, so a bare string check
    would also pass if the workflow failed for an unrelated reason.
    """
    src = tmp_path / "missing_src"
    _make_pack_referencing_skill(
        src, name="missingpack", prompt="zz-nonexistent-charter-xyz/SKILL.md"
    )
    assert not (cwd_repo / "skills" / "zz-nonexistent-charter-xyz").exists()

    assert not packs.validate_pack(src, str(cwd_repo)).ok

    # Same pack, same repo, skill now present — isolates the missing prompt dir
    # as the cause of the failure above.
    _write_skill(cwd_repo / "skills" / "zz-nonexistent-charter-xyz")
    assert packs.validate_pack(src, str(cwd_repo)).ok


def test_validate_ignores_ambient_repo_root(tmp_path, cwd_repo, monkeypatch):
    """An unrelated REPO_ROOT must not lend its skills to validation.

    config_root() falls back to $ORCHESTRATOR_REPO_ROOT/$REPO_ROOT, which is
    always set inside an orchestrator step — without clearing it, a pack would
    validate against skills the *installing* repo doesn't have.
    """
    other = tmp_path / "other_repo"
    _write_skill(other / "skills" / "elsewhere-charter")
    (other / ".orchestrator" / "config" / "workflows").mkdir(parents=True)

    src = tmp_path / "ambient_src"
    _make_pack_referencing_skill(src, name="ambientpack", prompt="elsewhere-charter/SKILL.md")

    monkeypatch.setenv("REPO_ROOT", str(other))
    assert not packs.validate_pack(src, str(cwd_repo)).ok

    monkeypatch.setenv("ORCHESTRATOR_REPO_ROOT", str(other))
    assert not packs.validate_pack(src, str(cwd_repo)).ok


def test_pack_local_skill_shadows_repo_copy(tmp_path, cwd_repo):
    """A pack that ships a skill validates against its own copy first."""
    _write_skill(cwd_repo / "skills" / "dual-charter", "Repo copy.\n")

    src = tmp_path / "shadow_src"
    _make_pack_referencing_skill(src, name="shadowpack", prompt="dual-charter/SKILL.md")
    _write_skill(src / "skills" / "dual-charter", "Pack copy.\n")

    result = packs.validate_pack(src, str(cwd_repo))
    assert result.ok, result.errors

    dirs = packs._validation_skill_search_dirs(src, cwd_repo)
    assert dirs[0] == src / "skills"
    assert cwd_repo / "skills" in dirs


def test_validation_restores_skill_env(tmp_path, cwd_repo):
    src = tmp_path / "env_src"
    _make_minimal_pack(src, name="envpack")

    packs.validate_pack(src, str(cwd_repo))
    assert "ORCHESTRATOR_SKILLS_PREPEND" not in os.environ


@pytest.mark.parametrize("route", ["orchestrator_config", "repo_root"])
def test_vendored_skill_resolves_without_override(
    tmp_path, cwd_repo, vendored_root, monkeypatch, route
):
    src = tmp_path / "resolve_src"
    _make_minimal_pack(src, name="resolvable", step_id="vendored")
    packs.pack_add(str(src), repo_root=str(tmp_path))

    vendored_skill = cwd_repo / "skills" / "vendored-agent"
    assert vendored_skill.is_dir()

    if route == "orchestrator_config":
        monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(vendored_root))
    else:
        monkeypatch.setenv("REPO_ROOT", str(cwd_repo))
        monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_SKILLS_TEST_OVERRIDE", raising=False)
    # validate_pack mutates os.environ directly; make the "no override" claim
    # load-bearing rather than assumed.
    assert "ORCHESTRATOR_SKILLS_TEST_OVERRIDE" not in os.environ

    from orchestrator_next.parser import resolve_prompt_file, skill_search_dirs

    assert cwd_repo / "skills" in skill_search_dirs()
    assert resolve_prompt_file("vendored-agent/prompt.md") == (vendored_skill / "prompt.md").resolve()
