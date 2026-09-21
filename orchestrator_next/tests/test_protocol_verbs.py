"""Protocol v2 verbs end-to-end: start -> step -> done -> step.

The recipe is exec -> judgment -> exec, which is the shape that proves the two
things Phase 1.1 promises: `step` runs exec steps internally and only surfaces
at a judgment step, and `done --out` validates the structured output against
the contract's `out:` block instead of parsing a COMPLETION text block.

Nothing below the engine is faked. Real contracts, real dispatcher, real
recorder, real state store — the model is simply never called, because these
verbs hand the payload out rather than running it.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator_next import protocol
from orchestrator_next.parser import KIND_EXEC, KIND_JUDGMENT
from orchestrator_next.tests.conftest import drive

STEPS = ("prep", "think", "finish")


@pytest.fixture
def repo(tmp_path):
    """A real git repo with the artifact dir the contracts point into."""
    root = tmp_path / "repo"
    (root / "spec" / "changes" / "p-run").mkdir(parents=True)
    (root / "README.md").write_text("repo\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t.test"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-m", "init"]
    ):
        subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, env=env, check=True)
    return root


@pytest.fixture
def pack(tmp_path, repo, monkeypatch):
    """A three-step pack: exec, judgment (with in:/out:), exec."""
    pack_root = repo / ".orchestrator" / "tp"
    steps = pack_root / "steps"
    (pack_root / "workflows").mkdir(parents=True)
    (pack_root / "workflows" / "mini.yaml").write_text(
        yaml.safe_dump({"steps": list(STEPS)}), encoding="utf-8"
    )

    for step_id in ("prep", "finish"):
        d = steps / step_id
        d.mkdir(parents=True)
        (d / "contract.yaml").write_text(
            yaml.safe_dump({"id": step_id, "version": 1, "kind": "exec",
                            "run": "script.sh"}),
            encoding="utf-8"
        )
        script = d / "script.sh"
        script.write_text(
            '#!/usr/bin/env bash\necho \'{"reason": "ran"}\'\n', encoding="utf-8"
        )
        script.chmod(0o755)

    think = steps / "think"
    think.mkdir(parents=True)
    (think / "contract.yaml").write_text(
        yaml.safe_dump({
            "id": "think",
            "version": 1,
            "kind": "judgment",
            "prompt": "SKILL.md",
            "max_turns": 12,
            "tools": ["fs.read", "fs.write"],
            "side_effects": [],
            "in": {"brief": {"artifact": "brief.md", "optional": True}},
            "out": {
                "notes": {"artifact": "notes.md"},
                "complexity": {"type": "enum", "values": ["S", "M", "L"]},
            },
        }),
        encoding="utf-8"
    )
    (think / "SKILL.md").write_text("# think\n\nThink about it.\n", encoding="utf-8")

    models = pack_root / "models.yaml"
    models.write_text(
        "models:\n  standard: {model_id: mock-model, tool: mock}\n"
        "step_models:\n" + "".join(f"  {s}: standard\n" for s in STEPS),
        encoding="utf-8"
    )
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(models))
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack_root))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path / "orchome"))
    monkeypatch.delenv("ORCHESTRATOR_STATE_URL", raising=False)
    monkeypatch.setenv(
        "ORCHESTRATOR_STATE_BACKEND", "file"
    )
    return pack_root


def _artifacts(repo: Path) -> Path:
    """The engine-owned artifacts base (plan Phase 2.1) — the `mini` recipe
    declares no artifacts_root, so this is where named artifacts land."""
    return repo / ".orchestrator" / "runs" / "p-run" / "artifacts"


def test_start_returns_the_exec_step_without_running_it(pack, repo):
    """start seeds the run and hands back the FIRST step, whatever its kind.

    The leading step is an exec step: the engine returns it as a payload
    naming the script, and never spawns anything itself.
    """
    result, code = protocol.start("mini", "p-run")
    assert code == 0
    assert result["slug"] == "p-run"
    assert result["run_id"]

    nxt = result["next"]
    assert nxt["status"] == "ready"
    assert nxt["kind"] == KIND_EXEC
    assert nxt["step_id"] == "prep"
    exec_payload = nxt["payload"]
    assert exec_payload["run_path"].endswith(".sh")
    assert Path(exec_payload["run_path"]).is_file()
    assert exec_payload["step_dir"]
    assert exec_payload["env"]["ORCHESTRATOR_STEP_ID"] == "prep"
    # Nothing ran: no history entry for it yet.
    assert yaml.safe_load(Path(result["state"]).read_text())["step_history"] == []

    # Drive it the way a driver does, and the judgment step comes next.
    nxt = drive(result["state"], first=nxt)
    assert nxt["status"] == "ready"
    assert nxt["kind"] == KIND_JUDGMENT
    assert nxt["step_id"] == "think"

    payload = nxt["payload"]
    assert payload["max_turns"] == 12
    assert payload["tools"] == ["fs.read", "fs.write"]
    # Artifacts are resolved to absolute paths by the engine (principle 4).
    assert payload["out"]["notes"] == str(_artifacts(repo) / "notes.md")
    assert payload["in"]["brief"] == str(_artifacts(repo) / "brief.md")
    assert payload["out_schema"]["complexity"]["values"] == ["S", "M", "L"]
    # The engine composes no prompt: it names the charter to read.
    assert "system" not in payload and "prompt" not in payload
    assert payload["prompt_path"].endswith(".md")


def test_done_records_and_returns_next_step(pack, repo):
    """A valid --out advances the run through the trailing exec step to done."""
    started, _ = protocol.start("mini", "p-run")
    run = started["state"]
    drive(run, first=started["next"])          # run the leading exec step
    (_artifacts(repo) / "notes.md").parent.mkdir(parents=True, exist_ok=True)
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")

    result, code = protocol.done(
        run, "think",
        out={"notes": str(_artifacts(repo) / "notes.md"), "complexity": "M"}
    )
    assert code == 0
    assert result["status"] == "ok"
    # `finish` is an exec step: `done` hands it back for the driver to run.
    assert result["next"]["kind"] == KIND_EXEC
    assert drive(run, first=result["next"])["status"] == "done"


def test_done_rejects_missing_artifact(pack, repo):
    """A declared artifact that was never written fails the call, not the run."""
    started, _ = protocol.start("mini", "p-run")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.done(
            started["state"], "think",
            out={"complexity": "M"}
        )
    assert "notes" in str(exc.value)
    assert "artifact not found" in str(exc.value)


def test_done_rejects_enum_outside_declared_values(pack, repo):
    started, _ = protocol.start("mini", "p-run")
    drive(started["state"], first=started["next"])
    (_artifacts(repo) / "notes.md").parent.mkdir(parents=True, exist_ok=True)
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.done(
            started["state"], "think",
            out={"notes": "notes.md", "complexity": "XXL"}
        )
    assert "complexity" in str(exc.value)


def test_done_rejects_a_declared_value_left_out_entirely(pack, repo):
    """The v1 engine coerced a completed step to failed when a contract's
    required output was missing. v2 rejects the `done` call instead: the
    harness fixes the step's output and retries, rather than the engine
    recording a half-finished step (protocol v2 §5)."""
    started, _ = protocol.start("mini", "p-run")
    drive(started["state"], first=started["next"])
    (_artifacts(repo) / "notes.md").parent.mkdir(parents=True, exist_ok=True)
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.done(
            started["state"], "think",
            out={"notes": "notes.md"},  # complexity declared, never reported
        )
    assert "complexity" in str(exc.value)
    assert "missing" in str(exc.value)
















def test_resolve_run_rejects_unknown_ref(pack):
    with pytest.raises(protocol.ProtocolError):
        protocol.resolve_run("definitely-not-a-run")


def test_step_on_unknown_run_is_an_engine_error(pack, capsys):
    """Unknown run is exit 3 with a JSON error — never a silent new run."""
    code = protocol.main("step", ["nope-not-here"])
    assert code == protocol.EXIT_ERROR
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "error"


def test_start_on_a_live_slug_resumes_rather_than_reseeds(pack, repo):
    """A second ``start`` on the same slug picks the run back up.

    It used to mint a fresh ``run_id`` and strand the first run mid-step, so a
    driver that calls ``start`` to resume (the Claude mod's ``run`` tool does)
    silently lost every step already recorded.
    """
    first, code = protocol.start("mini", "p-resume")
    assert code == 0
    assert first.get("resumed") is not True

    second, code = protocol.start("mini", "p-resume")
    assert code == 0
    assert second["resumed"] is True
    assert second["run_id"] == first["run_id"]
    assert second["state"] == first["state"]
    assert second["next"]["step_id"] == first["next"]["step_id"]


def test_start_on_an_unknown_slug_still_seeds(pack, repo):
    """The resume path must not swallow a genuinely new run."""
    result, code = protocol.start("mini", "p-fresh")
    assert code == 0
    assert result.get("resumed") is not True
    assert result["run_id"]
