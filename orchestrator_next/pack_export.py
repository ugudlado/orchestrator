"""Pack -> plugin generator (protocol v2 Phase 4.2/4.3, docs/protocol-v2.md §4).

`orchestrator pack --target claude|codex --out <dir> [<pack-root>]` reads a
config pack (`.orchestrator/<pack>/`: `workflows/*.yaml` + `steps/<id>/`) and
emits a Claude Code or Codex plugin directory that drives it: an
`agents/<step>.md` per judgment step (frontmatter model/tools from the
contract), a fallback `skills/.../SKILL.md` driver loop, and (Claude only) a
stub hooks module for the future Mod integration (Phase 4.1).

Generation is idempotent: re-running overwrites files this tool generated and
removes any it generated previously but no longer emits, tracked in
`.generated-manifest.json` at the plugin root. Files it never generated are
left untouched.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MANIFEST_NAME = ".generated-manifest.json"

# --- model alias -> agent frontmatter model (docs/claude-mod-api-notes.md,
# plugin-dev:agent-development skill: model must be inherit/sonnet/opus/haiku) --
ALIAS_TO_CLAUDE_MODEL = {
    "strong": "opus",
    "standard": "sonnet",
    "fast": "haiku",
    "code": "sonnet",
}

# Contract `tools:` capability names -> Claude Code tool names. Unknown
# entries are dropped and reported as a warning (never silently invented).
TOOL_MAP: dict[str, list[str]] = {
    "fs.read": ["Read"],
    "fs.write": ["Write", "Edit"],
    "fs.list": ["Glob", "Grep"],
    "shell.run": ["Bash"],
    "shell.test": ["Bash"],
    "git.read": ["Bash"],
    "git.write": ["Bash"],
}


class PackExportError(ValueError):
    """Raised when the pack root or its contracts can't be read/exported."""


@dataclass
class StepInfo:
    step_id: str
    kind: str  # exec | judgment | gate
    description: str
    tools: list[str]
    alias: str | None
    skill_body: str | None  # judgment steps only
    outputs: dict[str, dict]


# --- pack reading -----------------------------------------------------------


def _load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[i + 1 :]).lstrip("\n")
    return text


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""


def _pack_description(pack_root: Path) -> str:
    pack_yaml = _load_yaml_file(pack_root / "pack.yaml")
    if isinstance(pack_yaml.get("description"), str) and pack_yaml["description"].strip():
        return pack_yaml["description"].strip()
    return f"Generated from orchestrator pack at {pack_root}"


def _pack_name(pack_root: Path) -> str:
    pack_yaml = _load_yaml_file(pack_root / "pack.yaml")
    name = pack_yaml.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return pack_root.name


def _pack_version(pack_root: Path) -> str:
    lock = _load_yaml_file(pack_root / "config-lock.yaml")
    version = lock.get("version")
    if version:
        return str(version)
    pack_yaml = _load_yaml_file(pack_root / "pack.yaml")
    version = pack_yaml.get("version")
    if version:
        return str(version)
    return "0.0.0"


def _step_models(pack_root: Path) -> dict[str, str]:
    models = _load_yaml_file(pack_root / "models.yaml")
    step_models = models.get("step_models")
    return step_models if isinstance(step_models, dict) else {}


def _resolve_kind(step_id: str, data: dict[str, Any], has_run: bool, has_prompt: bool) -> str:
    raw = data.get("kind")
    if isinstance(raw, str):
        raw = {"agent": "judgment", "script": "exec"}.get(raw, raw)
    if raw in ("exec", "judgment", "gate"):
        return raw
    if has_run:
        return "exec"
    if has_prompt:
        return "judgment"
    return "gate"


