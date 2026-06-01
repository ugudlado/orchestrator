"""State introspection + done-payload construction for run-workflow.sh.

Consolidates the inline `python3 -c` blocks that the shell driver previously
embedded. Subcommands map 1:1 to the bash call sites:

  state-field      — read a top-level key from state.yaml (with default)
  workflow-meta    — multi-line meta block for agent prompts
  log-step-usage   — formatted usage summary for the last terminal row of a step
  build-payload    — emit a JSON done-payload for orchestrator done

Stdout is the contract; stderr is logging. All subcommands exit 0 on
recoverable conditions (missing state, missing PyYAML) so the shell driver's
`|| true` fallbacks keep working.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _load_state(path: str) -> dict[str, Any] | None:
    try:
        import yaml
    except ImportError:
        return None
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except OSError:
        return None
    return raw if isinstance(raw, dict) else {}


def cmd_state_field(args: argparse.Namespace) -> int:
    raw = _load_state(args.state_yaml)
    if raw is None:
        print(args.default)
        return 0
    val = raw.get(args.key)
    if (val is None or val == "") and args.fallback:
        val = raw.get(args.fallback)
    print(val if val not in (None, "") else args.default)
    return 0


def _resolve_artifact_dir(
    cid: str, repo: str, wt: str, schema: str
) -> tuple[str, str]:
    """Return (artifact_dir, workflow_state_dir) for a given run.

    Priority:
    1. workflow YAML `artifact_dir:` template (relative paths resolved from repo_root).
       Supports {change_id}, {repo_root}, {worktree_path} substitutions.
    2. Default: spec/changes/<change_id> under worktree_path (if set) or repo_root.
    """
    # Try to read artifact_dir from the workflow YAML.
    if schema and repo:
        wf_yaml = Path(repo) / "config" / "workflows" / f"{schema}.yaml"
        if wf_yaml.is_file():
            try:
                import yaml as _yaml

                wf = _yaml.safe_load(wf_yaml.read_text()) or {}
                tmpl = wf.get("artifact_dir", "")
                if tmpl:
                    rendered = (
                        str(tmpl)
                        .replace("{change_id}", cid)
                        .replace("{repo_root}", repo)
                        .replace("{worktree_path}", wt or repo)
                    )
                    # Resolve relative paths against repo_root.
                    resolved = Path(rendered)
                    if not resolved.is_absolute():
                        resolved = Path(repo) / resolved
                    base = str(resolved.parent)
                    return str(resolved), base
            except Exception:  # noqa: BLE001
                pass  # fall through to default

    # Default convention: spec/changes/<change_id> under worktree or repo.
    base_root = wt if wt else repo
    return f"{base_root}/spec/changes/{cid}", f"{base_root}/spec/changes"


def cmd_workflow_meta(args: argparse.Namespace) -> int:
    raw = _load_state(args.state_yaml) or {}
    p = Path(args.state_yaml)
    cid = raw.get("change_id") or raw.get("slug") or p.parent.name
    wt = raw.get("worktree_path") or ""
    schema = raw.get("schema") or ""
    repo = raw.get("repo_root") or ""

    artifact_dir, workflow_state_dir = _resolve_artifact_dir(cid, repo, wt, schema)

    print(f"Workflow: change_id={cid} schema={schema} repo_root={repo}")
    print(f"state_yaml_path={p}")
    if wt:
        print(f"worktree_path={wt}")
    print(f"artifact_dir={artifact_dir}")
    print(f"workflow_state_dir={workflow_state_dir}")
    return 0


def _format_duration_ms(ms: float) -> str:
    if ms >= 60_000:
        return f"duration={ms / 60_000:.1f}m"
    if ms >= 1_000:
        return f"duration={ms / 1_000:.1f}s"
    return f"duration={int(ms)}ms"


def cmd_last_terminal_step(args: argparse.Namespace) -> int:
    """Emit JSON for the most recent terminal step_history row (for inline-step logging)."""
    raw = _load_state(args.state_yaml)
    if raw is None:
        print("{}")
        return 0
    terminal = frozenset(
        {"completed", "recovered", "failed", "abandoned", "blocked", "escalate_to_architect"}
    )
    for entry in reversed(raw.get("step_history") or []):
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status not in terminal:
            continue
        print(
            json.dumps(
                {
                    "step_id": entry.get("step_id"),
                    "phase": entry.get("phase", "main"),
                    "status": status,
                    "attempt": entry.get("attempt"),
                }
            )
        )
        return 0
    print("{}")
    return 0


def cmd_log_step_usage(args: argparse.Namespace) -> int:
    raw = _load_state(args.state_yaml)
    if raw is None:
        return 0
    history = raw.get("step_history") or []
    usage: dict[str, Any] | None = None
    status: str | None = None
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        if entry.get("step_id") != args.step_id or entry.get("phase", "main") != args.phase:
            continue
        if entry.get("status") in ("completed", "recovered", "failed"):
            usage = entry.get("usage") or {}
            status = entry.get("status")
            break
    if usage is None:
        return 0

    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    cost = usage.get("cost_usd")
    model = usage.get("model")
    dur = usage.get("duration_ms")

    parts: list[str] = []
    if model and str(model) not in ("none", "unknown", ""):
        parts.append(f"model={model}")
    if inp or out:
        parts.append(f"tokens in={int(inp)} out={int(out)}")
    if isinstance(cost, (int, float)):
        parts.append(f"cost=${float(cost):.4f}")
    if isinstance(dur, (int, float)) and dur > 0:
        parts.append(_format_duration_ms(float(dur)))

    if not parts:
        if status in ("completed", "recovered") and not inp and not out:
            print("  usage: no tokens (inline/script)", file=sys.stderr)
        return 0
    print("  " + " · ".join(parts), file=sys.stderr)
    return 0


_EMPTY_USAGE = {"input_tokens": 0, "output_tokens": 0, "model": "none"}

def _load_usage_file(path: str) -> dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def cmd_build_payload(args: argparse.Namespace) -> int:
    payload: dict[str, Any]
    if args.kind == "script":
        payload = {
            "step_id": args.step_id,
            "phase": args.phase,
            "status": args.status,
            "outputs": {},
            "usage": dict(_EMPTY_USAGE),
        }
        if args.started_at:
            payload["started_at"] = args.started_at
    elif args.kind == "failed":
        payload = {
            "step_id": args.step_id,
            "phase": args.phase,
            "status": "failed",
            "agent": args.agent,
            "outputs": {
                "task_execution_result": {"status": "failed", "exit_code": args.exit_code},
            },
            "usage": dict(_EMPTY_USAGE),
        }
    elif args.kind == "agent":
        completion = json.loads(sys.stdin.read() or "{}")
        payload = dict(completion)
        payload["step_id"] = args.step_id
        payload["phase"] = args.phase
        payload["agent"] = args.agent
        raw_outputs = payload.get("outputs")
        if not isinstance(raw_outputs, dict):
            payload["outputs"] = {}
        # Agents sometimes place contract output keys beside status (not under outputs:).
        for key in ("learn_result", "phase_review_report", "discovery_result"):
            if key in payload and key not in payload["outputs"]:
                payload["outputs"][key] = payload.pop(key)
        if getattr(args, "usage_file", ""):
            file_usage = _load_usage_file(args.usage_file)
            if file_usage:
                payload["usage"] = {**dict(_EMPTY_USAGE), **file_usage}
        if not payload.get("usage"):
            payload["usage"] = dict(_EMPTY_USAGE)
        if args.started_at:
            payload["started_at"] = args.started_at
    else:
        sys.stderr.write(f"unknown payload kind: {args.kind}\n")
        return 2

    print(json.dumps(payload))
    return 0


_PI_SETTINGS_PATH = Path.home() / ".pi" / "agent" / "settings.json"


def cmd_pi_settings(args: argparse.Namespace) -> int:
    """Emit the pi agent's saved provider/model/thinking as JSON.

    Single source of truth for both the invoke_tool subprocess flags and the
    run-workflow.sh log line. Always exits 0; missing/unreadable settings
    produce {} so callers can fall back without branching on exit code.
    """
    out: dict[str, str] = {}
    if _PI_SETTINGS_PATH.is_file():
        try:
            data = json.loads(_PI_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for key, field in (
            ("defaultProvider", "provider"),
            ("defaultModel", "model"),
            ("defaultThinkingLevel", "thinking"),
        ):
            val = data.get(key)
            if val:
                out[field] = str(val)
    print(json.dumps(out))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="state_inspect")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("state-field", help="read a top-level key from state.yaml")
    p.add_argument("state_yaml")
    p.add_argument("key")
    p.add_argument("--fallback", default="", help="key to try if primary is missing/empty")
    p.add_argument("--default", default="", help="value to print when nothing resolves")
    p.set_defaults(func=cmd_state_field)

    p = sub.add_parser("workflow-meta", help="emit multi-line workflow metadata for agent prompts")
    p.add_argument("state_yaml")
    p.set_defaults(func=cmd_workflow_meta)

    p = sub.add_parser("log-step-usage", help="format usage line for last terminal step row")
    p.add_argument("state_yaml")
    p.add_argument("step_id")
    p.add_argument("phase", nargs="?", default="main")
    p.set_defaults(func=cmd_log_step_usage)

    p = sub.add_parser(
        "last-terminal-step",
        help="emit JSON for the latest terminal step_history entry",
    )
    p.add_argument("state_yaml")
    p.set_defaults(func=cmd_last_terminal_step)

    p = sub.add_parser("build-payload", help="emit JSON done-payload")
    p.add_argument("kind", choices=("script", "failed", "agent"))
    p.add_argument("--step-id", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--status", default="completed", help="script-kind only")
    p.add_argument("--agent", default="inline")
    p.add_argument("--exit-code", type=int, default=0, help="failed-kind only")
    p.add_argument("--stdout-file", default="", help="agent-kind only")
    p.add_argument(
        "--usage-file",
        default="",
        help="agent-kind: JSON file with adapter-normalized usage from invoke_tool",
    )
    p.add_argument(
        "--cwd",
        default="",
        help="agent-kind: tool working directory (legacy; unused for usage)",
    )
    p.add_argument("--started-at", default="")
    p.set_defaults(func=cmd_build_payload)

    p = sub.add_parser("pi-settings", help="emit pi agent's saved provider/model/thinking as JSON")
    p.set_defaults(func=cmd_pi_settings)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
