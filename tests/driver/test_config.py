"""Config: defaults, environment overrides, and submit rejecting what the config does not allow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from orchestrator_driver import cli
from orchestrator_driver.config import Config, ConfigError


def test_defaults(tmp_path):
    c = Config.load(None, env={})
    home = Path.home() / ".orchestrator"
    assert (c.db, c.runs_root) == (home / "driver.db", home / "runs")
    assert (c.agent, c.agents, c.model_override, c.notify_cmd) == ("claude", {}, "", "")
    assert (c.gate_timeout_days, c.poll_seconds, c.pack, c.projects, c.projects_root) == (7, 0.5, None, (), None)
    assert (c.sandbox_domains, c.step_env_keep) == ((), ())


def test_file_and_env_override(tmp_path):
    f = tmp_path / "d.yaml"
    f.write_text(
        yaml.safe_dump(
            {
                "db": "data/x.db",
                "pack": "/p",
                "agent": "codex",
                "agents": {"explore": "stub"},
                "sandbox_domains": ["a.example", "b.example"],
                "gate_timeout_days": 2,
                "projects": ["/r1"],
            }
        )
    )
    c = Config.load(f, env={})
    assert c.db == tmp_path / "data" / "x.db"  # relative paths are relative to the file
    assert (c.agent, c.agents, c.sandbox_domains, c.gate_timeout_days) == (
        "codex",
        {"explore": "stub"},
        ("a.example", "b.example"),
        2,
    )
    env = {"ORCH_DB": "/e.db", "ORCH_AGENT": "stub", "ORCH_SANDBOX_DOMAINS": "c.example", "ORCH_POLL_SECONDS": "2"}
    c = Config.load(f, env=env)
    assert (c.db, c.agent, c.sandbox_domains, c.poll_seconds) == (Path("/e.db"), "stub", ("c.example",), 2)
    assert c.pack == Path("/p") and c.gate_timeout_days == 2  # untouched keys keep the file's value
    assert Config.load(f, env={"ORCH_DRIVER_CONFIG": str(f)}).agent == "codex"
    assert Config.load(None, env={"ORCHESTRATOR_CONFIG": "/engine/pack"}).pack == Path("/engine/pack")


@pytest.mark.parametrize(
    "bad", [{"agnet": "stub"}, {"agent": "gpt"}, {"agents": {"x": "nope"}}, {"poll_seconds": 0}, {"projects": 3}]
)
def test_bad_config_rejected(tmp_path, bad):
    f = tmp_path / "d.yaml"
    f.write_text(yaml.safe_dump(bad))
    with pytest.raises(ConfigError):
        Config.load(f, env={})


def test_projects_root_lists_git_repos_only(tmp_path, env):
    root = tmp_path / "code"
    (root / "plain").mkdir(parents=True)
    (root / "repo" / ".git").mkdir(parents=True)
    c = Config(projects_root=root, projects=(env.project,))
    assert set(c.allowed_projects()) == {(root / "repo").resolve(), env.project.resolve()}
    assert c.resolve_project("repo") == (root / "repo").resolve()


def sub(env, capsys, *args) -> tuple[int, str]:
    rc = cli.main(["submit", *args, "--config", str(env.config)])
    return rc, capsys.readouterr().err


def test_submit_rejections(env, capsys, tmp_path):
    ok = ["--workflow", "dev", "--project", str(env.project), "--slug", "one", "--ticket", "t"]
    assert sub(env, capsys, *ok)[0] == 0
    run = env.run("one")
    assert run["request"]["artifacts_dir"] == str(env.run_dir("one") / "artifacts")
    assert run["request"]["repo_root"] == str(env.project.resolve())

    runs = len(env.db.runs())

    def bad(**kw) -> str:
        args = {"workflow": "dev", "project": str(env.project), "slug": "two", "ticket": "t", **kw}
        rc, err = sub(env, capsys, *(x for k, v in args.items() for x in (f"--{k}", v)))
        assert rc == 2 and len(env.db.runs()) == runs
        return err

    assert "unknown workflow 'nope'" in bad(workflow="nope")
    assert "not one of the configured projects" in bad(project=str(tmp_path))
    assert "not one of the configured projects" in bad(project="elsewhere")
    assert "invalid slug" in bad(slug="../x")
    assert "invalid slug" in bad(slug="has space")
    assert "already exists" in bad(slug="one")
    env.run_dir("taken").mkdir(parents=True)
    assert "already exists" in bad(slug="taken")


def test_submit_ticket_file_and_workflows(env, capsys, tmp_path):
    t = tmp_path / "t.md"
    t.write_text("from a file")
    rc = cli.main(
        [
            "submit",
            "--workflow",
            "dev",
            "--project",
            "app",
            "--slug",
            "f",
            "--ticket-file",
            str(t),
            "--config",
            str(env.config),
        ]
    )
    assert rc == 0 and env.run("f")["request"]["ticket"] == "from a file"
    capsys.readouterr()
    assert cli.main(["workflows", "--config", str(env.config)]) == 0
    listed = {w["name"]: w for w in json.loads(capsys.readouterr().out)}
    assert "dev" in listed and listed["dev"]["steps"] > 0 and listed["dev"]["gates"] >= 1
