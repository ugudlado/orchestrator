# orchestrator — dispatcher CLI for the orchestrator workflow engine.
#
# Usage:
#   orchestrator next <state.yaml>
#
# Exit codes (ORC-45 two-path dispatch protocol):
#   0 + JSON with agent key  — agent step; driver spawns Agent tool
#   0 + no JSON              — inline script ran and recorded; driver loops
#   1                        — workflow complete; driver reads state.yaml
#   2                        — step blocked; driver reads state.yaml
#   3                        — ContractDispatchError or other error
"""Entry point for the `orchestrator` CLI.

Reached three ways, all equivalent: the `orchestrator` console script of a
wheel install, the bin/orchestrator dev-checkout shim, and
`python -m orchestrator_next`.
"""
from __future__ import annotations

import os
import sys


def _usage() -> None:
    print(
        "Usage:\n"
        "  orchestrator <workflow> <ticket-id> [--repo PATH] [flag=value ...]\n"
        "      Run a workflow by name (config/workflows/<workflow>.yaml), e.g.\n"
        "      `orchestrator feature ORC-1`, `orchestrator bugfix ORC-2`,\n"
        "      `orchestrator patch ORC-3`, `orchestrator design ORC-4`,\n"
        "      `orchestrator implement ORC-5`, `orchestrator complete ORC-6`\n"
        "      (see config/workflows/<workflow>.yaml).\n"
        "  orchestrator run <ticket-id> [--schema feature|bugfix] [--repo PATH] [flag=value ...]\n"
        "      Explicit form when not using a workflow subcommand name.\n"
        "  orchestrator next <state.yaml>\n"
        "  orchestrator done <state.yaml>   # JSON payload on stdin\n"
        "  orchestrator graph <schema>              # Mermaid flowchart of a workflow schema\n"
        "  orchestrator validate-workflow <schema-name>\n"
        "  orchestrator config-path   # print the bundled/checkout config dir (for ORCHESTRATOR_CONFIG)\n"
        "  orchestrator report --state <state.yaml> | --all [--repo PATH] [--json]\n"
        "  orchestrator doctor",
        file=sys.stderr,
    )
    sys.exit(3)


def _run_verb(argv: list[str]) -> None:
    """`orchestrator run` — ticket-driven workflow driver (in-process loop)."""
    from orchestrator_next.paths import ConfigRootError
    from orchestrator_next.run_loop import run_cmd
    try:
        raise SystemExit(run_cmd(argv))
    except ConfigRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(7)


def _append_in_progress_state_entry_if_absent(
    state_yaml_path: str,
    *,
    step_id: str,
    phase: str,
    attempt: int,
    agent: str,
    started_at: str,
) -> None:
    """Append an in_progress entry to state.yaml.step_history if not already present.

    Checks for an existing entry matching (step_id, phase, status='in_progress').
    If one already exists, returns without writing. Otherwise appends the new entry
    and writes back via parser.safe_write_yaml (pre-write-bytes corruption guard).

    This is a narrow state.yaml writer — separate from the main `record` writer.
    """
    from pathlib import Path as _Path

    import yaml as _yaml  # noqa: PLC0415 — lazily import; record already imported at top of its module

    from orchestrator_next.parser import safe_write_yaml as _safe_write_yaml

    with open(state_yaml_path, "rb") as _f:
        _pre_write_bytes = _f.read()

    try:
        _state_raw = _yaml.safe_load(_pre_write_bytes.decode("utf-8")) or {}
    except _yaml.YAMLError:
        # Pre-parse failure — do not write; let the next dispatch surface the corruption.
        return

    _history = list(_state_raw.get("step_history") or [])

    # Check if an in_progress entry already exists for (step_id, phase).
    # Also skip the pre-stamp when a terminal entry (completed/failed/escalate_to_architect)
    # already exists for the same (step_id, phase, attempt) — the dispatcher saw the row
    # but a stale state read or path-resolution mismatch caused it to dispatch the same
    # step again. Pre-stamping in that case creates an orphan in_progress row at the
    # tail that masks the prior completion on the next `next` call.
    _TERMINAL_STATUSES = {"completed", "failed", "escalate_to_architect", "blocked"}
    for _e in _history:
        if not isinstance(_e, dict):
            continue
        if _e.get("step_id") != step_id or _e.get("phase") != phase:
            continue
        if _e.get("status") == "in_progress":
            return  # already present — no write needed
        if _e.get("status") in _TERMINAL_STATUSES and _e.get("attempt") == attempt:
            return  # terminal entry exists for this attempt — do not orphan it

    _history.append({
        "step_id": step_id,
        "phase": phase,
        "status": "in_progress",
        "agent": agent,
        "attempt": attempt,
        "started_at": started_at,
    })
    _state_raw["step_history"] = _history

    try:
        _safe_write_yaml(_Path(state_yaml_path), _state_raw, _pre_write_bytes)
    except _yaml.YAMLError:
        pass  # pre-write bytes already restored by safe_write_yaml


