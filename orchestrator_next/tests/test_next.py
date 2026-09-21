"""`orchestrator next` — the whole engine, exercised as a pure function.

Given a workflow, the step that just ran and how it went, `next` says what
runs next. It stores nothing, so every test here is one call in and one
answer out.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator_next.nextstep import NextError, next_step

WORKFLOW = {
    "name": "mini",
    "artifacts_root": "spec/changes/{slug}",
    "steps": [
        "prep",                                              # exec
        {"gate": "signoff", "show": ["notes"], "approve_as": "impl_token"},
        {"id": "think", "on_failure": "prep", "max_retries": 2,
         "requires": "impl_token"},                          # judgment
        "finish",                                            # exec
    ],
}


@pytest.fixture
def pack(tmp_path) -> Path:
    """A four-step pack: exec, gate, judgment (with out:), exec."""
    root = tmp_path / "pack"
    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "mini.yaml").write_text(
        yaml.safe_dump(WORKFLOW), encoding="utf-8"
    )
    for step_id in ("prep", "finish"):
        d = root / "steps" / step_id
        d.mkdir(parents=True)
        (d / "contract.yaml").write_text(
            yaml.safe_dump({"id": step_id, "kind": "exec", "run": "script.sh"}),
            encoding="utf-8",
        )
        (d / "script.sh").write_text(
            '#!/usr/bin/env bash\necho \'{"reason": "ran"}\'\n', encoding="utf-8"
        )
    think = root / "steps" / "think"
    think.mkdir(parents=True)
    (think / "contract.yaml").write_text(
        yaml.safe_dump({
            "id": "think", "kind": "judgment", "prompt": "SKILL.md",
            "max_turns": 12, "tools": ["fs.read"],
            "out": {
                "notes": {"artifact": "notes.md"},
                "complexity": {"type": "enum", "values": ["S", "M", "L"]},
                "verdict": {"type": "enum", "values": ["pass", "needs_work"],
                            "fail_on": ["needs_work"]},
            },
        }),
        encoding="utf-8",
    )
    (think / "SKILL.md").write_text("# think\n", encoding="utf-8")
    return root


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """The worktree the driver runs from — the CLI's working directory."""
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


def _next(pack, repo, **kw):
    return next_step("mini", config_root=pack, slug="s1", **kw)


# --------------------------------------------------------------- the basics
def test_no_after_returns_the_first_step(pack, repo):
    r = _next(pack, repo)
    assert r["status"] == "ready"
    assert r["step_id"] == "prep"
    assert r["kind"] == "exec"
    assert r["route"] == "next"
    assert r["payload"]["run_path"].endswith("prep/script.sh")
    assert "recorded" not in r, "nothing ran yet"


def test_completed_returns_the_following_entry(pack, repo):
    r = _next(pack, repo, after="prep", exit_code=0)
    assert r["status"] == "ready"
    assert r["step_id"] == "signoff"
    assert r["route"] == "next"


def test_the_last_step_completing_ends_the_run(pack, repo):
    r = _next(pack, repo, after="finish", exit_code=0)
    assert r == {"status": "done", "recorded": r["recorded"]}
    assert r["recorded"]["status"] == "completed"


def test_an_unknown_step_is_an_error(pack, repo):
    with pytest.raises(NextError, match="not in this workflow"):
        _next(pack, repo, after="nope", status="completed")


# ------------------------------------------------------------------- gates
def test_a_gate_is_emitted_with_resolved_show_paths(pack, repo):
    r = _next(pack, repo, after="prep", exit_code=0)
    assert r["kind"] == "gate"
    payload = r["payload"]
    assert payload["approve_as"] == "impl_token"
    # `notes` is declared by think's out:, so the gate shows that path.
    # Relative: the driver joins it to the worktree it owns.
    assert payload["show"]["notes"] == "spec/changes/s1/notes.md"


def test_a_gate_is_passed_by_reporting_it_completed(pack, repo):
    r = _next(pack, repo, after="signoff", status="completed")
    assert r["status"] == "ready"
    assert r["step_id"] == "think"
    assert r["kind"] == "judgment"
    # `requires:` rides along as data; with no state the engine cannot check it.
    assert r["payload"]["requires"] == "impl_token"
    assert r["payload"]["prompt_path"].endswith("think/SKILL.md")


