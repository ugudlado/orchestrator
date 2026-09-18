"""Signoff gates end to end (docs/protocol-v2.md §7, plan Phase 3.1).

Covers the gate contract from both ends: the recipe shape that declares one
(`{gate: ..., show: [...], approve_as: ...}` plus `requires:` downstream), and
the verbs that drive it (`step` mints and parks, `approve` resumes, `cancel`
aborts). Uses a synthetic pack so the assertions are about the engine, not
about whichever steps the real pack happens to ship today.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
import yaml

from orchestrator_next import gates, protocol
from orchestrator_next.parser import KIND_GATE


# ---------------------------------------------------------------------------
# a minimal pack: write -> gate -> guarded write
# ---------------------------------------------------------------------------
@pytest.fixture
def gates_pack(tmp_path):
    """A pack whose recipe is: design (writes design.md) | gate | implement."""
    root = tmp_path / "pack"
    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "feature.yaml").write_text(yaml.safe_dump({
        "name": "feature",
        "steps": [
            "design",
            {"gate": "design-signoff", "show": ["design"],
             "approve_as": "impl_token"},
            {"id": "implement", "requires": "impl_token"},
        ],
    }, sort_keys=False), encoding="utf-8")

    for step_id, out_name, side_effects in (
        ("design", "design.md", []),
        ("implement", "impl.md", ["write:git"]),
    ):
        d = root / "steps" / step_id
        d.mkdir(parents=True)
        (d / "contract.yaml").write_text(yaml.safe_dump({
            "id": step_id, "version": 1, "kind": "judgment",
            "tools": ["fs.write"], "side_effects": side_effects,
            "prompt": "SKILL.md",
            "out": {out_name.split(".")[0]: {"artifact": out_name}},
        }, sort_keys=False), encoding="utf-8")
        (d / "SKILL.md").write_text(f"Do {step_id}.\n", encoding="utf-8")

    (root / "models.yaml").write_text(yaml.safe_dump({
        "models": {"standard": {"tool": "claude", "model_id": "claude-sonnet-5"}},
        "step_models": {"design": "standard", "implement": "standard"},
    }, sort_keys=False), encoding="utf-8")
    return root


@pytest.fixture
def pack(gates_pack):
    return gates_pack


@pytest.fixture
def gates_run(tmp_path, pack, monkeypatch):
    """A seeded state whose plan is the pack's three nodes, gate in the middle."""
    repo = tmp_path / "repo"
    (repo / ".orchestrator" / "runs" / "g1" / "artifacts").mkdir(parents=True)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path / "orchome"))
    monkeypatch.setenv("ORCHESTRATOR_STATE_BACKEND", "file")
    monkeypatch.setenv("ORCHESTRATOR_SKIP_USAGE_CHECK", "1")
    monkeypatch.delenv("ORCHESTRATOR_STATE_URL", raising=False)

    state = tmp_path / "g1_state.yaml"
    state.write_text(yaml.safe_dump({
        "change_id": "g1", "slug": "g1", "schema": "feature",
        "status": "active", "phase": "main",
        "repo_root": str(repo), "worktree_path": str(repo),
        "workflow_plan": {"main": {"nodes": [
            {"id": "design", "depends_on": [], "status": "pending"},
            {"id": "design-signoff", "depends_on": ["design"],
             "status": "pending", "kind": "gate", "show": ["design"],
             "approve_as": "impl_token"},
            {"id": "implement", "depends_on": ["design-signoff"],
             "status": "pending", "requires": "impl_token"},
        ], "filtered": []}},
        "step_history": [],
    }, sort_keys=False), encoding="utf-8")
    return str(state)


@pytest.fixture
def run(gates_run):
    return gates_run


def _artifacts(run_path):
    raw = yaml.safe_load(open(run_path, encoding="utf-8"))
    return protocol._artifact_base(raw)


def _finish_design(run):
    """Drive the first step so the run is parked at the gate."""
    result, _ = protocol.step(run)
    assert result["step_id"] == "design", result
    base = _artifacts(run)
    base.mkdir(parents=True, exist_ok=True)
    (base / "design.md").write_text("the design\n" * 60, encoding="utf-8")
    protocol.done(run, "design", out={"design": "design.md"},
                  usage={"input_tokens": 10, "output_tokens": 5})


