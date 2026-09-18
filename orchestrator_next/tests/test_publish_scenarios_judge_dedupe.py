"""Semantic near-dup dedupe in publish_scenarios, on top of the sha256 dedupe.

judge None -> hash dedupe only (today's behavior). judge available -> a
candidate row whose Noul similarity to an existing row is >= 0.85 is
dropped and reported to stderr as a near-duplicate.
"""
from __future__ import annotations

from orchestrator_next import judge as judge_mod
from orchestrator_next import publish_scenarios as ps
from orchestrator_next import state_store as ss


def _row(text):
    return {"id": text, "input": text, "expected": "pass"}


class _FakeResult:
    def __init__(self, noul):
        self.noul = noul


class _FakeResponse:
    def __init__(self, nouls):
        self.nouls = nouls


def _learn_rows(rows):
    return [{"step_id": "review", "proposed_row": r} for r in rows]


def test_near_duplicate_dropped_when_judge_flags_high_similarity(tmp_path, monkeypatch):
    pack_dir = tmp_path / "pack"
    target = pack_dir / "steps" / "review" / "scenarios" / "train.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text('{"id": "existing-1", "input": "old scenario", "expected": "pass"}\n')

    def _ask(*, state, questions):
        assert set(questions.keys()) == {"0"}
        return _FakeResponse({"0": _FakeResult(0.95)})

    monkeypatch.setattr(judge_mod, "ask", _ask)
    monkeypatch.setattr(judge_mod, "noul", lambda **kw: object())
    monkeypatch.setattr(ss, "default_state_url", lambda repo_root="": "fake://handle")
    monkeypatch.setattr(ss, "list_learn_rows", lambda url, accepted=None: _learn_rows([_row("new scenario")]))

    written = ps.publish(pack_dir)
    assert written == {}
    assert target.read_text().count("\n") == 1  # nothing appended


def test_judge_none_falls_back_to_hash_dedupe_only(tmp_path, monkeypatch):
    pack_dir = tmp_path / "pack"
    target = pack_dir / "steps" / "review" / "scenarios" / "train.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text('{"id": "existing-1", "input": "old scenario", "expected": "pass"}\n')

    monkeypatch.setattr(judge_mod, "ask", lambda **kw: None)
    monkeypatch.setattr(ss, "default_state_url", lambda repo_root="": "fake://handle")
    monkeypatch.setattr(ss, "list_learn_rows", lambda url, accepted=None: _learn_rows([_row("brand new scenario")]))

    written = ps.publish(pack_dir)
    assert written == {"review": 1}
    assert "brand new scenario" in target.read_text()


def test_judge_low_similarity_row_is_appended(tmp_path, monkeypatch):
    pack_dir = tmp_path / "pack"
    target = pack_dir / "steps" / "review" / "scenarios" / "train.jsonl"
    target.parent.mkdir(parents=True)
    target.write_text('{"id": "existing-1", "input": "old scenario", "expected": "pass"}\n')

    def _ask(*, state, questions):
        return _FakeResponse({"0": _FakeResult(0.2)})

    monkeypatch.setattr(judge_mod, "ask", _ask)
    monkeypatch.setattr(judge_mod, "noul", lambda **kw: object())
    monkeypatch.setattr(ss, "default_state_url", lambda repo_root="": "fake://handle")
    monkeypatch.setattr(ss, "list_learn_rows", lambda url, accepted=None: _learn_rows([_row("different scenario")]))

    written = ps.publish(pack_dir)
    assert written == {"review": 1}
    assert "different scenario" in target.read_text()
