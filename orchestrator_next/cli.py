# orchestrator — workflow engine CLI (protocol v2; see docs/protocol-v2.md).
#
# User-facing:
#   orchestrator start | step | done | approve | cancel | status | events
#   orchestrator run --headless | headless
#   orchestrator doctor | report | graph | validate-workflow
#   orchestrator config pull | config-path | state | pack
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
        "Usage (protocol v2 — see docs/protocol-v2.md):\n"
        "  orchestrator start <recipe> <slug> [--inputs JSON] [--ticket-id ID] --json\n"
        "  orchestrator step <run> --json\n"
        "  orchestrator done <run> <step_id> --out JSON --usage JSON [--status S]\n"
        "  orchestrator approve <run> <token> [--edits JSON]   (resume a gate)\n"
        "  orchestrator cancel <run>                           (abort a run)\n"
        "  orchestrator status <run> --json | orchestrator events <run> --json\n"
        "  orchestrator run --headless <recipe> <slug>   (engine drives the model)\n"
        "  orchestrator headless <run>                   (resume a headless run)\n"
        "\n"
        "  orchestrator config pull <git-or-path> [pack] [--skills] [--ref REF]\n"
        "      Install into .orchestrator/<pack>/ (pack defaults to source basename).\n"
        "  orchestrator doctor [--models-config PATH]\n"
        "  orchestrator report --state <state.yaml> | --all [--repo PATH] [--json]\n"
        "  orchestrator graph <workflow> | orchestrator validate-workflow <workflow>\n"
        "  orchestrator state <list|show|migrate|project> | orchestrator pack …\n"
        "  orchestrator reset-step <step-id> <state.yaml>\n"
        "\n"
        "  --models-config PATH  Override models.yaml for this invocation\n"
        "                        (also: models.config=PATH)",
        file=sys.stderr,
    )
    sys.exit(3)


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


