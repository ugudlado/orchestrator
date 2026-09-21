"""Opaque run input: the slug is identity, user_input is opaque text for the
workflow, and ticket_id is set only when the caller says so. Driven through the
protocol-v2 verbs (`start` then `step`), which is the only way to run a workflow.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import yaml

from orchestrator_next.protocol import start
from orchestrator_next.seed import seed_state_file
from orchestrator_next.tests.store_fixture import install_test_store


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, env=env)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env=env)
    return repo


def _mini_pack(tmp_path: Path) -> Path:
    pack = tmp_path / "pack"
    (pack / "workflows").mkdir(parents=True)
    (pack / "workflows" / "lite.yaml").write_text(
        "steps:\n  - load-ticket-context\n  - finish\n", encoding="utf-8"
    )
    repo_root = Path(__file__).resolve().parents[2]
    src = repo_root / "config" / "steps" / "load-ticket-context"
    if not (src / "contract.yaml").is_file():
        src = repo_root / ".orchestrator" / "workflows" / "steps" / "load-ticket-context"
    dest = pack / "steps" / "load-ticket-context"
    dest.mkdir(parents=True)
    (dest / "contract.yaml").write_text(
        (src / "contract.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    script = (src / "script.sh").read_text(encoding="utf-8")
    lib = pack / "lib" / "ticket"
    lib.mkdir(parents=True)
    real_lib = src.parents[1] / "lib" / "ticket"  # steps/../lib or config/lib
    if not real_lib.is_dir():
        real_lib = repo_root / "config" / "lib" / "ticket"
    if not real_lib.is_dir():
        real_lib = repo_root / ".orchestrator" / "workflows" / "lib" / "ticket"
    for f in real_lib.iterdir():
        if f.is_file():
            (lib / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            if f.suffix == ".sh":
                (lib / f.name).chmod(0o755)
    sh = dest / "script.sh"
    sh.write_text(script, encoding="utf-8")
    sh.chmod(0o755)

    fin = pack / "steps" / "finish"
    fin.mkdir(parents=True)
    (fin / "contract.yaml").write_text("id: finish\nversion: 1\nrun: script.sh\n", encoding="utf-8")
    fsh = fin / "script.sh"
    fsh.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            python3 - <<'PY'
            import json, os
            print(json.dumps({
              "step_id": "finish",
              "phase": os.environ.get("ORCHESTRATOR_PHASE", "main"),
              "status": "completed",
              "outputs": {"reason": "done"},
            }))
            PY
            """
        ),
        encoding="utf-8",
    )
    fsh.chmod(0o755)
    (pack / "models.yaml").write_text("models: {}\nstep_models: {}\n", encoding="utf-8")
    return pack


def test_seed_user_input_not_as_ticket_id(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    pack = tmp_path / "cfg"
    (pack / "workflows").mkdir(parents=True)
    (pack / "workflows" / "lite.yaml").write_text("steps:\n  - finish\n", encoding="utf-8")
    (pack / "steps" / "finish").mkdir(parents=True)
    (pack / "steps" / "finish" / "contract.yaml").write_text(
        "id: finish\nversion: 1\nrun: script.sh\n", encoding="utf-8"
    )
    (pack / "steps" / "finish" / "script.sh").write_text("#!/bin/bash\necho '{}'\n", encoding="utf-8")
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    state = tmp_path / "state.yaml"
    run_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    seed_state_file(
        state,
        slug=run_id,
        schema="lite",
        repo_root=str(repo),
        user_input="ORC-99",
    )
    raw = yaml.safe_load(state.read_text())
    assert raw["change_id"] == run_id
    assert raw["slug"] == run_id
    assert raw.get("user_input") == "ORC-99"
    assert "ticket_id" not in raw or raw.get("ticket_id") in ("", None)


def _drive(state_path: str, limit: int = 10) -> dict:
    """Walk to a terminal result, running exec steps as a driver does."""
    from orchestrator_next.tests.conftest import drive

    return drive(state_path, limit=limit)


def test_start_stores_free_text_as_opaque_user_input(tmp_path, monkeypatch):
    install_test_store(monkeypatch)
    repo = _git_repo(tmp_path)
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    monkeypatch.delenv("BACKLOG_URL", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(repo))

    started, _ = start("lite", "add-empty-title-validation")
    assert _drive(started["state"])["status"] == "done"

    raw = yaml.safe_load(Path(started["state"]).read_text())
    assert raw["user_input"] == "add-empty-title-validation"
    assert raw["change_id"] == "add-empty-title-validation"
    assert "ticket_id" not in raw or raw.get("ticket_id") in ("", None)

    brief = repo / "spec" / "changes" / "add-empty-title-validation" / "ticket-context.md"
    assert brief.is_file()
    assert "empty-title" in brief.read_text()


def test_start_explicit_ticket_id_seeds_ticket_identity(tmp_path, monkeypatch):
    install_test_store(monkeypatch)
    repo = _git_repo(tmp_path)
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    monkeypatch.setenv("REPO_ROOT", str(repo))

    started, _ = start("lite", "ORC-42", ticket_id="ORC-42")
    raw = yaml.safe_load(Path(started["state"]).read_text())
    assert raw["ticket_id"] == "ORC-42"
    assert raw["user_input"] == "ORC-42"
    assert raw["change_id"] == "orc-42"
    assert raw["slug"] == "orc-42"


def test_ticket_shaped_input_writes_stub_without_backlog(tmp_path, monkeypatch):
    install_test_store(monkeypatch)
    repo = _git_repo(tmp_path)
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    monkeypatch.delenv("BACKLOG_URL", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(repo))

    started, _ = start("lite", "ORC-42")
    assert _drive(started["state"])["status"] == "done"

    ctx = repo / "spec" / "changes" / "orc-42" / "ticket-context.md"
    assert ctx.is_file()
    assert "ORC-42" in ctx.read_text()
    raw = yaml.safe_load(Path(started["state"]).read_text())
    assert raw.get("ticket_id") == "ORC-42"  # state_patch from the step


def test_start_on_a_live_slug_resumes_rather_than_reseeding(tmp_path, monkeypatch):
    install_test_store(monkeypatch)
    repo = _git_repo(tmp_path)
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    monkeypatch.delenv("BACKLOG_URL", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(repo))

    first, _ = start("lite", "hello-world")
    again, _ = start("lite", "hello-world")
    assert again.get("resumed") is True
    assert again["run_id"] == first["run_id"]
    assert again["state"] == first["state"]