# ---------------------------------------------------------------------------
# 1. recipe -> gate nodes
# ---------------------------------------------------------------------------
def test_generate_plan_promotes_a_gate_entry_into_a_gate_node(tmp_path, pack,
                                                              monkeypatch):
    from orchestrator_next.generate_plan import generate_plan

    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    state = tmp_path / "plan_state.yaml"
    state.write_text(yaml.safe_dump({
        "change_id": "p", "slug": "p", "schema": "feature", "phase": "main",
        "repo_root": str(tmp_path),
        "workflow_plan": {"main": {
            "active": ["design", "design-signoff", "implement"], "filtered": []}},
        "step_history": [],
    }, sort_keys=False), encoding="utf-8")

    generate_plan(str(state))
    nodes = yaml.safe_load(state.read_text())["workflow_plan"]["main"]["nodes"]
    by_id = {n["id"]: n for n in nodes}

    assert by_id["design-signoff"]["kind"] == "gate"
    assert by_id["design-signoff"]["show"] == ["design"]
    assert by_id["design-signoff"]["approve_as"] == "impl_token"
    assert by_id["implement"]["requires"] == "impl_token"
    # A plain step keeps no gate keys at all.
    assert "kind" not in by_id["design"]


def test_step_id_of_reads_a_gate_entry():
    from orchestrator_next.workflow_steps import is_gate_entry, step_id_of

    entry = {"gate": "design-signoff", "show": ["design"],
             "approve_as": "impl_token"}
    assert step_id_of(entry) == "design-signoff"
    assert is_gate_entry(entry)
    assert not is_gate_entry({"id": "implement"})


# ---------------------------------------------------------------------------
# 2. step at a gate: preview, token, blocked, idempotent
# ---------------------------------------------------------------------------
def test_step_at_a_gate_blocks_with_a_preview_and_a_token(run):
    _finish_design(run)

    result, code = protocol.step(run)
    assert code == 0
    assert result["status"] == "blocked"
    assert result["kind"] == KIND_GATE
    assert result["step_id"] == "design-signoff"

    payload = result["payload"]
    assert payload["token"]
    preview = payload["preview"]
    assert preview["step_id"] == "design-signoff"
    assert preview["token_name"] == "impl_token"

    shown = preview["show"]["design"]
    assert shown["exists"] is True
    assert shown["path"].endswith("design.md")
    assert len(shown["sha256"]) == 64
    # The preview is a head, not the whole file.
    assert shown["head"].count("\n") + 1 == gates.PREVIEW_HEAD_LINES

    # The run itself is parked, not merely reported as parked.
    raw = yaml.safe_load(open(run, encoding="utf-8"))
    assert raw["status"] == "blocked"
    assert raw["gates"][0]["status"] == "pending"
    assert raw["gates"][0]["token_name"] == "impl_token"


