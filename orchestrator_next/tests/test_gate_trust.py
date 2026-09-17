"""A gate must not mint a token over work nothing stands behind.

Live run 01a0af00: design abandoned without writing design.md, design-review
ran anyway, returned verdict needs_work, and — because its own contract
declares `out: design` ("rewrites design.md") — wrote its findings *into*
design.md. The signoff gate then previewed `design: exists=true` and minted a
token. Approving would have pushed an empty design into implement.

The preview now carries provenance per `show:` artifact: which node produced
it, that node's status and attempts, and the most recent reviewer verdict
recorded against it. The gate refuses to mint when a producer is not
completed, or when the last review of it was negative.
"""
from __future__ import annotations

import pytest
import yaml

from orchestrator_next import protocol
from orchestrator_next.parser import KIND_GATE
from orchestrator_next.tests.test_gates import _artifacts

# `gates_pack` / `gates_run` are defined in test_gates.py; loading that module
# as a plugin registers them here without importing the names (which would
# shadow this module's own `pack` fixture).
pytest_plugins = ["orchestrator_next.tests.test_gates"]


@pytest.fixture
def pack(gates_pack):
    """The gates pack, with `design` declaring a reviewable verdict.

    The real design-review contract declares
    `verdict: {type: enum, values: [pass, needs_work], fail_on: [needs_work]}`;
    the gate reads the same `fail_on:` the router does, so the fixture has to
    declare it for the trust check to have anything to key on.
    """
    contract = gates_pack / "steps" / "design" / "contract.yaml"
    data = yaml.safe_load(contract.read_text(encoding="utf-8"))
    data["out"]["verdict"] = {
        "type": "enum", "values": ["pass", "needs_work"],
        "fail_on": ["needs_work"], "optional": True,
    }
    contract.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return gates_pack


@pytest.fixture
def gate_run(gates_run):
    return gates_run


def _run_design(run_path, *, design_status="completed", write=True,
                verdict=None):
    """Dispatch `design` and write its artifact, without recording it yet."""
    result, _ = protocol.step(run_path)
    assert result["step_id"] == "design", result
    base = _artifacts(run_path)
    base.mkdir(parents=True, exist_ok=True)
    if write:
        (base / "design.md").write_text("the design\n" * 60, encoding="utf-8")
    return base


def _record_design(run_path, base, *, design_status="completed", write=True,
                   verdict=None):
    out = {"design": "design.md"} if write else {}
    if verdict is not None:
        out["verdict"] = verdict
    protocol.done(run_path, "design", out=out,
                  usage={"input_tokens": 10, "output_tokens": 5},
                  status=design_status)


def _drive_to_gate(run_path, *, design_status="completed", verdict=None,
                   write=True):
    """Run `design` and record it, leaving the run parked at the gate."""
    base = _run_design(run_path, write=write)
    _record_design(run_path, base, design_status=design_status, write=write,
                   verdict=verdict)
    return base


def _add_review(run_path, verdict):
    """Append a design-review entry that rewrote design.md with `verdict`."""
    raw = yaml.safe_load(open(run_path, encoding="utf-8"))
    raw["step_history"].append({
        "step_id": "design-review", "phase": "main", "status": "completed",
        "attempt": 1, "outputs": {"verdict": verdict},
        "artifacts": [{"name": "design", "path": "design.md",
                       "sha256": "deadbeef"}],
    })
    open(run_path, "w", encoding="utf-8").write(
        yaml.safe_dump(raw, sort_keys=False))


def _force_gate(run_path):
    """Mark design completed by hand so the gate is what we are testing."""
    raw = yaml.safe_load(open(run_path, encoding="utf-8"))
    for node in raw["workflow_plan"]["main"]["nodes"]:
        if node["id"] == "design":
            node["status"] = "completed"
    raw["status"] = "active"
    open(run_path, "w", encoding="utf-8").write(yaml.safe_dump(raw, sort_keys=False))


