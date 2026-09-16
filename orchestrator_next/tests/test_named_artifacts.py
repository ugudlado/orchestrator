"""Named artifacts end to end: recipe fields, prompt templating, wiring, record.

Covers plan Phase 2.2 (``{in.x}`` / ``{out.y}`` in charters, wiring errors at
validate-workflow) and 2.3 (artifacts hashed onto the node, ``validate:``
scripts, ``status --json``), plus the 2.4 state-diet fields.
"""
from __future__ import annotations

import hashlib
import subprocess

import pytest
import yaml

from orchestrator_next import parser, protocol, record, validate_workflow


# ---------------------------------------------------------------------------
# fixtures: a tiny two-step pack
# ---------------------------------------------------------------------------
def _write_step(steps: "object", step_id: str, contract: dict, charter: str = "") -> None:
    d = steps / step_id
    d.mkdir(parents=True)
    (d / "contract.yaml").write_text(yaml.safe_dump(contract, sort_keys=False),
                                     encoding="utf-8")
    if charter:
        (d / "SKILL.md").write_text(charter, encoding="utf-8")


@pytest.fixture
def pack(tmp_path, monkeypatch):
    """A pack with `brief` (exec) → `think` (judgment) reading its output."""
    root = tmp_path / "pack"
    steps = root / "steps"
    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "wf.yaml").write_text(
        yaml.safe_dump({"steps": ["brief", "think"]}, sort_keys=False),
        encoding="utf-8",
    )
    _write_step(steps, "brief", {
        "id": "brief", "kind": "exec", "run": "script.sh",
        "out": {"brief": {"artifact": "brief.md"}},
    })
    (steps / "brief" / "script.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
    _write_step(steps, "think", {
        "id": "think", "kind": "judgment", "prompt": "SKILL.md",
        "in": {"brief": {"artifact": "brief.md"}},
        "out": {"notes": {"artifact": "notes.md"}},
    }, charter="Read {in.brief}. Write your notes to {out.notes}.\n")
    models = root / "models.yaml"
    models.write_text(
        "models:\n  standard: {model_id: mock-model, tool: mock}\n"
        "step_models:\n  brief: standard\n  think: standard\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(models))
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(root))
    monkeypatch.setenv("REPO_ROOT", str(tmp_path / "repo"))
    (tmp_path / "repo").mkdir()
    return root


def _state(tmp_path, schema="wf"):
    return {
        "change_id": "r1", "slug": "r1", "schema": schema, "recipe": schema,
        "status": "active", "phase": "main", "repo_root": str(tmp_path / "repo"),
        "workflow_plan": {"main": {"nodes": [
            {"id": "brief", "status": "completed"},
            {"id": "think", "depends_on": ["brief"], "status": "pending"},
        ], "filtered": []}},
        "step_history": [],
    }


# ---------------------------------------------------------------------------
# 2.1 — recipe fields
# ---------------------------------------------------------------------------
def test_recipe_parses_artifacts_root_and_inputs(pack):
    (pack / "workflows" / "wf.yaml").write_text(yaml.safe_dump({
        "artifacts_root": "spec/changes/{slug}",
        "inputs": {"ticket": {"artifact": "ticket.md"}},
        "steps": ["brief", "think"],
    }, sort_keys=False), encoding="utf-8")
    recipe = parser.load_recipe("wf")
    assert recipe.artifacts_root == "spec/changes/{slug}"
    assert recipe.inputs == {"ticket": {"artifact": "ticket.md"}}
    assert recipe.steps == ["brief", "think"]


def test_recipe_without_the_new_keys_still_loads(pack):
    recipe = parser.load_recipe("wf")
    assert recipe.artifacts_root == ""
    assert recipe.inputs == {}


def test_recipe_rejects_a_non_string_artifacts_root(pack):
    (pack / "workflows" / "wf.yaml").write_text(
        yaml.safe_dump({"artifacts_root": ["a"], "steps": []}), encoding="utf-8")
    with pytest.raises(parser.ContractError):
        parser.load_recipe("wf")


def test_artifact_base_follows_the_recipe_override(pack, tmp_path):
    (pack / "workflows" / "wf.yaml").write_text(yaml.safe_dump({
        "artifacts_root": "spec/changes/{slug}", "steps": ["brief", "think"],
    }, sort_keys=False), encoding="utf-8")
    base = protocol._artifact_base(_state(tmp_path))
    assert base == tmp_path / "repo" / "spec" / "changes" / "r1"


def test_artifact_base_defaults_to_the_engine_location(pack, tmp_path):
    base = protocol._artifact_base(_state(tmp_path))
    assert base == tmp_path / "repo" / ".orchestrator" / "runs" / "r1" / "artifacts"


# ---------------------------------------------------------------------------
# 2.2 — templating
# ---------------------------------------------------------------------------
def test_render_placeholders_substitutes_both_sides():
    out = protocol.render_placeholders(
        "read {in.a} write {out.b}", {"a": "/A"}, {"b": "/B"})
    assert out == "read /A write /B"


def test_render_placeholders_leaves_unknown_names_alone():
    assert protocol.render_placeholders("{in.zzz}", {"a": "/A"}, {}) == "{in.zzz}"


def test_placeholder_names_finds_every_reference():
    assert protocol.placeholder_names("{in.a} x {out.b} y {in.a}") == {
        ("in", "a"), ("out", "b")}


def test_dispatched_charter_carries_resolved_paths(pack, tmp_path, monkeypatch):
    """The judgment payload's system prompt has real paths, not placeholders."""
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(_state(tmp_path)), encoding="utf-8")
    result, code = protocol.step(str(state_path))
    assert code == 0, result
    system = result["payload"]["system"]
    assert "{in.brief}" not in system and "{out.notes}" not in system
    assert result["payload"]["in"]["brief"] in system
    assert result["payload"]["out"]["notes"] in system