def test_gate_payload_shape_matches_what_the_mod_reads(run):
    """Drift guard: `protocol.ts`'s `GatePayload` type and every `gate.<path>`
    the Mod actually reads must resolve against a real `_gate_payload`.

    The Mod's `register.ts`/`protocol.ts` are generated straight from this
    engine (`pack_export.generate_claude`) but are hand-maintained TypeScript,
    not derived from `protocol.py` by any tool — nothing stops the two
    drifting apart the way `GatePayload` once did (flat `step_id`/`show`/
    `approve_as`/`gate_token` fields that `_gate_payload` never emitted,
    silently reading as `undefined` everywhere: "Approve undefined?").

    This greps the Mod source for the field paths it dereferences off a
    `GatePayload` and walks each one against an actual blocked-gate payload,
    so a future rename on either side fails a test instead of failing
    silently in the running Mod.
    """
    import re
    from pathlib import Path

    mod_dir = Path(__file__).resolve().parents[1] / "mod"
    register_src = (mod_dir / "register.ts").read_text(encoding="utf-8")
    protocol_src = (mod_dir / "protocol.ts").read_text(encoding="utf-8")

    # Every `gate.<dotted path>` register.ts dereferences off a GatePayload
    # (`gate` is that function's parameter name throughout runGate/pane wiring).
    paths = set(re.findall(r"\bgate\.([a-zA-Z_][a-zA-Z0-9_.]*)", register_src))
    assert paths, "no gate.<path> reads found — the grep itself drifted"
    assert "preview.token_name" in paths
    assert "preview.show" in paths
    assert "step_id" in paths

    _finish_design(run)
    result, code = protocol.step(run)
    assert code == 0
    assert result["status"] == "blocked"

    # StepResult.step_id, the sibling gateOf() reads step_id from (protocol.ts
    # documents this explicitly) — required even though it is not a `gate.`
    # path in register.ts.
    assert result["step_id"]

    payload = result["payload"]

    def resolve(obj, dotted):
        for part in dotted.split("."):
            assert isinstance(obj, dict), f"payload.{dotted}: {part!r} is not on a dict"
            assert part in obj, f"payload.{dotted}: {part!r} missing"
            obj = obj[part]
        return obj

    for dotted in paths:
        if dotted == "step_id":
            # payload.step_id is NOT trustworthy (this is the exact bug):
            # gateOf() must use the StepResult-level step_id instead. Assert
            # the sibling exists rather than the payload key, so this test
            # would have failed before that fix and stays meaningful after.
            continue
        resolve(payload, dotted)

    # GatePayload's declared shape must still name every field register.ts
    # dereferences, so `tsc` — not just this test — catches a future drift.
    for dotted in paths:
        top = dotted.split(".")[0]
        assert re.search(rf"\b{re.escape(top)}\??:", protocol_src), (
            f"GatePayload has no {top!r} field for register.ts's gate.{dotted}"
        )


def test_re_stepping_a_blocked_gate_returns_the_same_token(run):
    _finish_design(run)
    first, _ = protocol.step(run)
    second, _ = protocol.step(run)

    assert first["payload"]["token"] == second["payload"]["token"]
    raw = yaml.safe_load(open(run, encoding="utf-8"))
    assert len(raw["gates"]) == 1, "a second poll minted a second token"


def test_preview_reports_a_show_artifact_that_was_never_written(run):
    """A missing artifact is shown as missing, not silently omitted."""
    base = _artifacts(run)
    base.mkdir(parents=True, exist_ok=True)
    result, _ = protocol.step(run)
    protocol.done(run, "design", out={"design": "design.md"},
                  usage={"input_tokens": 1, "output_tokens": 1},
                  status="abandoned")
    # design was abandoned, so its node re-queues; force the gate instead.
    raw = yaml.safe_load(open(run, encoding="utf-8"))
    for node in raw["workflow_plan"]["main"]["nodes"]:
        if node["id"] == "design":
            node["status"] = "completed"
    open(run, "w", encoding="utf-8").write(yaml.safe_dump(raw, sort_keys=False))

    result, _ = protocol.step(run)
    assert result["kind"] == KIND_GATE
    assert result["payload"]["preview"]["show"]["design"]["exists"] is False


# ---------------------------------------------------------------------------
# 3. approve / cancel
# ---------------------------------------------------------------------------
def test_approve_unblocks_the_run_and_dispatches_the_guarded_step(run):
    _finish_design(run)
    blocked, _ = protocol.step(run)
    token = blocked["payload"]["token"]

    result, code = protocol.approve(run, token, edits={"note": "ship it"})
    assert code == 0
    assert result["status"] == "ok"
    assert result["gate_id"] == "design-signoff"
    assert result["token_name"] == "impl_token"
    assert result["edits"] == {"note": "ship it"}
    # The next step the engine hands back is the one the gate was guarding.
    assert result["next"]["step_id"] == "implement"
    assert result["next"]["status"] == "ready"

    raw = yaml.safe_load(open(run, encoding="utf-8"))
    assert raw["status"] == "active"
    assert raw["gates"][0]["status"] == "approved"
    # Edits are recorded verbatim, in the gate record and in history.
    assert raw["gates"][0]["edits"] == {"note": "ship it"}
    approvals = [e for e in raw["step_history"] if e["step_id"] == "design-signoff"]
    assert approvals and approvals[-1]["outputs"]["edits"] == {"note": "ship it"}


