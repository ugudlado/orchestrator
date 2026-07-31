"""`orchestrator models init` — machine bootstrap (D2).

Scans PATH for the binaries named by the engine-bundled models.example.yaml
(claude, cursor-agent, codex, pi — the normal fresh-machine case) and writes
~/.orchestrator/models.yaml binding each core alias to a fallback chain (D3)
of the candidates whose binary was found, best-available first, PLUS the
full `tools:` entries for the tools those chains reference — so the home
file is self-sufficient at runtime even with no config-root `tools:` layer
(the slim workflow-pack-root case D4 targets).

Detection happens once, at init — the output is a plain file the user reads
and edits, not runtime magic. `doctor`'s subprocess-on-PATH check catches
later drift (a binary that disappears after init).
"""
from __future__ import annotations

import shutil
import sys
from typing import Any

import yaml

from orchestrator_next.model_routes import user_models_path
from orchestrator_next.paths import ConfigRootError, config_root, engine_data_dir


def _load_seed() -> dict[str, Any]:
    candidates = [engine_data_dir() / "models.example.yaml"]
    try:
        candidates.insert(0, config_root() / "models.example.yaml")
    except ConfigRootError:
        pass
    data: dict[str, Any] = {}
    for example_path in candidates:
        try:
            data = yaml.safe_load(example_path.read_text()) or {}
            break
        except (OSError, yaml.YAMLError):
            continue
    return {"tools": data.get("tools") or {}, "models": data.get("models") or {}}


def build_bindings() -> tuple[dict[str, list[dict]], dict[str, dict], list[str]]:
    """Return (models block, tools block, human-readable reason lines) to write."""
    seed = _load_seed()
    seed_tools: dict[str, Any] = seed["tools"]
    seed_models: dict[str, Any] = seed["models"]

    models_out: dict[str, list[dict]] = {}
    tools_out: dict[str, dict] = {}
    reasons: list[str] = []

    for alias in sorted(seed_models):
        raw = seed_models[alias]
        candidates = raw if isinstance(raw, list) else [raw]
        candidates = [c for c in candidates if isinstance(c, dict)]

        available: list[dict] = []
        tried: list[str] = []
        for cand in candidates:
            tool_name = str(cand.get("subprocess") or "")
            tool_entry = seed_tools.get(tool_name) or {}
            binary = tool_entry.get("binary") or tool_name
            found = bool(shutil.which(binary))
            tried.append(f"{tool_name} ({binary}): {'found' if found else 'not found'}")
            if found:
                available.append(dict(cand))
                tools_out.setdefault(tool_name, dict(tool_entry))  # full entry — D1 is wholesale-wins

        if available:
            chosen = available[0]
            models_out[alias] = available
            extra = f", {len(available) - 1} fallback(s) kept" if len(available) > 1 else ""
            reasons.append(
                f"{alias} -> {chosen['subprocess']} ({chosen.get('model_id', '')}){extra}"
                f"  [{'; '.join(tried)}]"
            )
        else:
            reasons.append(f"{alias} -> UNRESOLVED (no candidate binary on PATH)  [{'; '.join(tried)}]")

    return models_out, tools_out, reasons


def main(argv: list[str]) -> int:
    force = "--force" in argv

    out_path = user_models_path()
    if out_path.exists() and not force:
        print(
            f"error: {out_path} already exists — pass --force to overwrite",
            file=sys.stderr,
        )
        return 1

    models_out, tools_out, reasons = build_bindings()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.dump({"models": models_out, "tools": tools_out}, sort_keys=True))

    print(f"wrote {out_path}")
    for line in reasons:
        print(f"  {line}")
    unresolved = [r for r in reasons if "UNRESOLVED" in r]
    if unresolved:
        print(
            f"warning: {len(unresolved)} alias(es) have no available binary on PATH — "
            "edit the file by hand or install the missing tool.",
            file=sys.stderr,
        )
    return 0
