"""T-3: Tests for `_read_state_env.sh` flag resolvers (RED before T-4).

`complete-workflow.sh` reads `flags.merge_to_main` and `flags.worktree` from
state.yaml via the `_read_state_env.sh` allowlist. T-4 adds two resolvers,
`MERGE_TO_MAIN` and `WORKTREE`, to the embedded-Python RESOLVERS map.

These tests prove:
  - MERGE_TO_MAIN resolves to flags.merge_to_main (true / false)
  - WORKTREE resolves to flags.worktree
  - an absent `flags:` map yields empty strings, no crash
  - an unknown var name still exits non-zero (allowlist intact — regression)
"""
from __future__ import annotations

import os
import subprocess

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
_HELPER = os.path.join(_REPO_ROOT, "config", "scripts", "inline",
                       "_read_state_env.sh")


def _resolve(state_path, *vars_):
    """Source _read_state_env.sh, call read_state_env, echo the resulting vars.

    The function's own return code is captured into RSE_RC and echoed, since a
    later `echo` would otherwise mask it. Returns (rse_rc, {var: value}).
    """
    echo = "; ".join(f'echo "{v}=[${{{v}:-}}]"' for v in vars_)
    script = (
        f'source "{_HELPER}"\n'
        f'read_state_env "{state_path}" {" ".join(vars_)}; RSE_RC=$?\n'
        f"{echo}\n"
        'echo "RSE_RC=[$RSE_RC]"\n'
    )
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True
    )
    values = {}
    for line in proc.stdout.splitlines():
        if "=[" in line and line.endswith("]"):
            k, v = line.split("=[", 1)
            values[k] = v[:-1]
    rse_rc = int(values.pop("RSE_RC", "0") or "0")
    return rse_rc, values


def _write_state(tmp_path, state):
    p = tmp_path / "state.yaml"
    p.write_text(yaml.safe_dump(state, sort_keys=False))
    return str(p)


def test_merge_to_main_resolves_true(tmp_path):
    state = _write_state(tmp_path, {
        "change_id": "x",
        "flags": {"merge_to_main": True, "worktree": True},
    })
    rc, vals = _resolve(state, "MERGE_TO_MAIN")
    assert rc == 0, "read_state_env exited non-zero for MERGE_TO_MAIN"
    assert vals.get("MERGE_TO_MAIN") == "True", (
        f"MERGE_TO_MAIN should resolve to flags.merge_to_main, got {vals!r}"
    )


def test_merge_to_main_resolves_false(tmp_path):
    state = _write_state(tmp_path, {
        "change_id": "x",
        "flags": {"merge_to_main": False, "worktree": True},
    })
    rc, vals = _resolve(state, "MERGE_TO_MAIN")
    assert rc == 0
    assert vals.get("MERGE_TO_MAIN") == "False", (
        f"MERGE_TO_MAIN should resolve to False, got {vals!r}"
    )


def test_worktree_resolves(tmp_path):
    state = _write_state(tmp_path, {
        "change_id": "x",
        "flags": {"merge_to_main": False, "worktree": False},
    })
    rc, vals = _resolve(state, "WORKTREE")
    assert rc == 0
    assert vals.get("WORKTREE") == "False", (
        f"WORKTREE should resolve to flags.worktree, got {vals!r}"
    )


def test_absent_flags_map_yields_empty(tmp_path):
    """A state.yaml with no `flags:` map → resolvers yield empty strings,
    no crash, exit 0."""
    state = _write_state(tmp_path, {"change_id": "x"})
    rc, vals = _resolve(state, "MERGE_TO_MAIN", "WORKTREE")
    assert rc == 0, "read_state_env crashed on a state.yaml with no flags map"
    assert vals.get("MERGE_TO_MAIN") == "", (
        f"absent flags → MERGE_TO_MAIN should be empty, got {vals!r}"
    )
    assert vals.get("WORKTREE") == "", (
        f"absent flags → WORKTREE should be empty, got {vals!r}"
    )


def test_unknown_var_still_exits_nonzero(tmp_path):
    """The allowlist must stay closed — an unknown var name exits non-zero."""
    state = _write_state(tmp_path, {"change_id": "x", "flags": {}})
    rc, _vals = _resolve(state, "TOTALLY_UNKNOWN_VAR")
    assert rc != 0, "unknown var name should exit non-zero (allowlist breach)"


def test_existing_resolvers_still_work(tmp_path):
    """Regression: adding flag resolvers must not break CHANGE_ID / BRANCH."""
    state = _write_state(tmp_path, {
        "change_id": "orc-test", "branch": "feature/orc-test",
        "flags": {"merge_to_main": True, "worktree": True},
    })
    rc, vals = _resolve(state, "CHANGE_ID", "BRANCH")
    assert rc == 0
    assert vals.get("CHANGE_ID") == "orc-test"
    assert vals.get("BRANCH") == "feature/orc-test"
