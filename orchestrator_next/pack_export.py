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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

MANIFEST_NAME = ".generated-manifest.json"

# --- model alias -> agent frontmatter model (docs/claude-mod-api-notes.md,
# plugin-dev:agent-development skill, checked 2026-09-17: agent frontmatter
# `model:` accepts only inherit/sonnet/opus/haiku — "fable" is NOT a valid
# frontmatter value, so fable stays mapped to its nearest frontmatter
# equivalent, opus, below).
# This mapping only decides what the generated agents/<step>.md's `model:`
# frontmatter says; it has no effect on which model actually runs. At spawn
# time the Claude Mod (register.ts's spawnModelOf) passes a model ALIAS —
# "sonnet"/"opus"/"haiku"/"fable" — as $.agent.spawn's `model`, derived from
# `model_id` (the real routed id from models.yaml) via
# MODEL_FAMILY_TO_SPAWN_ALIAS in protocol.ts; spawn's `model` overrides
# frontmatter, and unlike frontmatter it DOES accept "fable" (verified against
# Claude Code 2.1.274 — see docs/claude-mod-api-notes.md). So a fable-routed
# step spawns on fable even though its generated frontmatter still says opus.
# Keep every models.yaml alias/tier mapped here to the nearest of the four
# frontmatter values so the generated file is never silently wrong even
# though it's inert.
ALIAS_TO_CLAUDE_MODEL = {
    "strong": "opus",
    "standard": "sonnet",
    "fast": "haiku",
    "code": "sonnet",
    "fable": "opus",
    "opus": "opus",
    "sonnet": "sonnet",
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


# A step id becomes a path segment (`agents/<id>.md`) and a subagent type, so
# it is restricted to what is safe in both: no separators, no `..`, no spaces.
STEP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _check_step_id(step_id: str, source: Path) -> None:
    """Reject a step id that could escape the output directory."""
    if not STEP_ID_RE.match(step_id):
        raise PackExportError(
            f"invalid step id {step_id!r} in {source}: step ids must match "
            f"{STEP_ID_RE.pattern} (they become file names and agent types)"
        )


def _frontmatter(fields: dict[str, Any]) -> str:
    """Render a YAML frontmatter block, quoting every value safely.

    Descriptions and names come from pack-authored SKILL.md and contract.yaml
    files; interpolating them raw lets a colon, a quote or a newline break out
    of the block and inject arbitrary frontmatter keys. `yaml.safe_dump` is
    what decides the quoting here, never an f-string.
    """
    body = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return "---\n" + body + "---"


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
        _check_step_id(step_id, contract_path)
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
        # ponytail: any git.* capability is Bash; the contract vocabulary is open-ended there
        claude_tools = TOOL_MAP.get(cap) or (["Bash"] if cap.startswith("git.") else None)
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
    fields: dict[str, Any] = {
        "name": step.step_id,
        "description": step.description,
        "model": model,
    }
    if mapped_tools:
        fields["tools"] = mapped_tools
    body = (step.skill_body or "").rstrip("\n")
    content = _frontmatter(fields) + "\n\n" + body + "\n" + _out_contract_section(step.outputs)
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
   - `status: needs_you` with a `payload.ask` — an await_input step. Use
     AskUserQuestion with `payload.ask` and up to 4 of `payload.options` as
     choices (mention any beyond 4 in the question text; free text is fine
     too). Then run:
     `orchestrator resume <run_id> "<answer>" --json`
     and loop back to step 2 with its result.
   - `status: needs_you` with `payload.abandoned_step` — a step was recorded
     `abandoned` and nothing downstream can run. Use AskUserQuestion with
     `payload.reason` and options `retry` / `cancel` / `leave`. On retry:
     `orchestrator reset-step <run_id> <payload.abandoned_step> --json` and
     loop back to step 2 with its `next`. On cancel:
     `orchestrator cancel <run_id>`. On leave: stop and report the run is
     parked, with both commands above as next steps.
   - `status: needs_you` with no `payload.ask` and no `payload.abandoned_step`
     — stop and report to the user; the engine can't proceed without a human
     decision.
   - `status: done` — the run is complete; report the final artifacts.
   - `status: error` — stop and report the error.
4. Loop back to step 2 until `status` is `done`, `needs_you`, or `error`.

Every CLI call is a Bash invocation. Never spawn a vendor CLI directly —
`orchestrator` is the only process this skill runs.
"""


# --- Claude Mod (Phase 4.1) --------------------------------------------------

MOD_DIR = Path(__file__).resolve().parent / "mod"

# TypeScript sources copied verbatim from orchestrator_next/mod/ into
# `hooks/` of the generated plugin.
MOD_SOURCES = ("register.ts", "protocol.ts", "pane.ts", "actions.ts")

# The type declarations `import type … from 'claude-code'` resolves against,
# and the tsconfig that points at them. Both are emitted so `tsc -p <plugin>`
# typechecks the hooks module without a Claude Code checkout.
MOD_TSCONFIG = {
    "compilerOptions": {
        "target": "es2023",
        "lib": ["es2023"],
        "types": [],
        "module": "esnext",
        "moduleResolution": "bundler",
        "strict": True,
        "noUncheckedIndexedAccess": True,
        "noEmit": True,
        "skipLibCheck": True,
    },
    "include": ["types", "hooks"],
}


def _mod_source(name: str) -> str:
    path = MOD_DIR / name
    if not path.is_file():
        raise PackExportError(f"missing Claude Mod source {path}")
    return path.read_text()


# Where the `claude-code` declarations are looked for, in order. The wheel
# does not vendor them: they are ~400 KB, early access, and regenerated by
# `/plugin-types` inside Claude Code, so they belong to the machine.
DEFAULT_TYPES_PATH = Path.home() / ".claude" / "types" / "claude-code.d.ts"
TYPES_ENV_VAR = "CLAUDE_CODE_TYPES"

TYPES_SOURCES_HINT = (
    "--types <path>, $CLAUDE_CODE_TYPES, or "
    "~/.claude/types/claude-code.d.ts (written by /plugin-types)"
)


def resolve_types_path(explicit: str | Path | None = None) -> Path | None:
    """The `claude-code.d.ts` to copy into the plugin, or None when there is none.

    First hit wins: the `--types` flag, then `$CLAUDE_CODE_TYPES`, then
    `~/.claude/types/claude-code.d.ts` (where `/plugin-types` writes). The
    two explicit sources outrank the ambient file. An explicit path that does
    not exist is an error; the ambient one is simply skipped.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise PackExportError(f"--types {path} does not exist")
        return path
    from_env = os.environ.get(TYPES_ENV_VAR, "").strip()
    if from_env:
        path = Path(from_env).expanduser()
        if path.is_file():
            return path
    if DEFAULT_TYPES_PATH.is_file():
        return DEFAULT_TYPES_PATH
    return None


def _claude_code_dts(types_path: str | Path | None = None) -> str | None:
    """The declarations' text, or None when no source resolved."""
    path = resolve_types_path(types_path)
    if path is None:
        return None
    try:
        return path.read_text()
    except OSError as exc:
        raise PackExportError(f"cannot read types at {path}: {exc}") from exc


def _readme_claude(plugin_name: str) -> str:
    return f"""# {plugin_name}

Generated by `orchestrator pack --target claude`. Do not hand-edit files
listed in `{MANIFEST_NAME}` — they are overwritten on regenerate.

## Load

```
claude --plugin-dir <this-directory>
```

The `skills/orchestrate/SKILL.md` fallback driver works out of the box via
Bash + the Agent tool, with no function hooks and no environment variable.

## Claude Mod (function hooks)

```
CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude --plugin-dir <this-directory>
```

Then ask for a run in the prompt, for example:

```
use orchestrator run with recipe feature slug orc-1
```

That calls the `run` tool `hooks/register.ts` registers. It seeds the run,
lets the CLI batch the script steps, spawns one subagent per judgment step
(the `agents/<step>.md` definitions here carry its tools and model), asks you
to approve each gate, and reports the final status. The `status` tool prints
a run's nodes, usage and cost.

While a run is active, Edit/Write/NotebookEdit and `git commit` /
`git push` are refused **inside the subagents this plugin spawned** until a
gate is approved. Your own session is never gated, and a subagent started any
other way is not either — the hook only knows the agent ids it spawned.

## Using the pane and /orchestrator

You never have to type a run request into the chat. The pane and the
`/orchestrator` command drive a run on their own, and both call exactly the
same functions (`hooks/actions.ts`) the `run` tool does, so a verb means one
thing however you reach for it.

### The command

| Command                                       | Does                                       |
| --------------------------------------------- | ------------------------------------------ |
| `/orchestrator`                               | toggles the pane; opens the wizard when nothing is running |
| `/orchestrator run [recipe] [slug] [ticket…]` | starts a run, asking for whatever you left out |
| `/orchestrator approve`                       | approves the gate the run is parked at     |
| `/orchestrator cancel`                        | cancels the run                            |
| `/orchestrator retry`                         | resets the abandoned step and carries on   |
| `/orchestrator resume <text>`                 | answers the question the run is parked on  |
| `/orchestrator status`                        | prints the node list (or every live run) as text |
| `/orchestrator pane`                          | opens the pane                             |

`/orchestrator run` with nothing after it is a wizard: it offers the recipes
`orchestrator recipes --json` reports (four at a time, with **Other** for the
rest), then asks for a slug and an optional ticket. Dismiss any popup and
nothing is started.

### The pane

While the mod drives a run it opens a side pane (`hooks/pane.ts` draws it)
listing the run's nodes: a status glyph, the step id, its kind, its attempts,
the model it ran on and what it cost. The header carries the slug, the short
run id, the run status and the elapsed clock; the footer the total cost
(marked `(partial)` when a step billed on a model with no pricing row), the
run's phase and the driver's.

Along the bottom is an action row for whatever the run is doing right now:

| The run is…                  | The pane offers                          |
| ---------------------------- | ---------------------------------------- |
| idle / finished              | **Start run** (or **Start another**), **Close** |
| driving                      | **Cancel**                               |
| parked at a gate             | **Approve**, **Cancel**                  |
| parked on an abandoned step  | **Retry**, **Cancel**                    |
| awaiting an answer           | one button per option (up to four), **Answer…**, **Cancel** |

When the run is waiting on a question, the pane shows the question itself
above the buttons. Dismissing the popup does **not** take the decision with
it: the same buttons stay live in the pane, a toast says so, and
`/orchestrator approve` (or `resume`, or `retry`) answers the same thing from
the prompt. Whichever you use first wins.

The pane opens itself only when the terminal is wide enough (144 columns, the
width below which the surface would park an unasked pane undrawn). Escape
closes it and `/orchestrator` toggles it back.

## Typechecking the hooks

```
npx -y typescript@5 tsc -p <this-directory>/tsconfig.json --noEmit
```

`types/claude-code.d.ts` is the declaration file the hooks import
`claude-code` from. It is early access, machine-local, and not shipped in the
orchestrator wheel, so `pack` copies it in from the first of these that
exists:

1. `orchestrator pack --target claude --types <path/to/claude-code.d.ts>`
2. `$CLAUDE_CODE_TYPES`
3. `~/.claude/types/claude-code.d.ts`, where `/plugin-types` writes it

With none of them, `types/` is simply not written and `pack` says so. The
plugin still loads and runs either way: only `tsc` needs the declarations.
Run `/plugin-types` inside Claude Code to produce them.
"""


def generate_claude(
    pack_root: Path, out_dir: Path, types_path: str | Path | None = None
) -> tuple[list[str], list[str]]:
    """Generate a Claude Code plugin directory. Returns (files, warnings).

    ``types_path`` is the `--types` flag; with none, the declarations are
    looked for at the other sources in ``TYPES_SOURCES_HINT``.
    """
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
            "description": (
                f"{plugin_name}: drives an orchestrator recipe through the CLI — "
                "one subagent per judgment step, an approval dialog at each gate, "
                "and writes refused inside a step until a gate is approved"
            ),
            "modules": ["./register.ts"],
        },
        indent=2,
    ) + "\n"
    for source in MOD_SOURCES:
        generated[f"hooks/{source}"] = _mod_source(source)

    generated["tsconfig.json"] = json.dumps(MOD_TSCONFIG, indent=2) + "\n"
    # The declarations are the machine's, not the wheel's: the plugin loads
    # without them (the runtime never reads types), only `tsc` needs them.
    dts = _claude_code_dts(types_path)
    if dts is not None:
        generated["types/claude-code.d.ts"] = dts
    else:
        warnings.append(
            "claude target: no claude-code.d.ts found, so types/ was not "
            f"written and `tsc -p` has nothing to check against. Looked at: "
            f"{TYPES_SOURCES_HINT}."
        )

    generated["README.md"] = _readme_claude(plugin_name)

    files = _write_generated(out_dir, generated, warnings)
    return files, warnings


