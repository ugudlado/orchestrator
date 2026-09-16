"""Phase 3.2 — pack trust, the config lock, and `config update`.

A pack is executable content pulled off the network, so these tests pin the
two things that keep that safe: a remote source must be listed in
`~/.orchestrator/trust.toml` before anything is cloned, and every pull records
what it installed (commit + per-step contract versions) so drift and contract
widening are both visible afterwards.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator_next import config_pull, trust


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _make_pack_source(root: Path, *, design_version: int = 1,
                      tools: list[str] | None = None) -> Path:
    """A minimal pack tree (workflows/ + steps/) inside a fresh git repo."""
    (root / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "workflows" / "feature.yaml").write_text(
        yaml.safe_dump({"name": "feature", "steps": ["design"]}), encoding="utf-8"
    )
    step = root / "steps" / "design"
    step.mkdir(parents=True, exist_ok=True)
    step.joinpath("contract.yaml").write_text(
        yaml.safe_dump({
            "id": "design",
            "version": design_version,
            "kind": "judgment",
            "tools": tools if tools is not None else ["fs.read"],
            "side_effects": [],
        }),
        encoding="utf-8",
    )
    step.joinpath("SKILL.md").write_text("Design it.\n", encoding="utf-8")
    return root


def _init_git_repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "pack")
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never read the developer's real ~/.orchestrator/trust.toml."""
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.delenv("ORCHESTRATOR_TRUST_ALL", raising=False)


# ---------------------------------------------------------------------------
# Trust
# ---------------------------------------------------------------------------
def test_local_path_source_is_always_allowed(tmp_path):
    src = _make_pack_source(tmp_path / "src")
    # No trust.toml exists at all — a local path must still be pullable.
    assert not trust.trust_file().exists()
    assert "local path" in trust.check_source(str(src))


def test_remote_source_refused_without_trust_file(tmp_path):
    with pytest.raises(trust.TrustError) as exc:
        trust.check_source("https://github.com/someone/evil.git")
    msg = str(exc.value)
    assert "no trust list" in msg
    # The refusal must hand the user the exact block they need.
    assert "[[allow]]" in msg and "someone/evil.git" in msg


def test_remote_source_refused_when_no_allow_entry_matches(tmp_path):
    path = trust.trust_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[[allow]]\nsource = "https://github.com/ugudlado/*"\n', encoding="utf-8")
    with pytest.raises(trust.TrustError, match="no \\[\\[allow\\]\\] entry"):
        trust.check_source("https://github.com/someone/evil.git")
    # ... and the glob does match its own org.
    assert "allowed by" in trust.check_source("https://github.com/ugudlado/workflows.git")


def test_trust_all_env_bypasses_everything(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_TRUST_ALL", "1")
    assert "bypassed" in trust.check_source("https://github.com/someone/evil.git")


def test_require_signed_without_gpg_refuses(tmp_path, monkeypatch):
    path = trust.trust_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'require_signed = true\n[[allow]]\nsource = "*"\n', encoding="utf-8"
    )
    monkeypatch.setattr(trust.shutil, "which", lambda _name: None)
    with pytest.raises(trust.TrustError, match="gpg is not on PATH"):
        trust.verify_signature(tmp_path, None)


def test_unsigned_only_warns_when_not_required(tmp_path, monkeypatch, capsys):
    path = trust.trust_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[[allow]]\nsource = "*"\n', encoding="utf-8")
    monkeypatch.setattr(trust.shutil, "which", lambda _name: None)
    assert "unsigned" in trust.verify_signature(tmp_path, None)


def test_key_fingerprints_are_parsed():
    doc = {"keys": [{"fingerprint": "ABC123"}, {"nope": 1}]}
    assert trust.key_fingerprints(doc) == ["ABC123"]


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------
def test_pull_writes_lock_with_commit_and_step_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_TRUST_ALL", "1")
    src = _make_pack_source(tmp_path / "src", design_version=3)
    head = _init_git_repo(src)
    repo = tmp_path / "consumer"
    repo.mkdir()

    lock = config_pull.pull(
        repo_root=repo, source=str(src), pack_name="wf", ref=None, export_skills=False
    )
    assert lock["commit"] == head
    assert lock["source_sha"] == head          # legacy field kept for back-compat
    assert lock["steps"] == {"design": 3}
    on_disk = yaml.safe_load(
        (repo / ".orchestrator" / "wf" / "config-lock.yaml").read_text(encoding="utf-8")
    )
    assert on_disk["commit"] == head