def _graph_verb(args: list[str]) -> None:
    """`orchestrator graph <schema>` — print a Mermaid flowchart, exit 0.

    Read-only: no state.yaml write.
    """
    schema_name = args[0] if args else ""
    from orchestrator_next.graph import render_workflow_graph
    try:
        mermaid_src = render_workflow_graph(schema_name)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(3)
    print(mermaid_src, end="")
    sys.exit(0)


def _workflow_subcommands() -> set[str]:
    """ORC-108: each config/workflows/<name>.yaml is a CLI subcommand.

    `orchestrator feature <id>` == `orchestrator run <id> --schema feature`.
    Resolved dynamically so adding a workflow file adds a subcommand with no
    code change. Falls back to the empty set if the dir is unreadable.
    """
    import glob
    from orchestrator_next.paths import config_root
    try:
        wf_dir = str(config_root() / "workflows")
        return {
            os.path.splitext(os.path.basename(p))[0]
            for p in glob.glob(os.path.join(wf_dir, "*.yaml"))
        }
    except (OSError, RuntimeError):
        return set()


def _default_repo_root_env() -> None:
    """config_root()'s repo-local fallback (<repo>/.orchestrator/config/) needs
    a repo root to check. `run` derives one from --repo/spec/project.yaml;
    other verbs (doctor, models) have no such flag, so default REPO_ROOT
    to the git toplevel (else cwd) whenever it isn't already set — a no-op if
    the repo has no vendored config, since config_root() only uses it when
    <repo>/.orchestrator/config/workflows/ actually exists.
    """
    if os.environ.get("REPO_ROOT") or os.environ.get("ORCHESTRATOR_REPO_ROOT"):
        return
    import subprocess
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        top = ""
    os.environ["REPO_ROOT"] = top or os.getcwd()


