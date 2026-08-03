"""Model → (tool, model_id) resolution.

Reads the `models:` block from models.yaml (and optional override files /
JSON env override), returning the execution config for a model tier.

Optional `step_models:` maps step_id → tier alias and wins over a step
contract's `model:` field (per-step fallthrough across layers).

Precedence (highest wins): ORCHESTRATOR_MODEL_ROUTE_OVERRIDES (per-run
field-level override, set by CLI model.<alias>.<field>= args) >
ORCHESTRATOR_MODELS_CONFIG file (CLI --models-config) >
repo/explicit config root models.yaml > ~/.orchestrator/models.yaml >
bundled defaults. Repo beats global for file layers: a vendored
(.orchestrator/) or explicitly pointed-at config root outranks the
user's home file; the bundled models.yaml is only the floor.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml


def _load_yaml(path: str | None) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {}
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return {}


def _models_map(path: str | None) -> dict[str, Any]:
    models = _load_yaml(path).get("models")
    return models if isinstance(models, dict) else {}


def _step_models_map(path: str | None) -> dict[str, Any]:
    step_models = _load_yaml(path).get("step_models")
    return step_models if isinstance(step_models, dict) else {}


def _tools_map(path: str | None) -> dict[str, Any]:
    tools = _load_yaml(path).get("tools")
    return tools if isinstance(tools, dict) else {}


def user_models_path() -> Path:
    """User-level model routing override at ~/.orchestrator/models.yaml."""
    return Path.home() / ".orchestrator" / "models.yaml"


def _layer_chain(routes_yaml: str | None) -> list[tuple[str, str]]:
    """Lowest→highest precedence. Repo beats global: a vendored/explicit
    config root ranks above ~/.orchestrator/models.yaml; the global defaults
    (dev-checkout config or downloaded pack) rank below it. env_file
    (ORCHESTRATOR_MODELS_CONFIG / --models-config) is the highest file layer.
    """
    from orchestrator_next.paths import bundled_config_root, pack_root

    cfg = ("config_root", routes_yaml or "")
    home = ("user_home", str(user_models_path()))
    env = ("env_file", os.environ.get("ORCHESTRATOR_MODELS_CONFIG") or "")
    global_floors = {
        (bundled_config_root() / "models.yaml").resolve(),
        (pack_root() / "config" / "models.yaml").resolve(),
    }
    is_global = bool(routes_yaml) and Path(routes_yaml).resolve() in global_floors
    return [cfg, home, env] if is_global else [home, cfg, env]


def resolve_tool_template(tool_name: str, routes_yaml: str | None) -> tuple[str, list[str]]:
    """Return (binary, args_template) for `tool_name` from the layered `tools:`
    block (same `_layer_chain` precedence as `models:`).

    Wholesale-wins: the highest-precedence layer that defines `tool_name` owns
    its entire entry — no cross-layer field merge (same rule as D3's `models:`
    resolution). Falls back to (tool_name, []) when no layer defines it, so
    callers keep working with a bare binary name on PATH.
    """
    # _layer_chain is lowest-to-highest precedence; walk highest-first so the
    # first layer that names the tool wins wholesale.
    for _label, path in reversed(_layer_chain(routes_yaml)):
        entry = _tools_map(path).get(tool_name)
        if isinstance(entry, dict):
            binary = entry.get("binary") or tool_name
            template = entry.get("args_template") or []
            return binary, list(template)
    return tool_name, []


def _winning_alias_entry(alias: str, routes_yaml: str | None) -> tuple[Any, str]:
    """Return (raw entry, source label) for `alias` from the highest-precedence
    FILE layer that names it — dict (scalar route) or list (fallback chain).

    LOCKED wholesale-wins rule (D3): the highest layer that names an alias owns
    it entirely, list or scalar — lower layers are fully ignored for that
    alias. No cross-layer field merging, no element-wise list merging. This is
    a deliberate behavior change from the old resolver, which accumulated
    fields across layers with dict.update() — harmless for scalar routes, but
    that would silently corrupt a list-shaped candidate (dict.update() on a
    list of 2-key dicts unpacks into {key: value} garbage) or crash outright.
    A scalar alias defined in exactly one layer still resolves unchanged;
    what stops working is a *higher* layer partially overriding a *lower*
    layer's fields for the same alias (e.g. home sets only model_id and
    expects to inherit tool from config_root) — that partial merge was
    undocumented/untested and is intentionally not preserved.
    """
    # _layer_chain is lowest-to-highest precedence; walk highest-first so the
    # first layer that names the alias wins wholesale.
    for label, path in reversed(_layer_chain(routes_yaml)):
        entry = _models_map(path).get(alias)
        if entry is not None:
            return entry, label
    return None, ""


def _binary_on_path(tool_name: str, routes_yaml: str | None) -> bool:
    import shutil
    binary, _template = resolve_tool_template(tool_name, routes_yaml)
    return bool(shutil.which(binary))


def resolve_step_alias(
    step_id: str,
    contract_alias: str | None,
    routes_yaml: str | None,
) -> str:
    """Return the tier alias for `step_id` from `step_models:`.

    Highest-precedence layer that names `step_id` under `step_models:` wins
    for that step only (per-step fallthrough). `contract_alias` is ignored in
    normal operation (contracts no longer declare model:); kept only as an
    optional legacy fallback when no step_models entry exists.
    """
    for _label, path in reversed(_layer_chain(routes_yaml)):
        mapping = _step_models_map(path)
        if step_id in mapping:
            val = mapping[step_id]
            if val is not None and str(val).strip():
                return str(val).strip()
    return str(contract_alias or "").strip()


def resolve_route(alias: str, routes_yaml: str | None) -> dict[str, Any]:
    """Resolve `alias` to a single concrete route, as one unit (D3).

    Returns a dict:
      tool, model_id            — the chosen candidate's fields ("" if unresolved)
      source                 — layer label that supplied the winning alias entry
      active_index            — 0-based index of the chosen candidate within
                                its chain (0 for a scalar route or the first
                                chain candidate)
      num_candidates          — length of the alias's candidate chain (1 for scalar)
      is_fallback              — True iff active_index > 0 (only chains can trip this)

    Selection semantics:
      - scalar route (dict)  → NOT PATH-gated; always returned as-is. A scalar
        pointed at a missing binary still dispatches (and fails at invoke) —
        it must never silently become "no route" (exit 4). This preserves
        today's behavior for every alias that hasn't opted into a chain.
      - list route (chain)   → PATH-gated; the first candidate whose
        `tool`'s tools:-resolved binary is on PATH wins. If every
        candidate's binary is absent, tool/model_id come back "" so the
        run_loop caller raises the existing no-route error (exit 4).

    ORCHESTRATOR_MODEL_ROUTE_OVERRIDES (JSON env) and CLI model.<alias>.<field>=
    overrides are NOT part of the wholesale-wins rule — they are a separate,
    higher-precedence field-level override applied on top of the selected
    candidate (so a partial override like {"model_id": "..."} still works,
    inheriting tool from whichever candidate PATH-selected).
    """
    raw, source = _winning_alias_entry(alias, routes_yaml)

    if isinstance(raw, list):
        candidates = [c for c in raw if isinstance(c, dict)]
        chosen, chosen_idx = None, -1
        for idx, cand in enumerate(candidates):
            tool = cand.get("tool")
            if tool and _binary_on_path(str(tool), routes_yaml):
                chosen, chosen_idx = cand, idx
                break
        entry = chosen or {}
        active_index = max(chosen_idx, 0)
        num_candidates = len(candidates)
    else:
        entry = raw if isinstance(raw, dict) else {}
        active_index = 0
        num_candidates = 1

    overrides = json.loads(os.environ.get("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES") or "{}")
    ov = overrides.get(alias) or {}

    tool_val = str(ov.get("tool") or entry.get("tool") or "")
    model_id_val = str(ov.get("model_id") or entry.get("model_id") or "")

    return {
        "tool": tool_val,
        "model_id": model_id_val,
        "source": "$ORCHESTRATOR_MODEL_ROUTE_OVERRIDES" if ov else source,
        "active_index": active_index,
        "num_candidates": num_candidates,
        "is_fallback": num_candidates > 1 and active_index > 0,
    }


def resolve_field(model: str, routes_yaml: str | None, field: str) -> str:
    """Return one route field for `model` ("" if unset). Thin wrapper over
    resolve_route so tool/model_id are always resolved from the SAME
    chosen candidate (never mixed across candidates)."""
    return str(resolve_route(model, routes_yaml).get(field) or "")


def resolve_all_with_source(routes_yaml: str) -> dict[str, dict[str, Any]]:
    """Return every alias with resolved fields, source, and chain metadata.

    Keeps the flat tool/model_id/*_source keys (both sourced
    from the one winning layer+candidate per alias, per the wholesale-wins
    rule) so existing consumers (doctor, models verb) keep working unchanged.
    Adds candidates/active_index/num_candidates/is_fallback for chain-aware
    rendering (D2/D3 verb + doctor WARN).
    """
    overrides = json.loads(os.environ.get("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES") or "{}")

    aliases: set[str] = set()
    for _label, path in _layer_chain(routes_yaml):
        aliases.update(_models_map(path).keys())
    aliases.update(overrides.keys())

    result: dict[str, dict[str, Any]] = {}
    for alias in sorted(aliases):
        route = resolve_route(alias, routes_yaml)
        raw, _source = _winning_alias_entry(alias, routes_yaml)
        candidates = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])

        result[alias] = {
            "tool": route["tool"],
            "tool_source": route["source"],
            "model_id": route["model_id"],
            "model_id_source": route["source"],
            "candidates": candidates,
            "active_index": route["active_index"],
            "num_candidates": route["num_candidates"],
            "is_fallback": route["is_fallback"],
        }

    return result
