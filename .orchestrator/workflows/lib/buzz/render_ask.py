#!/usr/bin/env python3
"""Render a plain-language ask for a workflow step from asks.yaml.

Stdlib only. asks.yaml is constrained to top-level ``step_id: |`` block
scalars, so a tiny parser suffices when PyYAML is absent; PyYAML is used
when importable (it accepts the same documents).

Usage as a library::

    from render_ask import render
    text = render("implement", {"agent_name": "Implementer", ...})
"""
from __future__ import annotations

from pathlib import Path

ASKS_PATH = Path(__file__).resolve().parent / "asks.yaml"

FALLBACK_KEY = "_default"


class AskRenderError(ValueError):
    """A template could not be rendered with the supplied variables."""


def _parse_block_scalars(text: str) -> dict[str, str]:
    """Parse the constrained `key: |` block-scalar format of asks.yaml."""
    templates: dict[str, str] = {}
    key: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#") or (not line.strip() and key is None):
            continue
        if line[:1] not in ("", " ") and line.rstrip().endswith(": |"):
            if key is not None:
                templates[key] = "\n".join(lines).strip("\n")
            key = line.rstrip()[: -len(": |")].strip()
            lines = []
        elif key is not None:
            lines.append(line[2:] if line.startswith("  ") else line)
    if key is not None:
        templates[key] = "\n".join(lines).strip("\n")
    return templates


def load_templates(path: Path = ASKS_PATH) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    try:  # PyYAML when available (engine venv); fallback parser otherwise.
        import yaml  # type: ignore

        raw = yaml.safe_load(text) or {}
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
    except ImportError:
        pass
    return _parse_block_scalars(text)


def render(step_id: str, vars: dict[str, str], path: Path = ASKS_PATH) -> str:
    """Render the ask for step_id; unknown step uses the generic fallback.

    Raises AskRenderError when the template references a placeholder that is
    not present in ``vars``.
    """
    templates = load_templates(path)
    template = templates.get(step_id) or templates.get(FALLBACK_KEY)
    if template is None:
        raise AskRenderError(
            f"no template for step '{step_id}' and no '{FALLBACK_KEY}' fallback in {path}"
        )

    class _Strict(dict):
        def __missing__(self, name: str) -> str:
            raise AskRenderError(
                f"template for step '{step_id}' references unknown placeholder "
                f"'{{{name}}}' — supply it in vars"
            )

    try:
        return template.format_map(_Strict(vars))
    except AskRenderError:
        raise
    except (ValueError, IndexError) as exc:  # malformed {..} in template
        raise AskRenderError(
            f"template for step '{step_id}' is malformed: {exc}"
        ) from exc


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Render an ask template")
    ap.add_argument("--step", required=True)
    ap.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    ns = ap.parse_args()
    pairs = dict(v.split("=", 1) for v in ns.var if "=" in v)
    try:
        print(render(ns.step, pairs))
    except AskRenderError as exc:
        print(f"render_ask: {exc}", file=sys.stderr)
        sys.exit(3)
