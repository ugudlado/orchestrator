#!/usr/bin/env python3
"""ingest-pricing.py — Insert a new row into the DuckDB `pricing` table.

Usage:
  python scripts/ingest-pricing.py \\
    --model claude-haiku-5-0 \\
    --input-usd 1.0 --output-usd 5.0 \\
    --cache-read-usd 0.1 --cache-creation-usd 1.25 \\
    --effective-from 2026-06-01T00:00:00 [--is-local] [--db PATH]

On success: prints "inserted <model> @ <effective_from>" and exits 0.
On error: prints to stderr and exits non-zero.

Import path:
  The script resolves `orchestrator_next.upsert` via sys.path manipulation before
  any import. Primary path: $ORCHESTRATOR_HOME/config/scripts. Fallback: walks up
  from __file__ to find the repo root (directory containing config/scripts/orchestrator_next).
  The walk-up fallback is developer convenience; ORCHESTRATOR_HOME is the primary.
"""
import os
import sys


def _resolve_orchestrator_home():
    """Return the repo root that contains config/scripts/orchestrator_next/, or None.

    Checks (in order):
    1. $ORCHESTRATOR_HOME env var — but only if it has config/scripts/orchestrator_next/
       (the env var may point to a deployed config dir without the module source).
    2. Walk up from __file__ until config/scripts/orchestrator_next/ is found.
    """
    env = os.environ.get("ORCHESTRATOR_HOME")
    if env and os.path.isdir(
        os.path.join(env, "config", "scripts", "orchestrator_next")
    ):
        return env
    # Walk up from __file__ to find the repo root.
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    while True:
        parent = os.path.dirname(cur)
        if parent == cur:  # filesystem root
            break
        if os.path.isdir(os.path.join(cur, "config", "scripts", "orchestrator_next")):
            return cur
        cur = parent
    return None


_home = _resolve_orchestrator_home()
if _home:
    _scripts_dir = os.path.join(_home, "config", "scripts")
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)

import argparse  # noqa: E402
import datetime as dt  # noqa: E402
import duckdb  # noqa: E402
from orchestrator_next.upsert import ensure_schema  # noqa: E402

# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

_EPILOG = """\
Example:
  python scripts/ingest-pricing.py \\
    --model claude-haiku-5-0 \\
    --input-usd 1.0 --output-usd 5.0 \\
    --cache-read-usd 0.1 --cache-creation-usd 1.25 \\
    --effective-from 2026-06-01T00:00:00
"""


def _build_parser():
    p = argparse.ArgumentParser(
        prog="ingest-pricing.py",
        description="Insert a new pricing row with a fresh effective_from timestamp.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--model", required=True, help="Model ID (e.g. claude-haiku-5-0)")
    p.add_argument("--input-usd", type=float, required=True,
                   help="Input token rate in USD per million tokens")
    p.add_argument("--output-usd", type=float, required=True,
                   help="Output token rate in USD per million tokens")
    p.add_argument("--cache-read-usd", type=float, required=True,
                   help="Cache read rate in USD per million tokens")
    p.add_argument("--cache-creation-usd", type=float, default=None,
                   help="Cache creation rate in USD per million tokens (optional)")
    p.add_argument("--is-local", action="store_true", default=False,
                   help="Mark as a local model (default: False)")
    p.add_argument(
        "--effective-from",
        default=None,
        help="ISO 8601 timestamp for this pricing row (default: UTC now)",
    )
    p.add_argument(
        "--db",
        default=None,
        help="Path to DuckDB file (default: $METRICS_DB, then "
             "$ORCHESTRATOR_HOME/metrics.duckdb, then $HOME/.state/orchestrator.duckdb)",
    )
    return p


def _resolve_db_path(args_db):
    """Return the DuckDB path to use."""
    if args_db:
        return args_db
    explicit = os.environ.get("METRICS_DB")
    if explicit:
        return explicit
    if _home:
        return os.path.join(_home, "metrics.duckdb")
    home = os.environ.get("HOME", "")
    return os.path.join(home, ".state", "orchestrator.duckdb")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(args):
    """Validate args. Returns error message string or None if valid."""
    errors = []
    for name, value in [
        ("--input-usd", args.input_usd),
        ("--output-usd", args.output_usd),
        ("--cache-read-usd", args.cache_read_usd),
    ]:
        if value < 0:
            errors.append(f"{name} must be >= 0 (got {value})")
    if args.cache_creation_usd is not None and args.cache_creation_usd < 0:
        errors.append(f"--cache-creation-usd must be >= 0 (got {args.cache_creation_usd})")
    if args.effective_from is not None:
        try:
            dt.datetime.fromisoformat(args.effective_from)
        except ValueError as exc:
            errors.append(f"--effective-from is not a valid ISO timestamp: {exc}")
    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Determine effective_from value (raw string used in output).
    if args.effective_from is None:
        args.effective_from = dt.datetime.utcnow().isoformat(timespec="seconds")

    # Validation — must happen BEFORE any DB interaction.
    errors = _validate(args)
    if errors:
        for msg in errors:
            print(f"error: {msg}", file=sys.stderr)
        sys.exit(2)

    # Parse effective_from to a datetime for the DB.
    effective_from_parsed = dt.datetime.fromisoformat(args.effective_from)

    db_path = _resolve_db_path(args.db)
    db = duckdb.connect(db_path)
    ensure_schema(db)
    try:
        db.execute(
            "INSERT INTO pricing"
            "(model_id, input_usd, output_usd, cache_read_usd, "
            "cache_creation_usd, is_local, effective_from) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                args.model,
                args.input_usd,
                args.output_usd,
                args.cache_read_usd,
                args.cache_creation_usd,
                args.is_local,
                effective_from_parsed,
            ],
        )
        db.commit()
    except duckdb.ConstraintException:
        print(
            f"error: duplicate (model_id={args.model}, effective_from={args.effective_from})",
            file=sys.stderr,
        )
        db.close()
        sys.exit(2)
    finally:
        db.close()

    print(f"inserted {args.model} @ {args.effective_from}")


if __name__ == "__main__":
    main()
