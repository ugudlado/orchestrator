"""T-1: RED tests for overlay_text resolution (AC-6)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator_next.agent_overlay import overlay_text


def _overlay_path(repo_root: Path, agent_name: str) -> Path:
    return repo_root / ".orchestrator" / "agents" / f"{agent_name}.md"


def test_overlay_text_returns_file_text_when_present_and_non_empty(tmp_path: Path) -> None:
    overlay = _overlay_path(tmp_path, "developer")
    overlay.parent.mkdir(parents=True)
    overlay.write_text("Always run pytest before committing.\n", encoding="utf-8")

    result = overlay_text(str(tmp_path), "developer")

    assert "Always run pytest before committing." in result
    assert result.strip() != ""


def test_overlay_text_returns_empty_when_file_absent(tmp_path: Path) -> None:
    assert overlay_text(str(tmp_path), "developer") == ""


def test_overlay_text_returns_empty_when_file_whitespace_only(tmp_path: Path) -> None:
    overlay = _overlay_path(tmp_path, "developer")
    overlay.parent.mkdir(parents=True)
    overlay.write_text("   \n\t\n", encoding="utf-8")

    assert overlay_text(str(tmp_path), "developer") == ""


def test_overlay_text_returns_empty_on_decode_error(tmp_path: Path) -> None:
    overlay = _overlay_path(tmp_path, "developer")
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(b"\xff\xfe invalid utf-8")

    with patch(
        "orchestrator_next.agent_overlay.Path.read_text",
        side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "invalid"),
    ):
        assert overlay_text(str(tmp_path), "developer") == ""


def test_overlay_text_returns_empty_on_os_error(tmp_path: Path) -> None:
    overlay = _overlay_path(tmp_path, "developer")
    overlay.parent.mkdir(parents=True)
    overlay.write_text("content", encoding="utf-8")

    with patch(
        "orchestrator_next.agent_overlay.Path.read_text",
        side_effect=OSError("permission denied"),
    ):
        assert overlay_text(str(tmp_path), "developer") == ""