def test_dispatch_creates_the_artifacts_dir(pack, tmp_path):
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(_state(tmp_path)), encoding="utf-8")
    protocol.step(str(state_path))
    assert protocol._artifact_base(_state(tmp_path)).is_dir()


# ---------------------------------------------------------------------------
# 2.2 — wiring validation
# ---------------------------------------------------------------------------
def test_wiring_accepts_an_input_produced_upstream(pack, tmp_path):
    validate_workflow.validate_workflow("wf", str(tmp_path / "repo"))


def test_wiring_rejects_an_input_nobody_produces(pack, tmp_path, capsys):
    (pack / "steps" / "think" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "think", "kind": "judgment", "prompt": "SKILL.md",
        "in": {"ghost": {"artifact": "ghost.md"}},
        "out": {"notes": {"artifact": "notes.md"}},
    }, sort_keys=False), encoding="utf-8")
    (pack / "steps" / "think" / "SKILL.md").write_text("no placeholders\n",
                                                       encoding="utf-8")
    with pytest.raises(SystemExit):
        validate_workflow.validate_workflow("wf", str(tmp_path / "repo"))
    err = capsys.readouterr().err
    assert "think: in.ghost" in err


def test_wiring_accepts_an_input_declared_by_the_recipe(pack, tmp_path):
    (pack / "workflows" / "wf.yaml").write_text(yaml.safe_dump({
        "inputs": {"ghost": {"artifact": "ghost.md"}},
        "steps": ["brief", "think"],
    }, sort_keys=False), encoding="utf-8")
    (pack / "steps" / "think" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "think", "kind": "judgment", "prompt": "SKILL.md",
        "in": {"ghost": {"artifact": "ghost.md"}},
        "out": {"notes": {"artifact": "notes.md"}},
    }, sort_keys=False), encoding="utf-8")
    (pack / "steps" / "think" / "SKILL.md").write_text("ok\n", encoding="utf-8")
    validate_workflow.validate_workflow("wf", str(tmp_path / "repo"))


def test_wiring_accepts_an_optional_unproduced_input(pack, tmp_path):
    (pack / "steps" / "think" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "think", "kind": "judgment", "prompt": "SKILL.md",
        "in": {"ghost": {"artifact": "ghost.md", "optional": True}},
        "out": {"notes": {"artifact": "notes.md"}},
    }, sort_keys=False), encoding="utf-8")
    (pack / "steps" / "think" / "SKILL.md").write_text("ok\n", encoding="utf-8")
    validate_workflow.validate_workflow("wf", str(tmp_path / "repo"))


