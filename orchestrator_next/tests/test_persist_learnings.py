"""Tests for the persist-learnings step.

The step is the only writer into a prompt pack's scenarios/train.jsonl, so its
contract is: never corrupt a bank, never fail the workflow, never sweep someone
else's uncommitted work into a commit. Every test runs the real script as a
subprocess against a real git repo — the git behavior is half the step.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STEP_DIR = _REPO_ROOT / "config" / "steps" / "persist-learnings"
_SCRIPT = _STEP_DIR / "persist_learnings.py"
_WORKFLOWS = _REPO_ROOT / "config" / "workflows"

VALID_ROW = {
    "id": "prefer-readme-scope",
    "scenario": "No ticket body exists; only a README describes the feature.",
    "expect": ["Prefer README-derived scope", "Do not invent ticket text"],
}


# ---------------------------------------------------------------------------
# Fixture: a git repo with one prompt pack, a state dir, and a staging file
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class Sandbox:
    """A repo with skills/<pack>/, a state dir, and helpers to drive the step."""

    def __init__(self, tmp_path: Path, packs=("explore",)) -> None:
        self.repo = tmp_path / "repo"
        self.skills = self.repo / "skills"
        self.state_dir = tmp_path / "state" / "orc-1"
        self.state_dir.mkdir(parents=True)
        for name in packs:
            pack = self.skills / name
            (pack / "scenarios").mkdir(parents=True)
            (pack / "SKILL.md").write_text("# pack\n")
        self.state_yaml = self.state_dir / "state.yaml"
        self.state_yaml.write_text(yaml.safe_dump({"change_id": "orc-1"}))
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        self.prompt_dirs = {name: str(self.skills / name) for name in packs}

    @property
    def staging(self) -> Path:
        return self.state_dir / "proposed-scenarios.jsonl"

    def train(self, pack: str = "explore") -> Path:
        return self.skills / pack / "scenarios" / "train.jsonl"

    def stage(self, *lines: str) -> None:
        self.staging.write_text("".join(line + "\n" for line in lines))

    def stage_rows(self, *rows: dict) -> None:
        self.stage(*(json.dumps(row) for row in rows))

    def run(self, **env_extra) -> dict:
        """Run the step; return its parsed completion payload."""
        env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.repo.parent),
            "ORCHESTRATOR_STEP_DIR": str(_STEP_DIR),
            "ORCHESTRATOR_STATE_YAML_PATH": str(self.state_yaml),
            "ORCHESTRATOR_REPO_ROOT": str(self.repo),
            "ORCHESTRATOR_CHANGE_ID": "orc-1",
            "ORCHESTRATOR_PROMPT_DIRS": json.dumps(self.prompt_dirs),
            "ORCHESTRATOR_PROMPT_PATH": str(self.skills),
            **env_extra,
        }
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert proc.returncode == 0, (
            f"the step must never fail the workflow; stderr:\n{proc.stderr}"
        )
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        assert lines, f"no completion JSON on stdout; stderr:\n{proc.stderr}"
        payload = json.loads(lines[-1])
        assert payload["status"] == "completed"
        return payload["outputs"]["persist_learnings"]

    def git_status(self) -> str:
        return _git(self.repo, "status", "--porcelain").stdout.strip()

    def last_commit_subject(self) -> str:
        return _git(self.repo, "log", "-1", "--format=%s").stdout.strip()


@pytest.fixture
def sandbox(tmp_path):
    return Sandbox(tmp_path)


def _skip_reasons(outputs: dict) -> str:
    return " | ".join(entry["reason"] for entry in outputs["skipped"])


# ---------------------------------------------------------------------------
# No-op and happy path
# ---------------------------------------------------------------------------

def test_missing_staging_file_is_a_noop(sandbox):
    outputs = sandbox.run()
    assert outputs == {"persisted": [], "skipped": [], "commits": []}
    assert not sandbox.train().exists()
    assert sandbox.git_status() == ""


def test_clean_append_commits_one_single_line_row(sandbox):
    sandbox.stage_rows({"step_id": "explore", "row": VALID_ROW})

    outputs = sandbox.run()

    assert outputs["skipped"] == []
    assert outputs["persisted"] == [
        {
            "pack": str(sandbox.skills / "explore"),
            "path": str(sandbox.train()),
            "ids": ["prefer-readme-scope"],
        }
    ]
    lines = sandbox.train().read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == VALID_ROW
    assert outputs["commits"][0]["committed"] is True
    assert sandbox.last_commit_subject() == "chore(orc-1): learn scenarios"
    assert sandbox.git_status() == ""


def test_staging_file_is_consumed(sandbox):
    sandbox.stage_rows({"step_id": "explore", "row": VALID_ROW})
    sandbox.run()
    assert not sandbox.staging.exists(), (
        "the staging file must be consumed or a re-run re-applies its rows"
    )


def test_append_preserves_existing_rows_and_missing_trailing_newline(sandbox):
    existing = {"id": "already-here", "scenario": "Prior row.", "expect": ["keep it"]}
    sandbox.train().write_text(json.dumps(existing))  # no trailing newline
    _git(sandbox.repo, "add", "-A")
    _git(sandbox.repo, "commit", "-q", "-m", "seed scenarios")
    sandbox.stage_rows({"step_id": "explore", "row": VALID_ROW})

    outputs = sandbox.run()

    assert outputs["skipped"] == []
    lines = sandbox.train().read_text().splitlines()
    assert [json.loads(ln)["id"] for ln in lines] == ["already-here", "prefer-readme-scope"]


# ---------------------------------------------------------------------------
# Validation — malformed rows are dropped, never fatal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_row,expected_reason",
    [
        ({"id": "x", "scenario": "s", "expect": ["e"], "extra": 1}, "unknown extra"),
        ({"id": "x", "scenario": "s"}, "missing expect"),
        ({"id": "  ", "scenario": "s", "expect": ["e"]}, "id must be a non-empty string"),
        ({"id": "x", "scenario": "", "expect": ["e"]}, "scenario must be a non-empty string"),
        ({"id": "x", "scenario": "s", "expect": []}, "expect must be a non-empty list"),
        ({"id": "x", "scenario": "s", "expect": ["ok", ""]}, "expect must be a non-empty list"),
        ({"id": "x", "scenario": "s", "expect": "not-a-list"}, "expect must be a non-empty list"),
    ],
)
def test_malformed_rows_are_skipped(sandbox, bad_row, expected_reason):
    sandbox.stage_rows(
        {"step_id": "explore", "row": bad_row},
        {"step_id": "explore", "row": VALID_ROW},
    )

    outputs = sandbox.run()

    assert expected_reason in _skip_reasons(outputs)
    assert outputs["persisted"][0]["ids"] == ["prefer-readme-scope"], (
        "one bad row must not block the good rows beside it"
    )


def test_pretty_printed_row_is_rejected(sandbox):
    sandbox.staging.write_text(
        json.dumps({"step_id": "explore", "row": VALID_ROW}, indent=2) + "\n"
    )

    outputs = sandbox.run()

    assert outputs["persisted"] == []
    assert "single line of valid JSON" in _skip_reasons(outputs)
    assert not sandbox.train().exists()


def test_duplicate_json_key_is_rejected(sandbox):
    sandbox.stage('{"step_id": "explore", "step_id": "explore", "row": {}}')

    outputs = sandbox.run()

    assert "duplicate JSON key" in _skip_reasons(outputs)


def test_row_without_step_id_is_skipped(sandbox):
    sandbox.stage_rows({"row": VALID_ROW})

    outputs = sandbox.run()

    assert outputs["persisted"] == []
    assert "step_id must be a non-empty string" in _skip_reasons(outputs)


def test_flat_and_scenario_wrapper_shapes_are_accepted(sandbox):
    sandbox.stage_rows(
        {"step_id": "explore", "scenario": VALID_ROW},
        {"step_id": "explore", **{**VALID_ROW, "id": "flat-shape"}},
    )

    outputs = sandbox.run()

    assert outputs["skipped"] == []
    assert outputs["persisted"][0]["ids"] == ["prefer-readme-scope", "flat-shape"]


# ---------------------------------------------------------------------------
# Duplicate ids
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("split", ["train", "dev", "holdout"])
def test_id_already_in_any_split_is_rejected(sandbox, split):
    bank = sandbox.skills / "explore" / "scenarios" / f"{split}.jsonl"
    bank.write_text(json.dumps(VALID_ROW) + "\n")
    _git(sandbox.repo, "add", "-A")
    _git(sandbox.repo, "commit", "-q", "-m", "seed scenarios")
    sandbox.stage_rows({"step_id": "explore", "row": VALID_ROW})

    outputs = sandbox.run()

    assert outputs["persisted"] == []
    assert "duplicate scenario id" in _skip_reasons(outputs)


def test_duplicate_id_within_one_batch_is_rejected_once(sandbox):
    sandbox.stage_rows(
        {"step_id": "explore", "row": VALID_ROW},
        {"step_id": "explore", "row": {**VALID_ROW, "scenario": "Same id, new text."}},
    )

    outputs = sandbox.run()

    assert outputs["persisted"][0]["ids"] == ["prefer-readme-scope"]
    assert "duplicate scenario id" in _skip_reasons(outputs)
    assert len(sandbox.train().read_text().splitlines()) == 1


# ---------------------------------------------------------------------------
# Target resolution and confinement
# ---------------------------------------------------------------------------

def test_unknown_step_id_is_skipped(sandbox):
    sandbox.stage_rows({"step_id": "not-in-this-workflow", "row": VALID_ROW})

    outputs = sandbox.run()

    assert outputs["persisted"] == []
    assert "no prompt dir in ORCHESTRATOR_PROMPT_DIRS" in _skip_reasons(outputs)


def test_pack_outside_the_prompt_roots_is_refused(sandbox, tmp_path):
    outside = tmp_path / "elsewhere" / "explore"
    (outside / "scenarios").mkdir(parents=True)
    sandbox.prompt_dirs = {"explore": str(outside)}
    sandbox.stage_rows({"step_id": "explore", "row": VALID_ROW})

    outputs = sandbox.run()

    assert outputs["persisted"] == []
    assert "outside the allowed prompt roots" in _skip_reasons(outputs)
    assert not (outside / "scenarios" / "train.jsonl").exists()


# ---------------------------------------------------------------------------
# Git safety
# ---------------------------------------------------------------------------

def test_dirty_target_is_refused_without_appending(sandbox):
    sandbox.train().write_text(json.dumps(VALID_ROW) + "\n")
    _git(sandbox.repo, "add", "-A")
    _git(sandbox.repo, "commit", "-q", "-m", "seed scenarios")
    with sandbox.train().open("a") as f:
        f.write(json.dumps({"id": "wip", "scenario": "in flight", "expect": ["x"]}) + "\n")
    before = sandbox.train().read_text()
    sandbox.stage_rows({"step_id": "explore", "row": {**VALID_ROW, "id": "new-row"}})

    outputs = sandbox.run()

    assert outputs["persisted"] == []
    assert outputs["commits"] == []
    assert "already has uncommitted changes" in _skip_reasons(outputs)
    assert sandbox.train().read_text() == before, "someone else's WIP must be untouched"


def test_commit_leaves_unrelated_changes_uncommitted(sandbox):
    (sandbox.repo / "engine.py").write_text("wip\n")
    _git(sandbox.repo, "add", "engine.py")
    sandbox.stage_rows({"step_id": "explore", "row": VALID_ROW})

    outputs = sandbox.run()

    assert outputs["commits"][0]["committed"] is True
    assert "engine.py" in sandbox.git_status(), (
        "the pathspec commit must not sweep in concurrently staged work"
    )


def test_packs_in_different_repos_commit_separately(tmp_path):
    sandbox = Sandbox(tmp_path, packs=("explore", "design"))
    second = tmp_path / "second"
    pack = second / "skills" / "review"
    (pack / "scenarios").mkdir(parents=True)
    _git(second, "init", "-q")
    _git(second, "config", "user.email", "test@example.com")
    _git(second, "config", "user.name", "Test")
    _git(second, "add", "-A")
    _git(second, "commit", "-q", "--allow-empty", "-m", "init")
    sandbox.prompt_dirs["review"] = str(pack)
    sandbox.stage_rows(
        {"step_id": "explore", "row": VALID_ROW},
        {"step_id": "design", "row": {**VALID_ROW, "id": "design-row"}},
        {"step_id": "review", "row": {**VALID_ROW, "id": "review-row"}},
    )

    outputs = sandbox.run(
        ORCHESTRATOR_PROMPT_PATH=os.pathsep.join(
            [str(sandbox.skills), str(second / "skills")]
        )
    )

    assert outputs["skipped"] == []
    roots = {entry["git_root"] for entry in outputs["commits"]}
    assert roots == {
        _git(sandbox.repo, "rev-parse", "--show-toplevel").stdout.strip(),
        _git(second, "rev-parse", "--show-toplevel").stdout.strip(),
    }
    assert all(entry["committed"] for entry in outputs["commits"])
    # Both packs of the first repo land in one commit for that repo.
    assert len(outputs["commits"]) == 2


def test_pack_outside_any_git_repo_still_appends(tmp_path):
    sandbox = Sandbox(tmp_path)
    loose = tmp_path / "loose"
    pack = loose / "explore"
    (pack / "scenarios").mkdir(parents=True)
    sandbox.prompt_dirs = {"explore": str(pack)}
    sandbox.stage_rows({"step_id": "explore", "row": VALID_ROW})

    outputs = sandbox.run(ORCHESTRATOR_PROMPT_PATH=str(loose))

    assert outputs["persisted"][0]["ids"] == ["prefer-readme-scope"]
    assert outputs["commits"][0]["committed"] is False
    assert "not inside a git repository" in outputs["commits"][0]["reason"]


# ---------------------------------------------------------------------------
# Workflow wiring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "schema", ["feature", "bugfix", "design", "implement", "patch", "autopilot"]
)
def test_persist_learnings_runs_between_learn_and_eval_prompts(schema):
    from orchestrator_next.workflow_steps import step_id_of

    steps = [
        step_id_of(entry)
        for entry in yaml.safe_load((_WORKFLOWS / f"{schema}.yaml").read_text())["steps"]
    ]
    assert steps.index("learn") + 1 == steps.index("persist-learnings"), (
        f"{schema}.yaml: persist-learnings must run immediately after learn"
    )
    assert steps.index("persist-learnings") < steps.index("eval-prompts"), (
        f"{schema}.yaml: scenarios must land before they are evaluated"
    )
