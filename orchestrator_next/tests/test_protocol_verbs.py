"""Protocol v2 verbs end-to-end: start -> step -> done -> step.

The recipe is exec -> judgment -> exec, which is the shape that proves the two
things Phase 1.1 promises: `step` runs exec steps internally and only surfaces
at a judgment step, and `done --out` validates the structured output against
the contract's `out:` block instead of parsing a COMPLETION text block.

Nothing below the engine is faked. Real contracts, real dispatcher, real
recorder, real state store — the model is simply never called, because these
verbs hand the payload out rather than running it.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from orchestrator_next import protocol
from orchestrator_next.parser import KIND_JUDGMENT

STEPS = ("prep", "think", "finish")


@pytest.fixture
def repo(tmp_path):
    """A real git repo with the artifact dir the contracts point into."""
    root = tmp_path / "repo"
    (root / "spec" / "changes" / "p-run").mkdir(parents=True)
    (root / "README.md").write_text("repo\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t.test"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-m", "init"],
    ):
        subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, env=env, check=True)
    return root


@pytest.fixture
def pack(tmp_path, repo, monkeypatch):
    """A three-step pack: exec, judgment (with in:/out:), exec."""
    pack_root = repo / ".orchestrator" / "tp"
    steps = pack_root / "steps"
    (pack_root / "workflows").mkdir(parents=True)
    (pack_root / "workflows" / "mini.yaml").write_text(
        yaml.safe_dump({"steps": list(STEPS)}), encoding="utf-8"
    )

    for step_id in ("prep", "finish"):
        d = steps / step_id
        d.mkdir(parents=True)
        (d / "contract.yaml").write_text(
            yaml.safe_dump({"id": step_id, "version": 1, "kind": "exec",
                            "run": "script.sh"}),
            encoding="utf-8",
        )
        script = d / "script.sh"
        script.write_text(
            '#!/usr/bin/env bash\necho \'{"reason": "ran"}\'\n', encoding="utf-8"
        )
        script.chmod(0o755)

    think = steps / "think"
    think.mkdir(parents=True)
    (think / "contract.yaml").write_text(
        yaml.safe_dump({
            "id": "think",
            "version": 1,
            "kind": "judgment",
            "prompt": "SKILL.md",
            "max_turns": 12,
            "tools": ["fs.read", "fs.write"],
            "side_effects": [],
            "in": {"brief": {"artifact": "brief.md", "optional": True}},
            "out": {
                "notes": {"artifact": "notes.md"},
                "complexity": {"type": "enum", "values": ["S", "M", "L"]},
            },
        }),
        encoding="utf-8",
    )
    (think / "SKILL.md").write_text("# think\n\nThink about it.\n", encoding="utf-8")

    models = pack_root / "models.yaml"
    models.write_text(
        "models:\n  standard: {model_id: mock-model, tool: mock}\n"
        "step_models:\n" + "".join(f"  {s}: standard\n" for s in STEPS),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_MODELS_CONFIG", str(models))
    monkeypatch.setenv("ORCHESTRATOR_CONFIG", str(pack_root))
    monkeypatch.setenv("REPO_ROOT", str(repo))
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path / "orchome"))
    monkeypatch.delenv("ORCHESTRATOR_STATE_URL", raising=False)
    monkeypatch.setenv(
        "ORCHESTRATOR_STATE_BACKEND", "file"
    )
    return pack_root


def _artifacts(repo: Path) -> Path:
    """The engine-owned artifacts base (plan Phase 2.1) — the `mini` recipe
    declares no artifacts_root, so this is where named artifacts land."""
    return repo / ".orchestrator" / "runs" / "p-run" / "artifacts"


def test_start_runs_exec_then_stops_at_judgment(pack, repo):
    """start seeds the run and returns the first judgment payload.

    The leading exec step must already have run: the harness never sees it.
    """
    result, code = protocol.start("mini", "p-run")
    assert code == 0
    assert result["slug"] == "p-run"
    assert result["run_id"]

    nxt = result["next"]
    assert nxt["status"] == "ready"
    assert nxt["kind"] == KIND_JUDGMENT
    assert nxt["step_id"] == "think"

    payload = nxt["payload"]
    assert payload["max_turns"] == 12
    assert payload["tools"] == ["fs.read", "fs.write"]
    assert payload["model"] == "standard"
    assert payload["model_id"] == "mock-model"
    # Artifacts are resolved to absolute paths by the engine (principle 4).
    assert payload["out"]["notes"] == str(_artifacts(repo) / "notes.md")
    assert payload["in"]["brief"] == str(_artifacts(repo) / "brief.md")
    assert payload["out_schema"]["complexity"]["values"] == ["S", "M", "L"]
    # A migrated step's prompt carries the structured-output contract, not
    # the legacy COMPLETION block.
    assert "COMPLETION:" not in payload["system"]
    assert "```json" in payload["system"]


def test_done_records_and_returns_next_step(pack, repo):
    """A valid --out advances the run through the trailing exec step to done."""
    started, _ = protocol.start("mini", "p-run")
    run = started["state"]
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")

    result, code = protocol.done(
        run, "think",
        out={"notes": str(_artifacts(repo) / "notes.md"), "complexity": "M"},
        usage={"input_tokens": 120, "output_tokens": 45, "model": "mock-model"},
    )
    assert code == 0
    assert result["status"] == "ok"
    # `finish` is an exec step, so `done`'s own next-step lookup runs it and
    # reports the completed run rather than handing anything back.
    assert result["next"]["status"] == "done"


def test_done_rejects_missing_artifact(pack, repo):
    """A declared artifact that was never written fails the call, not the run."""
    started, _ = protocol.start("mini", "p-run")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.done(
            started["state"], "think",
            out={"complexity": "M"},
            usage={"input_tokens": 1, "output_tokens": 1},
        )
    assert "notes" in str(exc.value)
    assert "artifact not found" in str(exc.value)


def test_done_rejects_enum_outside_declared_values(pack, repo):
    started, _ = protocol.start("mini", "p-run")
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.done(
            started["state"], "think",
            out={"notes": "notes.md", "complexity": "XXL"},
            usage={"input_tokens": 1, "output_tokens": 1},
        )
    assert "complexity" in str(exc.value)


def test_done_rejects_a_declared_value_left_out_entirely(pack, repo):
    """The v1 engine coerced a completed step to failed when a contract's
    required output was missing. v2 rejects the `done` call instead: the
    harness fixes the step's output and retries, rather than the engine
    recording a half-finished step (protocol v2 §5)."""
    started, _ = protocol.start("mini", "p-run")
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.done(
            started["state"], "think",
            out={"notes": "notes.md"},  # complexity declared, never reported
            usage={"input_tokens": 1, "output_tokens": 1},
        )
    assert "complexity" in str(exc.value)
    assert "missing" in str(exc.value)


def test_done_rejects_zero_usage(pack, repo):
    """protocol-v2 §5 keeps record.py's existing usage guard verbatim."""
    started, _ = protocol.start("mini", "p-run")
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")
    with pytest.raises(protocol.ProtocolError) as exc:
        protocol.done(
            started["state"], "think",
            out={"notes": "notes.md", "complexity": "S"},
            usage={"input_tokens": 0, "output_tokens": 0},
        )
    assert "agent_step_missing_usage" in str(exc.value)


