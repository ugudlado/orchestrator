"""Opaque CLI input: engine mints UUID; workflow load-ticket-context classifies."""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import yaml

from orchestrator_next.run_loop import run_cmd, seed_state_file


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


def test_run_cmd_mints_uuid_and_stores_brief(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    monkeypatch.delenv("BACKLOG_URL", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(repo))
    # ticketing unset → brief path for free text
    code = run_cmd(["add empty-title validation", "--schema", "lite", "--repo", str(repo)])
    assert code == 1
    out = capsys.readouterr().out
    assert "run_id=" in out
    run_id = [ln.split("=", 1)[1].strip() for ln in out.splitlines() if ln.startswith("run_id=")][0]
    assert len(run_id) == 36
    state_dir = repo / ".orchestrator" / run_id
    states = list(state_dir.glob("*_lite_state.yaml"))
    assert states
    raw = yaml.safe_load(states[-1].read_text())
    assert raw["user_input"] == "add empty-title validation"
    assert raw["change_id"] == run_id
    brief = repo / "spec" / "changes" / run_id / "ticket-context.md"
    assert brief.is_file()
    assert "empty-title" in brief.read_text()
    assert "Feature brief" in brief.read_text()


def test_run_cmd_ticket_shaped_writes_stub_without_backlog(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    monkeypatch.delenv("BACKLOG_URL", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(repo))
    code = run_cmd(["ORC-42", "--schema", "lite", "--repo", str(repo)])
    assert code == 1
    out = capsys.readouterr().out
    run_id = [ln.split("=", 1)[1].strip() for ln in out.splitlines() if ln.startswith("run_id=")][0]
    # Identity is UUID, not orc-42
    assert run_id.lower() != "orc-42"
    assert (repo / ".orchestrator" / run_id).is_dir()
    assert not (repo / ".orchestrator" / "orc-42").exists()
    ctx = repo / "spec" / "changes" / run_id / "ticket-context.md"
    assert ctx.is_file()
    text = ctx.read_text()
    assert "ORC-42" in text
    raw = yaml.safe_load(next((repo / ".orchestrator" / run_id).glob("*_state.yaml")).read_text())
    assert raw.get("ticket_id") == "ORC-42"  # state_patch from step


def test_run_cmd_resume_by_run_id(tmp_path, monkeypatch, capsys):
    repo = _git_repo(tmp_path)
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    monkeypatch.delenv("BACKLOG_URL", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(repo))
    run_cmd(["hello world", "--schema", "lite", "--repo", str(repo)])
    out1 = capsys.readouterr().out
    run_id = [ln.split("=", 1)[1].strip() for ln in out1.splitlines() if ln.startswith("run_id=")][0]
    # Second call with run_id resumes (workflow already complete → still exits 1 quickly)
    code = run_cmd([run_id, "ignored-direction", "--schema", "lite", "--repo", str(repo)])
    assert code in (1, 0, 3)  # complete or idle
    capsys.readouterr()
    # Should not print a new run_id= for resume (or may print pause run_id)
    states = list((repo / ".orchestrator" / run_id).glob("*_state.yaml"))
    assert len(states) == 1  # no second seed dir