def load_pack_steps(pack_root: Path) -> list[StepInfo]:
    """Read every `steps/<id>/contract.yaml` under `pack_root` into StepInfo."""
    steps_dir = pack_root / "steps"
    if not steps_dir.is_dir():
        raise PackExportError(f"no steps/ directory under pack root {pack_root}")

    step_models = _step_models(pack_root)
    warnings: list[str] = []
    infos: list[StepInfo] = []

    for step_dir in sorted(p for p in steps_dir.iterdir() if p.is_dir()):
        contract_path = step_dir / "contract.yaml"
        if not contract_path.is_file():
            continue
        data = _load_yaml_file(contract_path)
        step_id = str(data.get("id") or step_dir.name)
        run_ref = data.get("run")
        prompt_ref = data.get("prompt")
        kind = _resolve_kind(step_id, data, run_ref is not None, prompt_ref is not None)

        tools_raw = data.get("tools") or []
        tools = [str(t) for t in tools_raw] if isinstance(tools_raw, list) else []
        outputs_raw = data.get("out") or {}
        outputs = outputs_raw if isinstance(outputs_raw, dict) else {}

        skill_body = None
        description = ""
        if kind == "judgment":
            prompt_name = prompt_ref if isinstance(prompt_ref, str) else "SKILL.md"
            prompt_path = step_dir / prompt_name
            if not prompt_path.is_file():
                # v1 fallback name.
                prompt_path = step_dir / "SKILL.md"
            if prompt_path.is_file():
                raw_text = prompt_path.read_text()
                skill_body = _strip_frontmatter(raw_text)
                description = _first_line(skill_body) or step_id
            else:
                warnings.append(f"step {step_id}: prompt file not found, using contract id as description")
                skill_body = f"# {step_id}\n\n(No SKILL.md found for this step.)\n"
                description = step_id

        alias = step_models.get(step_id) if kind == "judgment" else None

        infos.append(
            StepInfo(
                step_id=step_id,
                kind=kind,
                description=description,
                tools=tools,
                alias=alias,
                skill_body=skill_body,
                outputs=outputs,
            )
        )

    return infos


# --- Claude target -----------------------------------------------------------


def _map_tools_claude(step_id: str, tools: list[str]) -> tuple[list[str], list[str]]:
    """Return (mapped_claude_tools, warnings) for a step's `tools:` list."""
    mapped: list[str] = []
    warnings: list[str] = []
    for cap in tools:
        claude_tools = TOOL_MAP.get(cap)
        if claude_tools is None:
            warnings.append(f"step {step_id}: unknown tool capability {cap!r}, omitted")
            continue
        for t in claude_tools:
            if t not in mapped:
                mapped.append(t)
    return mapped, warnings


def _agent_model_claude(alias: str | None) -> str:
    if not alias:
        return "sonnet"
    return ALIAS_TO_CLAUDE_MODEL.get(alias, "sonnet")


def _out_contract_section(outputs: dict[str, dict]) -> str:
    if not outputs:
        return (
            "\n## Output contract\n\n"
            "This step declares no `out:` schema. End your final message with a "
            "single fenced ```json block containing `{}`.\n"
        )
    lines = ["\n## Output contract\n", "End your final message with a single fenced ```json block matching:\n"]
    for name, spec in outputs.items():
        if "artifact" in spec:
            lines.append(f"- `{name}`: string — path to the written artifact ({spec['artifact']})")
        elif spec.get("type") == "enum":
            values = ", ".join(str(v) for v in spec.get("values", []))
            lines.append(f"- `{name}`: enum — one of [{values}]")
        else:
            lines.append(f"- `{name}`: {spec.get('type', 'value')}")
    return "\n".join(lines) + "\n"


def _agent_markdown_claude(step: StepInfo) -> tuple[str, list[str]]:
    mapped_tools, warnings = _map_tools_claude(step.step_id, step.tools)
    model = _agent_model_claude(step.alias)
    frontmatter_lines = [
        "---",
        f"name: {step.step_id}",
        f"description: {step.description}",
        f"model: {model}",
    ]
    if mapped_tools:
        frontmatter_lines.append(f"tools: {json.dumps(mapped_tools)}")
    frontmatter_lines.append("---")
    body = (step.skill_body or "").rstrip("\n")
    content = "\n".join(frontmatter_lines) + "\n\n" + body + "\n" + _out_contract_section(step.outputs)
    return content, warnings


_ORCHESTRATE_SKILL_TEMPLATE = """---
name: orchestrate
description: Drive an orchestrator pack workflow via the CLI, dispatching judgment steps to subagents. Use when function hooks (the orchestrator Claude Mod) are unavailable.
---

# Orchestrate (fallback driver)

Fallback loop for when the orchestrator Claude Mod's function hooks are off.
Drives a run end to end using the `orchestrator` CLI and the Agent tool.

## Loop

1. Start (once): `orchestrator start <recipe> <slug> --inputs '{{...}}' --json`
   to get `run_id`.
2. Repeat: `orchestrator step <run_id> --json`.
3. Branch on `status`/`kind`:
   - `kind: exec` — the CLI already ran it; loop again.
   - `kind: judgment` — read `payload.step_id`, `payload.system`, `payload.in`,
     `payload.out_schema`. Invoke the Agent tool with
     `subagent_type: <payload.step_id>` and a prompt containing `system` plus
     the resolved `in` paths and the `out_schema`. Parse the trailing fenced
     ```json block from the agent's final message. Then run:
     `orchestrator done <run_id> <step_id> --out '<parsed json>' --usage '{{"input_tokens": N, "output_tokens": M}}'`
     using the subagent's reported token usage.
   - `kind: gate` — use AskUserQuestion to show `payload.show` artifacts and
     ask for approval. On yes: `orchestrator approve <run_id> <token>`. On no:
     `orchestrator cancel <run_id>` or wait for edits per the user's answer.
   - `status: needs_you` — stop and report to the user; the engine can't
     proceed without a human decision.
   - `status: done` — the run is complete; report the final artifacts.
   - `status: error` — stop and report the error.
4. Loop back to step 2 until `status` is `done`, `needs_you`, or `error`.

Every CLI call is a Bash invocation. Never spawn a vendor CLI directly —
`orchestrator` is the only process this skill runs.
"""