# --- Codex target -------------------------------------------------------------


def _agent_markdown_codex(step: StepInfo) -> str:
    model = _agent_model_claude(step.alias)  # same alias table; Codex has no distinct tier names here
    frontmatter = _frontmatter(
        {"name": step.step_id, "description": step.description, "model": model}
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

    files = _write_generated(out_dir, generated, warnings)
    return files, warnings


# --- shared write/manifest machinery -----------------------------------------


def _contained(out_dir: Path, rel: str) -> Path | None:
    """`out_dir / rel` when it stays inside `out_dir`, else None.

    Guards both halves of a regenerate: what we write (a step id or pack name
    reaching a path) and what we delete (a manifest an earlier run wrote, or
    that someone edited). `..`, an absolute path and a symlinked parent all
    resolve outside and are refused.
    """
    if not rel or os.path.isabs(rel):
        return None
    base = out_dir.resolve()
    # Resolve the PARENT and normalize the name, so a symlink at the leaf is
    # judged by where it sits rather than where it points: an in-tree link is
    # ours to unlink, and `..` in the path still escapes here.
    try:
        candidate = Path(os.path.normpath(base / rel))
        parent = candidate.parent.resolve()
    except OSError:
        return None
    if candidate != base and not candidate.is_relative_to(base):
        return None
    if parent != base and not parent.is_relative_to(base):
        return None
    return out_dir / rel


def _write_generated(
    out_dir: Path, generated: dict[str, str], warnings: list[str] | None = None
) -> list[str]:
    """Write `generated` (relpath -> content) under out_dir, remove stale
    files from a prior run's manifest, and write the new manifest.

    Only files this tool generated (per the manifest) are ever removed, and
    only where they resolve inside `out_dir`. A manifest entry pointing
    outside is skipped with a warning rather than followed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    report = warnings if warnings is not None else []
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
        if not isinstance(rel, str) or rel in generated:
            continue
        stale = _contained(out_dir, rel)
        if stale is None:
            report.append(
                f"manifest entry {rel!r} resolves outside {out_dir} — not deleted"
            )
            continue
        # A symlink is removed as the link, never followed to its target.
        if os.path.lexists(stale) and not stale.is_dir():
            stale.unlink()
            _prune_empty_dirs(stale.parent, out_dir)

    for rel, content in generated.items():
        dest = _contained(out_dir, rel)
        if dest is None:
            raise PackExportError(
                f"refusing to write {rel!r}: it resolves outside {out_dir}"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    manifest_path.write_text(json.dumps({"files": new_files}, indent=2) + "\n")
    return new_files


def _prune_empty_dirs(start: Path, stop_at: Path) -> None:
    """Remove the now-empty directories a deleted file left, inside `stop_at`.

    Every step is re-checked against the resolved `stop_at`, so a symlinked
    parent cannot walk the rmdir out of the output directory.
    """
    base = stop_at.resolve()
    current = start
    while current != stop_at:
        try:
            resolved = current.resolve()
        except OSError:
            return
        if resolved == base or not resolved.is_relative_to(base):
            return
        # A symlink to a directory is left alone: it is not ours to follow.
        if current.is_symlink() or not current.is_dir() or any(current.iterdir()):
            return
        current.rmdir()
        current = current.parent


# --- CLI entry ----------------------------------------------------------------


def _default_pack_root() -> Path:
    from orchestrator_next.paths import config_root

    return config_root()


# Manifest recording which pack/commit a generated plugin dir came from, so
# `orchestrator doctor` can tell a fresh plugin from a stale one without
# regenerating it. Colocated with the generator's own MANIFEST_NAME rather
# than folded into it, since config-lock hashing intentionally never reads
# generator output (a plugin dir is derived, not part of the pack).
PLUGIN_SOURCE_MANIFEST = ".plugin-source.json"


def default_plugin_root(repo_root: Path, pack_name: str) -> Path:
    """Stable plugin output dir: sibling of the pack, keyed by its folder name.

    ``<repo>/.orchestrator/plugins/<pack_name>/`` — never inside
    ``.orchestrator/<pack_name>/`` itself, since `config pull` replaces that
    whole tree on every pull (see ``pull_into_pack``) and would delete a
    plugin dir nested there.
    """
    return Path(repo_root) / ".orchestrator" / "plugins" / pack_name


def default_plugin_dir(repo_root: Path, pack_name: str, target: str) -> Path:
    """``<repo>/.orchestrator/plugins/<pack_name>/<target>/``."""
    return default_plugin_root(repo_root, pack_name) / target


def write_plugin_source_manifest(plugin_dir: Path, pack_name: str, pack_sha256: str | None) -> None:
    """Record which pack (by folder name + content hash) a plugin dir was
    generated from, for `doctor`'s freshness check."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / PLUGIN_SOURCE_MANIFEST).write_text(
        json.dumps({"pack": pack_name, "pack_sha256": pack_sha256}, indent=2) + "\n"
    )


