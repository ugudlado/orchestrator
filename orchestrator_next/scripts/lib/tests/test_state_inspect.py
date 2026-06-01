"""Tests for orchestrator_next/scripts/lib/state_inspect.py.

Mirror the six call sites in orchestrator_next/scripts/run-workflow.sh so a future refactor of
the argparse surface can't silently break the shell driver.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import state_inspect  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[4]

def _write_state(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "state.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return p


def _run(capsys, argv: list[str], stdin: str = "") -> tuple[int, str, str]:
    if stdin:
        sys.stdin = __import__("io").StringIO(stdin)
    try:
        rc = state_inspect.main(argv)
    finally:
        sys.stdin = sys.__stdin__
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


class TestStateField:
    def test_returns_primary_key(self, tmp_path, capsys):
        p = _write_state(tmp_path, {"change_id": "ORC-99", "slug": "fallback"})
        rc, out, _ = _run(capsys, ["state-field", str(p), "change_id"])
        assert rc == 0
        assert out.strip() == "ORC-99"

    def test_uses_fallback_when_primary_missing(self, tmp_path, capsys):
        p = _write_state(tmp_path, {"slug": "fallback-slug"})
        rc, out, _ = _run(capsys, ["state-field", str(p), "change_id", "--fallback", "slug"])
        assert rc == 0
        assert out.strip() == "fallback-slug"

    def test_uses_fallback_when_primary_empty_string(self, tmp_path, capsys):
        p = _write_state(tmp_path, {"change_id": "", "slug": "from-slug"})
        rc, out, _ = _run(capsys, ["state-field", str(p), "change_id", "--fallback", "slug"])
        assert out.strip() == "from-slug"

    def test_default_when_no_state(self, tmp_path, capsys):
        rc, out, _ = _run(
            capsys, ["state-field", str(tmp_path / "missing.yaml"), "change_id", "--default", "X"]
        )
        assert out.strip() == "X"


class TestWorkflowMeta:
    def test_emits_worktree_block_when_present(self, tmp_path, capsys):
        p = _write_state(
            tmp_path,
            {
                "change_id": "ORC-99",
                "schema": "feature",
                "repo_root": "/r",
                "worktree_path": "/wt",
            },
        )
        rc, out, _ = _run(capsys, ["workflow-meta", str(p)])
        assert rc == 0
        lines = out.splitlines()
        assert lines[0] == "Workflow: change_id=ORC-99 schema=feature repo_root=/r"
        assert f"state_yaml_path={p}" in lines
        # run-workflow.sh greps `^worktree_path=` — exact format matters.
        assert "worktree_path=/wt" in lines
        assert "artifact_dir=/wt/spec/changes/ORC-99" in lines
        assert "workflow_state_dir=/wt/spec/changes" in lines

    def test_falls_back_to_repo_when_no_worktree(self, tmp_path, capsys):
        p = _write_state(
            tmp_path,
            {"change_id": "ORC-1", "repo_root": "/r", "schema": "bugfix"},
        )
        _, out, _ = _run(capsys, ["workflow-meta", str(p)])
        assert "artifact_dir=/r/spec/changes/ORC-1" in out
        assert "worktree_path" not in out

    def test_change_id_falls_back_to_slug_then_dirname(self, tmp_path, capsys):
        p = _write_state(tmp_path, {"slug": "my-slug", "repo_root": "/r"})
        _, out, _ = _run(capsys, ["workflow-meta", str(p)])
        assert "change_id=my-slug" in out


class TestLogStepUsage:
    def test_formats_full_usage_line(self, tmp_path, capsys):
        p = _write_state(
            tmp_path,
            {
                "step_history": [
                    {
                        "step_id": "explore",
                        "phase": "main",
                        "status": "completed",
                        "usage": {
                            "input_tokens": 1234,
                            "output_tokens": 567,
                            "model": "claude-sonnet-4-6",
                            "cost_usd": 0.0234,
                            "duration_ms": 45123,
                        },
                    }
                ]
            },
        )
        rc, _, err = _run(capsys, ["log-step-usage", str(p), "explore", "main"])
        assert rc == 0
        assert "model=claude-sonnet-4-6" in err
        assert "tokens in=1234 out=567" in err
        assert "cost=$0.0234" in err
        assert "duration=45.1s" in err

    def test_skips_unknown_models(self, tmp_path, capsys):
        p = _write_state(
            tmp_path,
            {
                "step_history": [
                    {
                        "step_id": "seed",
                        "phase": "main",
                        "status": "completed",
                        "usage": {"input_tokens": 10, "output_tokens": 5, "model": "none"},
                    }
                ]
            },
        )
        _, _, err = _run(capsys, ["log-step-usage", str(p), "seed", "main"])
        assert "model=" not in err
        assert "tokens in=10 out=5" in err

    def test_prints_inline_note_when_no_tokens(self, tmp_path, capsys):
        p = _write_state(
            tmp_path,
            {
                "step_history": [
                    {
                        "step_id": "inline-step",
                        "phase": "main",
                        "status": "completed",
                        "usage": {"input_tokens": 0, "output_tokens": 0, "model": "none"},
                    }
                ]
            },
        )
        _, _, err = _run(capsys, ["log-step-usage", str(p), "inline-step", "main"])
        assert "usage: no tokens (inline/script)" in err

    def test_silent_when_step_missing(self, tmp_path, capsys):
        p = _write_state(tmp_path, {"step_history": []})
        rc, out, err = _run(capsys, ["log-step-usage", str(p), "explore", "main"])
        assert rc == 0
        assert out == ""
        assert err == ""

    def test_duration_minutes_branch(self, tmp_path, capsys):
        p = _write_state(
            tmp_path,
            {
                "step_history": [
                    {
                        "step_id": "s",
                        "phase": "main",
                        "status": "completed",
                        "usage": {"input_tokens": 1, "duration_ms": 125_000},
                    }
                ]
            },
        )
        _, _, err = _run(capsys, ["log-step-usage", str(p), "s", "main"])
        assert "duration=2.1m" in err


class TestBuildPayload:
    def test_script_kind_includes_started_at(self, capsys):
        rc, out, _ = _run(
            capsys,
            [
                "build-payload",
                "script",
                "--step-id",
                "seed",
                "--phase",
                "main",
                "--status",
                "completed",
                "--started-at",
                "2026-05-26T01:00:00Z",
            ],
        )
        assert rc == 0
        payload = json.loads(out)
        assert payload["step_id"] == "seed"
        assert payload["status"] == "completed"
        assert payload["outputs"] == {}
        assert payload["started_at"] == "2026-05-26T01:00:00Z"
        assert payload["usage"]["model"] == "none"

    def test_script_kind_omits_started_at_when_empty(self, capsys):
        _, out, _ = _run(
            capsys,
            ["build-payload", "script", "--step-id", "s", "--phase", "main", "--started-at", ""],
        )
        assert "started_at" not in json.loads(out)

    def test_failed_kind_carries_exit_code(self, capsys):
        _, out, _ = _run(
            capsys,
            [
                "build-payload",
                "failed",
                "--step-id",
                "explore",
                "--phase",
                "main",
                "--agent",
                "discoverer",
                "--exit-code",
                "7",
            ],
        )
        payload = json.loads(out)
        assert payload["status"] == "failed"
        assert payload["agent"] == "discoverer"
        assert payload["outputs"]["task_execution_result"]["exit_code"] == 7

    def test_agent_kind_merges_completion_and_reads_stdout(self, tmp_path, capsys):
        stdout_file = tmp_path / "tool_stdout.txt"
        stdout_file.write_text("raw tool output\n", encoding="utf-8")
        completion = json.dumps(
            {
                "status": "completed",
                "outputs": {"discovery.md": "discovery.md"},
                "usage": {"input_tokens": 100, "output_tokens": 50, "model": "claude-opus-4-7"},
            }
        )
        _, out, _ = _run(
            capsys,
            [
                "build-payload",
                "agent",
                "--step-id",
                "explore",
                "--phase",
                "main",
                "--agent",
                "discoverer",
                "--stdout-file",
                str(stdout_file),
            ],
            stdin=completion,
        )
        payload = json.loads(out)
        assert payload["step_id"] == "explore"
        assert payload["agent"] == "discoverer"
        # Raw driver stdout is not attached to the done payload (usage via --usage-file).
        assert "agentId:" not in str(payload)
        # Completion's usage wins over the default placeholder.
        assert payload["usage"]["model"] == "claude-opus-4-7"
        # Original completion outputs preserved.
        assert payload["outputs"] == {"discovery.md": "discovery.md"}

    def test_agent_kind_defaults_missing_outputs_and_hoists_learn_result(self, capsys):
        _, out, _ = _run(
            capsys,
            [
                "build-payload",
                "agent",
                "--step-id",
                "run-learn-cycle",
                "--phase",
                "main",
                "--agent",
                "workflow-learner",
                "--stdout-file",
                "/nonexistent/path",
            ],
            stdin=json.dumps({"status": "completed", "learn_result": {"completed": True}}),
        )
        payload = json.loads(out)
        assert isinstance(payload["outputs"], dict)
        assert payload["outputs"]["learn_result"] == {"completed": True}
        assert "learn_result" not in payload or payload.get("learn_result") is None

    def test_agent_kind_fills_default_usage_when_missing(self, capsys):
        _, out, _ = _run(
            capsys,
            [
                "build-payload",
                "agent",
                "--step-id",
                "s",
                "--phase",
                "main",
                "--agent",
                "x",
                "--stdout-file",
                "/nonexistent/path",
            ],
            stdin=json.dumps({"status": "completed", "outputs": {}}),
        )
        payload = json.loads(out)
        assert payload["usage"] == {"input_tokens": 0, "output_tokens": 0, "model": "none"}
        # Missing stdout file does not add legacy chat-driver fields to the payload.
        assert len([k for k in payload if k.startswith("agent") and k.endswith("_result")]) == 0

    def test_agent_kind_does_not_attach_stdout_when_agent_id_present(self, tmp_path, capsys):
        stdout_file = tmp_path / "tool_stdout.txt"
        stdout_file.write_text(
            "Async agent launched.\nagentId: a6e7ca188209d1f47 (internal)\n",
            encoding="utf-8",
        )
        _, out, _ = _run(
            capsys,
            [
                "build-payload",
                "agent",
                "--step-id",
                "explore",
                "--phase",
                "main",
                "--agent",
                "discoverer",
                "--stdout-file",
                str(stdout_file),
            ],
            stdin=json.dumps({"status": "completed", "outputs": {}}),
        )
        payload = json.loads(out)
        assert "agentId: a6e7ca188209d1f47" not in json.dumps(payload)

    def test_agent_kind_merges_usage_file_over_empty_usage(self, tmp_path, capsys):
        """Adapter usage JSON from --usage-file wins over _EMPTY_USAGE (ORC-111 AC-6)."""
        adapter_usage = {
            "input_tokens": 5290,
            "output_tokens": 31,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 200,
            "model": "claude-opus-4-8",
            "cost_usd": 0.1958375,
        }
        usage_file = tmp_path / "tool_usage.json"
        usage_file.write_text(json.dumps(adapter_usage), encoding="utf-8")
        stdout_file = tmp_path / "stdout.txt"
        stdout_file.write_text("COMPLETION only\n", encoding="utf-8")
        _, out, _ = _run(
            capsys,
            [
                "build-payload",
                "agent",
                "--step-id",
                "explore",
                "--phase",
                "main",
                "--agent",
                "developer",
                "--stdout-file",
                str(stdout_file),
                "--usage-file",
                str(usage_file),
            ],
            stdin=json.dumps({"status": "completed", "outputs": {}}),
        )
        payload = json.loads(out)
        assert payload["usage"]["input_tokens"] == 5290
        assert payload["usage"]["output_tokens"] == 31
        assert payload["usage"]["cache_read_input_tokens"] == 100
        assert payload["usage"]["cache_creation_input_tokens"] == 200
        assert payload["usage"]["model"] == "claude-opus-4-8"
        assert payload["usage"]["cost_usd"] == 0.1958375

    def test_agent_kind_no_claude_jsonl_fallback_when_tokenless(
        self, tmp_path, capsys, monkeypatch
    ):
        """Shell path must not read ~/.claude JSONL when COMPLETION has no tokens (AC-6)."""
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        repo = home / "code" / "feature_worktrees" / "orc-111"
        repo.mkdir(parents=True)
        repo_slug = "-" + str(repo).lstrip("/").replace("/", "-").replace("_", "-")

        projects = home / ".claude" / "projects" / repo_slug
        projects.mkdir(parents=True)
        session = projects / "sess-abc.jsonl"
        session.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-05-26T06:28:09.995Z",
                    "message": {
                        "model": "claude-opus-4-7",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 20,
                            "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0,
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        stdout_file = tmp_path / "stdout.txt"
        stdout_file.write_text("COMPLETION only\n", encoding="utf-8")
        _, out, _ = _run(
            capsys,
            [
                "build-payload",
                "agent",
                "--step-id",
                "explore",
                "--phase",
                "main",
                "--agent",
                "developer",
                "--stdout-file",
                str(stdout_file),
                "--cwd",
                str(repo),
            ],
            stdin=json.dumps({"status": "completed", "outputs": {}}),
        )
        payload = json.loads(out)
        assert len([k for k in payload if k.startswith("agent") and k.endswith("_result")]) == 0
        assert payload["usage"] == {"input_tokens": 0, "output_tokens": 0, "model": "none"}


class TestLastTerminalStep:
    def test_returns_latest_terminal_entry(self, tmp_path, capsys):
        state = _write_state(
            tmp_path,
            {
                "step_history": [
                    {"step_id": "explore", "phase": "main", "status": "in_progress", "attempt": 1},
                    {
                        "step_id": "expand-plan",
                        "phase": "main",
                        "status": "completed",
                        "attempt": 1,
                    },
                ],
            },
        )
        _, out, _ = _run(capsys, ["last-terminal-step", str(state)])
        data = json.loads(out)
        assert data["step_id"] == "expand-plan"
        assert data["status"] == "completed"

    def test_empty_when_no_terminal_rows(self, tmp_path, capsys):
        state = _write_state(
            tmp_path,
            {"step_history": [{"step_id": "x", "phase": "main", "status": "in_progress"}]},
        )
        _, out, _ = _run(capsys, ["last-terminal-step", str(state)])
        assert json.loads(out) == {}


class TestPiSettings:
    def test_returns_provider_model_thinking(self, tmp_path, capsys, monkeypatch):
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "defaultProvider": "anthropic",
                    "defaultModel": "claude-opus-4-7",
                    "defaultThinkingLevel": "high",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(state_inspect, "_PI_SETTINGS_PATH", settings)
        _, out, _ = _run(capsys, ["pi-settings"])
        data = json.loads(out)
        assert data == {
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "thinking": "high",
        }

    def test_empty_object_when_settings_missing(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(state_inspect, "_PI_SETTINGS_PATH", tmp_path / "nope.json")
        rc, out, _ = _run(capsys, ["pi-settings"])
        assert rc == 0
        assert json.loads(out) == {}

    def test_empty_object_when_settings_unparseable(self, tmp_path, capsys, monkeypatch):
        settings = tmp_path / "settings.json"
        settings.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(state_inspect, "_PI_SETTINGS_PATH", settings)
        _, out, _ = _run(capsys, ["pi-settings"])
        assert json.loads(out) == {}

    def test_omits_keys_with_empty_values(self, tmp_path, capsys, monkeypatch):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"defaultProvider": "anthropic", "defaultModel": ""}), encoding="utf-8")
        monkeypatch.setattr(state_inspect, "_PI_SETTINGS_PATH", settings)
        _, out, _ = _run(capsys, ["pi-settings"])
        assert json.loads(out) == {"provider": "anthropic"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
