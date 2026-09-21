"""Pack convention (Phase 4, no engine changes): a step that can't find its
upstream artifact validates that itself and fails with a missing-inputs
reason, routing through the normal reset_to/on_failure machinery.

Scenario: producer already ran and completed; its artifact then goes missing
(deleted mid-run — matches the plan's done-criteria scenario of deleting
design.md and resuming) before consumer runs. Resuming re-dispatches
consumer, which finds its input gone and must fail loudly rather than
invent scope, routing back to producer via reset_to."""
from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from orchestrator_next.seed import seed_state_file


def _drive(state_path, limit: int = 20) -> dict:
    """Walk until the run settles, running exec steps as a driver does."""
    from orchestrator_next.tests.conftest import drive

    return drive(state_path, limit=limit)


def _mini_pack(tmp_path: Path) -> Path:
    """producer (writes artifact) -> consumer (guards on it) -> finish."""
    pack = tmp_path / "pack"
    (pack / "workflows").mkdir(parents=True)
    (pack / "workflows" / "mini-missing.yaml").write_text(
        textwrap.dedent(
            """\
            steps:
              - producer
              - id: consumer
                on_failure: producer
              - finish
            """
        ),
        encoding="utf-8",
    )
    for step, script in {
        "producer": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            mkdir -p "$(dirname "$ARTIFACT")"
            echo "produced" > "$ARTIFACT"
            python3 - <<'PY'
            import json, os
            print(json.dumps({
              "step_id": "producer",
              "phase": os.environ.get("ORCHESTRATOR_PHASE", "main"),
              "status": "completed",
              "outputs": {"reason": "wrote artifact.txt"},
            }))
            PY
            """
        ),
        "consumer": textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            phase="${ORCHESTRATOR_PHASE:-main}"
            if [ ! -f "$ARTIFACT" ]; then
              python3 - "$phase" <<'PY'
            import json, sys
            print(json.dumps({
              "step_id": "consumer",
              "phase": sys.argv[1],
              "status": "failed",
              "outputs": {
                "reason": "missing inputs: artifact.txt",
                "reset_to": "producer",
              },
            }))
            PY
              exit 0
            fi
            python3 - "$phase" <<'PY'
            import json, sys
            print(json.dumps({
              "step_id": "consumer",
              "phase": sys.argv[1],
              "status": "completed",
              "outputs": {"reason": "artifact.txt present"},
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
              "outputs": {"reason": "done"},
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


def test_consumer_missing_input_routes_to_producer(tmp_path, monkeypatch):
    pack = _mini_pack(tmp_path)
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack))
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = repo / "artifact.txt"
    monkeypatch.setenv("ARTIFACT", str(artifact))
    state = tmp_path / "state.yaml"
    seed_state_file(state, slug="mini-missing-1", schema="mini-missing", repo_root=str(repo))

    # First drive: producer runs and writes the artifact, consumer sees it
    # and passes, finish runs. Full run completes.
    assert _drive(state)["status"] == "done"
    raw = yaml.safe_load(state.read_text())
    statuses = [
        (e.get("step_id"), e.get("status"))
        for e in raw["step_history"] if isinstance(e, dict)
    ]
    assert ("producer", "completed") in statuses
    assert ("consumer", "completed") in statuses
    assert ("finish", "completed") in statuses

    # Simulate the artifact going missing mid-run (deleted, wrong machine,
    # cache miss — matches the plan's done-criteria scenario) and force a
    # re-dispatch of consumer via reset_step.
    artifact.unlink()
    from orchestrator_next.reset_step import reset_step
    reset_step("consumer", str(state))

    # consumer fails missing-inputs -> reset_to producer -> re-runs -> completes
    assert _drive(state)["status"] == "done"

    raw = yaml.safe_load(state.read_text())
    statuses = [
        (e.get("step_id"), e.get("status"))
        for e in raw["step_history"] if isinstance(e, dict)
    ]
    assert ("consumer", "failed") in statuses
    failed_entry = next(
        e for e in raw["step_history"]
        if isinstance(e, dict) and e.get("step_id") == "consumer" and e.get("status") == "failed"
    )
    assert failed_entry["outputs"]["reason"] == "missing inputs: artifact.txt"
    assert failed_entry["outputs"]["reset_to"] == "producer"
    # Self-healed: producer re-ran to recreate the missing artifact, consumer
    # then passed and the workflow completed — no engine gate was needed, the
    # step's own guard + reset_to routing did the recovery.
    assert ("producer", "completed") in statuses
    assert ("consumer", "completed") in statuses
    assert ("finish", "completed") in statuses
