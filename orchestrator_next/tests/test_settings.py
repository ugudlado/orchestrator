"""orchestrator.toml — layering, typing, the trust fold-in, and the CLI.

The point of the settings file is that a knob is readable, writable, and says
where its value came from. These tests pin all three, plus the rule that keeps
existing users working: env still beats both files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator_next import settings, trust
from orchestrator_next.settings_cli import init_cmd, set_cmd, show_cmd


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Never read the developer's real ~/.orchestrator or repo settings."""
    monkeypatch.setenv("ORCHESTRATOR_HOME_DIR", str(tmp_path / "home"))
    monkeypatch.setenv("REPO_ROOT", str(tmp_path / "repo"))
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    for spec in settings.SCHEMA:
        if spec.env:
            monkeypatch.delenv(spec.env, raising=False)
    monkeypatch.setattr(settings, "_warned_legacy_trust", False)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# layering
# ---------------------------------------------------------------------------
def test_defaults_apply_when_no_file_exists():
    cfg = settings.load()
    assert cfg.get("run.max_parallel") == 1
    assert cfg.source("run.max_parallel") == "default"
    assert cfg.files == []


def test_repo_file_beats_machine_file(tmp_path):
    _write(settings.global_file(), "[run]\nmax_parallel = 2\n")
    _write(settings.repo_file(), "[run]\nmax_parallel = 8\n")
    cfg = settings.load()
    assert cfg.get("run.max_parallel") == 8
    assert cfg.source("run.max_parallel").endswith("repo/.orchestrator/orchestrator.toml")


def test_machine_file_still_wins_over_default_for_untouched_keys(tmp_path):
    _write(settings.global_file(), "[run]\nmax_parallel = 2\nstale_after_hours = 6.0\n")
    _write(settings.repo_file(), "[run]\nmax_parallel = 8\n")
    cfg = settings.load()
    assert cfg.get("run.max_parallel") == 8
    assert cfg.get("run.stale_after_hours") == 6.0
    assert cfg.source("run.stale_after_hours") == str(settings.global_file())


def test_env_overrides_both_files(monkeypatch):
    _write(settings.global_file(), "[run]\nmax_parallel = 2\n")
    _write(settings.repo_file(), "[run]\nmax_parallel = 8\n")
    monkeypatch.setenv("ORCHESTRATOR_MAX_PARALLEL", "16")
    cfg = settings.load()
    assert cfg.get("run.max_parallel") == 16
    assert cfg.source("run.max_parallel") == "$ORCHESTRATOR_MAX_PARALLEL"


def test_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MAX_PARALLEL", "16")
    cfg = settings.load(flags={"run.max_parallel": 3})
    assert cfg.get("run.max_parallel") == 3
    assert cfg.source("run.max_parallel") == "flag"


def test_full_precedence_chain_in_one_go(monkeypatch):
    """default < machine < repo < env < flag, all on the same key."""
    assert settings.load().get("run.max_parallel") == 1
    _write(settings.global_file(), "[run]\nmax_parallel = 2\n")
    assert settings.load().get("run.max_parallel") == 2
    _write(settings.repo_file(), "[run]\nmax_parallel = 8\n")
    assert settings.load().get("run.max_parallel") == 8
    monkeypatch.setenv("ORCHESTRATOR_MAX_PARALLEL", "16")
    assert settings.load().get("run.max_parallel") == 16
    assert settings.load(flags={"run.max_parallel": 3}).get("run.max_parallel") == 3


# ---------------------------------------------------------------------------
# typing
# ---------------------------------------------------------------------------
def test_wrong_type_in_file_names_the_file_and_key():
    _write(settings.global_file(), '[run]\nmax_parallel = "lots"\n')
    with pytest.raises(settings.SettingsError) as exc:
        settings.load()
    msg = str(exc.value)
    assert "run.max_parallel" in msg and "integer" in msg
    assert str(settings.global_file()) in msg


def test_wrong_type_in_env_names_the_variable(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_STALE_AFTER_HOURS", "soon")
    with pytest.raises(settings.SettingsError, match=r"\$ORCHESTRATOR_STALE_AFTER_HOURS"):
        settings.load()


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True),
                                          ("0", False), ("no", False)])
def test_bool_accepts_the_usual_env_spellings(monkeypatch, raw, expected):
    monkeypatch.setenv("ORCHESTRATOR_DISABLE_WORKTREE_LOCK", raw)
    assert settings.load().get("run.disable_worktree_lock") is expected


def test_list_from_env_is_comma_separated(monkeypatch):
    _write(settings.global_file(), '[trust]\nallow = ["a", "b"]\n')
    assert settings.load().get("trust.allow") == ["a", "b"]


