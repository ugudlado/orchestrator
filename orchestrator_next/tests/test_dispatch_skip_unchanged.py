"""Resume idempotency at the dispatcher: a node whose artifacts still hash the
same is skipped instead of re-run.

The unit of the check lives in `artifacts.node_is_unchanged`; what is covered
here is the dispatcher wiring around it — that a re-queued node is dropped from
the ready set, marked completed, flagged `skipped_unchanged`, and that the skip
is persisted through the same compare-and-swap save a claim uses. Changing an
input file must bring the node back.
"""
from __future__ import annotations

import json

import pytest

from orchestrator_next import state_store as ss
from orchestrator_next.artifacts import sha256_file
from orchestrator_next.dispatch import dispatch, dispatch_batch
from orchestrator_next.parser import load_state

STEPS = ("build", "publish")


@pytest.fixture
def pack(tmp_path, monkeypatch):
    """Two steps. `build` declares an in: and an out: artifact by name."""
    steps = tmp_path / "steps"
    for step_id in STEPS:
        d = steps / step_id
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {step_id}\n", encoding="utf-8")

    (steps / "build" / "contract.yaml").write_text(
        "id: build\nversion: 1\nprompt: SKILL.md\n"
        "in:\n  source:\n    artifact: source.txt\n"
        "out:\n  report:\n    artifact: report.md\n",
        encoding="utf-8",
    )
    (steps / "publish" / "contract.yaml").write_text(
        "id: publish\nversion: 1\nprompt: SKILL.md\n", encoding="utf-8"
    )

    monkeypatch.setenv("ORCHESTRATOR_STEP_CONTRACTS_TEST_OVERRIDE", str(steps))
    routes = tmp_path / "models.yaml"
    routes.write_text(
        "models:\n  standard: {model_id: mock-model, tool: mock}\n"
        "step_models:\n" + "".join(f"  {s}: standard\n" for s in STEPS),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(routes))
    return steps


@pytest.fixture
def repo(tmp_path):
    """A run whose artifacts base exists, holding both declared files."""
    root = tmp_path / "repo"
    base = root / ".orchestrator" / "runs" / "orc-skip" / "artifacts"
    base.mkdir(parents=True)
    (base / "source.txt").write_text("the input\n", encoding="utf-8")
    (base / "report.md").write_text("the output\n", encoding="utf-8")
    return root, base


def _doc(repo_root, base, *, build_status="pending"):
    """A plan whose `build` node carries the hashes of a previous completion."""
    return {
        "change_id": "orc-skip", "slug": "orc-skip", "ticket_id": "ORC-SKIP",
        "schema": "mock", "config_pack": "workflows", "status": "active",
        "repo_root": str(repo_root), "phase": "main",
        "workflow_plan": {"main": {"nodes": [
            {
                "id": "build",
                "depends_on": [],
                "status": build_status,
                "input_artifacts": [{
                    "name": "source", "path": "source.txt",
                    "sha256": sha256_file(base / "source.txt"),
                }],
                "artifacts": [{
                    "name": "report", "path": "report.md",
                    "sha256": sha256_file(base / "report.md"),
                }],
            },
            {"id": "publish", "depends_on": ["build"], "status": "pending"},
        ], "filtered": []}},
        "step_history": [], "retries": {},
    }


def _handle(tmp_path, doc):
    handle = str(tmp_path / "orc-skip.yaml")
    store, h = ss.open_store(handle)
    store.create(h, json.loads(json.dumps(doc)))
    return handle


def _nodes(handle):
    doc, _token, _h = ss.load_doc(handle)
    return {n["id"]: n for n in doc["workflow_plan"]["main"]["nodes"]}


# ------------------------------------------------------------------ skipping
def test_unchanged_node_is_skipped_and_successor_dispatched(tmp_path, pack, repo):
    """Nothing changed on disk, so `build` is completed without being run."""
    repo_root, base = repo
    handle = _handle(tmp_path, _doc(repo_root, base))

    action, code = dispatch(load_state(handle), handle)

    assert code == 0
    assert action["step_id"] == "publish", (
        "build had nothing to redo; the dispatcher must move to its successor"
    )

    nodes = _nodes(handle)
    assert nodes["build"]["status"] == "completed"
    assert nodes["build"]["skipped_unchanged"] is True
    assert "skipped_unchanged" not in nodes["publish"]