def claude_plugin_hint(plugin_dir: Path) -> str:
    """The one line printed after generation for the user to copy."""
    return (
        f"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude --plugin-dir {plugin_dir.resolve()}"
    )


def pack_export_cmd(argv: list[str]) -> int:
    target: str | None = None
    out: str | None = None
    types: str | None = None
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
        elif arg == "--types":
            i += 1
            types = argv[i] if i < len(argv) else None
        elif arg.startswith("--types="):
            types = arg.split("=", 1)[1]
        else:
            positional.append(arg)
        i += 1

    if target not in ("claude", "codex"):
        print("usage: orchestrator pack --target claude|codex [--out <dir>] [--types <claude-code.d.ts>] [<pack-root>]", file=os.sys.stderr)
        return 3

    try:
        pack_root = Path(positional[0]) if positional else _default_pack_root()
    except Exception as exc:  # ConfigRootError et al
        print(f"error: {exc}", file=os.sys.stderr)
        return 3

    pack_name = pack_root.resolve().name
    if out:
        out_dir = Path(out)
    else:
        from orchestrator_next.config_pull import resolve_repo_root

        repo_root = resolve_repo_root(None)
        out_dir = default_plugin_dir(repo_root, pack_name, target)

    try:
        if target == "claude":
            files, warnings = generate_claude(pack_root, out_dir, types)
        else:
            files, warnings = generate_codex(pack_root, out_dir)
    except PackExportError as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 3

    if not out:
        from orchestrator_next.config_pull import tree_sha256

        # Hash the vendored copy at <repo_root>/.orchestrator/<pack_name>/ —
        # the exact path `doctor.check_claude_plugin` recomputes against —
        # not `pack_root`. They usually coincide, but `pack_root` can be
        # redirected by ORCHESTRATOR_CONFIG (e.g. a dev checkout's own
        # `config` symlink) to a same-named pack living elsewhere; hashing
        # that would stamp the manifest with content doctor will never see
        # again, making a fresh plugin look permanently stale.
        vendored_pack_dir = repo_root / ".orchestrator" / pack_name
        hash_source = vendored_pack_dir if vendored_pack_dir.is_dir() else pack_root
        write_plugin_source_manifest(out_dir, pack_name, tree_sha256(hash_source))

    for w in warnings:
        print(f"warning: {w}", file=os.sys.stderr)
    print(json.dumps({"out": str(out_dir), "target": target, "files": files}, indent=2))
    if target == "claude":
        print(claude_plugin_hint(out_dir))
    return 0