def test_approve_with_a_wrong_token_is_refused(run):
    _finish_design(run)
    protocol.step(run)

    with pytest.raises(protocol.ProtocolError, match="unknown or expired"):
        protocol.approve(run, "not-the-token")

    raw = yaml.safe_load(open(run, encoding="utf-8"))
    assert raw["status"] == "blocked"
    assert raw["gates"][0]["status"] == "pending"


def test_a_token_cannot_be_approved_twice(run):
    _finish_design(run)
    token = protocol.step(run)[0]["payload"]["token"]
    protocol.approve(run, token)

    with pytest.raises(protocol.ProtocolError, match="approved"):
        protocol.approve(run, token)


def test_cancel_closes_the_run_and_its_pending_gates(run):
    _finish_design(run)
    protocol.step(run)

    result, code = protocol.cancel(run)
    assert code == 0
    assert result["run_status"] == "cancelled"
    assert result["cancelled_gates"] == ["design-signoff"]

    raw = yaml.safe_load(open(run, encoding="utf-8"))
    assert raw["gates"][0]["status"] == "cancelled"


# ---------------------------------------------------------------------------
# 4. requires: an unapproved token is needs_you, and status reports gates
# ---------------------------------------------------------------------------
def test_a_step_requiring_an_unapproved_token_is_not_dispatched(run):
    """Skipping the gate node must not let the guarded step through."""
    raw = yaml.safe_load(open(run, encoding="utf-8"))
    for node in raw["workflow_plan"]["main"]["nodes"]:
        if node["id"] in ("design", "design-signoff"):
            node["status"] = "completed"
    open(run, "w", encoding="utf-8").write(yaml.safe_dump(raw, sort_keys=False))

    result, code = protocol.step(run)
    assert code == 0
    assert result["status"] == "needs_you"
    assert result["step_id"] == "implement"
    assert result["requires"] == "impl_token"
    assert "impl_token" in result["detail"]


def test_status_reports_gate_token_and_gate_records(run):
    # `done` returns the next step, so finishing design already parks the run
    # at the gate and mints its token.
    _finish_design(run)
    token = protocol.step(run)[0]["payload"]["token"]

    parked, _ = protocol.status(run)
    assert parked["gate_token"] is None, "a pending gate is not an approval"
    assert parked["gates"][0]["status"] == "pending"
    kinds = {n["id"]: n["kind"] for n in parked["nodes"]}
    assert kinds["design-signoff"] == KIND_GATE

    protocol.approve(run, token)
    after, _ = protocol.status(run)
    assert after["gate_token"] == token
    assert after["gates"][0]["status"] == "approved"


# ---------------------------------------------------------------------------
# 5. validate-workflow: gates before writes
# ---------------------------------------------------------------------------
def _validate(pack_root, monkeypatch, capsys):
    from orchestrator_next.validate_workflow import validate_workflow

    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack_root))
    try:
        validate_workflow("feature", str(pack_root))
    except SystemExit as exc:
        return int(exc.code or 1), capsys.readouterr().err
    return 0, capsys.readouterr().err


def test_validate_accepts_a_write_step_behind_a_gate(pack, monkeypatch, capsys):
    code, err = _validate(pack, monkeypatch, capsys)
    assert code == 0, err


def test_validate_rejects_a_write_step_with_no_gate(pack, monkeypatch, capsys):
    recipe = pack / "workflows" / "feature.yaml"
    recipe.write_text(yaml.safe_dump({
        "name": "feature", "steps": ["design", "implement"],
    }, sort_keys=False), encoding="utf-8")

    code, err = _validate(pack, monkeypatch, capsys)
    assert code == 1
    assert "gates before writes" in err
    assert "write:git" in err


