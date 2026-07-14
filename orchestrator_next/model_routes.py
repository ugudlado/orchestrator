"""Model → (subprocess tool, model_id) resolution.

Reads the `models:` block from models.yaml (and optional override files /
JSON env override), returning the execution config for a model tier.

Precedence (highest wins): ORCHESTRATOR_MODEL_ROUTE_OVERRIDES (JSON env)
> ORCHESTRATOR_MODELS_CONFIG file > ~/.orchestrator/models.yaml > routes_yaml file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml


def _models_map(path: str | None) -> dict[str, Any]:
    if not path or not Path(path).is_file():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return {}
    models = data.get("models")
    return models if isinstance(models, dict) else {}


def user_models_path() -> Path:
    """User-level model routing override at ~/.orchestrator/models.yaml."""
    return Path.home() / ".orchestrator" / "models.yaml"


def _layer_chain(routes_yaml: str | None) -> list[tuple[str, str]]:
    return [
        ("config_root", routes_yaml or ""),
        ("user_home", str(user_models_path())),
        ("env_file", os.environ.get("ORCHESTRATOR_MODELS_CONFIG") or ""),
    ]


def resolve_field(model: str, routes_yaml: str | None, field: str) -> str:
    """Return one route field for `model` ("" if unset)."""
    overrides = json.loads(os.environ.get("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES") or "{}")

    entry: dict[str, Any] = {}
    for _label, path in _layer_chain(routes_yaml):
        entry.update(_models_map(path).get(model) or {})
    ov = overrides.get(model) or {}
    return str(ov.get(field) or entry.get(field) or "")


def resolve_all_with_source(routes_yaml: str) -> dict[str, dict[str, str]]:
    """Return every tier with resolved fields and per-field source labels."""
    overrides = json.loads(os.environ.get("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES") or "{}")

    tiers: set[str] = set()
    for _label, path in _layer_chain(routes_yaml):
        tiers.update(_models_map(path).keys())
    tiers.update(overrides.keys())

    result: dict[str, dict[str, str]] = {}
    for tier in sorted(tiers):
        entry: dict[str, Any] = {}
        sources: dict[str, str] = {}

        for label, path in _layer_chain(routes_yaml):
            tier_data = _models_map(path).get(tier) or {}
            for fld in ("subprocess", "model_id"):
                if fld in tier_data and tier_data[fld] is not None:
                    entry[fld] = tier_data[fld]
                    sources[f"{fld}_source"] = label

        ov = overrides.get(tier) or {}
        for fld in ("subprocess", "model_id"):
            if fld in ov and ov[fld] is not None:
                entry[fld] = ov[fld]
                sources[f"{fld}_source"] = "$ORCHESTRATOR_MODEL_ROUTE_OVERRIDES"

        result[tier] = {
            "subprocess": str(entry.get("subprocess") or ""),
            "subprocess_source": sources.get("subprocess_source", ""),
            "model_id": str(entry.get("model_id") or ""),
            "model_id_source": sources.get("model_id_source", ""),
        }

    return result
