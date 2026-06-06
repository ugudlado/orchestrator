"""Model → (subprocess tool, model_id) resolution.

Reads the `models:` block from models.yaml (and an optional override file /
JSON env override), returning the execution config for a model tier.

Precedence (highest wins): ORCHESTRATOR_MODEL_ROUTE_OVERRIDES (JSON env)
> ORCHESTRATOR_MODELS_CONFIG file > routes_yaml file.
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
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    models = data.get("models")
    return models if isinstance(models, dict) else {}


def resolve_field(model: str, routes_yaml: str | None, field: str) -> str:
    """Return one route field for `model` ("" if unset)."""
    overrides = json.loads(os.environ.get("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES") or "{}")
    models_config = os.environ.get("ORCHESTRATOR_MODELS_CONFIG") or ""

    entry: dict[str, Any] = {}
    entry.update(_models_map(routes_yaml).get(model) or {})
    entry.update(_models_map(models_config).get(model) or {})
    ov = overrides.get(model) or {}
    return str(ov.get(field) or entry.get(field) or "")


def resolve_subprocess(model: str, routes_yaml: str | None) -> str:
    """Tool binary key (e.g. 'claude', 'cursor') for a model tier."""
    return resolve_field(model, routes_yaml, "subprocess")


def resolve_model_id(model: str, routes_yaml: str | None) -> str:
    """Concrete model_id (e.g. 'claude-opus-4-7') for a model tier; '' if unset."""
    return resolve_field(model, routes_yaml, "model_id")
