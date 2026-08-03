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

    header = ("TIER", "TOOL", "MODEL_ID", "SOURCE")
    table_rows = [
        (tier, entry.get("tool", ""), entry.get("model_id", ""),
         entry.get("tool_source") or entry.get("model_id_source") or "")
        for tier, entry in sorted(rows.items())
    ]
    widths = [max(map(len, col)) for col in zip(header, *table_rows)]
    for row in (header, *table_rows):
        print("  ".join(f"{cell:<{w}}" for cell, w in zip(row, widths)))

    # D3: render the full candidate chain per alias, marking the active one.
    chained = {tier: entry for tier, entry in sorted(rows.items()) if entry.get("num_candidates", 1) > 1}
    if chained:
        print()
        print("Fallback chains:")
        for tier, entry in chained.items():
            active_idx = entry["active_index"]
            fallback_flag = " [FALLBACK]" if entry.get("is_fallback") else ""
            print(f"  {tier}:{fallback_flag}")
            for idx, cand in enumerate(entry.get("candidates") or []):
                marker = "*" if idx == active_idx else " "
                sub = cand.get("tool", "")
                mid = cand.get("model_id", "")
                print(f"    {marker} #{idx}  tool={sub}  model_id={mid}")
    return 0
