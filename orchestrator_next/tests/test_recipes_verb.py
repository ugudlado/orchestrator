"""`orchestrator recipes --json` and `orchestrator status --json` with no run.

Both exist so the Claude Mod can draw a picker without anyone typing a recipe
name into chat: `recipes` fills the wizard's option list, and a `status` with
no run answers "is anything running?" — which decides whether `/orchestrator`
toggles the pane or opens the wizard.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from orchestrator_next import protocol


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


@pytest.fixture
def two_packs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo with two packs, one recipe name shared between them."""
    repo = tmp_path / "repo"
    for pack in ("alpha", "beta"):
        root = repo / ".orchestrator" / pack
        _write_yaml(
            root / "workflows" / "shared.yaml",
            {
                "name": "shared",
                "inputs": {"ticket": {"type": "string"}},
                "steps": [
                    "explore",
                    {"gate": "design-signoff", "show": ["design"]},
                    "implement",
                ],
            },
        )
    # Only alpha has this one, so its bare name is unambiguous.
    _write_yaml(
        repo / ".orchestrator" / "alpha" / "workflows" / "solo.yaml",
        {"name": "solo", "steps": ["explore"]},
    )
    monkeypatch.chdir(repo)
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.delenv("ORCHESTRATOR_CONFIG", raising=False)
    return repo


def test_recipes_lists_every_pack_with_steps_gates_and_inputs(two_packs: Path) -> None:
    rows, code = protocol.recipes()

    assert code == 0
    by_key = {(row["name"], row["pack"]): row for row in rows}
    assert set(by_key) == {("shared", "alpha"), ("shared", "beta"), ("solo", "alpha")}

    shared = by_key[("shared", "alpha")]
    assert shared["steps"] == 3
    # Only the `{gate: ...}` entries are gates — the plain step ids are not.
    assert shared["gates"] == ["design-signoff"]
    assert shared["inputs"] == {"ticket": {"type": "string"}}

    solo = by_key[("solo", "alpha")]
    assert solo["gates"] == []
    assert solo["inputs"] == {}


def test_recipes_reports_a_broken_recipe_without_dropping_the_listing(
    two_packs: Path,
) -> None:
    """One unparseable YAML must not take the whole menu down."""
    broken = two_packs / ".orchestrator" / "alpha" / "workflows" / "broken.yaml"
    broken.write_text("steps: [a, b\n  bad: {")

    rows, code = protocol.recipes()

    assert code == 0
    row = next(r for r in rows if r["name"] == "broken")
    assert row["error"]
    assert row["steps"] == 0
    # The healthy recipes are still listed beside it.
    assert {r["name"] for r in rows} >= {"shared", "solo"}


def test_recipes_tolerates_a_recipe_that_is_not_a_mapping(two_packs: Path) -> None:
    bad = two_packs / ".orchestrator" / "alpha" / "workflows" / "scalar.yaml"
    bad.write_text("just a string\n")

    rows, _code = protocol.recipes()

    row = next(r for r in rows if r["name"] == "scalar")
    assert row["error"] == "recipe is not a mapping"


def test_recipes_verb_prints_a_json_array(
    two_packs: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` is what the mod shells out to, so its stdout must parse."""
    code = protocol.main("recipes", ["--json"])

    assert code == 0
    rows = json.loads(capsys.readouterr().out)
    assert isinstance(rows, list)
    assert {row["name"] for row in rows} == {"shared", "solo"}


def test_status_with_no_run_lists_live_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`status --json` with no run answers the picker, not an error."""
    monkeypatch.setenv("ORCHESTRATOR_STATE_ROOT", str(tmp_path / "state"))

    class _Store:
        def list_ids(self, *, archived: bool = False) -> list[str]:
            return ["run-a", "run-b"]

        def load(self, run_id: str, *, archived: bool = False) -> str:
            if run_id == "run-a":
                return yaml.safe_dump({
                    "run_id": "run-a",
                    "slug": "orc-1",
                    "status": "active",
                    "schema": "feature",
                    "step_history": [
                        {"step_id": "explore"},
                        {"step_id": "design"},
                    ],
                })
            return yaml.safe_dump({
                "run_id": "run-b",
                "slug": "orc-2",
                "status": "completed",
                "schema": "bugfix",
                "step_history": [],
            })

    monkeypatch.setattr(
        "orchestrator_next.run_store.open_store", lambda: _Store()
    )

    code = protocol.main("status", ["--json"])

    assert code == 0
    rows = json.loads(capsys.readouterr().out)
    # Active first, so a single-live-run session resolves without asking.
    assert [row["slug"] for row in rows] == ["orc-1", "orc-2"]
    assert rows[0]["run_id"] == "run-a"
    assert rows[0]["run_status"] == "active"
    assert rows[0]["recipe"] == "feature"
    # The step the run stands at is the last one its history touched.
    assert rows[0]["current_step"] == "design"
    # An ongoing run has no end: reporting one would sort it among the
    # finished runs, which is the opposite of where it belongs.
    assert rows[0]["ended_at"] is None
    assert rows[1]["current_step"] is None


def test_runs_skips_a_run_whose_state_will_not_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Store:
        def list_ids(self, *, archived: bool = False) -> list[str]:
            return ["bad", "good"]

        def load(self, run_id: str, *, archived: bool = False) -> str:
            return "{{{ not yaml" if run_id == "bad" else yaml.safe_dump(
                {"run_id": "good", "slug": "ok", "status": "active"}
            )

    monkeypatch.setattr(
        "orchestrator_next.run_store.open_store", lambda: _Store()
    )

    rows, code = protocol.runs()

    assert code == 0
    assert [row["slug"] for row in rows] == ["ok"]
