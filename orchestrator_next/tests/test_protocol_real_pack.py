"""The protocol-v2 verbs against the real pack's `feature` recipe.

This is the migration proof for Phase 1.1-1.3: the three contracts that gained
`kind`/`in`/`out` (explore, design, design-review) must dispatch through
`start` / `step` / `done` with their real SKILL.md charters, real models.yaml
aliases, and the real recipe — not a synthetic fixture.

The recipe's leading exec steps touch the host checkout (worktree creation,
ticket fetch), so this test walks the recipe from `explore` onward on a
pre-seeded plan rather than running those scripts in a sandbox. What it proves
is the contract surface, which is what Phase 1.2/1.3 changed.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator_next import protocol
from orchestrator_next.parser import KIND_JUDGMENT

PACK = Path(__file__).resolve().parents[2] / ".orchestrator" / "workflows"
MIGRATED = ("explore", "design", "design-review")

pytestmark = pytest.mark.skipif(
    not (PACK / "steps" / "design" / "contract.yaml").is_file(),
    reason="local workflows pack not vendored in this checkout",
)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "spec" / "changes" / "e2e").mkdir(parents=True)
    (root / "README.md").write_text("repo\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in (["init", "-b", "main"], ["config", "user.email", "t@t.test"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-m", "init"]):
        subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, env=env, check=True)
    return root


@pytest.fixture
def seeded(tmp_path, repo, monkeypatch):
    """A run whose plan holds only the three migrated steps, in recipe order."""
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(PACK))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path / "orchome"))
    monkeypatch.setenv("ORCHESTRATOR_STATE_BACKEND", "file")
    monkeypatch.delenv("ORCHESTRATOR_STATE_URL", raising=False)

    state = tmp_path / "e2e_state.yaml"
    nodes = [
        {"id": "explore", "depends_on": [], "status": "pending"},
        {"id": "design", "depends_on": ["explore"], "status": "pending"},
        {"id": "design-review", "depends_on": ["design"], "status": "pending"},
    ]
    state.write_text(yaml.safe_dump({
        "change_id": "e2e", "slug": "e2e", "schema": "feature",
        "config_pack": "workflows", "status": "active", "phase": "main",
        "repo_root": str(repo), "worktree_path": str(repo),
        "workflow_plan": {"main": {"nodes": nodes, "filtered": []}},
        "step_history": [],
    }, sort_keys=False), encoding="utf-8")
    return str(state)


def _artifacts(repo: Path) -> Path:
    return repo / "spec" / "changes" / "e2e"


def test_recipe_lists_the_migrated_steps_in_order():
    """Guards against the recipe and the migration drifting apart."""
    recipe = yaml.safe_load(
        (PACK / "workflows" / "feature.yaml").read_text(encoding="utf-8"))
    ids = [s if isinstance(s, str) else s.get("id") for s in recipe["steps"]]
    assert ids.index("explore") < ids.index("design") < ids.index("design-review")


@pytest.mark.parametrize("step_id", MIGRATED)
def test_migrated_contracts_declare_protocol_v2_shape(step_id):
    from orchestrator_next.parser import load_contract_for_step

    os.environ["ORCHESTRATOR_CONFIG"] = str(PACK)
    contract = load_contract_for_step(step_id)
    assert contract.kind == KIND_JUDGMENT
    assert contract.outputs, f"{step_id} declares no out:"
    assert contract.tools, f"{step_id} declares no tools:"
    assert contract.max_turns and contract.max_turns > 0


def test_walks_explore_design_design_review(seeded, repo):
    """start/step/done drives the three real steps with real charters."""
    art = _artifacts(repo)
    outs = {
        "explore": {"discovery": "discovery.md"},
        "design": {"design": "design.md", "tasks": "tasks.yaml",
                   "complexity": "M"},
        "design-review": {"design": "design.md", "verdict": "pass"},
    }

    for expected in MIGRATED:
        result, code = protocol.step(seeded)
        assert code == 0
        assert result["status"] == "ready", result
        assert result["kind"] == KIND_JUDGMENT
        assert result["step_id"] == expected, result

        payload = result["payload"]
        # The real SKILL.md charter is in the payload, with the structured
        # output contract replacing the COMPLETION block.
        assert payload["system"].strip()
        assert "COMPLETION:" not in payload["system"].split("---")[-1]
        assert "```json" in payload["system"]
        assert payload["cwd"] == str(repo)
        # Every declared artifact resolves under the run's artifact base —
        # here the pack's own `artifacts_root: spec/changes/{slug}` override.
        for path in payload["out"].values():
            assert path.startswith(str(art))
        # Phase 2.2: the charter's {in.x} / {out.y} are already resolved to
        # absolute paths — the agent never sees a placeholder.
        assert "{in." not in payload["system"]
        assert "{out." not in payload["system"]
        for name, path in payload["out"].items():
            if name in ("discovery", "design", "tasks"):
                assert path in payload["system"], name

        # Write whatever artifacts this step declared, then report them.
        for name, spec in outs[expected].items():
            if str(spec).endswith((".md", ".yaml")):
                (art / str(spec)).write_text(f"{expected}:{name}\n",
                                             encoding="utf-8")

        done_result, done_code = protocol.done(
            seeded, expected, out=outs[expected],
            usage={"input_tokens": 500, "output_tokens": 120,
                   "model": "claude-sonnet-5"},
        )
        assert done_code == 0
        assert done_result["status"] == "ok"

    final, _ = protocol.step(seeded)
    assert final["status"] == "done"

    status, _ = protocol.status(seeded)
    done_ids = {n["id"] for n in status["nodes"] if n["status"] == "completed"}
    assert set(MIGRATED) <= done_ids
    assert status["usage"]["input_tokens"] == 1500

    # Phase 2.3: every artifact the real steps wrote is recorded by hash,
    # against the override base rather than the engine default.
    assert status["artifacts_base"] == str(art)
    recorded = {(a["step_id"], a["name"], a["path"]) for a in status["artifacts"]}
    assert ("explore", "discovery", "discovery.md") in recorded
    assert ("design", "tasks", "tasks.yaml") in recorded
    # A hash is what that node saw when it completed, not a live checksum:
    # design-review rewrote design.md after design recorded it, so the two
    # design.md rows legitimately differ.
    by_step = {(a["step_id"], a["name"]): a["sha256"] for a in status["artifacts"]}
    assert by_step[("design", "design")] != by_step[("design-review", "design")]
    assert by_step[("design-review", "design")] == hashlib.sha256(
        (art / "design.md").read_bytes()).hexdigest()
    assert by_step[("explore", "discovery")] == hashlib.sha256(
        (art / "discovery.md").read_bytes()).hexdigest()


def test_done_rejects_a_design_that_never_wrote_tasks_yaml(seeded, repo):
    """The out: block is enforced against the real design contract."""
    art = _artifacts(repo)
    (art / "discovery.md").write_text("d\n", encoding="utf-8")
    protocol.step(seeded)
    protocol.done(seeded, "explore", out={"discovery": "discovery.md"},
                  usage={"input_tokens": 10, "output_tokens": 5})

    protocol.step(seeded)
    (art / "design.md").write_text("design\n", encoding="utf-8")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.done(seeded, "design",
                      out={"design": "design.md", "complexity": "M"},
                      usage={"input_tokens": 10, "output_tokens": 5})
    assert "tasks" in str(exc.value)
