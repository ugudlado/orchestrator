"""Contract-declared PII never reaches the run doc (plan Phase 3.3).

A step may declare ``pii: [customer_email]`` naming keys of its own
``in:``/``out:``. Those values are replaced with ``[redacted]`` in the
step_history entry the engine saves. Artifacts are untouched: the DB only ever
held a name, a path and a hash, and the path is what lets someone with
filesystem access go read the real file.
"""
from __future__ import annotations

import hashlib

import pytest
import yaml

from orchestrator_next.record import record
from orchestrator_next.redact import REDACTED


@pytest.fixture
def pack(tmp_path, monkeypatch):
    """A one-step pack whose contract declares an email out: as PII."""
    root = tmp_path / "pack"
    steps = root / "steps" / "collect"
    steps.mkdir(parents=True)
    (steps / "contract.yaml").write_text(yaml.safe_dump({
        "id": "collect", "version": 1, "kind": "judgment",
        "prompt": "SKILL.md",
        "pii": ["customer_email"],
        "out": {
            "customer_email": {"type": "string"},
            "summary": {"artifact": "summary.md"},
        },
    }, sort_keys=False), encoding="utf-8")
    (steps / "SKILL.md").write_text("Collect it.\n", encoding="utf-8")
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE",
                       str(root / "steps"))
    monkeypatch.setenv("ORCHESTRATOR_SKIP_USAGE_CHECK", "1")
    return root


@pytest.fixture
def state(tmp_path, pack):
    """A run parked on the `collect` node, with its artifact already written."""
    repo = tmp_path / "repo"
    # No `schema:` here on purpose: a named recipe would pull in that pack's
    # artifacts_root override, and this test is about redaction, not layout.
    artifacts = repo / ".orchestrator" / "runs" / "pii" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "summary.md").write_text("no secrets here\n", encoding="utf-8")

    path = tmp_path / "pii_state.yaml"
    path.write_text(yaml.safe_dump({
        "change_id": "pii", "slug": "pii",
        "status": "active", "phase": "main",
        "repo_root": str(repo), "worktree_path": str(repo),
        "workflow_plan": {"main": {"nodes": [
            {"id": "collect", "depends_on": [], "status": "in_progress"},
        ], "filtered": []}},
        "step_history": [],
    }, sort_keys=False), encoding="utf-8")
    return path


def _record(state, **overrides):
    payload = {
        "step_id": "collect",
        "phase": "main",
        "status": "completed",
        "agent": "standard",
        "outputs": {
            "customer_email": "ada@example.com",
            "summary": "summary.md",
            "reason": "collected the record",
        },
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    payload.update(overrides)
    result, code = record(str(state), payload)
    assert code == 0, result
    return yaml.safe_load(state.read_text(encoding="utf-8"))


def test_declared_pii_is_redacted_from_the_history_entry(state):
    raw = _record(state)
    entry = raw["step_history"][-1]

    assert entry["outputs"]["customer_email"] == REDACTED
    # Only the declared name is touched; the rest of the entry is intact.
    assert entry["outputs"]["reason"] == "collected the record"
    assert entry["step_id"] == "collect"
    assert entry["status"] == "completed"
    assert entry["usage"]["input_tokens"] == 10

    # The raw value is nowhere in the saved document.
    assert "ada@example.com" not in yaml.safe_dump(raw)


def test_redaction_reaches_the_nested_evidence_copy(state):
    """`evidence.outputs` is a second copy of the same outputs — redact it too."""
    raw = _record(state)
    entry = raw["step_history"][-1]

    assert entry["evidence"]["outputs"]["customer_email"] == REDACTED


def test_artifacts_survive_redaction_untouched(state, tmp_path):
    """A path and hash are how a human finds the file; they are not the PII."""
    raw = _record(state)
    node = raw["workflow_plan"]["main"]["nodes"][0]

    recorded = {a["name"]: a for a in node["artifacts"]}
    assert recorded["summary"]["path"] == "summary.md"
    assert recorded["summary"]["sha256"] == hashlib.sha256(
        b"no secrets here\n").hexdigest()


def test_a_contract_without_pii_records_the_value_verbatim(state, pack,
                                                           monkeypatch):
    """Redaction is opt-in: no `pii:` key means nothing is rewritten."""
    contract = pack / "steps" / "collect" / "contract.yaml"
    doc = yaml.safe_load(contract.read_text())
    del doc["pii"]
    contract.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    entry = _record(state)["step_history"][-1]
    assert entry["outputs"]["customer_email"] == "ada@example.com"


def test_parser_reads_pii_off_every_contract_kind(tmp_path, monkeypatch):
    """All three contract dataclasses carry `pii:`, defaulting to empty."""
    from orchestrator_next.parser import load_contract_for_step

    steps = tmp_path / "steps"
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))

    (steps / "judge").mkdir(parents=True)
    (steps / "judge" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "judge", "version": 1, "prompt": "SKILL.md", "pii": ["secret"],
    }, sort_keys=False), encoding="utf-8")
    (steps / "judge" / "SKILL.md").write_text("x\n", encoding="utf-8")

    (steps / "exec").mkdir(parents=True)
    (steps / "exec" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "exec", "version": 1, "run": "script.sh", "pii": ["token"],
    }, sort_keys=False), encoding="utf-8")
    (steps / "exec" / "script.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")

    (steps / "gate").mkdir(parents=True)
    (steps / "gate" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "gate", "version": 1, "kind": "gate", "approve_as": "t",
        "pii": ["note"],
    }, sort_keys=False), encoding="utf-8")

    (steps / "plain").mkdir(parents=True)
    (steps / "plain" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "plain", "version": 1, "prompt": "SKILL.md",
    }, sort_keys=False), encoding="utf-8")
    (steps / "plain" / "SKILL.md").write_text("x\n", encoding="utf-8")

    assert load_contract_for_step("judge").pii == ["secret"]
    assert load_contract_for_step("exec").pii == ["token"]
    assert load_contract_for_step("gate").pii == ["note"]
    assert load_contract_for_step("plain").pii == []


def test_a_non_list_pii_key_is_rejected(tmp_path, monkeypatch):
    from orchestrator_next.parser import ContractError, load_contract_for_step

    steps = tmp_path / "steps"
    (steps / "bad").mkdir(parents=True)
    (steps / "bad" / "contract.yaml").write_text(yaml.safe_dump({
        "id": "bad", "version": 1, "prompt": "SKILL.md", "pii": "secret",
    }, sort_keys=False), encoding="utf-8")
    (steps / "bad" / "SKILL.md").write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))

    with pytest.raises(ContractError, match="pii"):
        load_contract_for_step("bad")