def _state_verb(argv: list[str]) -> int:
    """`orchestrator state <migrate|list|show|project>` — state-store admin.

    The store is selected by URL, so every subcommand takes one:
      file:  /path/to/x_state.yaml        (or a bare path)
      sqlite: sqlite:///path/state.db#<run-id>
      pg:     postgresql://user@host/db#<run-id>

    ORCHESTRATOR_STATE_URL supplies a default store for `list` and `migrate`.
    """
    import json as _json
    from orchestrator_next import state_store as ss

    usage = (
        "usage:\n"
        "  orchestrator state list   [<store-url>]\n"
        "  orchestrator state show   <handle> [--json]\n"
        "  orchestrator state migrate <state.yaml>... --to <store-url>\n"
        "  orchestrator state project <handle> <dest.yaml>\n"
    )
    if not argv:
        sys.stderr.write(usage)
        return 3
    sub, rest = argv[0], argv[1:]

    try:
        if sub == "list":
            url = rest[0] if rest else (ss.default_state_url() or os.getcwd())
            store, handle = ss.open_store(url if "#" in url or "://" in url else url)
            runs = store.list_runs(handle)
            if not runs:
                print("no runs found")
                return 0
            print(f"{'run':<44}{'schema':<10}{'status':<10}{'steps':>6}")
            for r in runs:
                print(f"{str(r['run_id'])[-43:]:<44}{r['schema']:<10}"
                      f"{r['status']:<10}{r['steps']:>6}")
            return 0

        if sub == "show":
            if not rest:
                sys.stderr.write(usage)
                return 3
            doc, _token, _h = ss.load_doc(rest[0])
            if "--json" in rest:
                print(_json.dumps(doc, indent=2, sort_keys=False))
            else:
                import yaml as _yaml
                print(_yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
            return 0

        if sub == "migrate":
            if "--to" not in rest:
                sys.stderr.write(usage)
                return 3
            cut = rest.index("--to")
            sources, target = rest[:cut], rest[cut + 1:]
            if not sources or not target:
                sys.stderr.write(usage)
                return 3
            dest = target[0]
            failures = 0
            for src in sources:
                try:
                    handle = ss.import_yaml(src, dest)
                    print(f"  migrated {src} -> {handle}")
                except (FileExistsError, ValueError, OSError) as exc:
                    sys.stderr.write(f"  SKIP {src}: {exc}\n")
                    failures += 1
            return 1 if failures and failures == len(sources) else 0

        if sub == "project":
            if len(rest) < 2:
                sys.stderr.write(usage)
                return 3
            out = ss.project_yaml(rest[0], rest[1])
            print(out)
            return 0

    except (ss.StateNotFoundError, ss.StateConflictError, ValueError) as exc:
        sys.stderr.write(f"orchestrator state: {exc}\n")
        return 3

    sys.stderr.write(usage)
    return 3


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
        sub = args[1] if len(args) > 1 else ""
        if sub == "pull":
            from orchestrator_next.config_pull import main as _config_pull_main
            sys.exit(_config_pull_main(args[2:]))
        if sub == "update":
            from orchestrator_next.config_pull import update_main as _config_update_main
            sys.exit(_config_update_main(args[2:]))
        print(
            "usage: orchestrator config pull <git-or-path> [pack] "
            "[--repo PATH] [--ref REF] [--skills]\n"
            "       orchestrator config update [pack] [--repo PATH] [--ref REF] [--yes]",
            file=sys.stderr,
        )
        sys.exit(3)
    _default_repo_root_env()
    _core_verbs = (
        # protocol v2 (docs/protocol-v2.md §3)
        "start", "step", "done", "status", "events", "approve", "cancel",
        "run", "headless",
        # inspection / admin
        "graph", "doctor", "validate-workflow", "report", "state", "pack",
        "reset-step",
    )
    if not args or args[0] not in _core_verbs:
        _usage()
    # Apply --models-config early so every verb that resolves routes sees it.
    verb, *rest = args
    rest = consume_models_config_argv(rest)
    args = [verb, *rest]

    if args[0] == "state":
        sys.exit(_state_verb(args[1:]))

    # --- protocol v2 verbs (docs/protocol-v2.md §3) ------------------------
    if args[0] in ("start", "step", "done", "status", "events", "approve", "cancel"):
        from orchestrator_next.protocol import main as _protocol_main
        sys.exit(_protocol_main(args[0], args[1:]))

    # Every verb except doctor/pack needs a second argument.
    if len(args) < 2 and args[0] not in ("doctor", "pack"):
        _usage()

    if args[0] == "run":
        # `run` exists only to drive the model in-process; the engine never
        # self-drives a harness step (protocol-v2 principle 1).
        if "--headless" not in args:
            print("error: `orchestrator run` requires --headless; use "
                  "`orchestrator start` for harness-driven runs "
                  "(docs/protocol-v2.md §3)", file=sys.stderr)
            sys.exit(3)
        from orchestrator_next.headless import run_headless_cmd
        sys.exit(run_headless_cmd([a for a in args[1:] if a != "--headless"]))
    if args[0] == "headless":
        from orchestrator_next.headless import resume_headless_cmd
        sys.exit(resume_headless_cmd(args[1:]))

    if args[0] == "doctor":
        from orchestrator_next.doctor import _doctor_main
        sys.exit(_doctor_main(args[1:]))

    if args[0] == "pack":
        if len(args) > 1 and args[1] == "publish-scenarios":
            from orchestrator_next.publish_scenarios import publish_scenarios_cmd
            sys.exit(publish_scenarios_cmd(args[2:]))
        from orchestrator_next.pack_export import pack_export_cmd
        sys.exit(pack_export_cmd(args[1:]))

    if args[0] == "report":
        from orchestrator_next.report import main as _report_main
        sys.exit(_report_main(args[1:]))

    # Read-only DAG-visibility verb — no state.yaml write.
    if args[0] == "graph":
        _graph_verb(args[1:])

    # Reset a step and everything declared after it back to pending. Works on
    # a v2 run: `orchestrator status <run>` prints the state path to pass here.
    if args[0] == "reset-step":
        if len(args) < 3:
            print("usage: orchestrator reset-step <step-id> <state.yaml>", file=sys.stderr)
            sys.exit(3)
        from orchestrator_next.reset_step import reset_step as _reset_step
        try:
            _reset_step(args[1], args[2])
        except (ValueError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(3)
        sys.exit(0)

    if args[0] == "validate-workflow":
        from orchestrator_next.validate_workflow import main as _vw_main
        sys.exit(_vw_main(args[1:]))

    _usage()


if __name__ == "__main__":
    main()
