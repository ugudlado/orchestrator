"""Headless driver: the engine runs the model itself.

The Anthropic client is a stub — no network, no API key. Everything else is
real: the same start/step/done verbs, the same contracts, the same recorder.
The stub returns a tool_use turn and then a final JSON block, which is the
exact shape `run_judgment` has to survive.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from orchestrator_next import headless, protocol

STEPS = ("prep", "think")


# ---------------------------------------------------------------------------
# stub SDK objects
# ---------------------------------------------------------------------------
def _text_block(text: str):
    return SimpleNamespace(type="text", text=text,
                           model_dump=lambda: {"type": "text", "text": text})


def _tool_block(tool_id: str, name: str, args: dict):
    return SimpleNamespace(
        type="tool_use", id=tool_id, name=name, input=args,
        model_dump=lambda: {"type": "tool_use", "id": tool_id,
                            "name": name, "input": args},
    )


class FakeMessages:
    """Replays a scripted list of responses and records every request."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages ran out of scripted responses")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def _response(blocks, *, input_tokens=100, output_tokens=25):
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "spec" / "changes" / "h-run").mkdir(parents=True)
    (root / "README.md").write_text("repo\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in (["init", "-b", "main"], ["config", "user.email", "t@t.test"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-m", "init"]):
        subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, env=env, check=True)
    return root


@pytest.fixture
def pack(tmp_path, repo, monkeypatch):
    pack_root = repo / ".orchestrator" / "hp"
    steps = pack_root / "steps"
    (pack_root / "workflows").mkdir(parents=True)
    (pack_root / "workflows" / "mini.yaml").write_text(
        yaml.safe_dump({"steps": list(STEPS)}), encoding="utf-8"
    )

    prep = steps / "prep"
    prep.mkdir(parents=True)
    (prep / "contract.yaml").write_text(
        yaml.safe_dump({"id": "prep", "version": 1, "kind": "exec",
                        "run": "script.sh"}), encoding="utf-8")
    script = prep / "script.sh"
    script.write_text('#!/usr/bin/env bash\necho \'{"reason": "ran"}\'\n',
                      encoding="utf-8")
    script.chmod(0o755)

    think = steps / "think"
    think.mkdir(parents=True)
    (think / "contract.yaml").write_text(
        yaml.safe_dump({
            "id": "think", "version": 1, "kind": "judgment",
            "prompt": "SKILL.md", "max_turns": 6,
            "tools": ["fs.read", "fs.write"],
            "out": {
                "notes": {"artifact": "notes.md"},
                "complexity": {"type": "enum", "values": ["S", "M", "L"]},
            },
        }), encoding="utf-8")
    (think / "SKILL.md").write_text("# think\n\nThink.\n", encoding="utf-8")

    models = pack_root / "models.yaml"
    models.write_text(
        "models:\n  standard: {model_id: claude-sonnet-5, tool: mock}\n"
        "step_models:\n" + "".join(f"  {s}: standard\n" for s in STEPS),
        encoding="utf-8")
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(models))
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack_root))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path / "orchome"))
    monkeypatch.setenv("ORCHESTRATOR_STATE_BACKEND", "file")
    monkeypatch.delenv("ORCHESTRATOR_STATE_URL", raising=False)
    return pack_root


# ---------------------------------------------------------------------------
# tool runner
# ---------------------------------------------------------------------------
def test_fs_tools_round_trip(tmp_path):
    (tmp_path / "sub").mkdir()
    out, err = headless.run_tool(
        "fs_write", {"path": "sub/a.txt", "content": "hello"}, tmp_path)
    assert not err
    assert (tmp_path / "sub" / "a.txt").read_text() == "hello"

    out, err = headless.run_tool("fs_read", {"path": "sub/a.txt"}, tmp_path)
    assert (out, err) == ("hello", False)

    out, err = headless.run_tool("fs_list", {"path": "sub"}, tmp_path)
    assert "a.txt" in out and not err


def test_shell_run_reports_exit_code(tmp_path):
    out, err = headless.run_tool("shell_run", {"command": "exit 3"}, tmp_path)
    assert err is True
    assert "exit_code: 3" in out


def test_tools_refuse_to_escape_cwd(tmp_path):
    """A headless run is unattended; a traversal would silently corrupt the host."""
    (tmp_path / "inside").mkdir()
    out, err = headless.run_tool(
        "fs_read", {"path": "../outside.txt"}, tmp_path / "inside")
    assert err is True
    assert "escapes the step cwd" in out


