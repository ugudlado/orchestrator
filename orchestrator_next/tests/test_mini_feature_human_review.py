"""Small feature-shaped workflow: stub review → human-review pause → resume / reset_to."""
from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from orchestrator_next.run_loop import LOOP_PAUSED, run_loop, seed_state_file


def _mini_pack(tmp_path: Path) -> Path:
    """Build a tiny pack: done-review → human-review (script) → finish."""
    pack = tmp_path / "pack"
    (pack / "workflows").mkdir(parents=True)
    (pack / "workflows" / "mini-feature.yaml").write_text(
        textwrap.dedent(
            """\
            steps:
              - done-review
              - id: human-review
                on_failure: done-review
                max_retries: 8
              - finish
            """
        ),
        encoding="utf-8",
    )
    for step, script in {
        "done-review": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            python3 - <<'PY'
            import json, os
            print(json.dumps({
              "step_id": "done-review",
              "phase": os.environ.get("ORCHESTRATOR_PHASE", "main"),
              "status": "completed",
              "outputs": {"reason": "review stub passed"},
            }))
            PY
            """
        ),
        "human-review": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            python3 - <<'PY'
            import json, os, re
            direction = (os.environ.get("ORCHESTRATOR_USER_DIRECTION") or "").strip()
            phase = os.environ.get("ORCHESTRATOR_PHASE", "main")
            low = direction.lower()
            if not direction:
                print(json.dumps({
                  "step_id": "human-review",
                  "phase": phase,
                  "status": "await_input",
                  "outputs": {"ask": "Review passed. Approve or request changes."},
                }))
            elif re.search(r"\\b(approve|ship|lgtm|yes)\\b", low):
                print(json.dumps({
                  "step_id": "human-review",
                  "phase": phase,
                  "status": "completed",
                  "outputs": {"reason": "approved"},
                }))
            elif "design" in low:
                print(json.dumps({
                  "step_id": "human-review",
                  "phase": phase,
                  "status": "failed",
                  "outputs": {"reset_to": "done-review", "reason": direction},
                }))
            else:
                print(json.dumps({
                  "step_id": "human-review",
                  "phase": phase,
                  "status": "failed",
                  "outputs": {"reset_to": "done-review", "reason": direction},
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
    }.items():
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


def test_mini_feature_pause_then_approve(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state.yaml"
    seed_state_file(state, slug="mini-1", schema="mini-feature", repo_root=str(repo))

    # First drive: pause at human-review
    code = run_loop(str(state), repo_root=str(repo), models_yaml="")
    assert code == LOOP_PAUSED
    raw = yaml.safe_load(state.read_text())
    assert raw["next_step"]["step_id"] == "human-review"
    assert any(
        e.get("status") == "await_input" for e in raw["step_history"] if isinstance(e, dict)
    )

    # Resume with approve prose
    code = run_loop(
        str(state),
        repo_root=str(repo),
        models_yaml="",
        user_direction="LGTM ship it",
    )
    assert code == 1
    raw = yaml.safe_load(state.read_text())
    assert raw.get("status") in ("completed", "active", None) or raw.get("next_step") is None
    statuses = [e.get("status") for e in raw["step_history"] if isinstance(e, dict)]
    assert "await_input" in statuses
    assert statuses.count("completed") >= 2  # human-review + finish (and done-review)


def test_mini_feature_reset_to_done_review(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state.yaml"
    seed_state_file(state, slug="mini-2", schema="mini-feature", repo_root=str(repo))

    assert run_loop(str(state), repo_root=str(repo), models_yaml="") == LOOP_PAUSED

    code = run_loop(
        str(state),
        repo_root=str(repo),
        models_yaml="",
        user_direction="please fix the design of the empty state",
    )
    # After reset_to done-review, loop continues (scripts) until human-review awaits again
    # or completes a cycle — with our script, done-review completes then human-review
    # awaits again (no direction consumed twice... direction was consumed on first
    # human-review fail). Next human-review has empty direction → pause.
    assert code == LOOP_PAUSED
    raw = yaml.safe_load(state.read_text())
    # After a full cycle back to await, done-review may be completed again
    assert raw["next_step"]["step_id"] == "human-review"
    assert any(
        isinstance(e, dict)
        and e.get("step_id") == "human-review"
        and e.get("status") == "failed"
        and (e.get("outputs") or {}).get("reset_to") == "done-review"
        for e in raw["step_history"]
    )
