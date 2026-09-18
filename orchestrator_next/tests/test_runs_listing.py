"""`orchestrator status --json` with no run — the home screen's run list.

The pane's home screen draws one row per run, so this listing has to carry
everything such a row shows (when it started and ended, what it cost, how far
through the plan it got) without the caller falling back to one `status` call
per run. It also has to reach the ARCHIVED runs: `run_store` archives a
finished run by flipping a flag rather than deleting it, and a past-runs
section that empties itself the moment a run is archived is not a history.
"""
from __future__ import annotations

import json

import pytest
import yaml

from orchestrator_next import protocol


def _doc(**overrides: object) -> dict:
    """A state document with just enough shape for one listing row."""
    doc: dict = {
        "run_id": "r",
        "slug": "s",
        "status": "active",
        "schema": "feature",
        "step_history": [],
    }
    doc.update(overrides)
    return doc


class _Store:
    """A RunStore over an in-memory `{archived: {run_id: doc}}` map."""

    def __init__(self, live: dict[str, dict], archived: dict[str, dict] | None = None):
        self._by_flag = {False: live, True: archived or {}}

    def list_ids(self, *, archived: bool = False) -> list[str]:
        return list(self._by_flag[archived])

    def load(self, run_id: str, *, archived: bool = False) -> str | None:
        doc = self._by_flag[archived].get(run_id)
        return None if doc is None else yaml.safe_dump(doc)


def _install(monkeypatch: pytest.MonkeyPatch, store: _Store) -> None:
    monkeypatch.setattr("orchestrator_next.run_store.open_store", lambda: store)


# --- the fields a home-screen row needs ------------------------------------
def test_row_carries_timestamps_cost_and_node_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _Store({
        "r": _doc(
            status="completed",
            started_at="2026-09-18T10:00:00Z",
            ended_at="2026-09-18T10:30:00Z",
            workflow_plan={
                "design": {"nodes": [
                    {"id": "explore", "status": "completed"},
                    {"id": "design", "status": "completed"},
                ]},
                "build": {"nodes": [
                    {"id": "implement", "status": "pending"},
                ]},
            },
            step_history=[
                {"step_id": "explore", "usage": {"cost_usd": 0.25}},
                {"step_id": "design", "usage": {"cost_usd": 0.75}},
            ],
        ),
    }))

    rows, code = protocol.runs()

    assert code == 0
    [row] = rows
    assert row["started_at"] == "2026-09-18T10:00:00Z"
    assert row["ended_at"] == "2026-09-18T10:30:00Z"
    assert row["cost_usd"] == pytest.approx(1.0)
    assert row["cost_partial"] is False
    # Counted off the plan, so the denominator is the work the run set out to
    # do rather than the work it has recorded so far.
    assert (row["nodes_done"], row["nodes_total"]) == (2, 3)


def test_cost_partial_rides_along_when_an_attempt_could_not_be_priced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cost with an unpriced attempt in it is a floor, and must say so."""
    _install(monkeypatch, _Store({
        "r": _doc(step_history=[
            {"step_id": "a", "usage": {"cost_usd": 0.5}},
            {"step_id": "b", "usage": {"cost_usd": 0.0, "cost_partial": True}},
        ]),
    }))

    [row], _ = protocol.runs()

    assert row["cost_partial"] is True


def test_started_at_falls_back_to_the_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run seeded without its own stamp still reports when work began."""
    _install(monkeypatch, _Store({
        "r": _doc(step_history=[
            {"step_id": "a", "started_at": "2026-09-18T09:00:00Z"},
            {"step_id": "b", "started_at": "2026-09-18T09:05:00Z"},
        ]),
    }))

    [row], _ = protocol.runs()

    assert row["started_at"] == "2026-09-18T09:00:00Z"


# --- archived runs ---------------------------------------------------------
def test_archived_runs_are_listed_and_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _Store(
        {"live": _doc(run_id="live", slug="now")},
        {"old": _doc(run_id="old", slug="then", status="completed",
                     ended_at="2026-09-01T00:00:00Z")},
    ))

    rows, _ = protocol.runs()

    by_slug = {row["slug"]: row for row in rows}
    assert set(by_slug) == {"now", "then"}
    assert by_slug["now"]["archived"] is False
    assert by_slug["then"]["archived"] is True