def _register_ts_stub() -> str:
    return (
        "// TODO(Phase 4.1): implement the orchestrator Claude Mod hooks here.\n"
        "// See docs/claude-mod-api-notes.md for the verified Mod API surface\n"
        "// (agent.spawn, tool.register, ui.ask, process.run) and\n"
        "// docs/protocol-v2.md §3 for the CLI protocol this module should drive.\n"
        "export const register = (on, options) => {\n"
        "  // stub — no hooks registered yet.\n"
        "};\n"
    )


def _readme_claude(plugin_name: str) -> str:
    return f"""# {plugin_name}

Generated by `orchestrator pack --target claude`. Do not hand-edit files
listed in `{MANIFEST_NAME}` — they are overwritten on regenerate.

## Load

```
claude --plugin-dir <this-directory>
```

The `skills/orchestrate/SKILL.md` fallback driver works out of the box via
Bash + the Agent tool.

To enable the native Claude Mod integration (function hooks), set:

```
CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1
```

The Mod hooks in `hooks/register.ts` are currently a stub (Phase 4.1 is not
yet implemented) — the fallback skill above is the supported path today.
"""


def generate_claude(pack_root: Path, out_dir: Path) -> tuple[list[str], list[str]]:
    """Generate a Claude Code plugin directory. Returns (files, warnings)."""
    steps = load_pack_steps(pack_root)
    warnings: list[str] = []
    generated: dict[str, str] = {}

    plugin_name = f"orchestrator-{_pack_name(pack_root)}"
    plugin_json = {
        "name": plugin_name,
        "version": _pack_version(pack_root),
        "description": _pack_description(pack_root),
    }
    generated[".claude-plugin/plugin.json"] = json.dumps(plugin_json, indent=2) + "\n"

    for step in steps:
        if step.kind != "judgment":
            continue
        content, step_warnings = _agent_markdown_claude(step)
        warnings.extend(step_warnings)
        generated[f"agents/{step.step_id}.md"] = content

    generated["skills/orchestrate/SKILL.md"] = _ORCHESTRATE_SKILL_TEMPLATE

    generated["hooks/hooks.json"] = json.dumps(
        {
            "description": f"{plugin_name} orchestrator driver hooks (stub, Phase 4.1)",
            "modules": ["./register.ts"],
        },
        indent=2,
    ) + "\n"
    generated["hooks/register.ts"] = _register_ts_stub()

    generated["README.md"] = _readme_claude(plugin_name)

    files = _write_generated(out_dir, generated)
    return files, warnings


# --- Codex target -------------------------------------------------------------


def _agent_markdown_codex(step: StepInfo) -> str:
    model = _agent_model_claude(step.alias)  # same alias table; Codex has no distinct tier names here
    frontmatter = "\n".join(
        [
            "---",
            f"name: {step.step_id}",
            f"description: {step.description}",
            f"model: {model}",
            "---",
        ]
    )
    body = (step.skill_body or "").rstrip("\n")
    return frontmatter + "\n\n" + body + "\n" + _out_contract_section(step.outputs)


_ORCHESTRATOR_SKILL_CODEX_TEMPLATE = """---
name: orchestrator
description: Drive an orchestrator pack workflow via the CLI, dispatching judgment steps to agents. Codex has no function-hook enforcement — the CLI is the only gate.
---

# Orchestrator (Codex driver)

Same loop as the Claude Code fallback skill, driven over shell:

1. `orchestrator start <recipe> <slug> --inputs '{{...}}' --json` -> `run_id`.
2. Loop `orchestrator step <run_id> --json`:
   - `kind: exec` — already ran; loop again.
   - `kind: judgment` — delegate to the matching `agents/<step_id>.md` agent
     with the payload's `system`/`in`/`out_schema`. Parse the trailing fenced
     ```json block from its output, then
     `orchestrator done <run_id> <step_id> --out '<json>' --usage '{{...}}'`.
   - `kind: gate` — present `payload.show` to the user and wait for approval;
     `orchestrator approve <run_id> <token>` or `orchestrator cancel <run_id>`.
   - `status: needs_you` / `error` — stop and report.
   - `status: done` — report final artifacts.

Enforcement (write gating, usage checks) is CLI-side only in Codex — there is
no hooks layer here, unlike the Claude Mod (Phase 4.1).
"""


