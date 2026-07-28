"""T5: prompt_dir is carried on contracts and exported to agent dispatch env."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from orchestrator_next.dispatch import dispatch
from orchestrator_next.parser import load_state


def test_dispatch_exports_orchestrator_prompt_dir(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    prompt_dir = skills / "explore"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "SKILL.md").write_text("Explore body.\n")
    monkeypatch.setenv("ORCHESTRATOR_SKILLS_TEST_OVERRIDE", str(skills))

    steps = tmp_path / "steps"
    step = steps / "explore"
    step.mkdir(parents=True)
    (step / "contract.yaml").write_text(
        yaml.dump({"id": "explore", "version": 1, "model": "sonnet", "prompt": "explore/SKILL.md"})
    )
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))

    state_path = tmp_path / "state.yaml"
    state_path.write_text(
        yaml.dump(
            {
                "change_id": "t5",
                "phase": "main",
                "repo_root": str(tmp_path),
                "workflow_plan": {
                    "main": {
                        "nodes": [
                            {"id": "explore", "status": "pending"},
                        ]
                    }
                },
                "step_history": [],
            }
        )
    )

    state = load_state(str(state_path))
    action, code = dispatch(state, str(state_path))
    assert code == 0
    assert action["prompt_dir"] == str(prompt_dir.resolve())
    assert action["env"]["ORCHESTRATOR_PROMPT_DIR"] == str(prompt_dir.resolve())


def _multi_step_fixture(tmp_path, monkeypatch, step_ids, *, script_ids=()):
    """Build skills + step contracts + state.yaml for a multi-step workflow."""
    skills = tmp_path / "skills"
    for sid in step_ids:
        d = skills / sid
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"{sid} body.\n")
    monkeypatch.setenv("ORCHESTRATOR_SKILLS_TEST_OVERRIDE", str(skills))

    steps = tmp_path / "steps"
    for sid in step_ids:
        d = steps / sid
        d.mkdir(parents=True)
        (d / "contract.yaml").write_text(
            yaml.dump({"id": sid, "version": 1, "model": "sonnet", "prompt": f"{sid}/SKILL.md"})
        )
    for sid in script_ids:
        d = steps / sid
        d.mkdir(parents=True)
        (d / "script.sh").write_text("#!/usr/bin/env bash\ntrue\n")
        (d / "contract.yaml").write_text(
            yaml.dump({"id": sid, "version": 1, "run": "script.sh"})
        )
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))

    all_ids = list(script_ids) + list(step_ids)
    state_path = tmp_path / "state.yaml"
    state_path.write_text(
        yaml.dump(
            {
                "change_id": "t5",
                "phase": "main",
                "repo_root": str(tmp_path),
                "workflow_plan": {
                    "main": {
                        "nodes": [
                            # learn last, so it is the step that dispatches
                            {"id": sid, "status": "completed" if sid != "learn" else "pending"}
                            for sid in all_ids
                        ]
                    }
                },
                "step_history": [],
            }
        )
    )
    return skills, state_path


def test_prompt_dirs_map_covers_every_agent_step(tmp_path, monkeypatch):
    """The exported map has an entry per agent step, each an existing directory."""
    agent_ids = ["explore", "design", "implement", "learn"]
    skills, state_path = _multi_step_fixture(
        tmp_path, monkeypatch, agent_ids, script_ids=["create-worktree"]
    )

    state = load_state(str(state_path))
    action, code = dispatch(state, str(state_path))
    assert code == 0

    dirs = json.loads(action["env"]["ORCHESTRATOR_PROMPT_DIRS"])
    assert sorted(dirs) == sorted(agent_ids)
    for sid, path in dirs.items():
        assert Path(path).is_dir(), f"{sid} -> {path} is not a directory"
        assert (Path(path) / "SKILL.md").is_file()
    # Script steps have no prompt dir and must not appear.
    assert "create-worktree" not in dirs


def test_learn_colocation_append_target(tmp_path, monkeypatch):
    """learn resolves ANOTHER step's dir from the map and appends train.jsonl there.

    Resolution goes through the exported env var exactly as SKILL.md instructs —
    the test must not call load_contract_for_step itself, or it re-implements
    the thing it is meant to guard.
    """
    skills, state_path = _multi_step_fixture(
        tmp_path, monkeypatch, ["explore", "learn"]
    )

    state = load_state(str(state_path))
    action, code = dispatch(state, str(state_path))
    assert code == 0
    assert action["step_id"] == "learn"

    dirs = json.loads(action["env"]["ORCHESTRATOR_PROMPT_DIRS"])
    # learn writes beside the step it learned ABOUT, not beside itself.
    target_dir = Path(dirs["explore"])
    assert target_dir != Path(action["env"]["ORCHESTRATOR_PROMPT_DIR"])

    target = target_dir / "scenarios" / "train.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": "prefer-readme-scope",
        "scenario": "No ticket body; only a README exists.",
        "expect": ["Prefer README-derived scope", "Do not invent ticket text"],
    }
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")

    landed = skills / "explore" / "scenarios" / "train.jsonl"
    lines = [ln for ln in landed.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == "prefer-readme-scope"
    assert not (skills / "explore" / "scenarios" / "dev.jsonl").exists()
    assert not (skills / "learn" / "scenarios").exists()
