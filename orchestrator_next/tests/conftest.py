"""Shared pytest fixtures for orchestrator_next tests."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import yaml


def pytest_configure(config) -> None:
    """Tests need a real workflow pack. Resolve via the normal chain — a dev
    checkout's config/ if present, else the downloaded ~/.orchestrator/pack —
    and pin it so subprocess-spawning tests inherit a stable root."""
    stale = os.environ.get("ORCHESTRATOR_CONFIG")
    if stale and not (Path(stale) / "workflows").is_dir():
        del os.environ["ORCHESTRATOR_CONFIG"]  # stale export (e.g. deleted repo config/)
    if "ORCHESTRATOR_CONFIG" not in os.environ:
        from orchestrator_next.paths import ConfigRootError, config_root

        try:
            os.environ["ORCHESTRATOR_CONFIG"] = str(config_root())
        except ConfigRootError:
            pass  # pack-dependent tests will fail with the download hint


def install_step_models(
    monkeypatch,
    tmp_path,
    step_ids: Iterable[str],
    *,
    alias: str = "standard",
    models: dict | None = None,
    tools: dict | None = None,
) -> Path:
    """Point ORCHESTRATOR_MODELS_CONFIG at a models.yaml with step_models.

    Dispatch requires every prompt step in step_models; isolation via the
    models-config layer keeps the pack's ORCHESTRATOR_CONFIG intact.
    """
    path = Path(tmp_path) / "test-models.yaml"
    data: dict = {
        "models": models
        or {
            "standard": {"tool": "claude", "model_id": "claude-sonnet-5"},
            "strong": {"tool": "claude", "model_id": "claude-opus-5"},
            "code": {"tool": "cursor", "model_id": "composer-2.5"},
            "auto": {"tool": "claude", "model_id": "claude-sonnet-5"},
            "opus": {"tool": "claude", "model_id": "claude-opus-4-7"},
        },
        "step_models": {sid: alias for sid in step_ids},
    }
    if tools is not None:
        data["tools"] = tools
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(path))
    return path
