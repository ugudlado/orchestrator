"""Phase 3.3 — tenant_id, PII redaction, learn results, publish-scenarios.

The plan deliberately keeps the one-JSON-doc-plus-derived-index design, so
these tests pin the *additions* only: a tenant column that migrates in place,
a pure redaction helper record.py can call, and the learn → train.jsonl path.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from orchestrator_next import redact
from orchestrator_next import state_store as ss


def _url(tmp_path: Path, run: str = "") -> str:
    base = "sqlite://" + str(tmp_path / "state.db")
    return f"{base}#{run}" if run else base


# ---------------------------------------------------------------------------
# tenant_id
# ---------------------------------------------------------------------------
def test_new_db_has_tenant_columns(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_TENANT", raising=False)
    store, handle = ss.open_store(_url(tmp_path, "r1"))
    store.create(handle, {"slug": "s", "step_history": [{"step_id": "a", "usage": {}}]})
    conn = sqlite3.connect(tmp_path / "state.db")
    assert conn.execute("SELECT tenant_id FROM runs").fetchone() == ("default",)
    assert conn.execute("SELECT tenant_id FROM step_history").fetchone() == ("default",)


def test_orchestrator_tenant_env_sets_the_value(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_TENANT", "acme")
    store, handle = ss.open_store(_url(tmp_path, "r1"))
    store.create(handle, {"slug": "s", "step_history": [{"step_id": "a", "usage": {}}]})
    conn = sqlite3.connect(tmp_path / "state.db")
    assert conn.execute("SELECT tenant_id FROM runs").fetchone() == ("acme",)
    assert conn.execute("SELECT tenant_id FROM step_history").fetchone() == ("acme",)


def test_save_preserves_the_run_tenant_on_history_rebuild(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_TENANT", "acme")
    url = _url(tmp_path, "r1")
    store, handle = ss.open_store(url)
    token = store.create(handle, {"slug": "s", "step_history": []})
    # A later save must not silently re-tenant the run to whatever env says now.
    monkeypatch.setenv("ORCHESTRATOR_TENANT", "other")
    store.save(handle, {"slug": "s", "step_history": [{"step_id": "a", "usage": {}}]}, token)
    conn = sqlite3.connect(tmp_path / "state.db")
    assert conn.execute("SELECT tenant_id FROM step_history").fetchone() == ("acme",)


def test_existing_db_without_tenant_column_is_migrated_in_place(tmp_path):
    """A db created before the column existed upgrades on the next connect."""
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, slug TEXT, change_id TEXT, ticket_id TEXT,
            schema_name TEXT, config_pack TEXT, status TEXT, repo_root TEXT,
            worktree_path TEXT, branch TEXT, doc TEXT NOT NULL,
            version INTEGER NOT NULL, created_at TEXT, updated_at TEXT)
    """)
    conn.execute("""
        CREATE TABLE step_history (
            run_id TEXT NOT NULL, seq INTEGER NOT NULL, step_id TEXT, phase TEXT,
            status TEXT, agent TEXT, attempt INTEGER, started_at TEXT, ended_at TEXT,
            model TEXT, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_input_tokens INTEGER, cache_creation_input_tokens INTEGER,
            cost_usd REAL, duration_ms INTEGER, PRIMARY KEY (run_id, seq))
    """)
    conn.execute(
        "INSERT INTO runs VALUES ('old','s','','','f','','running','','','',?,1,'','')",
        (json.dumps({"slug": "s"}),),
    )
    conn.commit()
    conn.close()

    store, handle = ss.open_store(_url(tmp_path, "new"))
    store.create(handle, {"slug": "s2", "step_history": [{"step_id": "a", "usage": {}}]})

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    assert "tenant_id" in cols
    # The pre-existing row gets the column default, not a NULL.
    assert conn.execute("SELECT tenant_id FROM runs WHERE run_id='old'").fetchone() == (
        "default",
    )
    # And re-running _ensure_schema again is a no-op, not a duplicate-column error.
    store.load(handle)


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------
def test_redact_replaces_named_keys_only():
    entry = {
        "step_id": "design",
        "out": {"summary": "fine", "customer_email": "a@b.com"},
    }
    out = redact.redact_entry(entry, ["customer_email"])
    assert out["out"]["customer_email"] == redact.REDACTED
    assert out["out"]["summary"] == "fine"
    assert out["step_id"] == "design"


def test_redact_reaches_a_nested_key():
    entry = {"out": {"payload": {"api_key": "sk-123", "region": "eu"}}}
    out = redact.redact_entry(entry, ["api_key"])
    assert out["out"]["payload"] == {"api_key": redact.REDACTED, "region": "eu"}


def test_redact_leaves_artifact_paths_alone():
    """Paths stay — artifact contents are never in the DB to begin with."""
    entry = {"out": {"design": {"artifact": "design.md", "path": "/abs/design.md",
                                "sha256": "deadbeef"}}}
    out = redact.redact_entry(entry, ["design"])
    assert out["out"]["design"]["path"] == "/abs/design.md"
    assert out["out"]["design"]["sha256"] == "deadbeef"