def test_skip_is_persisted_through_the_store(tmp_path, pack, repo):
    """The skip is a state write, not a per-process decision."""
    repo_root, base = repo
    handle = _handle(tmp_path, _doc(repo_root, base))

    _doc_before, token_before, _h = ss.load_doc(handle)
    dispatch(load_state(handle), handle)
    _doc_after, token_after, _h = ss.load_doc(handle)

    assert token_after != token_before, "the skip must advance the store version"
    assert _nodes(handle)["build"]["status"] == "completed"


def test_changed_input_re_runs_the_node(tmp_path, pack, repo):
    """Touch the input the step reads: the recorded hashes no longer match."""
    repo_root, base = repo
    handle = _handle(tmp_path, _doc(repo_root, base))

    (base / "source.txt").write_text("a different input\n", encoding="utf-8")

    action, code = dispatch(load_state(handle), handle)

    assert code == 0
    assert action["step_id"] == "build", "a changed input must re-run the step"

    nodes = _nodes(handle)
    assert nodes["build"]["status"] == "in_progress", "re-run means a real claim"
    assert "skipped_unchanged" not in nodes["build"]


def test_changed_output_re_runs_the_node(tmp_path, pack, repo):
    """The output no longer matches what the step recorded producing."""
    repo_root, base = repo
    handle = _handle(tmp_path, _doc(repo_root, base))

    (base / "report.md").write_text("somebody edited this\n", encoding="utf-8")

    action, code = dispatch(load_state(handle), handle)

    assert code == 0
    assert action["step_id"] == "build"


def test_node_without_recorded_artifacts_is_never_skipped(tmp_path, pack, repo):
    """No record of a previous run means re-running is the safe default."""
    repo_root, base = repo
    doc = _doc(repo_root, base)
    doc["workflow_plan"]["main"]["nodes"][0].pop("artifacts")
    handle = _handle(tmp_path, doc)

    action, code = dispatch(load_state(handle), handle)

    assert code == 0
    assert action["step_id"] == "build"


def test_batch_dispatch_skips_unchanged_nodes_too(tmp_path, pack, repo):
    """The parallel path shares the same skip, and still claims what remains."""
    repo_root, base = repo
    handle = _handle(tmp_path, _doc(repo_root, base))

    actions, code = dispatch_batch(handle, max_parallel=4)

    assert code == 0
    assert [a["step_id"] for a in actions] == ["publish"]

    nodes = _nodes(handle)
    assert nodes["build"]["status"] == "completed"
    assert nodes["build"]["skipped_unchanged"] is True
    assert nodes["publish"]["status"] == "in_progress", "the survivor is claimed"


def test_missing_artifact_file_re_runs_the_node(tmp_path, pack, repo):
    """A recorded output that is gone cannot be unchanged."""
    repo_root, base = repo
    handle = _handle(tmp_path, _doc(repo_root, base))

    (base / "report.md").unlink()

    action, code = dispatch(load_state(handle), handle)

    assert code == 0
    assert action["step_id"] == "build"


def test_a_chain_of_unchanged_nodes_drains(tmp_path, pack, monkeypatch):
    """Two unchanged nodes in a row: completing the first unblocks the second.

    A single skip pass would report the phase complete while `publish` was still
    pending, because the ready set was computed before `build` was completed.
    """
    steps = pack
    (steps / "publish" / "contract.yaml").write_text(
        "id: publish\nversion: 1\nprompt: SKILL.md\n"
        "out:\n  site:\n    artifact: site.html\n",
        encoding="utf-8",
    )

    root = tmp_path / "chain"
    base = root / ".orchestrator" / "runs" / "orc-skip" / "artifacts"
    base.mkdir(parents=True)
    for name, text in (("source.txt", "in\n"), ("report.md", "out\n"),
                       ("site.html", "<p>built</p>\n")):
        (base / name).write_text(text, encoding="utf-8")

    doc = _doc(root, base)
    doc["workflow_plan"]["main"]["nodes"][1]["artifacts"] = [{
        "name": "site", "path": "site.html",
        "sha256": sha256_file(base / "site.html"),
    }]
    handle = _handle(tmp_path, doc)

    _action, code = dispatch(load_state(handle), handle)

    assert code == 1, "every node was unchanged, so the phase is complete"
    nodes = _nodes(handle)
    assert nodes["build"]["skipped_unchanged"] is True
    assert nodes["publish"]["skipped_unchanged"] is True, (
        "the second node must be skipped in the same dispatch call"
    )