def test_wiring_rejects_an_undeclared_placeholder(pack, tmp_path, capsys):
    (pack / "steps" / "think" / "SKILL.md").write_text(
        "write {out.zzz}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        validate_workflow.validate_workflow("wf", str(tmp_path / "repo"))
    assert "{out.zzz}" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 2.3 — recording artifacts onto the node
# ---------------------------------------------------------------------------
def _done_payload(step_id="think", **kw):
    payload = {
        "step_id": step_id, "phase": "main", "status": "completed",
        "agent": "claude", "attempt": 1,
        "usage": {"model": "m", "input_tokens": 10, "output_tokens": 5},
        "outputs": {"reason": "done"},
    }
    payload.update(kw)
    return payload


def test_record_hashes_declared_outputs_onto_the_node(pack, tmp_path):
    base = protocol._artifact_base(_state(tmp_path))
    base.mkdir(parents=True)
    (base / "brief.md").write_text("b\n", encoding="utf-8")
    (base / "notes.md").write_text("n\n", encoding="utf-8")

    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(_state(tmp_path)), encoding="utf-8")
    _result, rc = record.record(str(state_path), _done_payload())
    assert rc == 0

    doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    node = next(n for n in doc["workflow_plan"]["main"]["nodes"] if n["id"] == "think")
    assert node["artifacts"] == [{
        "name": "notes", "path": "notes.md",
        "sha256": hashlib.sha256(b"n\n").hexdigest(),
    }]
    # Input hashes are recorded too, so a resume can tell whether they moved.
    assert node["input_artifacts"] == [
        {"name": "brief", "sha256": hashlib.sha256(b"b\n").hexdigest()}
    ]


def test_record_runs_a_validate_script_and_rejects_a_failure(pack, tmp_path):
    (pack / "steps" / "think" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "think", "kind": "judgment", "prompt": "SKILL.md",
        "in": {"brief": {"artifact": "brief.md"}},
        "out": {"notes": {"artifact": "notes.md"}},
        "validate": "exit 7",
    }, sort_keys=False), encoding="utf-8")
    base = protocol._artifact_base(_state(tmp_path))
    base.mkdir(parents=True)
    (base / "notes.md").write_text("n\n", encoding="utf-8")

    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(_state(tmp_path)), encoding="utf-8")
    result, rc = record.record(str(state_path), _done_payload())
    assert rc == 3
    assert result["error"] == "validate_failed"

    doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    node = next(n for n in doc["workflow_plan"]["main"]["nodes"] if n["id"] == "think")
    assert node["status"] == "pending"  # not advanced


def test_record_accepts_a_passing_validate_script(pack, tmp_path):
    (pack / "steps" / "think" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "think", "kind": "judgment", "prompt": "SKILL.md",
        "out": {"notes": {"artifact": "notes.md"}},
        "validate": "true",
    }, sort_keys=False), encoding="utf-8")
    base = protocol._artifact_base(_state(tmp_path))
    base.mkdir(parents=True)
    (base / "notes.md").write_text("n\n", encoding="utf-8")
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(_state(tmp_path)), encoding="utf-8")
    _result, rc = record.record(str(state_path), _done_payload())
    assert rc == 0


def test_parser_rejects_a_non_string_validate(pack):
    (pack / "steps" / "think" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "think", "kind": "judgment", "prompt": "SKILL.md",
        "validate": ["a"],
    }, sort_keys=False), encoding="utf-8")
    with pytest.raises(parser.ContractError):
        parser.load_contract_for_step("think")


def test_status_reports_the_recorded_artifacts(pack, tmp_path):
    base = protocol._artifact_base(_state(tmp_path))
    base.mkdir(parents=True)
    (base / "notes.md").write_text("n\n", encoding="utf-8")
    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(_state(tmp_path)), encoding="utf-8")
    record.record(str(state_path), _done_payload())

    result, code = protocol.status(str(state_path))
    assert code == 0
    assert result["artifacts"] == [{
        "name": "notes", "path": "notes.md",
        "sha256": hashlib.sha256(b"n\n").hexdigest(), "step_id": "think",
    }]
    assert result["artifacts_base"] == str(base)


# ---------------------------------------------------------------------------
# 2.4 — state diet
# ---------------------------------------------------------------------------
def test_nodes_carry_only_the_declared_keys(pack, tmp_path):
    """A promoted plan node never copies contract prose into state."""
    from orchestrator_next.generate_plan import generate_plan

    state_path = tmp_path / "seed.yaml"
    state_path.write_text(yaml.safe_dump({
        "change_id": "r1", "slug": "r1", "schema": "wf", "status": "active",
        "repo_root": str(tmp_path / "repo"), "phase": "main",
        "workflow_plan": {"main": {"active": ["brief", "think"], "filtered": []}},
        "step_history": [],
    }, sort_keys=False), encoding="utf-8")
    generate_plan(str(state_path))

    doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    allowed = {"id", "status", "depends_on", "on_success", "on_failure", "max_retries"}
    for node in doc["workflow_plan"]["main"]["nodes"]:
        assert set(node) <= allowed, f"node leaked contract data: {set(node) - allowed}"
        for banned in ("rules", "goal", "inputs", "outputs", "prompt", "instruction"):
            assert banned not in node


