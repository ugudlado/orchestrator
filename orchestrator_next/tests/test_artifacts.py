"""Artifact paths, hashing, and resume idempotency (plan Phase 2.1 / 2.3)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from orchestrator_next import artifacts as art
from orchestrator_next import paths


# ---------------------------------------------------------------------------
# 2.1 — engine-owned run base
# ---------------------------------------------------------------------------
def test_run_dir_is_under_the_repo(tmp_path):
    assert paths.run_dir("abc", tmp_path) == tmp_path / ".orchestrator" / "runs" / "abc"


def test_artifacts_dir_defaults_to_the_engine_owned_location(tmp_path):
    state = {"slug": "abc", "repo_root": str(tmp_path)}
    assert paths.artifacts_dir(state) == (
        tmp_path / ".orchestrator" / "runs" / "abc" / "artifacts"
    )


def test_artifacts_dir_honours_a_recipe_template(tmp_path):
    state = {"slug": "abc", "repo_root": str(tmp_path)}
    assert paths.artifacts_dir(state, "spec/changes/{slug}") == (
        tmp_path / "spec" / "changes" / "abc"
    )


def test_artifacts_dir_is_worktree_aware(tmp_path):
    wt = tmp_path / "wt"
    state = {"slug": "abc", "repo_root": str(tmp_path), "worktree_path": str(wt)}
    assert paths.artifacts_dir(state).is_relative_to(wt)
    assert paths.scratch_dir(state).is_relative_to(wt)


def test_scratch_dir_is_beside_artifacts_not_inside_them(tmp_path):
    state = {"slug": "abc", "repo_root": str(tmp_path)}
    scratch = paths.scratch_dir(state)
    assert scratch == tmp_path / ".orchestrator" / "runs" / "abc" / "scratch"
    assert not scratch.is_relative_to(paths.artifacts_dir(state))


def test_gitignore_helper_adds_the_scratch_line_once(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    assert paths.ensure_scratch_gitignored(tmp_path) is True
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert paths.SCRATCH_GITIGNORE_LINE in text.splitlines()
    # Idempotent: a second call is a no-op.
    assert paths.ensure_scratch_gitignored(tmp_path) is False
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == text


def test_gitignore_helper_creates_the_file_when_absent(tmp_path):
    assert paths.ensure_scratch_gitignored(tmp_path) is True
    assert paths.SCRATCH_GITIGNORE_LINE in (
        tmp_path / ".gitignore"
    ).read_text(encoding="utf-8")


def test_run_id_is_unique_and_sortable():
    ids = [paths.new_run_id() for _ in range(5)]
    assert len(set(ids)) == 5
    assert ids == sorted(ids)  # uuid7 is time-ordered


def test_pack_sha_falls_back_to_hashing_workflow_yaml(tmp_path):
    wf = tmp_path / "workflows"
    wf.mkdir()
    (wf / "feature.yaml").write_text("steps: [a]\n", encoding="utf-8")
    first = paths.pack_sha(tmp_path)
    assert len(first) == 64
    (wf / "feature.yaml").write_text("steps: [a, b]\n", encoding="utf-8")
    assert paths.pack_sha(tmp_path) != first


# ---------------------------------------------------------------------------
# 2.3 — hashing
# ---------------------------------------------------------------------------
def test_sha256_file_matches_hashlib(tmp_path):
    f = tmp_path / "a.md"
    f.write_bytes(b"hello\n")
    assert art.sha256_file(f) == hashlib.sha256(b"hello\n").hexdigest()


def test_collect_records_name_relative_path_and_hash(tmp_path):
    (tmp_path / "design.md").write_text("d\n", encoding="utf-8")
    out = art.collect({"design": {"artifact": "design.md"}}, tmp_path)
    assert out == [{
        "name": "design",
        "path": "design.md",
        "sha256": hashlib.sha256(b"d\n").hexdigest(),
    }]


def test_collect_rejects_a_missing_required_artifact(tmp_path):
    with pytest.raises(art.ArtifactError) as exc:
        art.collect({"design": {"artifact": "design.md"}}, tmp_path)
    assert "design" in str(exc.value)


def test_collect_skips_a_missing_optional_artifact(tmp_path):
    assert art.collect(
        {"ticket": {"artifact": "ticket.md", "optional": True}}, tmp_path
    ) == []


def test_collect_ignores_scalar_outs(tmp_path):
    assert art.collect({"complexity": {"type": "enum", "values": ["S"]}}, tmp_path) == []


def test_collect_honours_a_done_override_path(tmp_path):
    other = tmp_path / "elsewhere.md"
    other.write_text("x\n", encoding="utf-8")
    out = art.collect(
        {"design": {"artifact": "design.md"}}, tmp_path, overrides={"design": str(other)}
    )
    assert out[0]["path"] == "elsewhere.md"


def test_collect_require_false_tolerates_missing_files(tmp_path):
    assert art.collect(
        {"design": {"artifact": "design.md"}}, tmp_path, require=False
    ) == []


def test_run_validate_raises_on_non_zero_exit(tmp_path):
    with pytest.raises(art.ArtifactError) as exc:
        art.run_validate("echo nope >&2; exit 1", tmp_path)
    assert "nope" in str(exc.value)


def test_run_validate_passes_on_zero_exit(tmp_path):
    art.run_validate("true", tmp_path)  # does not raise


# ---------------------------------------------------------------------------
# 2.3 — resume idempotency (pure function)
# ---------------------------------------------------------------------------
def test_unchanged_when_inputs_and_outputs_both_match():
    assert art.is_unchanged({"a": "1"}, {"b": "2"}, {"a": "1"}, {"b": "2"}) is True


def test_changed_when_an_input_hash_moved():
    assert art.is_unchanged({"a": "1"}, {"b": "2"}, {"a": "9"}, {"b": "2"}) is False


def test_changed_when_an_output_hash_moved():
    assert art.is_unchanged({"a": "1"}, {"b": "2"}, {"a": "1"}, {"b": "9"}) is False


def test_changed_when_a_recorded_output_no_longer_exists():
    assert art.is_unchanged({}, {"b": "2"}, {}, {}) is False


def test_never_skips_a_node_with_no_recorded_outputs():
    """A node that predates artifact recording must re-run, not be skipped."""
    assert art.is_unchanged({}, {}, {}, {}) is False


def test_hash_map_ignores_malformed_rows():
    assert art.hash_map([
        {"name": "a", "sha256": "1"}, {"name": "b"}, "junk", None,
    ]) == {"a": "1"}


class _Contract:
    def __init__(self, inputs, outputs):
        self.inputs = inputs
        self.outputs = outputs


def test_node_is_unchanged_end_to_end(tmp_path):
    (tmp_path / "in.md").write_text("i\n", encoding="utf-8")
    (tmp_path / "out.md").write_text("o\n", encoding="utf-8")
    contract = _Contract(
        {"src": {"artifact": "in.md"}}, {"dst": {"artifact": "out.md"}}
    )
    node = {
        "id": "design",
        "input_artifacts": [
            {"name": "src", "sha256": hashlib.sha256(b"i\n").hexdigest()}
        ],
        "artifacts": [
            {"name": "dst", "path": "out.md",
             "sha256": hashlib.sha256(b"o\n").hexdigest()}
        ],
    }
    assert art.node_is_unchanged(node, contract, tmp_path) is True

    # Touching the input invalidates the skip.
    (tmp_path / "in.md").write_text("changed\n", encoding="utf-8")
    assert art.node_is_unchanged(node, contract, tmp_path) is False


def test_relative_to_base_keeps_outside_paths_absolute(tmp_path):
    outside = Path("/etc/hosts")
    assert art.relative_to_base(outside, tmp_path) == str(outside)
