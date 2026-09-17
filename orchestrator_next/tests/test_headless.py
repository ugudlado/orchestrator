"""Headless driver: the engine runs the model itself.

The Anthropic client is a stub — no network, no API key. Everything else is
real: the same start/step/done verbs, the same contracts, the same recorder.
The stub returns a tool_use turn and then a final JSON block, which is the
exact shape `run_judgment` has to survive.
"""
from __future__ import annotations

import json
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
    # does not re-open the node, so the run does not loop. It does NOT count
    # as a completed run either: the step wrote none of its outputs, so drive
    # parks at needs_you (exit 2) for a human instead of reporting success.
    assert headless.drive(run, client=client) == 2
    assert len(client.messages.requests) == 1

    entries, _ = protocol.events(run)
    assert any(e["step_id"] == "think" and e["status"] == "abandoned"
               for e in entries)

    result, _ = protocol.status(run)
    think = next(n for n in result["nodes"] if n["id"] == "think")
    assert think["status"] == "abandoned", (
        "an abandoned step must not read as completed — dependents would run "
        "against artifacts it never wrote"
    )


# ---------------------------------------------------------------------------
# backend selection
# ---------------------------------------------------------------------------
def test_backend_defaults_to_claude_cli_without_api_credentials(monkeypatch):
    """The workstation case: Claude Code is logged in, no API key exists."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HEADLESS_BACKEND", raising=False)
    assert headless.resolve_backend() == headless.BACKEND_CLAUDE_CLI


def test_backend_defaults_to_anthropic_when_a_key_is_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("ORCHESTRATOR_HEADLESS_BACKEND", raising=False)
    assert headless.resolve_backend() == headless.BACKEND_ANTHROPIC


def test_backend_flag_beats_env_and_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ORCHESTRATOR_HEADLESS_BACKEND", "anthropic")
    assert headless.resolve_backend("claude-cli") == headless.BACKEND_CLAUDE_CLI


def test_backend_env_beats_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ORCHESTRATOR_HEADLESS_BACKEND", "claude-cli")
    assert headless.resolve_backend() == headless.BACKEND_CLAUDE_CLI


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_HEADLESS_BACKEND", raising=False)
    with pytest.raises(headless.HeadlessError) as exc:
        headless.resolve_backend("gpt")
    assert "unknown headless backend" in str(exc.value)


def test_missing_claude_cli_fails_before_any_step(monkeypatch):
    """A missing CLI must surface up front, not mid-run."""
    monkeypatch.setattr(headless.shutil, "which", lambda _name: None)
    with pytest.raises(headless.HeadlessError) as exc:
        headless.check_claude_cli()
    assert "needs the `claude` CLI on PATH" in str(exc.value)


# ---------------------------------------------------------------------------
# claude-cli argv
# ---------------------------------------------------------------------------
def _cli_payload(**overrides):
    payload = {
        "step_id": "think", "model": "standard", "model_id": "claude-sonnet-5",
        "max_turns": 6, "tools": ["fs.read", "fs.write", "git.read"],
        "system": "# think\n\nThink.", "in": {}, "out": {"notes": "/w/notes.md"},
        "out_schema": {"complexity": {"type": "enum", "values": ["S", "M", "L"]}},
        "cwd": "/w",
    }
    payload.update(overrides)
    return payload


def _flag(argv, name):
    return argv[argv.index(name) + 1]


def test_cli_argv_carries_the_print_contract():
    argv = headless.build_cli_argv(_cli_payload(), executable="claude")
    assert argv[:2] == ["claude", "-p"]
    assert _flag(argv, "--output-format") == "json"
    assert _flag(argv, "--model") == "claude-sonnet-5"
    assert _flag(argv, "--permission-mode") == "acceptEdits"
    assert "--no-session-persistence" in argv
    assert _flag(argv, "--system-prompt") == "# think\n\nThink."


def test_cli_argv_adds_turn_headroom_for_the_structured_reply():
    """`--json-schema` spends a turn emitting the result; max_turns is for work."""
    argv = headless.build_cli_argv(_cli_payload(max_turns=6))
    assert int(_flag(argv, "--max-turns")) == 6 + headless.CLI_TURN_HEADROOM


def test_cli_argv_maps_contract_tools_to_claude_tool_names():
    argv = headless.build_cli_argv(_cli_payload())
    allowed = _flag(argv, "--allowedTools").split(",")
    assert allowed == ["Read", "Write", "Edit", "Bash"]


def test_cli_argv_omits_allowed_tools_when_the_step_declares_none():
    argv = headless.build_cli_argv(_cli_payload(tools=[]))
    assert "--allowedTools" not in argv


def test_cli_tool_map_matches_pack_export():
    """One capability must mean one tool surface, whoever runs the step."""
    from orchestrator_next import pack_export

    assert headless.CLI_TOOL_MAP == pack_export.TOOL_MAP


def test_cli_argv_passes_the_step_budget_when_set(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_STEP_BUDGET_USD", "0.50")
    argv = headless.build_cli_argv(_cli_payload())
    assert _flag(argv, "--max-budget-usd") == "0.50"


def test_cli_argv_omits_budget_when_unset(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_STEP_BUDGET_USD", raising=False)
    assert "--max-budget-usd" not in headless.build_cli_argv(_cli_payload())


def test_cli_result_schema_describes_the_declared_outputs():
    schema = json.loads(_flag(headless.build_cli_argv(_cli_payload()),
                              "--json-schema"))
    assert schema["properties"]["notes"] == {"type": "string"}
    assert schema["properties"]["complexity"] == {
        "type": "string", "enum": ["S", "M", "L"]}
    # A step must always be able to report failure through the same block.
    assert schema["properties"]["status"]["enum"] == [
        "completed", "failed", "abandoned"]
    assert schema["required"] == ["reason"]
    assert schema["additionalProperties"] is False


# ---------------------------------------------------------------------------
# claude-cli result parsing
# ---------------------------------------------------------------------------
def _cli_result(**overrides):
    data = {
        "type": "result", "subtype": "success", "is_error": False,
        "num_turns": 3, "total_cost_usd": 0.0074,
        "result": '{"notes":"/w/notes.md","complexity":"M","reason":"ok"}',
        "structured_output": {"notes": "/w/notes.md", "complexity": "M",
                              "reason": "ok"},
        "usage": {"input_tokens": 12, "output_tokens": 34,
                  "cache_read_input_tokens": 56,
                  "cache_creation_input_tokens": 78},
        "modelUsage": {"claude-sonnet-5-20250929": {"inputTokens": 12}},
    }
    data.update(overrides)
    return data


def _fake_run(monkeypatch, data, *, returncode=0, stderr="", stdout=None):
    calls = {}

    def _run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv, returncode,
            stdout=json.dumps(data) if stdout is None else stdout,
            stderr=stderr)

    monkeypatch.setattr(headless.subprocess, "run", _run)
    return calls


def test_cli_judgment_parses_structured_output_and_usage(monkeypatch):
    calls = _fake_run(monkeypatch, _cli_result())
    outcome = headless.run_judgment_cli(_cli_payload(), executable="claude")

    assert outcome["out"] == {"notes": "/w/notes.md", "complexity": "M",
                              "reason": "ok"}
    assert outcome["usage"] == {
        # The billed id from modelUsage wins over the alias we asked for —
        # that is the id pricing needs.
        "model": "claude-sonnet-5-20250929",
        "input_tokens": 12, "output_tokens": 34,
        "cache_read_input_tokens": 56, "cache_creation_input_tokens": 78,
        "cost_usd_reported": 0.0074,
    }
    assert calls["kwargs"]["cwd"] == "/w"


def test_cli_judgment_falls_back_to_a_fenced_block(monkeypatch):
    """No structured_output (older CLI, or schema declined) still parses."""
    data = _cli_result(result='done\n```json\n{"complexity":"S"}\n```')
    data.pop("structured_output")
    _fake_run(monkeypatch, data)
    outcome = headless.run_judgment_cli(_cli_payload(), executable="claude")
    assert outcome["out"] == {"complexity": "S"}


def test_cli_judgment_falls_back_to_the_requested_model_id(monkeypatch):
    data = _cli_result(modelUsage={})
    _fake_run(monkeypatch, data)
    outcome = headless.run_judgment_cli(_cli_payload(), executable="claude")
    assert outcome["usage"]["model"] == "claude-sonnet-5"


def test_cli_judgment_raises_on_is_error(monkeypatch):
    _fake_run(monkeypatch, _cli_result(
        is_error=True, subtype="error_max_turns",
        errors=["Reached maximum number of turns (4)"]))
    with pytest.raises(headless.HeadlessError) as exc:
        headless.run_judgment_cli(_cli_payload(), executable="claude")
    assert "Reached maximum number of turns" in str(exc.value)


def test_cli_judgment_raises_on_nonzero_exit(monkeypatch):
    _fake_run(monkeypatch, _cli_result(), returncode=1, stderr="boom")
    with pytest.raises(headless.HeadlessError):
        headless.run_judgment_cli(_cli_payload(), executable="claude")


def test_cli_judgment_reports_unparseable_output_with_the_stderr_tail(monkeypatch):
    _fake_run(monkeypatch, {}, returncode=1, stdout="not json",
              stderr="something broke")
    with pytest.raises(headless.HeadlessError) as exc:
        headless.run_judgment_cli(_cli_payload(), executable="claude")
    assert "something broke" in str(exc.value)


def test_cli_judgment_explains_a_logged_out_cli(monkeypatch):
    """The fix is one command; say it instead of dumping a stack."""
    _fake_run(monkeypatch, {}, returncode=1, stdout="",
              stderr="Error: not logged in")
    with pytest.raises(headless.HeadlessError) as exc:
        headless.run_judgment_cli(_cli_payload(), executable="claude")
    assert "run `claude` once" in str(exc.value)


def test_cli_judgment_rejects_a_payload_with_no_model_id(monkeypatch):
    _fake_run(monkeypatch, _cli_result())
    with pytest.raises(headless.HeadlessError) as exc:
        headless.run_judgment_cli(_cli_payload(model_id=""),
                                  executable="claude")
    assert "resolved to no model id" in str(exc.value)


def test_cli_judgment_timeout_scales_with_turns(monkeypatch):
    calls = _fake_run(monkeypatch, _cli_result())
    headless.run_judgment_cli(_cli_payload(max_turns=60), executable="claude")
    assert calls["kwargs"]["timeout"] == (60 + headless.CLI_TURN_HEADROOM) * 60

    calls = _fake_run(monkeypatch, _cli_result())
    headless.run_judgment_cli(_cli_payload(max_turns=1), executable="claude")
    assert calls["kwargs"]["timeout"] == headless.CLI_MIN_TIMEOUT_S


# ---------------------------------------------------------------------------
# the loop, on the claude-cli backend
# ---------------------------------------------------------------------------
def test_drive_runs_the_recipe_on_the_claude_cli_backend(pack, repo, monkeypatch):
    """Same protocol walk, no SDK and no API key anywhere in the process."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_HEADLESS_BACKEND", raising=False)
    monkeypatch.setattr(headless.shutil, "which", lambda _name: "/usr/bin/claude")

    started, _ = protocol.start("mini", "h-run")
    run = started["state"]
    notes = repo / ".orchestrator" / "runs" / "h-run" / "artifacts" / "notes.md"

    def _run(argv, **kwargs):
        Path(kwargs["cwd"])  # the step really is given a cwd
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text("n\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(
            _cli_result(structured_output={"notes": "notes.md",
                                           "complexity": "L",
                                           "reason": "thought"})), stderr="")

    monkeypatch.setattr(headless.subprocess, "run", _run)
    assert headless.drive(run) == 0

    result, _ = protocol.status(run)
    think = next(n for n in result["nodes"] if n["id"] == "think")
    assert think["status"] == "completed"
    assert result["usage"]["input_tokens"] > 0


