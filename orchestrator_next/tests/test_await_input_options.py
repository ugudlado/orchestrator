"""Standardized await_input options: engine-side deterministic resume routing.

A step pauses with a labeled options list; `execute.route_awaiting_input`
matches the resume text to a label or number and advances or resets the DAG
without re-dispatching the step at all. Unmatched text falls through to the
step, which re-runs with the raw text and interprets it itself.

The loop below is what a harness does: `orchestrator step` until the run parks
on await_input, then `orchestrator resume <run> "<text>"` with the answer.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from orchestrator_next.protocol import ProtocolError, resume
from orchestrator_next.protocol import step as protocol_step
from orchestrator_next.seed import seed_state_file


def _mini_pack(tmp_path: Path) -> Path:
    """review (offers options) -> rework (only reachable via reset_to) -> finish."""
    pack = tmp_path / "pack"
    (pack / "workflows").mkdir(parents=True)
    (pack / "workflows" / "mini-options.yaml").write_text(
        textwrap.dedent(
            """\
            steps:
              - rework
              - id: review
                on_failure: rework
              - finish
            """
        ),
        encoding="utf-8",
    )
    scripts = {
        "rework": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            python3 - <<'PY'
            import json, os
            print(json.dumps({
              "step_id": "rework",
              "phase": os.environ.get("ORCHESTRATOR_PHASE", "main"),
              "status": "completed",
              "outputs": {"reason": "reworked"},
            }))
            PY
            """
        ),
        "review": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            direction="${ORCHESTRATOR_USER_DIRECTION:-}"
            phase="${ORCHESTRATOR_PHASE:-main}"
            if [ -z "$direction" ]; then
              python3 - "$phase" <<'PY'
            import json, sys
            print(json.dumps({
              "step_id": "review",
              "phase": sys.argv[1],
              "status": "await_input",
              "outputs": {
                "ask": "Review passed. Ship it, or send back?",
                "options": [
                  {"label": "approve"},
                  {"label": "rework", "reset_to": "rework"},
                ],
              },
            }))
            PY
              exit 0
            fi
            # Fallback path: only reached if the engine did NOT match an option
            # deterministically (proves unmatched text still reaches the step).
            python3 - "$phase" "$direction" <<'PY'
            import json, sys
            print(json.dumps({
              "step_id": "review",
              "phase": sys.argv[1],
              "status": "await_input",
              "outputs": {
                "ask": f"Didn't understand {sys.argv[2]!r}. Ship it, or send back?",
                "options": [
                  {"label": "approve"},
                  {"label": "rework", "reset_to": "rework"},
                ],
              },
            }))
            PY
            """
        ),
        "finish": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            python3 - <<'PY'
            import json, os
            print(json.dumps({
              "step_id": "finish",
              "phase": os.environ.get("ORCHESTRATOR_PHASE", "main"),
              "status": "completed",
              "outputs": {"reason": "shipped"},
            }))
            PY
            """
        ),
    }
    for step, script in scripts.items():
        d = pack / "steps" / step
        d.mkdir(parents=True)
        (d / "contract.yaml").write_text(
            f"id: {step}\nversion: 1\nrun: script.sh\n", encoding="utf-8"
        )
        sh = d / "script.sh"
        sh.write_text(script, encoding="utf-8")
        sh.chmod(0o755)
    (pack / "models.yaml").write_text("models: {}\nstep_models: {}\n", encoding="utf-8")
    return pack


def _seed(tmp_path, pack, monkeypatch, slug):
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    state = tmp_path / f"{slug}.yaml"
    seed_state_file(state, slug=slug, schema="mini-options", repo_root=str(repo))
    return state, repo


def _drive(state_path: Path, limit: int = 12) -> dict:
    """`orchestrator step` until the run is no longer dispatchable."""
    result: dict = {}
    for _ in range(limit):
        result, _ = protocol_step(str(state_path))
        if result.get("status") != "ready":
            return result
    raise AssertionError("step did not settle")


def _awaiting(state_path: Path) -> dict | None:
    return (yaml.safe_load(state_path.read_text()) or {}).get("awaiting")


def _resume(state_path: Path, repo: Path, text: str) -> dict:
    """`orchestrator resume <run> "<text>"` — the verb a harness calls."""
    result, _ = resume(str(state_path), text)
    return result["next"]