def test_redact_covers_edits_and_is_a_pure_copy():
    entry = {"edits": {"secret": "s"}, "out": {"secret": "s"}}
    out = redact.redact_entry(entry, ["secret"])
    assert out["edits"]["secret"] == redact.REDACTED
    assert out["out"]["secret"] == redact.REDACTED
    assert entry["out"]["secret"] == "s"  # original untouched


def test_redact_without_pii_keys_is_identity():
    entry = {"out": {"a": 1}}
    assert redact.redact_entry(entry, []) == entry


# ---------------------------------------------------------------------------
# learn_results
# ---------------------------------------------------------------------------
def test_learn_rows_roundtrip_and_accepted_filter(tmp_path):
    url = _url(tmp_path)
    ss.add_learn_row(url, "run-1", "design", {"rule": "cite the ticket"}, accepted=True)
    ss.add_learn_row(url, "run-1", "design", {"rule": "rejected idea"}, accepted=False)
    ss.add_learn_row(url, "run-2", "review", {"rule": "pending"}, accepted=None)

    every = ss.list_learn_rows(url)
    assert len(every) == 3
    assert {r["accepted"] for r in every} == {True, False, None}

    accepted = ss.list_learn_rows(url, accepted=True)
    assert [r["proposed_row"] for r in accepted] == [{"rule": "cite the ticket"}]
    assert accepted[0]["run_id"] == "run-1" and accepted[0]["step_id"] == "design"

    assert [r["proposed_row"]["rule"] for r in ss.list_learn_rows(url, accepted=False)] == [
        "rejected idea"
    ]


# ---------------------------------------------------------------------------
# pack publish-scenarios
# ---------------------------------------------------------------------------
def _pack_with_step(tmp_path: Path) -> Path:
    pack = tmp_path / ".orchestrator" / "wf"
    (pack / "steps" / "design").mkdir(parents=True)
    (pack / "workflows").mkdir(parents=True)
    return pack


def test_publish_scenarios_appends_and_dedupes(tmp_path):
    from orchestrator_next.publish_scenarios import publish

    url = _url(tmp_path)
    pack = _pack_with_step(tmp_path)
    ss.add_learn_row(url, "r1", "design", {"rule": "a"}, accepted=True)
    ss.add_learn_row(url, "r1", "design", {"rule": "b"}, accepted=True)
    ss.add_learn_row(url, "r1", "design", {"rule": "never"}, accepted=False)

    assert publish(pack, handle=url) == {"design": 2}
    train = pack / "steps" / "design" / "scenarios" / "train.jsonl"
    rules = [json.loads(line)["rule"] for line in train.read_text().splitlines()]
    assert rules == ["a", "b"]

    # Re-running is idempotent: the same rows hash to lines already present.
    assert publish(pack, handle=url) == {}
    assert len(train.read_text().splitlines()) == 2


def test_publish_scenarios_step_filter(tmp_path):
    from orchestrator_next.publish_scenarios import publish

    url = _url(tmp_path)
    pack = _pack_with_step(tmp_path)
    (pack / "steps" / "review").mkdir()
    ss.add_learn_row(url, "r1", "design", {"rule": "d"}, accepted=True)
    ss.add_learn_row(url, "r1", "review", {"rule": "v"}, accepted=True)

    assert publish(pack, step="review", handle=url) == {"review": 1}
    assert not (pack / "steps" / "design" / "scenarios" / "train.jsonl").exists()


def test_publish_scenarios_cli_writes_the_file(tmp_path, monkeypatch, capsys):
    from orchestrator_next.publish_scenarios import publish_scenarios_cmd

    url = _url(tmp_path)
    pack = _pack_with_step(tmp_path)
    ss.add_learn_row(url, "r1", "design", {"rule": "from cli"}, accepted=True)
    rc = publish_scenarios_cmd(
        ["wf", "--repo", str(tmp_path), "--state-url", url]
    )
    assert rc == 0
    train = pack / "steps" / "design" / "scenarios" / "train.jsonl"
    assert json.loads(train.read_text().strip())["rule"] == "from cli"
    assert "design: +1" in capsys.readouterr().out


def test_publish_scenarios_cli_rejects_unknown_pack(tmp_path, capsys):
    from orchestrator_next.publish_scenarios import publish_scenarios_cmd

    assert publish_scenarios_cmd(["nope", "--repo", str(tmp_path)]) == 1
    assert "no pack at" in capsys.readouterr().err


def test_report_reads_only_state_documents():
    """report.py must not resurrect runs/*.jsonl or metrics.md (plan 3.3)."""
    text = Path("orchestrator_next/report.py").read_text(encoding="utf-8")
    assert "results.jsonl" not in text
    assert "metrics.md" not in text


@pytest.mark.parametrize("value", ["", "   "])
def test_current_tenant_falls_back_to_default(monkeypatch, value):
    monkeypatch.setenv("ORCHESTRATOR_TENANT", value)
    assert ss.current_tenant() == "default"
