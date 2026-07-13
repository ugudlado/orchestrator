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
    widths = [len(h) for h in header]
    table_rows: list[tuple[str, str, str, str]] = []
    for tier in sorted(rows):
        entry = rows[tier]
        source = entry.get("subprocess_source") or entry.get("model_id_source") or ""
        table_rows.append(
            (
                tier,
                entry.get("subprocess", ""),
                entry.get("model_id", ""),
                source,
            )
        )
        widths[0] = max(widths[0], len(tier))
        widths[1] = max(widths[1], len(table_rows[-1][1]))
        widths[2] = max(widths[2], len(table_rows[-1][2]))
        widths[3] = max(widths[3], len(source))

    print(
        f"{header[0]:<{widths[0]}}  {header[1]:<{widths[1]}}  "
        f"{header[2]:<{widths[2]}}  {header[3]:<{widths[3]}}"
    )
    for tier, subprocess, model_id, source in table_rows:
        print(
            f"{tier:<{widths[0]}}  {subprocess:<{widths[1]}}  "
            f"{model_id:<{widths[2]}}  {source:<{widths[3]}}"
        )
    return 0