def test_status_reports_nodes_and_usage(pack, repo):
    started, _ = protocol.start("mini", "p-run")
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")
    protocol.done(
        started["state"], "think",
        out={"notes": "notes.md", "complexity": "L"},
        usage={"input_tokens": 100, "output_tokens": 20, "model": "mock-model"},
    )
    result, code = protocol.status(started["state"])
    assert code == 0
    ids = {n["id"]: n for n in result["nodes"]}
    assert set(ids) == set(STEPS)
    assert ids["think"]["kind"] == KIND_JUDGMENT
    assert ids["prep"]["kind"] == "exec"
    assert result["usage"]["input_tokens"] == 100
    assert result["gate_token"] is None


def test_events_lists_history(pack, repo):
    started, _ = protocol.start("mini", "p-run")
    entries, code = protocol.events(started["state"])
    assert code == 0
    assert any(e["step_id"] == "prep" for e in entries)


def test_resolve_run_rejects_unknown_ref(pack):
    with pytest.raises(protocol.ProtocolError):
        protocol.resolve_run("definitely-not-a-run")


def test_step_on_unknown_run_is_an_engine_error(pack, capsys):
    """Unknown run is exit 3 with a JSON error — never a silent new run."""
    code = protocol.main("step", ["nope-not-here"])
    assert code == protocol.EXIT_ERROR
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "error"