def main() -> None:
    args = sys.argv[1:]
    # config-path needs no config root set — it's how you discover the value
    # to put in ORCHESTRATOR_CONFIG in the first place.
    if args and args[0] == "config-path":
        from orchestrator_next.paths import bundled_config_root
        print(bundled_config_root())
        sys.exit(0)
    _default_repo_root_env()
    _wf_subcommands = _workflow_subcommands()
    _core_verbs = (
        "next", "done", "graph", "doctor", "models", "reset-step", "run", "validate-workflow",
        "report",
    )
    if not args or (args[0] not in _core_verbs and args[0] not in _wf_subcommands):
        _usage()
    # Every verb except doctor/models needs a second argument.
    if len(args) < 2 and args[0] not in ("doctor", "models"):
        _usage()
    # ORC-108: each config/workflows/<name>.yaml is a CLI subcommand → orchestrator-run.sh.
    if args[0] in _wf_subcommands:
        _run_verb([args[1], "--schema", args[0], *args[2:]])

    if args[0] == "run":
        _run_verb(args[1:])

    if args[0] == "doctor":
        from orchestrator_next.doctor import _doctor_main
        sys.exit(_doctor_main(args[1:]))

    if args[0] == "report":
        from orchestrator_next.report import main as _report_main
        sys.exit(_report_main(args[1:]))

    if args[0] == "models":
        if args[1:2] == ["init"]:
            from orchestrator_next.models_init import main as _models_init_main
            sys.exit(_models_init_main(args[2:]))
        from orchestrator_next.models_verb import main as _models_main
        sys.exit(_models_main(args[1:]))

    if args[0] == "done":
        from orchestrator_next.record import main as record_main
        sys.exit(record_main(sys.argv[1:]))

    # ORC-63: read-only DAG-visibility verb — no state.yaml write.
    if args[0] == "graph":
        _graph_verb(args[1:])

    # reset-step verb — reset a step and all subsequent steps to pending.
    if args[0] == "reset-step":
        if len(args) < 3:
            print("usage: orchestrator reset-step <step-id> <state.yaml>", file=sys.stderr)
            sys.exit(1)
        from orchestrator_next.reset_step import reset_step as _reset_step
        try:
            _reset_step(args[1], args[2])
        except (ValueError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if args[0] == "validate-workflow":
        from orchestrator_next.validate_workflow import main as _vw_main
        sys.exit(_vw_main(args[1:]))

    state_yaml_path = args[1]

    try:
        from orchestrator_next.parser import load_state, ContractNotFoundError as ContractDispatchError
        from orchestrator_next.dispatch import dispatch, emit_json
    except ImportError as exc:
        print(f"error: failed to import orchestrator_next — {exc}", file=sys.stderr)
        sys.exit(3)

    try:
        state = load_state(state_yaml_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(3)
    except Exception as exc:  # noqa: BLE001 — catch-all for malformed YAML; diagnosed below
        print(f"error: failed to parse state.yaml — {exc}", file=sys.stderr)
        sys.exit(3)

    try:
        action, exit_code = dispatch(state, state_yaml_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(3)
    except ContractDispatchError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(3)
    except Exception as exc:  # noqa: BLE001
        print(f"error: dispatch failed — {exc}", file=sys.stderr)
        sys.exit(3)

    if exit_code in (1, 2):
        sys.exit(exit_code)

    # --- Agent path (exit 0 + JSON with model key) ---
    if action.get("model"):
        from datetime import datetime, timezone
        _started_at = action.get("started_at") or datetime.now(timezone.utc).isoformat()
        try:
            _append_in_progress_state_entry_if_absent(
                state_yaml_path,
                step_id=action["step_id"],
                phase=action["phase"],
                attempt=int(action["attempt"]),
                agent=action["model"],
                started_at=_started_at,
            )
        except Exception as _ape:  # noqa: BLE001
            print(f"warning: state.yaml pending append failed — {_ape}", file=sys.stderr)

        # Running cost total re-derived from step_history[].usage.cost_usd (no
        # DuckDB). Additive field on the action JSON plus a human stderr line so
        # the self-driven caller shows it mid-run.
        try:
            from orchestrator_next.pricing import sum_cost_usd, format_cost_so_far
            action["estimated_cost_so_far"] = round(sum_cost_usd(state.raw), 6)
            print(format_cost_so_far(state.raw), file=sys.stderr)
        except Exception as _cse:  # noqa: BLE001
            print(f"warning: cost-so-far computation failed — {_cse}", file=sys.stderr)

        print(emit_json(action), end="")
        sys.exit(0)

    # --- Inline script path (exit 0 + no JSON) ---
    # Single canonical executor: the same run_script_step the in-process loop
    # uses. Standalone `orchestrator next` stays a working stepper; the divergent
    # duplicate that used to live here is gone (one behavior for both callers).
    if action.get("run"):
        from orchestrator_next.run_loop import run_script_step as _run_script_step

        _ok, _new_path = _run_script_step(action, state_yaml_path=state_yaml_path)
        sys.exit(0 if _ok else 3)


    sys.exit(exit_code)


if __name__ == "__main__":
    main()