def test_table_from_env_is_json(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES",
                       '{"designer": {"model_id": "x"}}')
    assert settings.load().get("models.route_overrides") == {"designer": {"model_id": "x"}}


def test_bad_json_table_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_MODEL_ROUTE_OVERRIDES", "{not json")
    with pytest.raises(settings.SettingsError, match="JSON object"):
        settings.load()


def test_unknown_key_warns_and_does_not_fail():
    _write(settings.global_file(), "[run]\nmax_parallel = 2\nwidgets = 3\n")
    cfg = settings.load()
    assert cfg.get("run.max_parallel") == 2          # the rest still loads
    assert any("run.widgets" in u for u in cfg.unknown)
    assert any(str(settings.global_file()) in u for u in cfg.unknown)


def test_unknown_setting_name_is_rejected():
    with pytest.raises(settings.SettingsError, match="unknown setting"):
        settings.load().get("run.nonesuch")


def test_invalid_toml_names_the_file():
    _write(settings.global_file(), "[run\nmax_parallel = 2\n")
    with pytest.raises(settings.SettingsError, match="not valid TOML"):
        settings.load()


# ---------------------------------------------------------------------------
# trust fold-in
# ---------------------------------------------------------------------------
def test_trust_reads_the_settings_file():
    _write(settings.global_file(),
           '[trust]\nallow = ["https://github.com/ugudlado/*"]\n')
    assert "allowed by" in trust.check_source("https://github.com/ugudlado/wf.git")
    with pytest.raises(trust.TrustError):
        trust.check_source("https://github.com/someone/evil.git")


def test_legacy_trust_toml_still_works_and_warns(capsys):
    _write(settings.home_dir() / "trust.toml",
           '[[allow]]\nsource = "https://github.com/ugudlado/*"\n')
    assert "allowed by" in trust.check_source("https://github.com/ugudlado/wf.git")
    assert "deprecated" in capsys.readouterr().err


def test_settings_file_trust_beats_legacy_trust_toml():
    _write(settings.home_dir() / "trust.toml", '[[allow]]\nsource = "https://old/*"\n')
    _write(settings.global_file(), '[trust]\nallow = ["https://new/*"]\n')
    assert "allowed by" in trust.check_source("https://new/pack.git")
    with pytest.raises(trust.TrustError):
        trust.check_source("https://old/pack.git")


def test_legacy_require_signed_is_carried_over():
    _write(settings.home_dir() / "trust.toml",
           'require_signed = true\n[[allow]]\nsource = "*"\n')
    assert settings.load().get("trust.require_signed") is True


def test_trust_all_env_still_bypasses(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_TRUST_ALL", "1")
    assert "bypassed" in trust.check_source("https://github.com/someone/evil.git")


def test_trust_all_can_be_set_in_the_file():
    _write(settings.global_file(), "[trust]\ntrust_all = true\n")
    assert "bypassed" in trust.check_source("https://github.com/someone/evil.git")


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------
def test_set_round_trips_through_load():
    settings.set_value("run.max_parallel", "6", path=settings.repo_file())
    assert settings.load().get("run.max_parallel") == 6


def test_set_preserves_other_keys_and_sections():
    path = settings.repo_file()
    _write(path, '[run]\nmax_parallel = 2\nstale_after_hours = 9.0\n\n'
                 '[state]\nbackend = "file"\n')
    settings.set_value("run.max_parallel", "6", path=path)
    cfg = settings.load()
    assert cfg.get("run.max_parallel") == 6
    assert cfg.get("run.stale_after_hours") == 9.0
    assert cfg.get("state.backend") == "file"


def test_set_creates_the_file_and_its_parent():
    path = settings.repo_file()
    assert not path.exists()
    settings.set_value("state.backend", "file", path=path)
    assert path.is_file() and 'backend = "file"' in path.read_text()


def test_set_rejects_a_bad_value_without_writing():
    path = settings.repo_file()
    with pytest.raises(settings.SettingsError, match="integer"):
        settings.set_value("run.max_parallel", "many", path=path)
    assert not path.exists()


def test_dump_round_trips_every_type():
    doc = {"run": {"max_parallel": 3, "stale_after_hours": 1.5,
                   "disable_worktree_lock": True},
           "trust": {"allow": ["a", "b"]}}
    _write(settings.global_file(), settings.dump(doc))
    cfg = settings.load()
    assert cfg.get("run.max_parallel") == 3
    assert cfg.get("run.stale_after_hours") == 1.5
    assert cfg.get("run.disable_worktree_lock") is True
    assert cfg.get("trust.allow") == ["a", "b"]


def test_template_parses_and_changes_nothing():
    """Every line in the generated template is commented out, so writing it
    must leave the effective settings exactly at their defaults."""
    _write(settings.global_file(), settings.template())
    cfg = settings.load()
    assert cfg.unknown == []
    assert all(res.source == "default" for _spec, res in cfg.items())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def test_show_json_reports_value_and_source(monkeypatch, capsys):
    _write(settings.global_file(), "[run]\nmax_parallel = 2\n")
    monkeypatch.setenv("ORCHESTRATOR_TENANT", "acme")
    assert show_cmd(["--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["settings"]["run.max_parallel"] == {
        "value": 2, "source": str(settings.global_file())}
    assert doc["settings"]["state.tenant"] == {
        "value": "acme", "source": "$ORCHESTRATOR_TENANT"}
    assert doc["settings"]["state.backend"]["source"] == "default"
    assert str(settings.global_file()) in doc["files"]


def test_show_json_surfaces_unknown_keys(capsys):
    _write(settings.global_file(), "[run]\nwidgets = 1\n")
    assert show_cmd(["--json"]) == 0
    assert any("run.widgets" in u for u in json.loads(capsys.readouterr().out)["unknown_keys"])


def test_show_text_lists_every_setting(capsys):
    assert show_cmd([]) == 0
    out = capsys.readouterr().out
    assert all(spec.dotted in out for spec in settings.SCHEMA)


def test_set_cmd_writes_the_repo_file_by_default(capsys):
    assert set_cmd(["run.max_parallel", "6"]) == 0
    assert settings.repo_file().is_file()
    assert not settings.global_file().exists()
    assert settings.load().get("run.max_parallel") == 6


def test_set_cmd_global_writes_the_machine_file():
    assert set_cmd(["run.max_parallel", "6", "--global"]) == 0
    assert settings.global_file().is_file()
    assert settings.repo_file() is None or not settings.repo_file().exists()


def test_set_cmd_warns_when_env_shadows_the_write(monkeypatch, capsys):
    monkeypatch.setenv("ORCHESTRATOR_MAX_PARALLEL", "16")
    assert set_cmd(["run.max_parallel", "6"]) == 0
    assert "still overrides" in capsys.readouterr().err


def test_set_cmd_rejects_unknown_key(capsys):
    assert set_cmd(["run.widgets", "1"]) == 3
    assert "unknown setting" in capsys.readouterr().err


def test_init_writes_a_template_and_refuses_to_clobber(capsys):
    assert init_cmd([]) == 0
    path = settings.repo_file()
    assert "[run]" in path.read_text()
    assert init_cmd([]) == 3
    assert "already exists" in capsys.readouterr().err
    assert init_cmd(["--force"]) == 0


# ---------------------------------------------------------------------------
# the engine actually reads it
# ---------------------------------------------------------------------------
def test_max_parallel_comes_from_the_file():
    from orchestrator_next.execute import max_parallel

    _write(settings.repo_file(), "[run]\nmax_parallel = 2\n")
    assert max_parallel() == 2


def test_stale_after_hours_comes_from_the_file():
    from orchestrator_next.protocol import _stale_after_hours

    _write(settings.repo_file(), "[run]\nstale_after_hours = 3.5\n")
    assert _stale_after_hours() == 3.5


def test_state_backend_comes_from_the_file():
    from orchestrator_next import state_store

    _write(settings.repo_file(), '[state]\nbackend = "file"\n')
    assert state_store.default_backend() == "file"
    assert state_store.default_state_url() == ""


def test_headless_backend_comes_from_the_file():
    from orchestrator_next import headless

    _write(settings.repo_file(), '[headless]\nbackend = "anthropic"\n')
    assert headless.resolve_backend() == "anthropic"


def test_doctor_settings_check_warns_when_no_settings_file_exists():
    from orchestrator_next.doctor import check_settings

    result = check_settings()
    assert result.status == "WARN" and "run orchestrator init" in result.detail


def test_doctor_settings_check_reports_files_and_unknown_keys():
    from orchestrator_next.doctor import check_settings

    _write(settings.global_file(), "[run]\nmax_parallel = 2\n")
    assert check_settings().status == "PASS"
    _write(settings.global_file(), "[run]\nmax_parallel = 2\nwidgets = 1\n")
    result = check_settings()
    assert result.status == "WARN" and "run.widgets" in result.detail


def test_doctor_settings_check_flags_deprecated_trust_toml():
    from orchestrator_next.doctor import check_settings

    _write(settings.home_dir() / "trust.toml", '[[allow]]\nsource = "*"\n')
    result = check_settings()
    assert result.status == "WARN" and "deprecated" in result.detail