def test_start_on_a_live_slug_resumes_rather_than_reseeds(pack, repo):
    """A second ``start`` on the same slug picks the run back up.

    It used to mint a fresh ``run_id`` and strand the first run mid-step, so a
    driver that calls ``start`` to resume (the Claude mod's ``run`` tool does)
    silently lost every step already recorded.
    """
    first, code = protocol.start("mini", "p-resume")
    assert code == 0
    assert first.get("resumed") is not True

    second, code = protocol.start("mini", "p-resume")
    assert code == 0
    assert second["resumed"] is True
    assert second["run_id"] == first["run_id"]
    assert second["state"] == first["state"]
    assert second["next"]["step_id"] == first["next"]["step_id"]


def test_start_on_an_unknown_slug_still_seeds(pack, repo):
    """The resume path must not swallow a genuinely new run."""
    result, code = protocol.start("mini", "p-fresh")
    assert code == 0
    assert result.get("resumed") is not True
    assert result["run_id"]


def test_done_prices_a_step_whose_usage_names_no_model(pack, repo):
    """A harness that reports tokens but not the model still gets a cost.

    The Claude mod sent only `{input_tokens, output_tokens}`, so pricing found
    no `usage.model`, recorded no cost, and `status --json` summed every agent
    step to `cost_usd: 0.0`. `done` now falls back to the model the dispatcher
    routed the step to, and flags the figure as an estimate.
    """
    import yaml as _yaml

    (pack / "pricing.yaml").write_text(
        "models:\n"
        "  - model_id: mock-model\n"
        "    input_usd: 3.0\n"
        "    output_usd: 15.0\n"
        "    cache_read_usd: 0.0\n"
        "    cache_creation_usd: 0.0\n"
        '    effective_from: "2000-01-01T00:00:00"\n',
        encoding="utf-8",
    )
    from orchestrator_next import pricing as _pricing_mod
    _pricing_mod._load_pricing_table.cache_clear()

    started, _ = protocol.start("mini", "p-run")
    run = started["state"]
    (_artifacts(repo) / "notes.md").write_text("notes\n", encoding="utf-8")

    _result, code = protocol.done(
        run, "think",
        out={"notes": str(_artifacts(repo) / "notes.md"), "complexity": "M"},
        usage={"input_tokens": 1_000_000, "output_tokens": 0},
    )
    assert code == 0

    raw = _yaml.safe_load(Path(run).read_text(encoding="utf-8"))
    entry = next(e for e in raw["step_history"] if e["step_id"] == "think")
    assert entry["usage"]["model"] == "mock-model"
    assert entry["usage"]["cost_partial"] is True
    assert entry["usage"]["cost_usd"] == pytest.approx(3.0)

    from orchestrator_next.pricing import sum_cost_usd
    assert sum_cost_usd(raw) == pytest.approx(3.0)
    _pricing_mod._load_pricing_table.cache_clear()