def test_validate_rejects_a_requires_naming_no_upstream_gate(pack, monkeypatch,
                                                             capsys):
    recipe = pack / "workflows" / "feature.yaml"
    recipe.write_text(yaml.safe_dump({
        "name": "feature",
        "steps": ["design", {"id": "implement", "requires": "ghost_token"}],
    }, sort_keys=False), encoding="utf-8")

    code, err = _validate(pack, monkeypatch, capsys)
    assert code == 1
    assert "ghost_token" in err


def test_validate_rejects_a_gate_with_no_approve_as(pack, monkeypatch, capsys):
    recipe = pack / "workflows" / "feature.yaml"
    recipe.write_text(yaml.safe_dump({
        "name": "feature",
        "steps": ["design", {"gate": "g", "show": ["design"]},
                  {"id": "implement", "requires": "impl_token"}],
    }, sort_keys=False), encoding="utf-8")

    code, err = _validate(pack, monkeypatch, capsys)
    assert code == 1
    assert "approve_as" in err


def test_v2_verbs_report_blocked_in_json_and_exit_zero(run, capsys):
    """Protocol v2 replaces exit-2-means-blocked with a JSON status field."""
    _finish_design(run)

    rc = protocol.main("step", [run, "--json"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["kind"] == KIND_GATE
    token = result["payload"]["token"]

    assert protocol.main("approve", [run, token, "--edits", '{"ok": true}']) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["status"] == "ok"
    assert approved["edits"] == {"ok": True}

    assert protocol.main("cancel", [run]) == 0
    assert json.loads(capsys.readouterr().out)["run_status"] == "cancelled"


def test_approve_with_a_bad_token_exits_three(run, capsys):
    _finish_design(run)
    protocol.main("step", [run, "--json"])
    capsys.readouterr()

    assert protocol.main("approve", [run, "bogus"]) == protocol.EXIT_ERROR
    err = json.loads(capsys.readouterr().out)
    assert err["status"] == "error"
    assert "unknown or expired" in err["error"]


def test_cli_routes_approve_and_cancel(run, pack, tmp_path, monkeypatch):
    """`orchestrator approve|cancel` reach the protocol module, not the usage text."""
    _finish_design(run)
    token = protocol.step(run)[0]["payload"]["token"]

    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    proc = subprocess.run(
        [sys.executable, "-m", "orchestrator_next", "approve", run, token],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["gate_id"] == "design-signoff"


# ---------------------------------------------------------------------------
# 7. headless
# ---------------------------------------------------------------------------
def test_headless_parks_at_a_gate_and_prints_the_preview(run, capsys):
    from orchestrator_next import headless

    _finish_design(run)
    rc = headless.drive(run, client=object())
    assert rc == 0

    out = capsys.readouterr()
    printed = json.loads(out.out)
    assert printed["status"] == "blocked"
    assert printed["kind"] == "gate"
    assert "orchestrator approve" in out.err


def test_headless_auto_approve_walks_through_the_gate(run, monkeypatch, capsys):
    from orchestrator_next import headless

    _finish_design(run)

    # The step after the gate would call the model; stub the judgment runner so
    # the test is about the gate, not about the API.
    base = _artifacts(run)

    def _fake_judgment(payload, client=None):
        (base / "impl.md").write_text("done\n", encoding="utf-8")
        return {"out": {"impl": "impl.md"}, "text": "{}",
                "usage": {"input_tokens": 5, "output_tokens": 5}}

    monkeypatch.setattr(headless, "run_judgment", _fake_judgment)

    rc = headless.drive(run, client=object(), auto_approve=True)
    assert rc == 0

    raw = yaml.safe_load(open(run, encoding="utf-8"))
    assert raw["gates"][0]["status"] == "approved"
    assert raw["gates"][0]["edits"] == {"auto_approved": True}
    done_ids = {e["step_id"] for e in raw["step_history"]
                if e["status"] == "completed"}
    assert "implement" in done_ids


# ---------------------------------------------------------------------------
# 8. write:workspace is exempt from the gate requirement
# ---------------------------------------------------------------------------
def _set_side_effects(pack, step_id, side_effects):
    contract = pack / "steps" / step_id / "contract.yaml"
    doc = yaml.safe_load(contract.read_text())
    doc["side_effects"] = side_effects
    contract.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_validate_exempts_write_workspace_from_needing_a_gate(pack, monkeypatch,
                                                              capsys):
    """Provisioning the run's own workspace cannot sit behind a gate.

    A worktree-create step writes git before any gate could exist — the gate's
    own artifacts live in the directory it makes — so requiring one would make
    every recipe unstartable.
    """
    recipe = pack / "workflows" / "feature.yaml"
    recipe.write_text(yaml.safe_dump({
        "name": "feature", "steps": ["design", "implement"],
    }, sort_keys=False), encoding="utf-8")
    _set_side_effects(pack, "implement", ["write:workspace"])

    code, err = _validate(pack, monkeypatch, capsys)
    assert code == 0, err
    assert "gates before writes" not in err


def test_validate_still_requires_a_gate_beside_a_workspace_write(pack,
                                                                 monkeypatch,
                                                                 capsys):
    """The exemption covers only write:workspace, not whatever rides with it."""
    recipe = pack / "workflows" / "feature.yaml"
    recipe.write_text(yaml.safe_dump({
        "name": "feature", "steps": ["design", "implement"],
    }, sort_keys=False), encoding="utf-8")
    _set_side_effects(pack, "implement", ["write:workspace", "write:ticket"])

    code, err = _validate(pack, monkeypatch, capsys)
    assert code == 1
    assert "write:ticket" in err
    assert "write:workspace" not in err


# ---------------------------------------------------------------------------
# 9. validate-workflow --json
# ---------------------------------------------------------------------------
def _validate_json(pack_root, monkeypatch, capsys):
    from orchestrator_next.validate_workflow import main

    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack_root))
    code = main(["feature", "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_validate_json_reports_ok_with_no_errors(pack, monkeypatch, capsys):
    code, doc = _validate_json(pack, monkeypatch, capsys)
    assert code == 0
    assert doc == {"ok": True, "errors": [], "warnings": []}


def test_validate_json_lists_each_error(pack, monkeypatch, capsys):
    recipe = pack / "workflows" / "feature.yaml"
    recipe.write_text(yaml.safe_dump({
        "name": "feature",
        "steps": ["design", {"id": "implement", "requires": "ghost_token"}],
    }, sort_keys=False), encoding="utf-8")

    code, doc = _validate_json(pack, monkeypatch, capsys)
    assert code == 1
    assert doc["ok"] is False
    assert any("ghost_token" in e for e in doc["errors"])
    # The `ERROR:` header is dropped in favor of its bullets.
    assert "gates before writes:" not in doc["errors"]


def test_validate_json_separates_warnings_from_errors(pack, monkeypatch, capsys):
    """A wiring WARN is reported as a warning, not an error: the recipe is
    still valid, so `ok` stays true and exit stays 0.

    An exec step declares no in:/out: at all, so the engine cannot see what it
    produces. A later step consuming its artifact gets "no declared producer"
    — a warning, because the producer may well write it.
    """
    setup = pack / "steps" / "setup"
    setup.mkdir(parents=True)
    (setup / "contract.yaml").write_text(yaml.safe_dump({
        "id": "setup", "version": 1, "run": "script.sh",
    }, sort_keys=False), encoding="utf-8")
    script = setup / "script.sh"
    script.write_text("#!/usr/bin/env bash\necho '{}'\n", encoding="utf-8")
    script.chmod(0o755)

    recipe = pack / "workflows" / "feature.yaml"
    doc_in = yaml.safe_load(recipe.read_text())
    doc_in["steps"].insert(0, "setup")
    recipe.write_text(yaml.safe_dump(doc_in, sort_keys=False), encoding="utf-8")

    contract = pack / "steps" / "design" / "contract.yaml"
    doc_c = yaml.safe_load(contract.read_text())
    doc_c.setdefault("in", {})["seed"] = {"artifact": "seed.md"}
    contract.write_text(yaml.safe_dump(doc_c, sort_keys=False), encoding="utf-8")

    code, doc = _validate_json(pack, monkeypatch, capsys)
    assert code == 0
    assert doc["ok"] is True
    assert doc["errors"] == []
    assert any("no declared producer" in w for w in doc["warnings"])
