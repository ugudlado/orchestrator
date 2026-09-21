# orchestrator — workflow engine CLI (core: step generator + step recorder).
#
# User-facing:
#   orchestrator start | step | done | status
#   orchestrator approve | resume | cancel | reset-step
#   orchestrator graph
#
# Everything else — model execution and selection, running scripts, metrics,
# cost, logs, reports — belongs to the driver loop that calls these verbs.
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
        "Usage — every verb takes --state DIR (or ORCHESTRATOR_STATE) and\n"
        "prints JSON:\n"
        "  orchestrator start <recipe> <slug> [--config PATH] [--inputs JSON]\n"
        "                                     [--ticket-id ID]\n"
        "  orchestrator step <run>\n"
        "  orchestrator done <run> <step_id> [--out JSON] [--status S]   (judgment)\n"
        "  orchestrator done <run> <step_id> --exit-code N\n"
        "                                    [--stdout-file PATH]        (exec)\n"
        "  orchestrator approve <run> <token> [--edits JSON]   (resume a gate)\n"
        "  orchestrator resume <run> \"<text>\"                  (answer await_input)\n"
        "  orchestrator cancel <run>                           (abort a run)\n"
        "  orchestrator reset-step <run> <step-id>\n"
        "  orchestrator status <run>\n"
        "  orchestrator graph <workflow>                       (Mermaid DAG)\n"
        "\n"
        "The pack root is an input: pass --config <path> to `start` (it is then\n"
        "stored on the run), or set ORCHESTRATOR_CONFIG.",
        file=sys.stderr,
    )
    sys.exit(3)


def _graph_verb(args: list[str]) -> None:
    """`orchestrator graph <schema>` — print a Mermaid flowchart, exit 0.

    Read-only: no state.yaml write.
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


def _default_repo_root_env() -> None:
    """Default REPO_ROOT to the cwd when the caller named none.

    Artifacts and the skills search resolve against it. The engine does not
    shell out to git to discover it: a driver that means a particular repo
    exports REPO_ROOT (or runs the verb there).
    """
    if os.environ.get("REPO_ROOT") or os.environ.get("ORCHESTRATOR_REPO_ROOT"):
        return
    os.environ["REPO_ROOT"] = os.getcwd()


def main() -> None:
    args = sys.argv[1:]
    _default_repo_root_env()
    _protocol_verbs = (
        "start", "step", "done", "status", "approve", "cancel",
        "resume", "reset-step",
    )
    if not args or args[0] not in (*_protocol_verbs, "graph"):
        _usage()

    if args[0] in _protocol_verbs:
        from orchestrator_next.protocol import main as _protocol_main
        sys.exit(_protocol_main(args[0], args[1:]))

    # Read-only DAG-visibility verb — no state.yaml write.
    if len(args) < 2:
        _usage()
    _graph_verb(args[1:])


if __name__ == "__main__":
    main()