def test_seeded_state_carries_run_id_recipe_and_pack_sha(pack, tmp_path):
    from orchestrator_next.run_loop import seed_state_file

    state_path = tmp_path / "seeded.yaml"
    seed_state_file(
        state_path, slug="r9", schema="wf",
        repo_root=str(tmp_path / "repo"), config_pack="pack",
    )
    doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert doc["run_id"]
    assert doc["recipe"] == "wf"
    assert len(doc["pack_sha"]) == 64  # non-git pack → yaml digest


def test_seeded_state_uses_a_caller_supplied_run_id(pack, tmp_path):
    from orchestrator_next.run_loop import seed_state_file

    state_path = tmp_path / "seeded.yaml"
    seed_state_file(state_path, slug="r9", schema="wf",
                    repo_root=str(tmp_path / "repo"), run_id="fixed-id")
    doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert doc["run_id"] == "fixed-id"


def test_pack_sha_prefers_the_git_head(pack, tmp_path):
    from orchestrator_next.paths import pack_sha

    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    for args in (["init", "-b", "main"], ["config", "user.email", "t@t.test"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-m", "init"]):
        subprocess.run(["git", "-C", str(pack), *args],
                       capture_output=True, env=env, check=True)
    head = subprocess.run(["git", "-C", str(pack), "rev-parse", "HEAD"],
                          capture_output=True, text=True, env=env).stdout.strip()
    assert pack_sha(pack) == head


def test_finalize_discards_scratch_but_keeps_artifacts(pack, tmp_path):
    from orchestrator_next.paths import scratch_dir
    from orchestrator_next.run_loop import discard_scratch

    state = _state(tmp_path)
    scratch = scratch_dir(state)
    scratch.mkdir(parents=True)
    (scratch / "tmp.txt").write_text("junk\n", encoding="utf-8")
    base = protocol._artifact_base(state)
    base.mkdir(parents=True, exist_ok=True)
    (base / "notes.md").write_text("keep\n", encoding="utf-8")

    assert discard_scratch(state) is True
    assert not scratch.exists()
    assert (base / "notes.md").is_file()


def test_discard_scratch_is_a_no_op_when_absent(pack, tmp_path):
    from orchestrator_next.run_loop import discard_scratch

    assert discard_scratch(_state(tmp_path)) is False


def test_finalize_state_discards_scratch(pack, tmp_path):
    """The engine-side teardown, not just the helper: finalize drops scratch."""
    from orchestrator_next.paths import scratch_dir
    from orchestrator_next.run_loop import _finalize_state

    state = _state(tmp_path)
    scratch = scratch_dir(state)
    scratch.mkdir(parents=True)
    (scratch / "junk.txt").write_text("x\n", encoding="utf-8")
    base = protocol._artifact_base(state)
    base.mkdir(parents=True, exist_ok=True)
    (base / "notes.md").write_text("keep\n", encoding="utf-8")

    state_path = tmp_path / "state.yaml"
    state_path.write_text(yaml.safe_dump(state), encoding="utf-8")
    _finalize_state(str(state_path))

    assert not scratch.exists()
    assert (base / "notes.md").is_file()
    doc = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    assert doc["status"] == "completed"


def test_wiring_warns_instead_of_failing_when_a_producer_is_unmigrated(
    pack, tmp_path, capsys
):
    """A step with no in:/out: at all may still write the file — warn, don't fail."""
    (pack / "steps" / "brief" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "brief", "kind": "exec", "run": "script.sh",
    }, sort_keys=False), encoding="utf-8")
    validate_workflow.validate_workflow("wf", str(tmp_path / "repo"))
    err = capsys.readouterr().err
    assert "WARN" in err and "in.brief has no declared producer" in err


def test_wiring_still_fails_when_every_upstream_step_is_migrated(
    pack, tmp_path, capsys
):
    """With no unmigrated step to hide behind, a missing producer is an error."""
    (pack / "steps" / "think" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "think", "kind": "judgment", "prompt": "SKILL.md",
        "in": {"ghost": {"artifact": "ghost.md"}},
        "out": {"notes": {"artifact": "notes.md"}},
    }, sort_keys=False), encoding="utf-8")
    (pack / "steps" / "think" / "SKILL.md").write_text("ok\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        validate_workflow.validate_workflow("wf", str(tmp_path / "repo"))
    assert "think: in.ghost" in capsys.readouterr().err
