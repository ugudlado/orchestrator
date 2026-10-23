"""CLI: writes go to the DB only; the worker (`serve`) picks them up within one poll (0.5 s by default)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import psycopg

from orchestrator_driver.config import Config, ConfigError
from orchestrator_driver.db import DB


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="driver.yaml (default: $ORCH_DRIVER_CONFIG, else ~/.orchestrator/driver.yaml)")
    ap = argparse.ArgumentParser(prog="orchestrator-driver")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, help: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=help)

    add("serve", "run the worker")
    add("workflows", "list the pack's workflows with step counts")
    p = add("submit", "queue a run")
    p.add_argument("--workflow", required=True, help="a workflow in the pack")
    p.add_argument("--project", required=True, help="a configured project (path or folder name)")
    p.add_argument("--slug", required=True, help="the run's name; must be new")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticket", help="the ticket or brief text")
    g.add_argument("--ticket-file", help="a file holding the ticket or brief text")
    p.add_argument("--ticket-id", default="", help="a tracker id; set, the ticket steps sync to it")
    p.add_argument("--stub-choices", default="{}", help='agent "stub" only: JSON {"step": {"key": value}}')
    p = add("decide", "approve or reject a gate")
    p.add_argument("slug"), p.add_argument("gate"), p.add_argument("attempt", type=int)
    p.add_argument("decision", choices=["approved", "rejected"])
    p.add_argument("--reset-to"), p.add_argument("--note")
    p = add("answer", "answer a question a step asked")
    p.add_argument("slug"), p.add_argument("step"), p.add_argument("wait", type=int)
    p.add_argument("--answer"), p.add_argument("--reset-to")
    add("stop", "stop a run and kill its running step").add_argument("slug")
    add("show", "a run's status, wait and last log lines").add_argument("slug")
    add("list", "all runs")
    return ap


def submit(cfg: Config, db: DB, a: argparse.Namespace) -> dict:
    pack, flows = cfg.pack_dir(), cfg.workflows()
    if a.workflow not in flows:
        raise ConfigError(f"unknown workflow {a.workflow!r} in {pack} (available: {', '.join(flows) or 'none'})")
    repo = cfg.resolve_project(a.project)
    cfg.check_slug(a.slug)
    run_dir = cfg.run_dir(a.slug)
    if db.run(a.slug) or run_dir.exists():
        raise ConfigError(f"run {a.slug!r} already exists; slugs are never reused")
    ticket = Path(a.ticket_file).expanduser().read_text() if a.ticket_file else a.ticket
    request = {
        "workflow": a.workflow,
        "pack": str(pack),
        "slug": a.slug,
        "repo_root": str(repo),
        "artifacts_dir": str(run_dir.resolve() / "artifacts"),
        "ticket": ticket,
        "ticket_id": a.ticket_id,
        "stub_choices": json.loads(a.stub_choices),
    }
    try:
        db.add_run(request)
    except DB.IntegrityError:  # two submits raced past the check above
        raise ConfigError(f"run {a.slug!r} already exists; slugs are never reused") from None
    return {"slug": a.slug, "run_dir": str(run_dir.resolve()), "artifacts_dir": request["artifacts_dir"]}


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        cfg = Config.load(a.config)
        if a.cmd == "serve":
            from orchestrator_driver.driver import serve

            cfg.check_pack()
            asyncio.run(serve(cfg))
            return 0
        if a.cmd == "workflows":
            print(json.dumps([{"name": n, **w} for n, w in cfg.workflows().items()], indent=2))
            return 0
        db = DB(cfg.db_url())
        if a.cmd == "submit":
            print(json.dumps(submit(cfg, db, a), indent=2))
            return 0
    except (ConfigError, OSError, ValueError, psycopg.Error) as e:
        print(f"orchestrator-driver: {e}", file=sys.stderr)
        return 2
    if a.cmd == "list":
        rows = [{"slug": r["slug"], "status": r["status"], "current_step": r["current_step"]} for r in db.runs()]
        print(json.dumps(rows, indent=2))
    elif not (r := db.run(a.slug)):
        print(f"no such run: {a.slug}", file=sys.stderr)
        return 1
    elif a.cmd == "show":
        print(
            json.dumps(
                {
                    "status": r["status"],
                    "waiting": r["waiting"],
                    "current_step": r["current_step"],
                    "pid": r["pid"],
                    "outcome": r["outcome"] and r["outcome"]["status"],
                    "log": db.tail(a.slug),
                },
                indent=2,
            )
        )
    elif a.cmd == "decide":
        data = {"gate": a.gate, "attempt": a.attempt, "decision": a.decision, "reset_to": a.reset_to, "note": a.note}
        db.add_event(a.slug, "gate.decided", data)
    elif a.cmd == "answer":
        data = {"step_id": a.step, "wait": a.wait, "answer": a.answer, "reset_to": a.reset_to}
        db.add_event(a.slug, "input.provided", data)
    elif a.cmd == "stop":
        db.add_event(a.slug, "stop", {})
    return 0