def test_drive_records_a_failed_cli_step_as_abandoned(pack, repo, monkeypatch):
    monkeypatch.setattr(headless.shutil, "which", lambda _name: "/usr/bin/claude")
    started, _ = protocol.start("mini", "h-run")
    run = started["state"]

    monkeypatch.setattr(headless.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, stdout=json.dumps(
                            _cli_result(structured_output={
                                "status": "failed",
                                "reason": "cannot proceed"})), stderr=""))
    # needs_you (2), not success: the step abandoned without writing outputs.
    assert headless.drive(run, backend="claude-cli") == 2

    entries, _ = protocol.events(run)
    assert any(e["step_id"] == "think" and e["status"] == "abandoned"
               for e in entries)


# ---------------------------------------------------------------------------
# CLI flag plumbing
# ---------------------------------------------------------------------------
def test_backend_flag_is_parsed_out_of_argv():
    args = ["mini", "h-run", "--backend", "claude-cli", "--auto-approve"]
    assert headless._take_backend(args) == "claude-cli"
    assert args == ["mini", "h-run", "--auto-approve"]


def test_no_backend_flag_leaves_argv_alone():
    args = ["mini", "h-run"]
    assert headless._take_backend(args) is None
    assert args == ["mini", "h-run"]


def test_run_headless_rejects_an_unknown_backend_before_seeding(pack, monkeypatch):
    """An unknown name must not leave a half-started run behind."""
    monkeypatch.setattr(headless, "start", lambda *a, **k:
                        pytest.fail("start() must not run"))
    assert headless.run_headless_cmd(["mini", "h-run", "--backend", "gpt"]) == 3


