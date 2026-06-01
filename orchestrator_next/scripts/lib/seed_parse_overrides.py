"""Parse key=value flag overrides and resolve active steps from a workflow schema.

Usage:
    python seed_parse_overrides.py <slug> <schema_name> <repo_root> <schema_yaml_path> [key=value ...]

Stdout: JSON with keys slug, schema_name, repo_root, flags, active, filtered.
Exit 1 on any pre-condition failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 4:
        print(
            "Usage: seed_parse_overrides.py <slug> <schema_name> <repo_root> <schema_yaml_path> [key=value ...]",
            file=sys.stderr,
        )
        return 1

    slug, schema_name, repo_root, schema_yaml_path = args[0], args[1], args[2], args[3]
    raw_overrides = args[4:]

    flags: dict = {}
    for arg in raw_overrides:
        if "=" not in arg:
            print(f"error: flag override '{arg}' must be in key=value format", file=sys.stderr)
            return 1
        k, v = arg.split("=", 1)
        flags[k] = True if v.lower() == "true" else (False if v.lower() == "false" else v)

    schema = yaml.safe_load(Path(schema_yaml_path).read_text())

    # The steps list IS the plan — no gate-filtering (ORC-108).
    active = [
        (step_entry.get("id", "") if isinstance(step_entry, dict) else str(step_entry))
        for step_entry in schema.get("steps", [])
    ]
    if not active:
        print(f"error: schema '{schema_name}' declares no steps", file=sys.stderr)
        return 1

    print(json.dumps({
        "slug": slug,
        "schema_name": schema_name,
        "repo_root": repo_root,
        "flags": flags,
        "active": active,
        "filtered": [],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
