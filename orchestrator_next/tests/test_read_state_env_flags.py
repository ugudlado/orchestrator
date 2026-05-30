"""Tests for `_read_state_env.sh` resolvers.

ORC-108 removed the `MERGE_TO_MAIN` resolver: merge_to_main is now a workflow
property read by the complete-phase wrapper as a top-level state fact, not a
flag pulled through this allowlist. (ORC-108 earlier removed the `WORKTREE`
resolver too: worktree is unconditional, keyed off `worktree_path` presence.)

These tests prove:
  - MERGE_TO_MAIN is no longer an allowed var (resolver deleted)
  - the allowlist stays closed — an unknown var name exits non-zero
  - the surviving resolvers (CHANGE_ID, BRANCH) still work
"""
from __future__ import annotations

import os
import subprocess

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_HELPER = os.path.join(_REPO_ROOT, "orchestrator_next", "scripts", "lib", "read-state-env.sh")


def _resolve(state_path, *vars_):
    """Source read-state-env.sh, call read_state_env, echo the resulting vars.

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


def test_merge_to_main_resolver_removed(tmp_path):
    """ORC-108: MERGE_TO_MAIN is no longer in the allowlist → exits non-zero."""
    state = _write_state(tmp_path, {"change_id": "x", "merge_to_main": True})
    rc, _vals = _resolve(state, "MERGE_TO_MAIN")
    assert rc != 0, "MERGE_TO_MAIN resolver must be removed (it's a top-level state fact now)"


def test_unknown_var_still_exits_nonzero(tmp_path):
    """The allowlist must stay closed — an unknown var name exits non-zero."""
    state = _write_state(tmp_path, {"change_id": "x"})
    rc, _vals = _resolve(state, "TOTALLY_UNKNOWN_VAR")
    assert rc != 0, "unknown var name should exit non-zero (allowlist breach)"


def test_existing_resolvers_still_work(tmp_path):
    """Regression: removing the merge resolver must not break CHANGE_ID / BRANCH."""
    state = _write_state(tmp_path, {
        "change_id": "orc-test", "branch": "feature/orc-test",
    })
    rc, vals = _resolve(state, "CHANGE_ID", "BRANCH")
    assert rc == 0
    assert vals.get("CHANGE_ID") == "orc-test"
    assert vals.get("BRANCH") == "feature/orc-test"