def test_resuming_a_live_slug_with_inputs_is_refused(monkeypatch, capsys):
    """`run --headless` on an existing slug must not silently drop --inputs.

    `start()` resumes a live slug rather than re-seeding, which is deliberate —
    but it keeps the run's original `user_input`. A caller that passed a fresh
    `--inputs` would otherwise watch the whole run judge the *previous* ticket
    text with no warning, which is exactly how a real headless run spent real
    money designing the wrong thing.
    """
    monkeypatch.setattr(headless, "resolve_backend", lambda requested=None: "claude-cli")
    monkeypatch.setattr(headless, "start", lambda *a, **k: (
        {"run_id": "r1", "slug": "hl-2", "state": "/tmp/s.yaml", "resumed": True,
         "next": {}}, 0))
    monkeypatch.setattr(headless, "drive", lambda *a, **k:
                        pytest.fail("drive() must not run on a dropped-input resume"))

    code = headless.run_headless_cmd(
        ["mini", "hl-2", "--inputs", '{"ticket": "a new ticket"}'])

    assert code == 3
    assert "resum" in capsys.readouterr().err.lower()


def test_resuming_a_live_slug_without_inputs_still_drives(monkeypatch):
    """No `--inputs` means nothing can be dropped: the resume proceeds."""
    monkeypatch.setattr(headless, "resolve_backend", lambda requested=None: "claude-cli")
    monkeypatch.setattr(headless, "start", lambda *a, **k: (
        {"run_id": "r1", "slug": "hl-2", "state": "/tmp/s.yaml", "resumed": True,
         "next": {}}, 0))
    monkeypatch.setattr(headless, "drive", lambda *a, **k: 0)

    assert headless.run_headless_cmd(["mini", "hl-2"]) == 0


