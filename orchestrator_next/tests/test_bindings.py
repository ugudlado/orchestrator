"""Team tool bindings: roles in contracts, layered files, payload and env."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from orchestrator_next.nextstep import NextError, next_step

LINEAR = {"provider": "linear", "via": "mcp", "server": "linear"}
GITHUB = {"provider": "github", "via": "cli", "command": "gh"}


def _bindings(path: Path, roles: dict) -> Path:
    path.write_text(yaml.safe_dump({"roles": roles}), encoding="utf-8")
    return path


def _pack(tmp_path: Path, tools: list[str], pack_bindings: dict | None = None) -> Path:
    root = tmp_path / "pack"
    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "w.yaml").write_text(
        yaml.safe_dump({"name": "w", "steps": ["work", {"gate": "ok", "approve_as": "t"}]}), encoding="utf-8"
    )
    step = root / "steps" / "work"
    step.mkdir(parents=True)
    contract = {"id": "work", "kind": "exec", "run": "script.sh", "tools": tools}
    (step / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    (step / "script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    if pack_bindings is not None:
        _bindings(root / "bindings.yaml", pack_bindings)
    return root


def _first(pack: Path, *files: Path) -> dict:
    return next_step("w", config_root=pack, slug="s", bindings_files=list(files))


def test_pack_default_used(tmp_path):
    pack = _pack(tmp_path, ["ticket.read"], {"ticket": LINEAR})
    assert _first(pack)["payload"]["bindings"] == {"ticket": LINEAR}


def test_flag_overrides_wholesale(tmp_path):
    pack = _pack(tmp_path, ["ticket.read"], {"ticket": LINEAR})
    team = _bindings(tmp_path / "team.yaml", {"ticket": {"provider": "jira", "via": "cli"}})
    # No field merging: `server` from the pack layer must not survive.
    assert _first(pack, team)["payload"]["bindings"] == {"ticket": {"provider": "jira", "via": "cli"}}


def test_later_flag_wins(tmp_path):
    pack = _pack(tmp_path, ["forge.pr"])
    a = _bindings(tmp_path / "a.yaml", {"forge": GITHUB})
    b = _bindings(tmp_path / "b.yaml", {"forge": {"provider": "gitlab", "via": "cli", "command": "glab"}})
    assert _first(pack, a, b)["payload"]["bindings"]["forge"]["provider"] == "gitlab"
    assert _first(pack, b, a)["payload"]["bindings"]["forge"]["provider"] == "github"


def test_only_used_roles_emitted(tmp_path):
    pack = _pack(tmp_path, ["ticket.read", "ticket.write", "fs.read"], {"ticket": LINEAR, "forge": GITHUB})
    assert _first(pack)["payload"]["bindings"] == {"ticket": LINEAR}


def test_unbound_role_is_none(tmp_path):
    pack = _pack(tmp_path, ["chat.post"])
    payload = _first(pack)["payload"]
    assert payload["bindings"] == {"chat": None}
    assert json.loads(payload["env"]["ORCHESTRATOR_BINDINGS"]) == {"chat": None}


def test_env_present_and_absent(tmp_path):
    bound = _first(_pack(tmp_path, ["forge.read"], {"forge": GITHUB}))["payload"]
    assert json.loads(bound["env"]["ORCHESTRATOR_BINDINGS"]) == {"forge": GITHUB}
    assert " " not in bound["env"]["ORCHESTRATOR_BINDINGS"]  # compact
    plain = _first(_pack(tmp_path / "x", ["fs.read"], {"forge": GITHUB}))["payload"]
    assert plain["bindings"] == {}
    assert "ORCHESTRATOR_BINDINGS" not in plain["env"]


@pytest.mark.parametrize(
    "roles",
    [
        {"ticket": {"provider": "linear", "via": "carrier-pigeon"}},
        {"ticket": {"provider": "linear"}},
        {"ticket": {"via": "mcp"}},
        {"ticket": {"provider": "linear", "via": "mcp", "port": 80}},
        {"ticket": "linear"},
        {"wiki": {"provider": "x", "via": "mcp"}},
    ],
)
def test_invalid_binding_is_error(tmp_path, roles):
    pack = _pack(tmp_path, ["ticket.read"])
    with pytest.raises(NextError, match="bindings file"):
        _first(pack, _bindings(tmp_path / "bad.yaml", roles))


def test_invalid_pack_file_and_shape_are_errors(tmp_path):
    pack = _pack(tmp_path, ["ticket.read"])
    (pack / "bindings.yaml").write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(NextError, match="roles"):
        _first(pack)


def test_missing_flag_file_is_error(tmp_path):
    pack = _pack(tmp_path, ["ticket.read"])
    with pytest.raises(NextError, match="nope.yaml"):
        _first(pack, tmp_path / "nope.yaml")


def test_gate_payload_has_no_bindings(tmp_path):
    pack = _pack(tmp_path, ["ticket.read"], {"ticket": LINEAR})
    result = next_step("w", config_root=pack, slug="s", after="work", status="completed")
    assert result["kind"] == "gate"
    assert "bindings" not in result["payload"]


def test_cli_flag_repeatable_and_missing_exits_3(tmp_path):
    pack = _pack(tmp_path, ["ticket.read"])
    a = _bindings(tmp_path / "a.yaml", {"ticket": LINEAR})
    b = _bindings(tmp_path / "b.yaml", {"ticket": {"provider": "jira", "via": "cli"}})

    def run(*extra):
        return subprocess.run(
            [sys.executable, "-m", "orchestrator_next", "next", "w", "--config", str(pack), "--slug", "s", *extra],
            capture_output=True,
            text=True,
            check=False,
        )

    ok = run("--bindings", str(a), "--bindings", str(b))
    assert ok.returncode == 0
    assert json.loads(ok.stdout)["payload"]["bindings"]["ticket"]["provider"] == "jira"
    bad = run("--bindings", str(tmp_path / "nope.yaml"))
    assert bad.returncode == 3
    assert json.loads(bad.stdout)["status"] == "error"
