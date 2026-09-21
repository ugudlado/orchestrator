# orchestrator — the workflow engine CLI. One verb:
#
#   orchestrator next <workflow> --config PATH [...]   what runs next
#
# The engine is a pure function of the workflow config plus the step that
# just ran. It keeps no state: history, attempts, gate approvals, worktrees
# and reports all belong to the driver that calls it.
"""Entry point for the `orchestrator` CLI.

Reached three ways, all equivalent: the `orchestrator` console script of a
wheel install, the bin/orchestrator dev-checkout shim, and
`python -m orchestrator_next`.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

USAGE = """\
Usage:
  orchestrator next <workflow> --config PATH [--slug S]
      [--after STEP (--status completed|failed|abandoned
                     | --exit-code N [--stdout-file F])
       [--out JSON] [--attempt N]]

Prints JSON: the step to run, or {"status": "done"|"needs_you"|"error"}.
With no --after it returns the workflow's first step.

Artifact paths (in/out, a gate's show) are RELATIVE — join them to the
worktree you are running in. Run the CLI with that worktree as the working
directory so --out artifact checks resolve there.
"""

EXIT_ERROR = 3


def _pop_flag(args: list[str], flag: str) -> str | None:
    """Remove ``--flag value`` from args and return the value."""
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args):
        raise ValueError(f"{flag} needs a value")
    value = args[i + 1]
    del args[i:i + 2]
    return value


def _next_verb(args: list[str]) -> int:
    from orchestrator_next.nextstep import NextError, next_step

    if not args or args[0].startswith("-"):
        raise ValueError("usage: orchestrator next <workflow> --config PATH [...]")
    workflow, rest = args[0], args[1:]

    config = _pop_flag(rest, "--config") or os.environ.get("ORCHESTRATOR_CONFIG", "")
    if not config:
        raise ValueError(
            "no config root — pass --config <pack> or set ORCHESTRATOR_CONFIG"
        )
    config_root = Path(config).expanduser()
    if not (config_root / "workflows").is_dir():
        raise ValueError(f"--config {config!r} is not a pack root (no workflows/)")

    slug = _pop_flag(rest, "--slug") or ""
    after = _pop_flag(rest, "--after") or ""
    status = _pop_flag(rest, "--status") or ""
    stdout_file = _pop_flag(rest, "--stdout-file") or ""
    raw_exit = _pop_flag(rest, "--exit-code")
    raw_attempt = _pop_flag(rest, "--attempt")
    raw_out = _pop_flag(rest, "--out")

    try:
        exit_code = None if raw_exit is None else int(raw_exit)
    except ValueError:
        raise ValueError(f"--exit-code takes a whole number, not {raw_exit!r}") from None
    try:
        attempt = 1 if raw_attempt is None else int(raw_attempt)
    except ValueError:
        raise ValueError(f"--attempt takes a whole number, not {raw_attempt!r}") from None

    out = {}
    if raw_out is not None:
        try:
            out = json.loads(raw_out)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--out must be valid JSON — {exc}") from None
        if not isinstance(out, dict):
            raise ValueError("--out must be a JSON object")

    try:
        result = next_step(
            workflow, config_root=config_root, slug=slug,
            after=after, status=status, exit_code=exit_code,
            stdout_file=stdout_file, out=out, attempt=attempt,
        )
    except NextError as exc:
        raise ValueError(str(exc)) from None

    print(json.dumps(result, sort_keys=True, indent=2, default=str))
    return 0


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE, file=sys.stderr)
        sys.exit(EXIT_ERROR)

    verb, rest = args[0], list(args[1:])
    try:
        if verb == "next":
            sys.exit(_next_verb(rest))
    except ValueError as exc:
        # Usage and infrastructure errors are JSON on stdout too, so a driver
        # never has to parse stderr. A protocol status (done / needs_you /
        # error-from-a-step) exits 0; only a broken call exits non-zero.
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        sys.exit(EXIT_ERROR)

    print(USAGE, file=sys.stderr)
    sys.exit(EXIT_ERROR)


if __name__ == "__main__":
    main()
