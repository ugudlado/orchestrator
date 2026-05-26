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
import re
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


def cmd_workflow_meta(args: argparse.Namespace) -> int:
    raw = _load_state(args.state_yaml) or {}
    p = Path(args.state_yaml)
    cid = raw.get("change_id") or raw.get("slug") or p.parent.name
    wt = raw.get("worktree_path") or ""
    schema = raw.get("schema") or ""
    repo = raw.get("repo_root") or ""

    print(f"Workflow: change_id={cid} schema={schema} repo_root={repo}")
    print(f"state_yaml_path={p}")
    if wt:
        print(f"worktree_path={wt}")
        print(f"artifact_dir={wt}/spec/changes/{cid}")
        print(f"workflow_state_dir={wt}/spec/changes")
    else:
        print(f"artifact_dir={repo}/spec/changes/{cid}")
        print(f"workflow_state_dir={repo}/spec/changes")
    return 0


def _format_duration_ms(ms: float) -> str:
    if ms >= 60_000:
        return f"duration={ms / 60_000:.1f}m"
    if ms >= 1_000:
        return f"duration={ms / 1_000:.1f}s"
    return f"duration={int(ms)}ms"


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
            print("  usage: no tokens (inline/script)")
        return 0
    print("  " + " · ".join(parts))
    return 0


_EMPTY_USAGE = {"input_tokens": 0, "output_tokens": 0, "model": "none"}

_AGENT_ID_FROM_TASK_RESULT_RE = re.compile(r"agentId:\s*([a-f0-9]{17})")


def _extract_agent_id_from_stdout(text: str | None) -> str | None:
    if not text:
        return None
    match = _AGENT_ID_FROM_TASK_RESULT_RE.search(text)
    return match.group(1) if match else None


def _usage_has_tokens(usage: dict[str, Any]) -> bool:
    return (
        (isinstance(usage.get("input_tokens"), (int, float)) and usage["input_tokens"] > 0)
        or (isinstance(usage.get("output_tokens"), (int, float)) and usage["output_tokens"] > 0)
    )


def _orchestrator_scripts_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "scripts"


def _usage_from_newest_driver_jsonl(cwd: str) -> dict[str, Any]:
    """Billing-truth usage from the newest Claude Code session JSONL for cwd."""
    if not cwd:
        return {}
    scripts_dir = str(_orchestrator_scripts_dir())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from orchestrator_next.jsonl_usage import _aggregate, _projects_root, _repo_slug
    except ImportError:
        return {}
    slug_dir = _projects_root() / _repo_slug(cwd)
    if not slug_dir.is_dir():
        return {}
    jsonls = list(slug_dir.glob("*.jsonl"))
    if not jsonls:
        return {}
    newest = max(jsonls, key=lambda p: p.stat().st_mtime)
    return _aggregate(newest) or {}


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
        stdout_text = ""
        if args.stdout_file and os.path.isfile(args.stdout_file):
            with open(args.stdout_file, encoding="utf-8", errors="replace") as f:
                stdout_text = f.read()
        if stdout_text and _extract_agent_id_from_stdout(stdout_text):
            payload["agent_task_result"] = stdout_text
        usage = payload.get("usage") or {}
        if not _usage_has_tokens(usage) and getattr(args, "cwd", ""):
            jsonl_usage = _usage_from_newest_driver_jsonl(args.cwd)
            if jsonl_usage:
                usage = {**dict(_EMPTY_USAGE), **usage, **jsonl_usage}
                payload["usage"] = usage
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

    p = sub.add_parser("build-payload", help="emit JSON done-payload")
    p.add_argument("kind", choices=("script", "failed", "agent"))
    p.add_argument("--step-id", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--status", default="completed", help="script-kind only")
    p.add_argument("--agent", default="inline")
    p.add_argument("--exit-code", type=int, default=0, help="failed-kind only")
    p.add_argument("--stdout-file", default="", help="agent-kind only")
    p.add_argument(
        "--cwd",
        default="",
        help="agent-kind: tool working directory for driver JSONL usage fallback",
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
