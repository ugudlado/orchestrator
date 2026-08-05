# orchestrator — workflow engine CLI.
#
# User-facing:
#   orchestrator <workflow> <ticket-id> …
#   orchestrator doctor
#   orchestrator report | graph
#
# Internal (still callable; omitted from usage):
#   run, next, done, config-path, validate-workflow, reset-step
#
# Exit codes for next/done dispatch protocol (ORC-45):
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
from pathlib import Path


def _usage() -> None:
    print(
        "Usage:\n"
        "  orchestrator <workflow> <ticket-id> [--repo PATH] [--models-config PATH] [flag=value ...]\n"
        "      Run a workflow from .orchestrator/<pack>/workflows/<workflow>.yaml.\n"
        "      Unique names: `orchestrator feature ORC-1`, `orchestrator bugfix ORC-2`.\n"
        "      Ambiguous names: `orchestrator mypack/feature ORC-1`.\n"
        "  orchestrator config pull <git-or-path> [pack] [--skills] [--ref REF]\n"
        "      Install into .orchestrator/<pack>/ (pack defaults to source basename).\n"
        "  orchestrator doctor [--models-config PATH]\n"
        "      Check that workflow config and model routing look correct.\n"
        "  orchestrator report --state <state.yaml> | --all [--repo PATH] [--json]\n"
        "  orchestrator graph <workflow>\n"
        "\n"
        "  --models-config PATH  Override models.yaml for this invocation\n"
        "                        (also: models.config=PATH)",
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

    Read-only: no state.yaml write. ``<schema>`` may be ``feature`` or
    ``mypack/feature``.
    """
    schema_name = args[0] if args else ""
    from orchestrator_next.graph import render_workflow_graph
    from orchestrator_next.paths import WorkflowRefError, resolve_workflow_ref
    try:
        _pack, workflow, cfg_root = resolve_workflow_ref(schema_name)
        os.environ["ORCHESTRATOR_CONFIG"] = str(cfg_root)
        mermaid_src = render_workflow_graph(workflow)
    except (FileNotFoundError, WorkflowRefError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(3)
    print(mermaid_src, end="")
    sys.exit(0)


def _workflow_subcommands() -> set[str]:
    """CLI tokens that map to a workflow: bare unique names + pack/workflow.

    Unique bare names are included. When a name appears in multiple packs,
    only the qualified ``pack/workflow`` forms are registered.
    """
    from orchestrator_next.paths import ConfigRootError, list_workflows

    try:
        index = list_workflows()
    except ConfigRootError:
        return set()
    out: set[str] = set()
    for workflow, hits in index.items():
        if len(hits) == 1:
            out.add(workflow)
        for pack_name, _root in hits:
            out.add(f"{pack_name}/{workflow}")
    return out


def _pin_config_from_state_file(state_yaml_path: str) -> None:
    """Set ORCHESTRATOR_CONFIG from state.config_pack when unset (multi-pack)."""
    if os.environ.get("ORCHESTRATOR_CONFIG"):
        return
    try:
        import yaml as _yaml
        raw = _yaml.safe_load(Path(state_yaml_path).read_text(encoding="utf-8")) or {}
    except Exception:
        return
    pack = raw.get("config_pack") or ""
    repo = raw.get("repo_root") or os.environ.get("REPO_ROOT") or ""
    if pack and repo:
        os.environ["ORCHESTRATOR_CONFIG"] = str(Path(repo) / ".orchestrator" / pack)
    if repo and not os.environ.get("REPO_ROOT"):
        os.environ["REPO_ROOT"] = str(repo)


def _default_repo_root_env() -> None:
    """Vendored packs live under <repo>/.orchestrator/<pack>/ — needs REPO_ROOT."""
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
    from orchestrator_next.models_config_cli import consume_models_config_argv

    args = sys.argv[1:]
    # config-path needs no config root set — it's how you discover the value
    # to put in ORCHESTRATOR_CONFIG in the first place.
    if args and args[0] == "config-path":
        from orchestrator_next.paths import ConfigRootError, config_root
        try:
            print(config_root())
        except ConfigRootError as exc:
            print(exc, file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    if args and args[0] == "config":
        if len(args) < 2 or args[1] != "pull":
            print(
                "usage: orchestrator config pull <git-or-path> [pack] "
                "[--repo PATH] [--ref REF] [--skills]",
                file=sys.stderr,
            )
            sys.exit(3)
        from orchestrator_next.config_pull import main as _config_pull_main
        sys.exit(_config_pull_main(args[2:]))
    _default_repo_root_env()
    _wf_subcommands = _workflow_subcommands()
    _core_verbs = (
        "next", "done", "graph", "doctor", "reset-step", "run", "validate-workflow",
        "report", "acp", "acp-run",
    )
    if not args or (args[0] not in _core_verbs and args[0] not in _wf_subcommands):
        _usage()
    # ORC-ACP: `orchestrator acp` — Agent Client Protocol server over stdio.
    if args[0] == "acp":
        from orchestrator_next.acp_server import main as acp_main
        sys.exit(acp_main())
    # ORC-ACP: `orchestrator acp-run <topic>` — drive the ACP server as a client.
    if args[0] == "acp-run":
        from orchestrator_next.acp_server import acp_run_main
        sys.exit(acp_run_main(args[1:]))
    # Apply --models-config early so every verb that resolves routes sees it.
    # `run` / workflow subcommands also consume it inside run_cmd; applying here
    # is idempotent and covers next/doctor.
    verb, *rest = args
    rest = consume_models_config_argv(rest)
    args = [verb, *rest]
    # Every verb except doctor needs a second argument.
    if len(args) < 2 and args[0] != "doctor":
        _usage()
    # Workflow tokens (bare or pack/workflow) → run --schema <ref>.
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

    if args[0] == "done":
        # argv: done <state.yaml> …
        if len(args) >= 2:
            _pin_config_from_state_file(args[1])
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

    # Pin the pack that seeded this run (multi-pack layouts).
    _pin_config_from_state_file(state_yaml_path)

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