def test_a_run_both_live_and_archived_is_listed_once_as_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live blob wins: it is the one the engine would load."""
    _install(monkeypatch, _Store(
        {"r": _doc(status="active")},
        {"r": _doc(status="completed")},
    ))

    rows, _ = protocol.runs()

    assert len(rows) == 1
    assert rows[0]["archived"] is False
    assert rows[0]["run_status"] == "active"


# --- ordering --------------------------------------------------------------
def test_ongoing_runs_come_first_then_newest_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ongoing first, then by `ended_at` descending — newest past run first."""
    _install(monkeypatch, _Store({
        "old": _doc(run_id="old", slug="old", status="completed",
                    ended_at="2026-09-01T00:00:00Z"),
        "new": _doc(run_id="new", slug="new", status="completed",
                    ended_at="2026-09-17T00:00:00Z"),
        "live": _doc(run_id="live", slug="live", status="active"),
        "held": _doc(run_id="held", slug="held", status="blocked"),
    }))

    rows, _ = protocol.runs()

    # Both ongoing runs come first; their order among themselves is by slug.
    assert {row["slug"] for row in rows[:2]} == {"live", "held"}
    assert [row["slug"] for row in rows[2:]] == ["new", "old"]


def test_needs_you_counts_as_ongoing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run waiting on a person is the one most worth pinning to the top."""
    _install(monkeypatch, _Store({
        "waiting": _doc(run_id="waiting", slug="waiting", status="needs_you"),
        "done": _doc(run_id="done", slug="done", status="completed",
                     ended_at="2026-09-18T00:00:00Z"),
    }))

    rows, _ = protocol.runs()

    assert [row["slug"] for row in rows] == ["waiting", "done"]
    assert rows[0]["ended_at"] is None


def test_a_finished_run_without_a_stamp_sorts_last_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _Store({
        "stamped": _doc(run_id="stamped", slug="stamped", status="completed",
                        ended_at="2026-09-10T00:00:00Z"),
        "bare": _doc(run_id="bare", slug="bare", status="cancelled"),
    }))

    rows, _ = protocol.runs()

    assert [row["slug"] for row in rows] == ["stamped", "bare"]


# --- the limit -------------------------------------------------------------
def test_limit_caps_after_sorting_so_the_ongoing_runs_survive_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap must not drop the row the list exists to show."""
    docs = {
        f"done-{i}": _doc(run_id=f"done-{i}", slug=f"done-{i}",
                          status="completed", ended_at=f"2026-09-{i:02d}T00:00:00Z")
        for i in range(1, 10)
    }
    docs["live"] = _doc(run_id="live", slug="live", status="active")
    _install(monkeypatch, _Store(docs))

    rows, _ = protocol.runs(3)

    assert len(rows) == 3
    assert rows[0]["slug"] == "live"
    # The two newest finished runs follow it.
    assert [row["slug"] for row in rows[1:]] == ["done-9", "done-8"]


def test_limit_zero_means_every_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Store({
        f"r{i}": _doc(run_id=f"r{i}", slug=f"r{i}") for i in range(30)
    }))

    rows, _ = protocol.runs(0)

    assert len(rows) == 30


def test_default_limit_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Store({
        f"r{i}": _doc(run_id=f"r{i}", slug=f"r{i:02d}") for i in range(30)
    }))

    rows, _ = protocol.runs()

    assert len(rows) == protocol.DEFAULT_RUN_LIMIT


# --- the CLI surface -------------------------------------------------------
def test_cli_limit_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, _Store({
        f"r{i}": _doc(run_id=f"r{i}", slug=f"r{i:02d}") for i in range(10)
    }))

    code = protocol.main("status", ["--json", "--limit", "4"])

    assert code == 0
    assert len(json.loads(capsys.readouterr().out)) == 4


def test_cli_rejects_a_non_numeric_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, _Store({"r": _doc()}))

    code = protocol.main("status", ["--json", "--limit", "lots"])

    assert code == protocol.EXIT_ERROR
    assert "--limit" in json.loads(capsys.readouterr().out)["error"]


def test_limit_is_ignored_when_a_run_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--limit` belongs to the listing; naming a run still reports that run."""
    seen: list[str] = []
    monkeypatch.setattr(
        protocol, "status", lambda ref: (seen.append(ref), ({}, 0))[1]
    )

    code = protocol.main("status", ["orc-1", "--json", "--limit", "4"])

    assert code == 0
    assert seen == ["orc-1"]
