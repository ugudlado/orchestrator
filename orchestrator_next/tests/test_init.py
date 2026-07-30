"""Tests for `orchestrator init` (T3 of distribution improvements)."""
from __future__ import annotations


from orchestrator_next.init import main


def test_init_creates_project_yaml_in_empty_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)

    rc = main([])
    out = capsys.readouterr().out

    target = tmp_path / "spec" / "project.yaml"
    assert rc == 0
    assert target.is_file()
    assert "wrote" in out
    assert "orchestrator doctor" in out
    assert "orchestrator run" in out
    # Content matches the bundled template — sanity-check a known key.
    assert "ticketing:" in target.read_text()


def test_init_second_run_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)

    rc1 = main([])
    assert rc1 == 0
    target = tmp_path / "spec" / "project.yaml"
    target.write_text("# user-edited\nversion: 1\n")

    rc2 = main([])
    out2 = capsys.readouterr().out

    assert rc2 == 0
    assert str(target) in out2
    # Existing file is untouched — not clobbered with the template again.
    assert target.read_text() == "# user-edited\nversion: 1\n"


def test_init_help_exits_zero(capsys):
    rc = main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Usage" in out


def test_cli_init_arm_dispatches(tmp_path, monkeypatch, capsys):
    """orchestrator init must reach the init arm (not fall through to _usage
    for having <2 args — that was the T3 landmine in the arg-count check)."""
    import sys

    from orchestrator_next.cli import main as cli_main

    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.delenv("ORCHESTRATOR_REPO_ROOT", raising=False)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "init"])

    try:
        cli_main()
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected SystemExit from cli.main()")

    assert (tmp_path / "spec" / "project.yaml").is_file()