def test_unknown_tool_is_an_error_not_a_crash(tmp_path):
    out, err = headless.run_tool("fs_delete", {"path": "x"}, tmp_path)
    assert err is True
    assert "unknown tool" in out


# ---------------------------------------------------------------------------
# final-JSON extraction
# ---------------------------------------------------------------------------
def test_extract_final_json_prefers_last_fenced_block():
    text = (
        'first ```json\n{"a": 1}\n``` then\n'
        '```json\n{"notes": "n.md", "complexity": "M"}\n```'
    )
    assert headless.extract_final_json(text) == {"notes": "n.md",
                                                 "complexity": "M"}


def test_extract_final_json_falls_back_to_bare_object():
    assert headless.extract_final_json('done.\n{"complexity": "S"}') == {
        "complexity": "S"}


def test_extract_final_json_raises_without_json():
    with pytest.raises(ValueError):
        headless.extract_final_json("no json at all")


# ---------------------------------------------------------------------------
# run_judgment
# ---------------------------------------------------------------------------
def test_run_judgment_executes_tool_then_final_json(pack, repo):
    started, _ = protocol.start("mini", "h-run")
    payload = started["next"]["payload"]
    notes = Path(payload["out"]["notes"])

    client = FakeClient([
        _response([_tool_block("t1", "fs_write",
                               {"path": str(notes), "content": "written\n"})]),
        _response([_text_block(
            '```json\n{"notes": "' + notes.name + '", "complexity": "M"}\n```'
        )], input_tokens=50, output_tokens=10),
    ])

    outcome = headless.run_judgment(payload, client=client)
    assert outcome["out"] == {"notes": notes.name, "complexity": "M"}
    # Usage accumulates across every turn, not just the last one.
    assert outcome["usage"]["input_tokens"] == 150
    assert outcome["usage"]["output_tokens"] == 35
    assert outcome["usage"]["model"] == "claude-sonnet-5"
    # The tool actually ran — this is the engine's own runner, not a mock.
    assert notes.read_text() == "written\n"

    first = client.messages.requests[0]
    assert first["model"] == "claude-sonnet-5"
    assert {t["name"] for t in first["tools"]} == {
        "fs_read", "fs_write", "fs_list", "shell_run"}


def test_run_judgment_retries_a_message_with_no_json(pack, repo):
    started, _ = protocol.start("mini", "h-run")
    payload = started["next"]["payload"]
    client = FakeClient([
        _response([_text_block("I am done thinking.")]),
        _response([_text_block('```json\n{"complexity": "S"}\n```')]),
    ])
    outcome = headless.run_judgment(payload, client=client)
    assert outcome["out"] == {"complexity": "S"}
    assert len(client.messages.requests) == 2


def test_run_judgment_gives_up_after_max_turns(pack, repo):
    started, _ = protocol.start("mini", "h-run")
    payload = started["next"]["payload"]
    client = FakeClient([_response([_text_block("still no json")])])
    with pytest.raises(headless.HeadlessError) as exc:
        headless.run_judgment(payload, client=client, max_turns=1)
    assert "no JSON object" in str(exc.value)


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------
def test_drive_runs_the_recipe_to_completion(pack, repo):
    """exec -> judgment -> done, with the engine calling the model itself."""
    started, _ = protocol.start("mini", "h-run")
    run = started["state"]
    notes = repo / ".orchestrator" / "runs" / "h-run" / "artifacts" / "notes.md"

    client = FakeClient([
        _response([_tool_block("t1", "fs_write",
                               {"path": str(notes), "content": "n\n"})]),
        _response([_text_block(
            '```json\n{"notes": "notes.md", "complexity": "L"}\n```')]),
    ])

    assert headless.drive(run, client=client) == 0

    result, _ = protocol.status(run)
    think = next(n for n in result["nodes"] if n["id"] == "think")
    assert think["status"] == "completed"
    assert result["usage"]["input_tokens"] > 0


def test_drive_reports_a_failed_step_as_abandoned(pack, repo):
    """A model that reports status: failed records abandoned, not completed."""
    started, _ = protocol.start("mini", "h-run")
    run = started["state"]
    client = FakeClient([
        _response([_text_block(
            '```json\n{"status": "failed", "reason": "cannot proceed"}\n```')]),
    ])
    # `abandoned` skips out-validation (there is no output to validate) and
    # does not re-open the node, so the run finishes rather than looping.
    assert headless.drive(run, client=client) == 0
    assert len(client.messages.requests) == 1

    entries, _ = protocol.events(run)
    assert any(e["step_id"] == "think" and e["status"] == "abandoned"
               for e in entries)
