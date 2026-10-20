"""Team tool bindings: which concrete tool fills a role.

A contract's ``tools:`` may name role capabilities (``ticket.read``,
``forge.pr``, ``chat.post``). Which product fills a role is a team choice, so
it lives in a bindings file, not in the workflow — the way models live in
``models.yaml``. The engine only loads, layers and echoes bindings; it never
guesses a binding for an unbound role.
"""

from __future__ import annotations

from pathlib import Path

import yaml

#: The roles a contract's ``tools:`` may name as ``<role>.<verb>``.
ROLES = frozenset({"ticket", "forge", "chat"})

#: How a bound tool is reached.
VIA = frozenset({"mcp", "cli", "script"})


class BindingsError(ValueError):
    """A bindings file is missing, unreadable or malformed."""


def tool_roles(tools: list[str]) -> list[str]:
    """The distinct roles a ``tools:`` list mentions, sorted."""
    return sorted({t.split(".", 1)[0] for t in tools if "." in t and t.split(".", 1)[0] in ROLES})


def load_bindings_file(path: Path) -> dict[str, dict[str, str]]:
    """Parse and validate one bindings file into ``{role: binding}``."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BindingsError(f"bindings file {path}: {exc.strerror or exc}") from exc
    except yaml.YAMLError as exc:
        raise BindingsError(f"bindings file {path}: invalid YAML — {exc}") from exc
    if doc is None:
        return {}
    roles = doc.get("roles") if isinstance(doc, dict) else None
    if not isinstance(roles, dict):
        raise BindingsError(f"bindings file {path}: expected a top-level 'roles:' mapping")
    out: dict[str, dict[str, str]] = {}
    for role, binding in roles.items():
        where = f"bindings file {path}: roles.{role}"
        if role not in ROLES:
            raise BindingsError(f"{where}: unknown role (expected one of {', '.join(sorted(ROLES))})")
        if not isinstance(binding, dict):
            raise BindingsError(f"{where}: expected a mapping")
        for key, value in binding.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise BindingsError(f"{where}: {key!r} must be a string value")
        for key in ("provider", "via"):
            if not binding.get(key):
                raise BindingsError(f"{where}: missing required string '{key}'")
        if binding["via"] not in VIA:
            raise BindingsError(f"{where}: via {binding['via']!r} not one of {sorted(VIA)}")
        out[role] = dict(binding)
    return out


def load_bindings(config_root: Path, files: list[Path] | None = None) -> dict[str, dict[str, str]]:
    """Merge the pack's ``bindings.yaml``, the repo's, then ``files``; later wins.

    Both the pack and repo files are optional. The repo file is
    ``<repo>/.orchestrator/bindings.yaml``, read only when the pack is installed
    at ``<repo>/.orchestrator/<pack>`` so a dev checkout never picks up a stray
    file. The highest layer naming a role owns it wholesale — no field merging.
    """
    merged: dict[str, dict[str, str]] = {}
    candidates = [config_root / "bindings.yaml"]
    if config_root.resolve().parent.name == ".orchestrator":
        candidates.append(config_root.parent / "bindings.yaml")
    for layer in candidates:
        if layer.is_file():
            merged.update(load_bindings_file(layer))
    for path in files or []:
        merged.update(load_bindings_file(path))
    return merged
