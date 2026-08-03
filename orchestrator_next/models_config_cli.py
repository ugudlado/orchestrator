"""CLI helpers for on-the-fly models.yaml overrides.

`--models-config PATH` (also `models.config=PATH`) sets
ORCHESTRATOR_MODELS_CONFIG for the process. That file is the highest-precedence
YAML layer in model_routes._layer_chain — above config-root and
~/.orchestrator/models.yaml — so a one-off file can override tiers,
step_models, and tools for a single invocation.
"""
from __future__ import annotations

import os
import sys


def extract_models_config_args(argv: list[str]) -> tuple[list[str], str | None]:
    """Pull --models-config / models.config= from argv. Returns (remaining, path|None)."""
    remaining: list[str] = []
    path: str | None = None
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--models-config":
            if not args:
                print("error: --models-config requires a path", file=sys.stderr)
                raise SystemExit(7)
            path = args.pop(0)
        elif a.startswith("models.config="):
            path = a[len("models.config=") :]
        else:
            remaining.append(a)
    return remaining, path


def apply_models_config(path: str | None) -> None:
    """Set ORCHESTRATOR_MODELS_CONFIG from an absolute path (no-op if None)."""
    if not path:
        return
    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        print(f"error: models config not found: {abs_path}", file=sys.stderr)
        raise SystemExit(7)
    os.environ["ORCHESTRATOR_MODELS_CONFIG"] = abs_path


def consume_models_config_argv(argv: list[str]) -> list[str]:
    """Extract + apply models-config flags; return remaining argv."""
    remaining, path = extract_models_config_args(argv)
    apply_models_config(path)
    return remaining
