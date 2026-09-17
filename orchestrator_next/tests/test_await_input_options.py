"""Standardized await_input options: engine-side deterministic resume routing
(Phase 5) — a step pauses with a labeled options list; the engine matches the
resume text to a label/number and advances or resets without re-dispatching
the step at all. Unmatched text falls through to the step for interpretation.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from orchestrator_next.run_loop import LOOP_PAUSED, run_loop, seed_state_file


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


def test_option_label_match_advances_without_redispatch(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    state, repo = _seed(tmp_path, pack, monkeypatch, "opt-1")

    code = run_loop(str(state), repo_root=str(repo), models_yaml="")
    assert code == LOOP_PAUSED
    raw = yaml.safe_load(state.read_text())
    assert raw["awaiting"]["step_id"] == "review"

    code = run_loop(str(state), repo_root=str(repo), models_yaml="", user_direction="approve")
    assert code == 1

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

    run_loop(str(state), repo_root=str(repo), models_yaml="")
    code = run_loop(str(state), repo_root=str(repo), models_yaml="", user_direction="1")
    assert code == 1
    raw = yaml.safe_load(state.read_text())
    review_entries = [e for e in raw["step_history"] if e.get("step_id") == "review"]
    assert review_entries[-1]["outputs"]["reason"] == "user selected: approve"


def test_option_with_reset_to_resets_dag(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    state, repo = _seed(tmp_path, pack, monkeypatch, "opt-3")

    run_loop(str(state), repo_root=str(repo), models_yaml="")
    code = run_loop(str(state), repo_root=str(repo), models_yaml="", user_direction="rework")
    # reset_to rework -> rework re-runs -> review pauses again (empty direction)
    assert code == LOOP_PAUSED

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

    run_loop(str(state), repo_root=str(repo), models_yaml="")
    code = run_loop(
        str(state), repo_root=str(repo), models_yaml="",
        user_direction="what does this even mean",
    )
    # No option matched -> review re-runs with the raw text -> its fallback
    # branch pauses again with a clarifying ask (proves the step, not the
    # engine, interpreted the freeform text).
    assert code == LOOP_PAUSED
    raw = yaml.safe_load(state.read_text())
    assert "what does this even mean" in raw["awaiting"]["ask"]