# --------------------------------------------------------- failure routing
def test_failed_routes_to_on_failure(pack, repo):
    r = _next(pack, repo, after="think", status="failed", attempt=1)
    assert r["status"] == "ready"
    assert r["step_id"] == "prep"
    assert r["route"] == "on_failure"
    assert r["attempt"] == 2


def test_retries_exhausted_needs_you(pack, repo):
    """think declares max_retries: 2, so a 2nd failure stops the run."""
    r = _next(pack, repo, after="think", status="failed", attempt=2)
    assert r["status"] == "needs_you"
    assert r["reason"] == "retries exhausted"


def test_failure_with_no_on_failure_target_needs_you(pack, repo):
    r = _next(pack, repo, after="finish", status="failed")
    assert r["status"] == "needs_you"
    assert "no on_failure" in r["reason"]


def test_reset_to_wins_over_the_static_edge(pack, repo):
    """A step may name its own rework target, at or before itself."""
    r = _next(pack, repo, after="think", status="failed",
              out={"reset_to": "signoff"}, attempt=1)
    assert r["step_id"] == "signoff"
    assert r["route"] == "reset_to"


def test_reset_to_after_the_current_step_is_ignored(pack, repo):
    """A failure must never skip work: a later target falls back to on_failure."""
    r = _next(pack, repo, after="think", status="failed",
              out={"reset_to": "finish"}, attempt=1)
    assert r["step_id"] == "prep"
    assert r["route"] == "on_failure"


def test_abandoned_re_queues_the_same_step(pack, repo):
    r = _next(pack, repo, after="think", status="abandoned", attempt=1)
    assert r["step_id"] == "think"
    assert r["route"] == "retry"
    assert r["attempt"] == 2


# ------------------------------------------------- the exec stdout protocol
def test_exec_stdout_is_parsed_and_echoed_not_applied(pack, repo, tmp_path):
    out = tmp_path / "out.json"
    out.write_text(
        '{"status": "completed", "outputs": {"reason": "did it"}, '
        '"state_patch": {"branch": "feat/x"}}\n',
        encoding="utf-8",
    )
    r = _next(pack, repo, after="prep", exit_code=0, stdout_file=str(out))
    assert r["step_id"] == "signoff"
    rec = r["recorded"]
    assert rec["outputs"] == {"reason": "did it"}
    assert rec["state_patch"] == {"branch": "feat/x"}
    # Echoed, never applied: the engine wrote nothing anywhere.
    assert list(repo.iterdir()) == []


def test_a_nonzero_exit_takes_the_failure_route(pack, repo):
    r = _next(pack, repo, after="think", exit_code=3, attempt=1)
    assert r["step_id"] == "prep"
    assert r["route"] == "on_failure"
    assert r["recorded"]["status"] == "failed"
    assert r["recorded"]["exit_code"] == 3


def test_await_input_needs_you(pack, repo, tmp_path):
    out = tmp_path / "out.json"
    out.write_text(
        '{"status": "await_input", "outputs": {"ask": "Ship it?", '
        '"options": [{"label": "yes"}]}}\n',
        encoding="utf-8",
    )
    r = _next(pack, repo, after="prep", exit_code=0, stdout_file=str(out))
    assert r["status"] == "needs_you"
    assert r["step_id"] == "prep"
    assert r["await_input"]["ask"] == "Ship it?"


def test_unparseable_stdout_is_a_plain_completion(pack, repo, tmp_path):
    out = tmp_path / "out.json"
    out.write_text("just some log lines\nnot json\n", encoding="utf-8")
    r = _next(pack, repo, after="prep", exit_code=0, stdout_file=str(out))
    assert r["step_id"] == "signoff"
    assert r["recorded"]["status"] == "completed"


# ------------------------------------- judgment output validation (trust boundary)
def _artifacts(repo) -> Path:
    return repo / "spec" / "changes" / "s1"


def test_invalid_out_is_an_error_not_a_route(pack, repo):
    """The agent claimed a value the contract does not allow."""
    _artifacts(repo).mkdir(parents=True)
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")
    r = _next(pack, repo, after="think", status="completed",
              out={"complexity": "XXL"})
    assert r["status"] == "error"
    assert "complexity" in r["error"]


def test_a_missing_artifact_is_an_error(pack, repo):
    r = _next(pack, repo, after="think", status="completed",
              out={"complexity": "M"})
    assert r["status"] == "error"
    assert "notes" in r["error"]


def test_a_satisfied_out_advances(pack, repo):
    _artifacts(repo).mkdir(parents=True)
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")
    r = _next(pack, repo, after="think", status="completed",
              out={"complexity": "M", "verdict": "pass"})
    assert r["status"] == "ready"
    assert r["step_id"] == "finish"


