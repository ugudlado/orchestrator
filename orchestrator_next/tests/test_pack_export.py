"""Tests for the pack -> plugin generator (docs/protocol-v2.md §4, Phase 4.3).

Builds a tiny fake pack (2 judgment steps, 1 exec step, 1 gate step) and
asserts the generated tree, agent frontmatter, tool mapping, and stale-file
removal on regenerate. One additional test runs against the real local pack
at .orchestrator/workflows/ when present (skipped otherwise).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from orchestrator_next import pack_export

REAL_PACK = Path(__file__).resolve().parents[2] / ".orchestrator" / "workflows"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _write_yaml(path: Path, data: dict) -> None:
    _write(path, yaml.safe_dump(data, sort_keys=False))


@pytest.fixture
def fake_pack(tmp_path: Path) -> Path:
    pack_root = tmp_path / "pack"

    _write_yaml(
        pack_root / "pack.yaml",
        {"name": "demo", "version": "1.2.3", "description": "Demo pack for tests", "protocol": 2},
    )
    _write_yaml(
        pack_root / "models.yaml",
        {
            "models": {"strong": {"model_id": "x", "tool": "claude"}},
            "step_models": {"design": "strong", "review": "standard"},
        },
    )

    # judgment step 1: design (strong -> opus), full tool set
    _write_yaml(
        pack_root / "steps" / "design" / "contract.yaml",
        {
            "id": "design",
            "version": 1,
            "kind": "judgment",
            "max_turns": 10,
            "tools": ["fs.read", "fs.write", "fs.list", "shell.run", "git.write", "made.up"],
            "in": {"discovery": {"artifact": "discovery.md"}},
            "out": {"design": {"artifact": "design.md"}, "complexity": {"type": "enum", "values": ["S", "M"]}},
        },
    )
    _write(
        pack_root / "steps" / "design" / "SKILL.md",
        "---\nname: architect\ndescription: unused\n---\n\n# Design the thing\n\nBody text.\n",
    )

    # judgment step 2: review (standard -> sonnet), minimal tools
    _write_yaml(
        pack_root / "steps" / "review" / "contract.yaml",
        {
            "id": "review",
            "version": 1,
            "kind": "judgment",
            "tools": ["fs.read"],
            "out": {"verdict": {"type": "enum", "values": ["pass", "fail"]}},
        },
    )
    _write(pack_root / "steps" / "review" / "SKILL.md", "Review the design.\n\nMore detail.\n")

    # exec step: no agent file should be generated for this
    _write_yaml(
        pack_root / "steps" / "build" / "contract.yaml",
        {"id": "build", "version": 1, "run": "script.sh"},
    )
    _write(pack_root / "steps" / "build" / "script.sh", "#!/bin/sh\necho '{}'\n")

    # gate step: no agent file, no SKILL.md
    _write_yaml(
        pack_root / "steps" / "signoff" / "contract.yaml",
        {"id": "signoff", "version": 1, "kind": "gate", "show": ["design"], "approve_as": "impl_token"},
    )

    _write_yaml(pack_root / "workflows" / "feature.yaml", {"name": "feature", "steps": ["design", "review", "build", "signoff"]})

    return pack_root


def test_load_pack_steps_classifies_kinds(fake_pack: Path) -> None:
    steps = {s.step_id: s for s in pack_export.load_pack_steps(fake_pack)}
    assert steps["design"].kind == "judgment"
    assert steps["review"].kind == "judgment"
    assert steps["build"].kind == "exec"
    assert steps["signoff"].kind == "gate"
    assert steps["design"].alias == "strong"
    assert steps["review"].alias == "standard"
    assert steps["build"].alias is None
    assert steps["design"].description == "Design the thing"


def test_generate_claude_tree_and_frontmatter(fake_pack: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out-claude"
    files, warnings = pack_export.generate_claude(fake_pack, out_dir)

    assert (out_dir / ".claude-plugin" / "plugin.json").is_file()
    manifest = json.loads((out_dir / pack_export.MANIFEST_NAME).read_text())
    assert set(manifest["files"]) == set(files)

    plugin_json = json.loads((out_dir / ".claude-plugin" / "plugin.json").read_text())
    assert plugin_json["name"] == "orchestrator-demo"
    assert plugin_json["version"] == "1.2.3"

    # judgment steps get agents; exec/gate do not.
    assert (out_dir / "agents" / "design.md").is_file()
    assert (out_dir / "agents" / "review.md").is_file()
    assert not (out_dir / "agents" / "build.md").exists()
    assert not (out_dir / "agents" / "signoff.md").exists()

    design_md = (out_dir / "agents" / "design.md").read_text()
    assert "name: design" in design_md
    assert "model: opus" in design_md  # strong -> opus
    assert "Design the thing" in design_md
    assert "## Output contract" in design_md
    assert "made.up" not in design_md  # unknown tool capability dropped
    assert '"Read"' in design_md or "Read" in design_md

    review_md = (out_dir / "agents" / "review.md").read_text()
    assert "model: sonnet" in review_md  # standard -> sonnet

    # unknown tool capability produced a warning, not a crash.
    assert any("made.up" in w for w in warnings)

    assert (out_dir / "skills" / "orchestrate" / "SKILL.md").is_file()
    assert (out_dir / "hooks" / "hooks.json").is_file()
    assert (out_dir / "hooks" / "register.ts").is_file()
    register_ts = (out_dir / "hooks" / "register.ts").read_text()
    assert "export const register" in register_ts
    assert "TODO" in register_ts
    assert (out_dir / "README.md").is_file()


def test_generate_codex_tree(fake_pack: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out-codex"
    files, warnings = pack_export.generate_codex(fake_pack, out_dir)

    assert (out_dir / ".codex-plugin" / "plugin.json").is_file()
    assert (out_dir / ".agents" / "plugins" / "marketplace.json").is_file()
    assert (out_dir / "skills" / "orchestrator" / "SKILL.md").is_file()
    assert (out_dir / "agents" / "design.md").is_file()
    assert (out_dir / "agents" / "review.md").is_file()
    assert not (out_dir / "agents" / "build.md").exists()
    assert any("marketplace.json" in w for w in warnings)

    design_md = (out_dir / "agents" / "design.md").read_text()
    # codex agents are plain frontmatter: name/description/model only
    assert "tools:" not in design_md
    assert "name: design" in design_md
    assert "model: opus" in design_md


def test_regenerate_is_idempotent_and_removes_stale_files(fake_pack: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    pack_export.generate_claude(fake_pack, out_dir)
    assert (out_dir / "agents" / "review.md").is_file()

    # A hand-authored file the generator never emitted must survive.
    untouched = out_dir / "commands" / "custom.md"
    untouched.parent.mkdir(parents=True, exist_ok=True)
    untouched.write_text("hand-authored, keep me")

    # Remove the review step from the pack and regenerate: its agent file
    # (previously generated) must be removed; the hand-authored file stays.
    review_contract = fake_pack / "steps" / "review" / "contract.yaml"
    review_contract.unlink()
    (fake_pack / "steps" / "review" / "SKILL.md").unlink()
    review_contract.parent.rmdir()

    files, _ = pack_export.generate_claude(fake_pack, out_dir)

    assert not (out_dir / "agents" / "review.md").exists()
    assert (out_dir / "agents" / "design.md").is_file()
    assert untouched.is_file()
    assert untouched.read_text() == "hand-authored, keep me"
    assert "agents/review.md" not in files


def test_pack_export_cmd_requires_target_and_out(tmp_path: Path) -> None:
    assert pack_export.pack_export_cmd([]) == 3
    assert pack_export.pack_export_cmd(["--target", "claude"]) == 3
    assert pack_export.pack_export_cmd(["--out", str(tmp_path)]) == 3


def test_pack_export_cmd_end_to_end(fake_pack: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "cmd-out"
    rc = pack_export.pack_export_cmd(["--target", "claude", "--out", str(out_dir), str(fake_pack)])
    assert rc == 0
    assert (out_dir / ".claude-plugin" / "plugin.json").is_file()


@pytest.mark.skipif(
    not (REAL_PACK / "steps" / "design" / "contract.yaml").is_file(),
    reason="local workflows pack not vendored in this checkout",
)
def test_generate_claude_against_real_local_pack(tmp_path: Path) -> None:
    out_dir = tmp_path / "real-pack-out"
    files, warnings = pack_export.generate_claude(REAL_PACK, out_dir)
    assert (out_dir / ".claude-plugin" / "plugin.json").is_file()
    assert (out_dir / "agents" / "design.md").is_file()
    # every generated agent must at least have a name/description/model frontmatter
    for rel in files:
        if rel.startswith("agents/") and rel.endswith(".md"):
            text = (out_dir / rel).read_text()
            assert text.startswith("---\n")
            assert "name:" in text
            assert "model:" in text
