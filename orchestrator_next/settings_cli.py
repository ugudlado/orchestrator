"""`orchestrator config show | set | init` — read and write orchestrator.toml."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from orchestrator_next import settings

USAGE = (
    "usage:\n"
    "  orchestrator config show [--json]\n"
    "  orchestrator config set <section.key> <value> [--global]\n"
    "  orchestrator config init [--global] [--force]\n"
)


def _repo_target() -> Path:
    path = settings.repo_file()
    if path is None:
        raise SystemExit(
            "error: no repo root — run inside a git repo or pass --global"
        )
    return path


def show_cmd(argv: list[str]) -> int:
    as_json = "--json" in argv
    try:
        cfg = settings.load()
    except settings.SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    if as_json:
        print(json.dumps({
            "files": [str(p) for p in cfg.files],
            "unknown_keys": cfg.unknown,
            "deprecated": cfg.deprecated,
            "settings": {
                spec.dotted: {"value": res.value, "source": res.source}
                for spec, res in cfg.items()
            },
        }, indent=2, sort_keys=True))
        return 0

    print("files: " + (", ".join(str(p) for p in cfg.files) or "(none)"))
    width = max(len(s.dotted) for s in settings.SCHEMA)
    for spec, res in cfg.items():
        print(f"{spec.dotted:<{width}}  {settings._fmt(res.value):<28}  {res.source}")
    for key in cfg.unknown:
        print(f"warning: unknown key {key}", file=sys.stderr)
    for path in cfg.deprecated:
        print(f"warning: {path} is deprecated — move [trust] into "
              f"{settings.global_file()}", file=sys.stderr)
    return 0


def set_cmd(argv: list[str]) -> int:
    is_global = "--global" in argv
    rest = [a for a in argv if a != "--global"]
    if len(rest) != 2:
        sys.stderr.write(USAGE)
        return 3
    dotted, raw = rest
    path = settings.global_file() if is_global else _repo_target()
    try:
        value = settings.set_value(dotted, raw, path=path)
    except settings.SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(f"{dotted} = {settings._fmt(value)}  ({path})")
    spec = settings.spec_for(dotted)
    if spec.env and os.environ.get(spec.env) is not None:
        print(f"note: ${spec.env} is set and still overrides this file",
              file=sys.stderr)
    return 0


def init_cmd(argv: list[str]) -> int:
    is_global = "--global" in argv
    path = settings.global_file() if is_global else _repo_target()
    if path.is_file() and "--force" not in argv:
        print(f"error: {path} already exists (use --force to overwrite)",
              file=sys.stderr)
        return 3
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.template(), encoding="utf-8")
    print(f"wrote {path}")
    return 0


def settings_verb(sub: str, argv: list[str]) -> int:
    if sub == "show":
        return show_cmd(argv)
    if sub == "set":
        return set_cmd(argv)
    if sub == "init":
        return init_cmd(argv)
    sys.stderr.write(USAGE)
    return 3
