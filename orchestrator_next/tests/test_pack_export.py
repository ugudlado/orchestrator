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

    orchestrate_skill = (out_dir / "skills" / "orchestrate" / "SKILL.md").read_text()
    assert "orchestrator resume" in orchestrate_skill
    assert (out_dir / "README.md").is_file()


def test_generate_claude_emits_mod(fake_pack: Path, tmp_path: Path) -> None:
    """The Phase 4.1 hooks module ships whole: sources, manifest, tsconfig."""
    out_dir = tmp_path / "out-mod"
    files, _warnings = pack_export.generate_claude(fake_pack, out_dir)

    hooks_json = json.loads((out_dir / "hooks" / "hooks.json").read_text())
    assert hooks_json["modules"] == ["./register.ts"]
    assert "orchestrator-demo" in hooks_json["description"]

    # Every mod source is copied verbatim from orchestrator_next/mod/.
    for name in pack_export.MOD_SOURCES:
        assert f"hooks/{name}" in files
        assert (out_dir / "hooks" / name).read_text() == (
            pack_export.MOD_DIR / name
        ).read_text()

    register_ts = (out_dir / "hooks" / "register.ts").read_text()
    assert "export function register" in register_ts
    assert "$.tool.register" in register_ts
    assert "$.agent.spawn" in register_ts
    assert "TODO" not in register_ts

    tsconfig = json.loads((out_dir / "tsconfig.json").read_text())
    assert tsconfig["include"] == ["types", "hooks"]
    assert tsconfig["compilerOptions"]["strict"] is True

    readme = (out_dir / "README.md").read_text()
    assert "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1" in readme
    assert "use orchestrator run with recipe feature slug orc-1" in readme


@pytest.fixture
def no_ambient_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither of the two implicit type sources resolves during a test."""
    monkeypatch.setattr(
        pack_export, "DEFAULT_TYPES_PATH", tmp_path / "absent" / "claude-code.d.ts"
    )
    monkeypatch.delenv(pack_export.TYPES_ENV_VAR, raising=False)


def test_generate_claude_types_are_optional(
    fake_pack: Path, tmp_path: Path, no_ambient_types: None
) -> None:
    """With no d.ts anywhere the plugin still generates, with a warning."""
    out_dir = tmp_path / "out-no-types"
    files, warnings = pack_export.generate_claude(fake_pack, out_dir)

    assert "types/claude-code.d.ts" not in files
    assert (out_dir / "hooks" / "register.ts").is_file()
    assert (out_dir / "tsconfig.json").is_file()  # emitted regardless
    # The warning names all three sources.
    warning = next(w for w in warnings if "claude-code.d.ts" in w)
    assert "--types" in warning
    assert "~/.claude/types/claude-code.d.ts" in warning
    assert "CLAUDE_CODE_TYPES" in warning


def test_types_resolution_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--types wins over $CLAUDE_CODE_TYPES, which wins over ~/.claude/types."""
    flag = tmp_path / "flag.d.ts"
    default = tmp_path / "default.d.ts"
    env = tmp_path / "env.d.ts"
    for path in (flag, default, env):
        path.write_text(f"// {path.name}\n")

    monkeypatch.setattr(pack_export, "DEFAULT_TYPES_PATH", default)
    monkeypatch.setenv(pack_export.TYPES_ENV_VAR, str(env))

    assert pack_export.resolve_types_path(flag) == flag
    assert pack_export.resolve_types_path(None) == env

    monkeypatch.delenv(pack_export.TYPES_ENV_VAR)
    assert pack_export.resolve_types_path(None) == default

    monkeypatch.setattr(
        pack_export, "DEFAULT_TYPES_PATH", tmp_path / "gone.d.ts"
    )
    assert pack_export.resolve_types_path(None) is None


def test_missing_explicit_types_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(pack_export.PackExportError) as exc:
        pack_export.resolve_types_path(tmp_path / "nope.d.ts")
    assert "does not exist" in str(exc.value)


def test_generate_claude_copies_resolved_types(
    fake_pack: Path, tmp_path: Path, no_ambient_types: None
) -> None:
    types = tmp_path / "given.d.ts"
    types.write_text("declare module 'claude-code' {}\n")

    out_dir = tmp_path / "out-with-types"
    files, warnings = pack_export.generate_claude(fake_pack, out_dir, types)

    assert "types/claude-code.d.ts" in files
    assert (out_dir / "types" / "claude-code.d.ts").read_text() == types.read_text()
    assert not any("claude-code.d.ts" in w for w in warnings)


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


def test_pack_export_cmd_requires_target(tmp_path: Path) -> None:
    assert pack_export.pack_export_cmd([]) == 3
    assert pack_export.pack_export_cmd(["--out", str(tmp_path)]) == 3


def test_pack_export_cmd_defaults_out_to_plugins_dir(fake_pack: Path, tmp_path: Path, monkeypatch) -> None:
    """No --out: writes under <repo>/.orchestrator/plugins/<pack-folder-name>/claude/."""
    repo = tmp_path / "consumer-repo"
    repo.mkdir()
    monkeypatch.setenv("REPO_ROOT", str(repo))
    rc = pack_export.pack_export_cmd(["--target", "claude", str(fake_pack)])
    assert rc == 0
    expected = repo / ".orchestrator" / "plugins" / fake_pack.name / "claude"
    assert (expected / ".claude-plugin" / "plugin.json").is_file()
    assert (expected / pack_export.PLUGIN_SOURCE_MANIFEST).is_file()


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


