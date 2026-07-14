"""`orchestrator models` — print effective model routing with source attribution."""
from __future__ import annotations

import sys

from orchestrator_next.model_routes import resolve_all_with_source
from orchestrator_next.paths import ConfigRootError, config_root


def main(argv: list[str]) -> int:
    del argv
    try:
        root = config_root()
    except ConfigRootError:
        print("error: no config root (set ORCHESTRATOR_CONFIG)", file=sys.stderr)
        return 1

    routes_yaml = str(root / "models.yaml")
    rows = resolve_all_with_source(routes_yaml)

    header = ("TIER", "SUBPROCESS", "MODEL_ID", "SOURCE")
    table_rows = [
        (tier, entry.get("subprocess", ""), entry.get("model_id", ""),
         entry.get("subprocess_source") or entry.get("model_id_source") or "")
        for tier, entry in sorted(rows.items())
    ]
    widths = [max(map(len, col)) for col in zip(header, *table_rows)]
    for row in (header, *table_rows):
        print("  ".join(f"{cell:<{w}}" for cell, w in zip(row, widths)))
    return 0