class TestPreviewCarriesProvenance:
    def test_preview_names_the_producing_node_and_its_status(self, gate_run):
        _drive_to_gate(gate_run)
        result, _ = protocol.step(gate_run)
        assert result["kind"] == KIND_GATE
        entry = result["payload"]["preview"]["show"]["design"]
        assert entry["produced_by"] == "design"
        assert entry["producer_status"] == "completed"
        assert entry["attempts"] == 1

    def test_preview_reports_written_by_when_a_later_node_rewrote_it(self, gate_run):
        """The last node to produce the artifact, not the first."""
        base = _run_design(gate_run)
        # A reviewer with `out: design` rewrites the file it reviews. It has to
        # land before the gate is first reached, or the gate already minted.
        _record_design(gate_run, base)
        (base / "design.md").write_text("## Review\nneeds_work\n", encoding="utf-8")
        _add_review(gate_run, "needs_work")

        result, _ = protocol.step(gate_run)
        entry = result["payload"]["preview"]["show"]["design"]
        assert entry["written_by"] == "design-review"
        assert entry["last_verdict"] == "needs_work"


class TestGateRefusesUntrustedWork:
    def test_gate_refuses_when_the_producer_abandoned(self, gate_run):
        _drive_to_gate(gate_run, design_status="abandoned", write=False)
        _force_gate(gate_run)
        result, _ = protocol.step(gate_run)
        assert result["status"] == "needs_you", (
            "a gate must not mint a token when the artifact's producer gave up"
        )
        assert "design" in str(result.get("detail") or "")
        raw = yaml.safe_load(open(gate_run, encoding="utf-8"))
        assert not raw.get("gates"), "no token may be minted"

    def test_gate_refuses_when_the_last_review_said_needs_work(self, gate_run):
        base = _run_design(gate_run)
        _record_design(gate_run, base)
        (base / "design.md").write_text("## Review\nneeds_work\n", encoding="utf-8")
        _add_review(gate_run, "needs_work")

        result, _ = protocol.step(gate_run)
        assert result["status"] == "needs_you"
        assert "needs_work" in str(result.get("detail") or "")
        # In this pack the gate sits directly after design, so `done` already
        # reached it and minted over then-clean work. A rejection arriving
        # afterwards must withdraw that token, not leave it approvable.
        raw = yaml.safe_load(open(gate_run, encoding="utf-8"))
        assert not [g for g in raw.get("gates") or []
                    if g.get("status") == "pending"], (
            "a token over rejected work must not stay pending"
        )

    def test_gate_still_mints_over_clean_work(self, gate_run):
        _drive_to_gate(gate_run)
        result, _ = protocol.step(gate_run)
        assert result["kind"] == KIND_GATE
        assert result["payload"]["token"]

    def test_a_later_passing_review_clears_an_earlier_rejection(self, gate_run):
        base = _run_design(gate_run)
        _record_design(gate_run, base)
        _add_review(gate_run, "needs_work")
        _add_review(gate_run, "pass")

        result, _ = protocol.step(gate_run)
        assert result["kind"] == KIND_GATE, (
            "the most recent verdict is what counts — a re-review that passed "
            "must let the gate mint"
        )


class TestProvenanceSurvivesARealRecord:
    """Regression: the history entry record() writes must carry `artifacts`.

    `gates.provenance()` derives `written_by` and `last_verdict` from
    `step_history[].artifacts`. record() used to hash a step's artifacts onto
    its *plan node* only, so in a real run those two fields were always empty
    and `untrusted()` could never see a failing verdict — the gate minted over
    rejected work. The fixtures above hand-wrote the key, so they passed while
    live runs did not. This one goes through record().
    """

    def test_recorded_verdict_reaches_the_gate_and_blocks_it(self, gate_run):
        _drive_to_gate(gate_run, verdict="needs_work")
        raw = yaml.safe_load(open(gate_run, encoding="utf-8"))
        entry = [h for h in raw["step_history"] if h["step_id"] == "design"][-1]
        assert entry.get("artifacts"), (
            "record() must mirror artifacts onto the history entry"
        )
        result, _ = protocol.step(gate_run)
        assert result["kind"] != KIND_GATE, (
            "gate minted over a design the reviewer rejected"
        )