# --- hardening (security review of 11a98a6) ----------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", "a/b", "..", "with space", "x" * 65, "sneaky/../../etc"],
)
def test_bad_step_id_is_rejected(fake_pack: Path, bad_id: str) -> None:
    """A step id becomes a file name, so anything path-shaped is refused."""
    _write_yaml(
        fake_pack / "steps" / "evil" / "contract.yaml",
        {"id": bad_id, "version": 1, "kind": "judgment", "out": {}},
    )
    _write(fake_pack / "steps" / "evil" / "SKILL.md", "Evil.\n")

    with pytest.raises(pack_export.PackExportError) as exc:
        pack_export.load_pack_steps(fake_pack)
    assert "invalid step id" in str(exc.value)


def test_good_step_ids_still_load(fake_pack: Path) -> None:
    _write_yaml(
        fake_pack / "steps" / "ok" / "contract.yaml",
        {"id": "a_b-C9", "version": 1, "kind": "judgment", "out": {}},
    )
    _write(fake_pack / "steps" / "ok" / "SKILL.md", "Fine.\n")
    steps = {s.step_id for s in pack_export.load_pack_steps(fake_pack)}
    assert "a_b-C9" in steps


def test_write_refuses_paths_outside_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    for rel in ("../escaped.md", "a/../../escaped.md", "/etc/passwd"):
        with pytest.raises(pack_export.PackExportError) as exc:
            pack_export._write_generated(out_dir, {rel: "x"})
        assert "resolves outside" in str(exc.value)
    assert not (tmp_path / "escaped.md").exists()


def test_stale_removal_ignores_paths_outside_out_dir(tmp_path: Path) -> None:
    """A tampered manifest cannot make the generator delete someone else's file."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    victim = tmp_path / "victim.md"
    victim.write_text("precious")

    (out_dir / pack_export.MANIFEST_NAME).write_text(
        json.dumps({"files": ["../victim.md", "/etc/hosts", "kept.md"]})
    )
    (out_dir / "kept.md").write_text("stale but ours")

    warnings: list[str] = []
    pack_export._write_generated(out_dir, {"new.md": "x"}, warnings)

    assert victim.read_text() == "precious"
    assert not (out_dir / "kept.md").exists()  # in-tree stale file is removed
    assert any("victim.md" in w for w in warnings)
    assert any("resolves outside" in w for w in warnings)


def test_stale_removal_unlinks_symlink_not_target(tmp_path: Path) -> None:
    """A manifest entry that is a symlink is removed as the link."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = tmp_path / "target.md"
    target.write_text("precious")
    link = out_dir / "link.md"
    link.symlink_to(target)

    (out_dir / pack_export.MANIFEST_NAME).write_text(json.dumps({"files": ["link.md"]}))
    pack_export._write_generated(out_dir, {"new.md": "x"}, [])

    assert target.read_text() == "precious"
    assert not link.is_symlink()


def test_frontmatter_escapes_injection(fake_pack: Path, tmp_path: Path) -> None:
    """A description can't break out of the YAML block and inject keys."""
    evil = 'Pwned\n---\nmodel: opus\ntools: ["Bash"]\nx: "y'
    _write(fake_pack / "steps" / "review" / "SKILL.md", evil + "\n\nBody.\n")

    out_dir = tmp_path / "out-inject"
    pack_export.generate_claude(fake_pack, out_dir)
    text = (out_dir / "agents" / "review.md").read_text()

    # The frontmatter block parses, and holds exactly the keys we emitted.
    block = text.split("---\n")[1]
    parsed = yaml.safe_load(block)
    assert parsed["description"] == evil.splitlines()[0] or "Pwned" in parsed["description"]
    assert parsed["model"] == "sonnet"  # not the injected `opus`
    assert set(parsed) <= {"name", "description", "model", "tools"}


def test_frontmatter_quotes_colons_and_quotes(fake_pack: Path, tmp_path: Path) -> None:
    _write(
        fake_pack / "steps" / "review" / "SKILL.md",
        'Fix: the "thing" — now\n\nBody.\n',
    )
    out_dir = tmp_path / "out-colon"
    pack_export.generate_claude(fake_pack, out_dir)
    block = (out_dir / "agents" / "review.md").read_text().split("---\n")[1]
    parsed = yaml.safe_load(block)
    assert parsed["description"] == 'Fix: the "thing" — now'


def test_codex_frontmatter_is_also_escaped(fake_pack: Path, tmp_path: Path) -> None:
    _write(fake_pack / "steps" / "review" / "SKILL.md", 'Bad: "x"\n---\nmodel: opus\n')
    out_dir = tmp_path / "out-codex-inject"
    pack_export.generate_codex(fake_pack, out_dir)
    block = (out_dir / "agents" / "review.md").read_text().split("---\n")[1]
    parsed = yaml.safe_load(block)
    assert parsed["model"] == "sonnet"
    assert set(parsed) <= {"name", "description", "model"}


def test_write_refuses_symlinked_directory_escape(tmp_path: Path) -> None:
    """An in-tree symlink to an outside directory is not a way out."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (out_dir / "escape").symlink_to(outside)

    with pytest.raises(pack_export.PackExportError):
        pack_export._write_generated(out_dir, {"escape/evil.md": "pwned"})
    assert not (outside / "evil.md").exists()