def test_option_label_match_advances_without_redispatch(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    state, repo = _seed(tmp_path, pack, monkeypatch, "opt-1")

    _drive(state)
    assert _awaiting(state)["step_id"] == "review"

    assert _resume(state, repo, "approve")["status"] == "done"

    raw = yaml.safe_load(state.read_text())
    assert "awaiting" not in raw
    statuses = [
        (e.get("step_id"), e.get("status"))
        for e in raw["step_history"] if isinstance(e, dict)
    ]
    # review's advance was recorded by the engine directly ("user selected:
    # approve") — the script's fallback branch (the "Didn't understand" ask)
    # never fired, proving the match short-circuited re-dispatch.
    review_entries = [e for e in raw["step_history"] if e.get("step_id") == "review"]
    assert len(review_entries) == 2  # the initial await_input + the engine's advance
    assert review_entries[-1]["outputs"]["reason"] == "user selected: approve"
    assert ("finish", "completed") in statuses


def test_option_number_match_advances(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    state, repo = _seed(tmp_path, pack, monkeypatch, "opt-2")

    _drive(state)
    assert _resume(state, repo, "1")["status"] == "done"

    raw = yaml.safe_load(state.read_text())
    review_entries = [e for e in raw["step_history"] if e.get("step_id") == "review"]
    assert review_entries[-1]["outputs"]["reason"] == "user selected: approve"


def test_option_with_reset_to_resets_dag(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    state, repo = _seed(tmp_path, pack, monkeypatch, "opt-3")

    _drive(state)
    _resume(state, repo, "rework")
    # reset_to rework -> rework re-runs -> review pauses again (no direction)

    raw = yaml.safe_load(state.read_text())
    statuses = [
        (e.get("step_id"), e.get("status"))
        for e in raw["step_history"] if isinstance(e, dict)
    ]
    assert ("rework", "completed") in statuses
    assert raw["next_step"]["step_id"] == "review"
    assert raw["awaiting"]["step_id"] == "review"


def test_unmatched_text_falls_through_to_step(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    state, repo = _seed(tmp_path, pack, monkeypatch, "opt-4")

    _drive(state)
    _resume(state, repo, "what does this even mean")
    # No option matched -> review re-ran with the raw text -> its fallback
    # branch pauses again with a clarifying ask (proves the step, not the
    # engine, interpreted the freeform text).
    raw = yaml.safe_load(state.read_text())
    assert "what does this even mean" in raw["awaiting"]["ask"]


# ---------------------------------------------------------------------------
# the `resume` verb itself
# ---------------------------------------------------------------------------
def test_step_reports_needs_you_with_the_ask_and_options(tmp_path, monkeypatch):
    """A parked run tells the harness what to ask and what the answers are."""
    pack = _mini_pack(tmp_path)
    state, _repo = _seed(tmp_path, pack, monkeypatch, "verb-1")

    result = _drive(state)
    assert result["status"] == "needs_you"
    assert result["step_id"] == "review"
    assert "Ship it" in result["payload"]["ask"]
    assert [o["label"] for o in result["payload"]["options"]] == ["approve", "rework"]


def test_step_on_a_parked_run_does_not_redispatch(tmp_path, monkeypatch):
    """Polling a parked run re-reports the question; it never re-runs the step."""
    pack = _mini_pack(tmp_path)
    state, _repo = _seed(tmp_path, pack, monkeypatch, "verb-2")

    _drive(state)
    before = len(yaml.safe_load(state.read_text())["step_history"])
    again = _drive(state)
    after = len(yaml.safe_load(state.read_text())["step_history"])

    assert again["status"] == "needs_you"
    assert after == before, "polling a parked run re-ran the step"


def test_resume_matches_by_label(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    state, _repo = _seed(tmp_path, pack, monkeypatch, "verb-3")
    _drive(state)

    result, code = resume(str(state), "approve")
    assert code == 0
    assert result["matched"] is True
    assert result["next"]["status"] == "done"


def test_resume_matches_by_index(tmp_path, monkeypatch):
    """A 1-based number picks the option at that position."""
    pack = _mini_pack(tmp_path)
    state, _repo = _seed(tmp_path, pack, monkeypatch, "verb-4")
    _drive(state)

    result, _ = resume(str(state), "2")  # 2 == rework, which resets the DAG
    assert result["matched"] is True

    raw = yaml.safe_load(state.read_text())
    statuses = [(e.get("step_id"), e.get("status")) for e in raw["step_history"]]
    assert ("rework", "completed") in statuses


def test_resume_with_a_wrong_answer_falls_through_to_the_step(tmp_path, monkeypatch):
    """Text matching no option is handed to the step, which asks again."""
    pack = _mini_pack(tmp_path)
    state, _repo = _seed(tmp_path, pack, monkeypatch, "verb-5")
    _drive(state)

    result, code = resume(str(state), "maybe later?")
    assert code == 0
    assert result["matched"] is False
    # The step re-ran with the raw text and asked a clarifying question.
    assert result["next"]["status"] == "needs_you"
    assert "maybe later?" in yaml.safe_load(state.read_text())["awaiting"]["ask"]


def test_resume_on_a_run_that_is_not_awaiting_is_an_error(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    state, _repo = _seed(tmp_path, pack, monkeypatch, "verb-6")

    with pytest.raises(ProtocolError, match="not awaiting input"):
        resume(str(state), "approve")