# -------------------------------------------------------------- invariants
def test_the_engine_never_spawns_a_process(pack, repo, monkeypatch):
    """The driver owns every subprocess; `next` is a pure function."""
    def boom(*args, **kwargs):
        raise AssertionError(f"the engine spawned a process: {args!r}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)

    _next(pack, repo)
    _next(pack, repo, after="prep", exit_code=0)
    _next(pack, repo, after="think", status="failed")


def test_the_payload_never_leaks_the_engines_environment(pack, repo, monkeypatch):
    """`next` prints its payload, so env must carry no ambient secrets."""
    monkeypatch.setenv("MY_API_TOKEN", "super-secret-value")
    r = _next(pack, repo)
    env = r["payload"]["env"]
    assert "MY_API_TOKEN" not in env
    assert "super-secret-value" not in str(r)
    assert env["ORCHESTRATOR_STEP_ID"] == "prep"
    assert env["CHANGE_ID"] == "s1"
    # The repo root is the driver's to know, not the engine's.
    assert "REPO_ROOT" not in env and "ORCHESTRATOR_REPO_ROOT" not in env


def test_next_writes_nothing(pack, repo):
    """No state: a full sweep of calls must leave the filesystem untouched."""
    before = sorted(p.name for p in repo.iterdir())
    _next(pack, repo)
    _next(pack, repo, after="prep", exit_code=0)
    _next(pack, repo, after="signoff", status="completed")
    _next(pack, repo, after="think", status="abandoned")
    assert sorted(p.name for p in repo.iterdir()) == before


# ------------------------------------------- every real workflow terminates
def test_every_real_workflow_walks_to_done(real_pack, tmp_path, monkeypatch):
    """Walk each shipped workflow start→finish with all-completed outcomes."""
    workflows = sorted(
        p.stem for p in (real_pack / "workflows").glob("*.yaml")
    )
    assert len(workflows) >= 8, workflows
    for name in workflows:
        repo = tmp_path / name
        repo.mkdir()
        monkeypatch.chdir(repo)          # the worktree the driver runs from
        seen, step = [], next_step(
            name, config_root=real_pack, slug="s1"
        )
        for _ in range(200):
            if step["status"] == "done":
                break
            assert step["status"] == "ready", (name, step)
            seen.append(step["step_id"])
            # Play the driver: write whatever the step declared it produces,
            # then report the declared values back.
            out = {}
            for out_name, path in (step["payload"].get("out") or {}).items():
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(f"{out_name}\n", encoding="utf-8")
                out[out_name] = path
            for out_name, spec in (step["payload"].get("out_schema") or {}).items():
                values = spec.get("values")
                out[out_name] = values[0] if values else "ok"
            step = next_step(
                name, config_root=real_pack, slug="s1",
                after=step["step_id"], status="completed", out=out,
            )
        else:
            raise AssertionError(f"{name} did not terminate: {seen}")
        # It visited every step the workflow declares, in order.
        declared = [
            s if isinstance(s, str) else (s.get("id") or s.get("gate"))
            for s in yaml.safe_load(
                (real_pack / "workflows" / f"{name}.yaml").read_text()
            )["steps"]
        ]
        assert seen == declared, name


# --------------------------------------------- relative paths & the slug
def test_artifact_paths_are_relative_to_the_working_dir(pack, repo):
    """The engine names a location under the worktree; it does not own one."""
    r = _next(pack, repo, after="signoff", status="completed")
    payload = r["payload"]
    assert payload["out"]["notes"] == "spec/changes/s1/notes.md"
    assert not Path(payload["out"]["notes"]).is_absolute()
    # The step's own files DO come back absolute: they derive from --config.
    assert Path(payload["prompt_path"]).is_absolute()
    assert Path(payload["step_dir"]).is_absolute()


def test_a_template_needing_a_slug_errors_without_one(pack):
    """No silent default: `{slug}` with no --slug is a loud failure."""
    with pytest.raises(NextError, match="no --slug"):
        next_step("mini", config_root=pack, slug="")


def test_out_artifact_check_resolves_against_the_cwd(pack, repo, tmp_path):
    """A relative --out path is checked in the tree the CLI runs from."""
    (repo / "spec" / "changes" / "s1").mkdir(parents=True)
    (repo / "spec" / "changes" / "s1" / "notes.md").write_text("n\n")
    r = _next(pack, repo, after="think", status="completed",
              out={"notes": "spec/changes/s1/notes.md", "complexity": "M",
                   "verdict": "pass"})
    assert r["status"] == "ready", r

    # The same relative path from a different cwd must NOT be found.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    import os
    os.chdir(elsewhere)
    r = _next(pack, repo, after="think", status="completed",
              out={"notes": "spec/changes/s1/notes.md", "complexity": "M",
                   "verdict": "pass"})
    assert r["status"] == "error"


def test_an_absolute_out_path_is_checked_as_given(pack, repo, tmp_path):
    """A driver may report an absolute path; it is honoured verbatim."""
    art = tmp_path / "somewhere" / "notes.md"
    art.parent.mkdir(parents=True)
    art.write_text("n\n")
    r = _next(pack, repo, after="think", status="completed",
              out={"notes": str(art), "complexity": "M", "verdict": "pass"})
    assert r["status"] == "ready", r


# ------------------------------------------- fail_on: the engine derives it
def test_a_fail_on_verdict_routes_as_failed_though_reported_completed(pack, repo):
    """A review saying needs_work must not advance onto the work it rejected."""
    (repo / "spec" / "changes" / "s1").mkdir(parents=True)
    (repo / "spec" / "changes" / "s1" / "notes.md").write_text("n\n")
    r = _next(pack, repo, after="think", status="completed", attempt=1,
              out={"complexity": "M", "verdict": "needs_work"})
    assert r["status"] == "ready"
    assert r["step_id"] == "prep", "should take think's on_failure edge"
    assert r["route"] == "on_failure"
    assert r["recorded"]["status"] == "failed"
    assert "fail_on" in r["recorded"]["derived_from"]
    assert "verdict=needs_work" in r["recorded"]["derived_from"]


def test_a_passing_verdict_still_advances(pack, repo):
    (repo / "spec" / "changes" / "s1").mkdir(parents=True)
    (repo / "spec" / "changes" / "s1" / "notes.md").write_text("n\n")
    r = _next(pack, repo, after="think", status="completed",
              out={"complexity": "M", "verdict": "pass"})
    assert r["step_id"] == "finish"
    assert r["recorded"]["status"] == "completed"
    assert "derived_from" not in r["recorded"]


def test_out_schema_exposes_fail_on(pack, repo):
    """The driver can see which values the contract treats as a rejection."""
    r = _next(pack, repo, after="signoff", status="completed")
    schema = r["payload"]["out_schema"]
    assert schema["verdict"]["fail_on"] == ["needs_work"]
    assert schema["complexity"].get("fail_on") is None


# --------------------------------------------------- ORCHESTRATOR_PROMPT_DIRS
def test_prompt_dirs_maps_every_judgment_step(pack, repo):
    """The learn charter needs step_id -> charter dir; it is pure config."""
    r = _next(pack, repo)
    dirs = json.loads(r["payload"]["env"]["ORCHESTRATOR_PROMPT_DIRS"])
    assert set(dirs) == {"think"}, "only judgment steps have charters"
    assert dirs["think"].endswith("steps/think")
    # Exec steps get the same map — a script may need to write beside another
    # step's charter (persist-learnings does).
    assert r["kind"] == "exec"


# ------------------------------------------------- attempts terminate a loop
def test_the_documented_attempt_rule_terminates_at_max_retries(pack, repo):
    """Walk think -> prep -> think … counting attempts the way the skill says.

    `--attempt` is how many times the step being reported has now run,
    counting this one. With `max_retries: 2` on think, the second report of a
    failing think must stop the run.
    """
    (repo / "spec" / "changes" / "s1").mkdir(parents=True)
    (repo / "spec" / "changes" / "s1" / "notes.md").write_text("n\n")
    runs: dict[str, int] = {}
    step, guard = "think", 0
    while guard < 10:
        guard += 1
        runs[step] = runs.get(step, 0) + 1          # this run of this step
        r = _next(pack, repo, after=step, status="completed", attempt=runs[step],
                  out={"complexity": "M", "verdict": "needs_work"}) \
            if step == "think" else \
            _next(pack, repo, after=step, exit_code=0, attempt=runs[step])
        if r["status"] == "needs_you":
            assert r["reason"] == "retries exhausted"
            assert runs["think"] == 2, runs      # max_retries: 2
            return
        step = r["step_id"]
    raise AssertionError(f"never exhausted retries: {runs}")