def test_cli_judgment_picks_the_routed_model_out_of_several(monkeypatch):
    """Claude Code bills background sub-tasks to haiku alongside the main model.

    `modelUsage` is then a multi-key dict in no meaningful order, so taking its
    first key attributed a Fable step's whole cost to haiku. A live hl-2 run
    priced all four judgment steps at haiku rates this way. Prefer the model we
    actually routed; only fall back to an arbitrary key when it is absent.
    """
    data = _cli_result(modelUsage={
        "claude-haiku-4-5-20251001": {"inputTokens": 3},
        "claude-sonnet-5-20250929": {"inputTokens": 12},
    })
    _fake_run(monkeypatch, data)
    outcome = headless.run_judgment_cli(_cli_payload(), executable="claude")
    assert outcome["usage"]["model"] == "claude-sonnet-5-20250929"


def test_cli_judgment_keeps_a_billed_id_the_route_does_not_name(monkeypatch):
    """An unrelated single key is still the billed id — keep reporting it."""
    data = _cli_result(modelUsage={"claude-opus-5-20260101": {"inputTokens": 9}})
    _fake_run(monkeypatch, data)
    outcome = headless.run_judgment_cli(_cli_payload(), executable="claude")
    assert outcome["usage"]["model"] == "claude-opus-5-20260101"