def test_lock_commit_falls_back_to_tree_hash_for_a_non_git_source(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_TRUST_ALL", "1")
    src = _make_pack_source(tmp_path / "plain")
    repo = tmp_path / "consumer"
    repo.mkdir()
    lock = config_pull.pull(
        repo_root=repo, source=str(src), pack_name="wf", ref=None, export_skills=False
    )
    assert lock["source_sha"] is None
    assert lock["commit"] == config_pull.tree_sha256(src)
    # Editing a step changes the identity — that is what drift detection needs.
    (src / "steps" / "design" / "SKILL.md").write_text("Changed.\n", encoding="utf-8")
    assert config_pull.tree_sha256(src) != lock["commit"]


# ---------------------------------------------------------------------------
# config update
# ---------------------------------------------------------------------------
def test_config_update_diffs_and_only_applies_with_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_TRUST_ALL", "1")
    src = _make_pack_source(tmp_path / "src", design_version=1, tools=["fs.read"])
    _init_git_repo(src)
    repo = tmp_path / "consumer"
    repo.mkdir()
    config_pull.pull(
        repo_root=repo, source=str(src), pack_name="wf", ref=None, export_skills=False
    )

    # The upstream pack widens the step: new version, a write tool, a side effect.
    _make_pack_source(src, design_version=2, tools=["fs.read", "fs.write"])
    contract = src / "steps" / "design" / "contract.yaml"
    doc = yaml.safe_load(contract.read_text(encoding="utf-8"))
    doc["side_effects"] = ["write:git"]
    contract.write_text(yaml.safe_dump(doc), encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "bump")

    installed = repo / ".orchestrator" / "wf" / "steps" / "design" / "contract.yaml"
    lines, lock = config_pull.update(repo_root=repo, pack_name="wf", apply=False)
    assert lock is None
    joined = "\n".join(lines)
    assert "design.version: 1 -> 2" in joined
    assert "design.tools" in joined and "fs.write" in joined
    assert "design.side_effects" in joined and "write:git" in joined
    # Dry run must not have touched the installed pack.
    assert yaml.safe_load(installed.read_text(encoding="utf-8"))["version"] == 1

    lines, lock = config_pull.update(repo_root=repo, pack_name="wf", apply=True)
    assert lock is not None and lock["steps"] == {"design": 2}
    assert yaml.safe_load(installed.read_text(encoding="utf-8"))["version"] == 2


def test_config_update_reports_added_and_removed_steps():
    old = {"design": {"version": 1}}
    new = {"review": {"version": 4, "kind": "judgment"}}
    lines = config_pull.diff_contracts(old, new)
    assert any(line.startswith("- design") for line in lines)
    assert any(line.startswith("+ review") for line in lines)


def test_config_update_needs_a_lock_source(tmp_path):
    repo = tmp_path / "consumer"
    (repo / ".orchestrator" / "wf").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="no `source`"):
        config_pull.update(repo_root=repo, pack_name="wf", apply=False)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
def test_doctor_pack_check_warns_on_missing_lock_and_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_TRUST_ALL", "1")
    from orchestrator_next.doctor import check_pack_trust_and_lock

    repo = tmp_path / "consumer"
    repo.mkdir()
    src = _make_pack_source(tmp_path / "src")
    config_pull.pull(
        repo_root=repo, source=str(src), pack_name="wf", ref=None, export_skills=False
    )
    result = check_pack_trust_and_lock(repo)
    assert result.status == "PASS", result.detail

    # Hand-edit the installed pack: the lock no longer describes what is there.
    (repo / ".orchestrator" / "wf" / "steps" / "design" / "SKILL.md").write_text(
        "Edited by hand.\n", encoding="utf-8"
    )
    result = check_pack_trust_and_lock(repo)
    assert result.status == "WARN" and "drifted" in result.detail

    # A pack with no lock at all is also a warning.
    hand = repo / ".orchestrator" / "handmade" / "workflows"
    hand.mkdir(parents=True)
    result = check_pack_trust_and_lock(repo)
    assert "handmade: no config-lock.yaml" in result.detail


def test_doctor_pack_check_warns_on_untrusted_remote_source(tmp_path):
    from orchestrator_next.doctor import check_pack_trust_and_lock

    repo = tmp_path / "consumer"
    pack = repo / ".orchestrator" / "wf"
    (pack / "workflows").mkdir(parents=True)
    (pack / "config-lock.yaml").write_text(
        yaml.safe_dump({"source": "https://github.com/someone/evil.git"}), encoding="utf-8"
    )
    path = trust.trust_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[[allow]]\nsource = "https://github.com/ugudlado/*"\n', encoding="utf-8")
    result = check_pack_trust_and_lock(repo)
    assert result.status == "WARN"
    assert "matches no [[allow]] entry" in result.detail