def _readme_codex(plugin_name: str) -> str:
    return f"""# {plugin_name}

Generated by `orchestrator pack --target codex`. Do not hand-edit files
listed in `{MANIFEST_NAME}` — they are overwritten on regenerate.

`.agents/plugins/marketplace.json` format is unverified against a live Codex
install — TODO: confirm against current Codex plugin docs before publishing.
"""


def generate_codex(pack_root: Path, out_dir: Path) -> tuple[list[str], list[str]]:
    """Generate a Codex plugin directory. Returns (files, warnings)."""
    steps = load_pack_steps(pack_root)
    warnings: list[str] = [
        "codex target: .agents/plugins/marketplace.json format is unverified "
        "(no live Codex install to check against) — see README TODO."
    ]
    generated: dict[str, str] = {}

    plugin_name = f"orchestrator-{_pack_name(pack_root)}"
    generated[".codex-plugin/plugin.json"] = json.dumps(
        {
            "name": plugin_name,
            "version": _pack_version(pack_root),
            "description": _pack_description(pack_root),
        },
        indent=2,
    ) + "\n"

    generated[".agents/plugins/marketplace.json"] = json.dumps(
        {
            "name": plugin_name,
            "version": _pack_version(pack_root),
            "plugins": [{"name": plugin_name, "source": "."}],
        },
        indent=2,
    ) + "\n"

    generated["skills/orchestrator/SKILL.md"] = _ORCHESTRATOR_SKILL_CODEX_TEMPLATE

    for step in steps:
        if step.kind != "judgment":
            continue
        generated[f"agents/{step.step_id}.md"] = _agent_markdown_codex(step)

    generated["README.md"] = _readme_codex(plugin_name)

    files = _write_generated(out_dir, generated)
    return files, warnings


# --- shared write/manifest machinery -----------------------------------------


def _write_generated(out_dir: Path, generated: dict[str, str]) -> list[str]:
    """Write `generated` (relpath -> content) under out_dir, remove stale
    files from a prior run's manifest, and write the new manifest.

    Only files this tool generated (per the manifest) are ever removed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / MANIFEST_NAME
    prev_files: list[str] = []
    if manifest_path.is_file():
        try:
            prev = json.loads(manifest_path.read_text())
            prev_files = prev.get("files", []) if isinstance(prev, dict) else []
        except (json.JSONDecodeError, OSError):
            prev_files = []

    new_files = sorted(generated.keys())

    for rel in prev_files:
        if rel in generated:
            continue
        stale = out_dir / rel
        if stale.is_file():
            stale.unlink()
            _prune_empty_dirs(stale.parent, out_dir)

    for rel, content in generated.items():
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    manifest_path.write_text(json.dumps({"files": new_files}, indent=2) + "\n")
    return new_files


def _prune_empty_dirs(start: Path, stop_at: Path) -> None:
    current = start
    while current != stop_at and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


# --- CLI entry ----------------------------------------------------------------


def _default_pack_root() -> Path:
    from orchestrator_next.paths import config_root

    return config_root()


def pack_export_cmd(argv: list[str]) -> int:
    target: str | None = None
    out: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--target":
            i += 1
            target = argv[i] if i < len(argv) else None
        elif arg.startswith("--target="):
            target = arg.split("=", 1)[1]
        elif arg == "--out":
            i += 1
            out = argv[i] if i < len(argv) else None
        elif arg.startswith("--out="):
            out = arg.split("=", 1)[1]
        else:
            positional.append(arg)
        i += 1

    if target not in ("claude", "codex"):
        print("usage: orchestrator pack --target claude|codex --out <dir> [<pack-root>]", file=os.sys.stderr)
        return 3
    if not out:
        print("usage: orchestrator pack --target claude|codex --out <dir> [<pack-root>]", file=os.sys.stderr)
        return 3

    try:
        pack_root = Path(positional[0]) if positional else _default_pack_root()
    except Exception as exc:  # ConfigRootError et al
        print(f"error: {exc}", file=os.sys.stderr)
        return 3

    out_dir = Path(out)
    try:
        if target == "claude":
            files, warnings = generate_claude(pack_root, out_dir)
        else:
            files, warnings = generate_codex(pack_root, out_dir)
    except PackExportError as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 3

    for w in warnings:
        print(f"warning: {w}", file=os.sys.stderr)
    print(json.dumps({"out": str(out_dir), "target": target, "files": files}, indent=2))
    return 0
