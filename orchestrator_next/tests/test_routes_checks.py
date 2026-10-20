"""Conditional routes, the skip fingerprint and declarative output checks.

Every pack here is built under tmp_path; nothing depends on a vendored pack.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from orchestrator_next.nextstep import NextError, next_step, step_entries
from orchestrator_next.parser import ContractError, load_contract_for_step


def _write_pack(root: Path, steps: list, contracts: dict[str, dict]) -> Path:
    """A pack whose steps are exec by default; ``contracts`` override per id."""
    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "w.yaml").write_text(
        yaml.safe_dump({"artifacts_root": "art", "steps": steps}), encoding="utf-8"
    )
    for entry in steps:
        sid = entry if isinstance(entry, str) else entry.get("id")
        if not sid:
            continue
        d = root / "steps" / sid
        d.mkdir(parents=True)
        contract = contracts.get(sid, {"kind": "exec", "run": "script.sh"})
        (d / "contract.yaml").write_text(yaml.safe_dump({"id": sid, **contract}), encoding="utf-8")
        if "run" in contract:
            (d / "script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        else:
            (d / "SKILL.md").write_text("# charter\n", encoding="utf-8")
    return root


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


def _next(pack, **kw):
    return next_step("w", config_root=pack, slug="s", **kw)


ROUTES = [
    {"when": {"severity": ["low", "trivial"]}, "goto": "quick"},
    {"when": {"severity": "high", "area": "billing"}, "goto": "escalate"},
    {"when": {"severity": "high"}, "goto": "later"},
]


@pytest.fixture
def branching(tmp_path, repo) -> Path:
    steps = [{"id": "triage", "routes": ROUTES}, "quick", "escalate", "later"]
    return _write_pack(tmp_path / "pack", steps, {})


# --------------------------------------------------------------- routes
def test_list_value_matches_any_item(branching):
    r = _next(branching, after="triage", status="completed", out={"severity": "trivial"})
    assert (r["step_id"], r["route"]) == ("quick", "branch")


def test_scalars_must_all_match(branching):
    r = _next(branching, after="triage", status="completed", out={"severity": "high", "area": "billing"})
    assert (r["step_id"], r["route"]) == ("escalate", "branch")
    r = _next(branching, after="triage", status="completed", out={"severity": "high", "area": "ui"})
    assert r["step_id"] == "later"


def test_first_matching_case_wins(branching):
    r = _next(branching, after="triage", status="completed", out={"severity": "high", "area": "billing"})
    assert r["step_id"] == "escalate", "the third case matches too, but comes later"


def test_no_match_falls_through_to_next(branching):
    r = _next(branching, after="triage", status="completed", out={"severity": "medium"})
    assert (r["step_id"], r["route"]) == ("quick", "next")


def test_absent_key_never_matches(branching):
    r = _next(branching, after="triage", status="completed", out={"area": "billing"})
    assert r["route"] == "next"


def test_exec_stdout_outputs_are_matched(tmp_path, repo):
    pack = _write_pack(tmp_path / "pack", [{"id": "a", "routes": [{"when": {"k": 1}, "goto": "c"}]}, "b", "c"], {})
    stdout = repo / "out.txt"
    stdout.write_text('{"k": 1}\n', encoding="utf-8")
    r = _next(pack, after="a", exit_code=0, stdout_file=str(stdout))
    assert (r["step_id"], r["route"]) == ("c", "branch")


def test_no_match_at_last_step_is_done(tmp_path, repo):
    pack = _write_pack(tmp_path / "pack", ["a", {"id": "z", "routes": [{"when": {"k": "v"}, "goto": "a"}]}], {})
    assert _next(pack, after="z", status="completed", out={"k": "other"})["status"] == "done"


def test_fail_on_wins_over_routes(tmp_path, repo):
    contract = {
        "kind": "judgment",
        "prompt": "SKILL.md",
        "out": {"verdict": {"type": "enum", "values": ["pass", "needs_work"], "fail_on": ["needs_work"]}},
    }
    steps = ["a", {"id": "j", "on_failure": "a", "routes": [{"when": {"verdict": "needs_work"}, "goto": "b"}]}, "b"]
    pack = _write_pack(tmp_path / "pack", steps, {"j": contract})
    r = _next(pack, after="j", status="completed", out={"verdict": "needs_work"})
    assert (r["step_id"], r["route"]) == ("a", "on_failure")


def test_failed_status_ignores_routes(tmp_path, repo):
    pack = _write_pack(tmp_path / "pack", [{"id": "a", "routes": [{"when": {"k": 1}, "goto": "b"}]}, "b"], {})
    r = _next(pack, after="a", status="failed", out={"k": 1})
    assert r["status"] == "needs_you"


def test_backward_goto_is_capped_by_max_retries(tmp_path, repo):
    steps = ["a", {"id": "b", "max_retries": 2, "routes": [{"when": {"again": True}, "goto": "a"}]}]
    pack = _write_pack(tmp_path / "pack", steps, {})
    r = _next(pack, after="b", status="completed", out={"again": True}, attempt=1)
    assert (r["step_id"], r["route"]) == ("a", "branch")
    r = _next(pack, after="b", status="completed", out={"again": True}, attempt=2)
    assert (r["status"], r["reason"]) == ("needs_you", "retries exhausted")


def test_self_goto_is_a_loop_and_forward_goto_is_not_capped(tmp_path, repo):
    steps = [
        {"id": "a", "routes": [{"when": {"k": 1}, "goto": "a"}, {"when": {"k": 2}, "goto": "c"}]},
        "b",
        "c",
    ]
    pack = _write_pack(tmp_path / "pack", steps, {})
    assert _next(pack, after="a", status="completed", out={"k": 1}, attempt=3)["status"] == "needs_you"
    assert _next(pack, after="a", status="completed", out={"k": 2}, attempt=99)["step_id"] == "c"


def test_default_matches_when_nothing_else_did(tmp_path, repo):
    pack = _write_pack(tmp_path / "pack", [{"id": "a", "routes": [{"goto": "c"}]}, "b", "c"], {})
    r = _next(pack, after="a", status="completed", out={"k": "anything"})
    assert (r["step_id"], r["route"]) == ("c", "branch")
    assert _next(pack, after="a", status="completed")["step_id"] == "c"


def test_default_after_a_non_matching_case(tmp_path, repo):
    routes = [{"when": {"k": 1}, "goto": "b"}, {"goto": "c"}]
    pack = _write_pack(tmp_path / "pack", [{"id": "a", "routes": routes}, "b", "c"], {})
    assert _next(pack, after="a", status="completed", out={"k": 1})["step_id"] == "b"
    r = _next(pack, after="a", status="completed", out={"k": 2})
    assert (r["step_id"], r["route"]) == ("c", "branch")


def test_gate_default_route_branches(tmp_path, repo):
    pack = _write_pack(tmp_path / "pack", [{"gate": "g", "routes": [{"goto": "c"}]}, "b", "c"], {})
    r = _next(pack, after="g", status="completed")
    assert (r["step_id"], r["route"]) == ("c", "branch")


def test_backward_default_goto_is_capped_by_max_retries(tmp_path, repo):
    pack = _write_pack(tmp_path / "pack", ["a", {"id": "b", "max_retries": 2, "routes": [{"goto": "a"}]}], {})
    assert _next(pack, after="b", status="completed", attempt=1)["step_id"] == "a"
    r = _next(pack, after="b", status="completed", attempt=2)
    assert (r["status"], r["reason"]) == ("needs_you", "retries exhausted")


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"id": "a", "routes": "b"}, "must be a list"),
        ({"id": "a", "routes": ["b"]}, "must be a mapping"),
        ({"id": "a", "routes": [{"when": {}, "goto": "b"}]}, "when must be a non-empty mapping"),
        ({"id": "a", "routes": [{"when": {"k": 1}}]}, "goto must be a step id"),
        ({"id": "a", "routes": [{"when": {"k": 1}, "goto": 3}]}, "goto must be a step id"),
        ({"id": "a", "routes": [{"when": {"k": 1}, "goto": "nope"}]}, "'nope' is not a step"),
        ({"id": "a", "on_failure": "nope"}, "on_failure 'nope' is not a step"),
        ({"gate": "a", "routes": [{"when": {"k": 1}, "goto": "b"}]}, "gate has no outputs"),
        ({"id": "a", "routes": [{"goto": "b"}, {"when": {"k": 1}, "goto": "b"}]}, "must be the last case"),
        ({"id": "a", "routes": [{"goto": "b"}, {"goto": "b"}]}, "must be the last case"),
        ({"id": "a", "routes": [{"when": {"k": 1}, "goto": "b"}, {"goto": "nope"}]}, "'nope' is not a step"),
        ({"id": "a", "routes": [{"when": None, "goto": "b"}]}, "when must be a non-empty mapping"),
        ({"id": "a", "routes": [{"when": "k", "goto": "b"}]}, "when must be a non-empty mapping"),
    ],
)
def test_bad_edges_are_next_errors(entry, message):
    with pytest.raises(NextError, match=message):
        step_entries({"steps": [entry, "b"]})


# --------------------------------------------------------------- fingerprint
@pytest.fixture
def fp_pack(tmp_path, repo) -> Path:
    contracts = {
        "work": {
            "kind": "judgment",
            "prompt": "SKILL.md",
            "in": {"spec": {"artifact": "spec.md"}},
            "skip_when_unchanged": True,
        },
        "push": {"kind": "exec", "run": "script.sh", "side_effects": ["git.push"]},
        "mutate": {"kind": "exec", "run": "script.sh", "state_mutating": True},
        "plain": {"kind": "exec", "run": "script.sh", "skip_when_unchanged": True},
        "quiet": {"kind": "exec", "run": "script.sh"},
    }
    return _write_pack(tmp_path / "pack", ["work", "push", "mutate", "plain", "quiet"], contracts)


def _fp(pack, step):
    return next_step("w", config_root=pack, slug="s", after=_prev(step), status="completed")["payload"]["fingerprint"]


def _prev(step):
    return {"work": "", "push": "work", "mutate": "push", "plain": "mutate", "quiet": "plain"}[step]


def _work_fp(pack):
    return next_step("w", config_root=pack, slug="s")["payload"]["fingerprint"]


def test_fingerprint_is_a_stable_hex_digest(fp_pack):
    first = _work_fp(fp_pack)
    assert len(first) == 64 and int(first, 16) >= 0
    assert _work_fp(fp_pack) == first


def test_fingerprint_changes_with_input_file(fp_pack, repo):
    missing = _work_fp(fp_pack)
    spec = repo / "art" / "spec.md"
    spec.parent.mkdir()
    spec.write_text("one", encoding="utf-8")
    one = _work_fp(fp_pack)
    spec.write_text("two", encoding="utf-8")
    assert len({missing, one, _work_fp(fp_pack)}) == 3


def test_fingerprint_changes_with_charter(fp_pack):
    before = _work_fp(fp_pack)
    (fp_pack / "steps" / "work" / "SKILL.md").write_text("# edited\n", encoding="utf-8")
    assert _work_fp(fp_pack) != before


def test_fingerprint_is_none_unless_opted_in(fp_pack):
    assert len(_fp(fp_pack, "plain")) == 64
    assert _fp(fp_pack, "quiet") is None, "not opted in"
    assert _fp(fp_pack, "push") is None
    assert _fp(fp_pack, "mutate") is None


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"side_effects": ["git.push"]}, "cannot be combined"),
        ({"state_mutating": True}, "cannot be combined"),
        ({"kind": "gate"}, "cannot be combined"),
        ({"skip_when_unchanged": "yes"}, "must be true or false"),
    ],
)
def test_parser_rejects_bad_skip_when_unchanged(tmp_path, extra, message):
    contract = {"kind": "exec", "run": "script.sh", "skip_when_unchanged": True, **extra}
    pack = _write_pack(tmp_path / "pack", ["a"], {"a": contract})
    with pytest.raises(ContractError, match=message):
        load_contract_for_step("a", pack)


# --------------------------------------------------------------- checks
def _checked_pack(tmp_path, checks: dict) -> Path:
    out = {"doc": {"artifact": "doc.md", "checks": checks}}
    contract = {"kind": "judgment", "prompt": "SKILL.md", "out": out}
    return _write_pack(tmp_path / "pack", [{"id": "a"}, "b"], {"a": contract})


def _report(pack, repo, body: str | None, **kw):
    if body is not None:
        (repo / "art").mkdir(exist_ok=True)
        (repo / "art" / "doc.md").write_text(body, encoding="utf-8")
    return _next(pack, after="a", status="completed", out={}, **kw)


@pytest.mark.parametrize(
    "checks, good, bad, fragment",
    [
        ({"min_bytes": 5}, "12345", "1234", "4 bytes, need at least 5"),
        ({"contains": ["alpha", "beta"]}, "alpha and beta", "alpha only", "does not contain 'beta'"),
        ({"parses": "json"}, '{"a": 1}', "{nope", "does not parse as json"),
        ({"parses": "yaml"}, "a: [1, 2]", "a: [1", "does not parse as yaml"),
    ],
)
def test_each_check_passes_and_fails(tmp_path, repo, checks, good, bad, fragment):
    pack = _checked_pack(tmp_path, checks)
    assert _report(pack, repo, good)["status"] == "ready"
    r = _report(pack, repo, bad)
    assert r["status"] == "error"
    assert r["error"].startswith("invalid out: out.doc:") and fragment in r["error"]


def test_all_failing_checks_are_reported_together(tmp_path, repo):
    pack = _checked_pack(tmp_path, {"min_bytes": 50, "contains": ["x"], "parses": "json"})
    error = _report(pack, repo, "{")["error"]
    assert "bytes" in error and "does not contain" in error and "does not parse" in error


def test_optional_unreported_artifact_skips_checks(tmp_path, repo):
    out = {"doc": {"artifact": "doc.md", "optional": True, "checks": {"min_bytes": 10}}}
    pack = _write_pack(tmp_path / "pack", ["a", "b"], {"a": {"kind": "judgment", "prompt": "SKILL.md", "out": out}})
    assert _next(pack, after="a", status="completed", out={})["status"] == "ready"


@pytest.mark.parametrize(
    "spec, message",
    [
        ({"artifact": "d.md", "checks": "big"}, "must be a mapping"),
        ({"artifact": "d.md", "checks": {"max_bytes": 1}}, "unknown keys \\['max_bytes'\\]"),
        ({"artifact": "d.md", "checks": {"min_bytes": "9"}}, "min_bytes must be a non-negative integer"),
        ({"artifact": "d.md", "checks": {"min_bytes": True}}, "min_bytes must be a non-negative integer"),
        ({"artifact": "d.md", "checks": {"contains": "x"}}, "contains must be a list of strings"),
        ({"artifact": "d.md", "checks": {"contains": [1]}}, "contains must be a list of strings"),
        ({"artifact": "d.md", "checks": {"parses": "toml"}}, "parses must be yaml or json"),
        ({"type": "enum", "values": ["a"], "checks": {"min_bytes": 1}}, "only valid on an artifact"),
    ],
)
def test_parser_rejects_bad_checks(tmp_path, spec, message):
    pack = _write_pack(
        tmp_path / "pack", ["a"], {"a": {"kind": "judgment", "prompt": "SKILL.md", "out": {"doc": spec}}}
    )
    with pytest.raises(ContractError, match=message):
        load_contract_for_step("a", pack)


def test_parser_rejects_checks_under_in(tmp_path):
    spec = {"artifact": "d.md", "checks": {"min_bytes": 1}}
    pack = _write_pack(tmp_path / "pack", ["a"], {"a": {"kind": "judgment", "prompt": "SKILL.md", "in": {"doc": spec}}})
    with pytest.raises(ContractError, match="only valid on an artifact under out"):
        load_contract_for_step("a", pack)
