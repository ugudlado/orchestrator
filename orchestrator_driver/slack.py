"""Slack bridge for the driver: gates and questions become Slack messages with buttons, and a click
becomes the same event `orchestrator-driver decide` / `answer` inserts.

    orchestrator-slack --config /etc/orchestrator/driver.yaml

It reads the driver's database (the runs table) every couple of seconds and posts each newly opened
gate or question once, and each finished run once. Socket Mode, so the machine needs no inbound port.
Run it as the driver's user: like Studio, it writes `events` rows and nothing else in that database.
What it has posted is kept in its own file beside the database (`slack.db`), so a restart doesn't post
twice, and a wait that opened while it was down is posted when it comes back.

Reviewing in Slack:
- A gate message carries the start of each shown document; the whole documents are uploaded as files
  in the message's thread (needs the `files:write` scope; without it the bridge falls back to snippets).
- Replies in that thread are the review: Reject and Answer open with them filled in as the note or
  answer (needs `channels:history`, or `groups:history` for a private channel).
- When a wait closes some other way (Studio, the CLI, a timeout), the message loses its buttons.

Env: SLACK_BOT_TOKEN (xoxb-), SLACK_APP_TOKEN (xapp-, connections:write), SLACK_CHANNEL (channel id;
invite the bot), SLACK_APPROVERS (optional comma-separated Slack user ids; when set, only they decide),
SLACK_REWORK (optional JSON {gate: step} added to the default step a Reject sends the run back to).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import stat
import time
from pathlib import Path

from orchestrator_driver.config import Config
from orchestrator_driver.db import DB

POLL = 2.0  # seconds between looks at the runs table
SNIPPET = 1200  # characters of each document shown in the message itself
FILE_LIMIT = 1 << 20  # largest document uploaded or read
# A reject with no step to go back to stops the run at the gate's on_failure (dev.yaml has none), so the
# Reject form starts with the step that made what the gate shows, as Studio does.
REWORK = {
    "design-signoff": "design",
    "hotfix-signoff": "diagnose",
    "quick-signoff": "triage",
    "pr-signoff": "implement",
}

TITLES = {
    "gate": ":raising_hand: *{slug}* is waiting for your sign-off at *{step_id}*",
    "question": ":speech_balloon: *{slug}* has a question at *{step_id}*",
    "finished": ":checkered_flag: *{slug}* finished: *{status}*",
}


# --- what to post ---------------------------------------------------------------------------------------------


def wait_key(slug: str, w: dict) -> str:
    """One key per visit of a gate (attempt) or question (wait): the bridge posts each key once."""
    n = w.get("attempt") if w["kind"] == "gate" else w.get("wait")
    return f"{slug}|{w['kind']}|{w['step_id']}|{n}"


def read_doc(path: str, runs_root: Path, limit: int = FILE_LIMIT) -> str | None:
    """A document a gate shows, only from under the runs root and never through a link (agents write
    these files, and anything the bridge reads goes to Slack). None when it can't or shouldn't be read."""
    p = Path(path)
    try:
        if not p.is_absolute() or not p.parent.resolve().is_relative_to(runs_root.resolve()):
            return None
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except (OSError, ValueError):
        return None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return None
        raw = f.read(limit + 1)
    return raw[:limit].decode(errors="replace")


def _code(text: str) -> str:
    # Documents are agent output: show them as code, so Slack renders no links or mentions.
    return "```" + text.replace("```", "` ` `") + "```"


def blocks(slug: str, w: dict, runs_root: Path) -> list[dict]:
    """The message for an open gate or question: title, what to look at, and the buttons."""
    out: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": TITLES[w["kind"]].format(slug=slug, **w)}}
    ]
    if w["kind"] == "gate":
        for name, path in list((w.get("show") or {}).items())[:8]:
            text = read_doc(path, runs_root)
            if text is None:
                body = f"*{name}*: (not shown: {Path(path).name} isn't a readable file under the runs folder)"
            else:
                more = f"\n… the whole {Path(path).name} is in the thread" if len(text) > SNIPPET else ""
                body = f"*{name}*\n{_code(text[:SNIPPET])}{more}"
            out.append({"type": "section", "text": {"type": "mrkdwn", "text": body[:2900]}})
    else:
        if w.get("reason"):  # the engine stopped the run (say retries ran out): say why
            out.append({"type": "section", "text": {"type": "plain_text", "text": f"Stopped: {w['reason']}"[:2900]}})
        if w.get("question"):
            out.append({"type": "section", "text": {"type": "plain_text", "text": str(w["question"])[:2900]}})
    ref = json.dumps({"slug": slug, "step_id": w["step_id"], "attempt": w.get("attempt"), "wait": w.get("wait")})
    if w["kind"] == "gate":
        buttons = [("Approve", "approve", "primary"), ("Reject…", "reject", "danger")]
    else:
        buttons = [("Answer…", "answer", "primary")]
    out.append(
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": t}, "action_id": a, "style": s, "value": ref}
                for t, a, s in buttons
            ],
        }
    )
    out.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "Reply in the thread to leave review notes; Reject or Answer sends them."}
            ],
        }
    )
    return out


def closed(msg_blocks: list[dict], verdict: str) -> list[dict]:
    """The message without its buttons (and the hint under them), plus who decided what."""
    kept = [b for b in msg_blocks if b.get("type") not in ("actions", "context")]
    return [*kept, {"type": "context", "elements": [{"type": "mrkdwn", "text": verdict}]}]


# --- what has been posted -------------------------------------------------------------------------------------


class Posted:
    """The bridge's own memory: which waits it posted (and where), and which finished runs it announced."""

    def __init__(self, path: Path):
        new = not path.exists()
        self.c = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute(
            "CREATE TABLE IF NOT EXISTS posted (key TEXT PRIMARY KEY, slug TEXT, channel TEXT, ts TEXT, open INTEGER)"
        )
        self.fresh = new

    def has(self, key: str) -> bool:
        return self.c.execute("SELECT 1 FROM posted WHERE key=?", (key,)).fetchone() is not None

    def add(self, key: str, slug: str, channel: str = "", ts: str = "", open_: bool = False) -> None:
        self.c.execute("INSERT OR REPLACE INTO posted VALUES (?,?,?,?,?)", (key, slug, channel, ts, int(open_)))

    def open_posts(self) -> list[tuple[str, str, str, str]]:
        return list(self.c.execute("SELECT key, slug, channel, ts FROM posted WHERE open=1"))

    def close(self, channel: str, ts: str) -> None:
        self.c.execute("UPDATE posted SET open=0 WHERE channel=? AND ts=?", (channel, ts))


# --- the loop -------------------------------------------------------------------------------------------------


class Bridge:
    def __init__(self, cfg: Config, web, channel: str, approvers: set[str], rework: dict[str, str]):
        self.cfg, self.web, self.channel = cfg, web, channel
        self.approvers, self.rework = approvers, {**REWORK, **rework}
        self.db = DB(cfg.db)
        self.posted = Posted(cfg.db.parent / "slack.db")
        self.can_upload = True

    async def sync(self) -> None:
        """Post what newly needs someone, close what no longer does. Called every POLL seconds."""
        runs = self.db.runs()
        if self.posted.fresh:  # first start: don't announce every run that finished before the bridge existed
            for r in runs:
                if r["status"] in ("done", "failed", "stopped"):
                    self.posted.add(f"{r['slug']}|finished", r["slug"])
            self.posted.fresh = False
        open_now = set()
        for r in runs:
            w = r["waiting"]
            if w and r["status"] in ("waiting", "needs_you"):
                key = wait_key(r["slug"], w)
                open_now.add(key)
                if not self.posted.has(key):
                    await self.post_wait(key, r, w)
            elif r["status"] in ("done", "failed", "stopped") and not self.posted.has(f"{r['slug']}|finished"):
                status = (r["outcome"] or {}).get("status") or r["status"]
                await self.web.chat_postMessage(
                    channel=self.channel, text=TITLES["finished"].format(slug=r["slug"], status=status)
                )
                self.posted.add(f"{r['slug']}|finished", r["slug"])
        for key, _slug, channel, ts in self.posted.open_posts():
            if key not in open_now:  # decided in Studio or the CLI, timed out, or the run was stopped
                await self.update(channel, ts, ":heavy_minus_sign: Closed: this was settled outside Slack.")

    async def post_wait(self, key: str, run: dict, w: dict) -> None:
        slug = run["slug"]
        text = TITLES[w["kind"]].format(slug=slug, **w)
        r = await self.web.chat_postMessage(channel=self.channel, text=text, blocks=blocks(slug, w, self.cfg.runs_root))
        self.posted.add(key, slug, r["channel"], r["ts"], open_=True)
        if w["kind"] == "gate":
            await self.upload_docs(r["channel"], r["ts"], w.get("show") or {})

    async def upload_docs(self, channel: str, ts: str, show: dict) -> None:
        """The whole shown documents as files in the gate message's thread, so they read in full on a phone."""
        if not self.can_upload:
            return
        for name, path in list(show.items())[:8]:
            text = read_doc(path, self.cfg.runs_root)
            if text is None:
                continue
            try:
                await self.web.files_upload_v2(
                    channel=channel, thread_ts=ts, content=text, filename=Path(path).name, title=name
                )
            except Exception as e:  # a missing scope must not stop the gate being posted
                if "missing_scope" in str(e):
                    self.can_upload = False
                    await self.web.chat_postMessage(
                        channel=channel,
                        thread_ts=ts,
                        text="(Whole documents aren't attached: the Slack app needs the files:write scope.)",
                    )
                    return
                raise

    async def update(self, channel: str, ts: str, verdict: str) -> None:
        """Swap a posted wait's buttons for the verdict, and stop tracking it."""
        hist = await self.web.conversations_history(channel=channel, latest=ts, inclusive=True, limit=1)
        msgs = hist.get("messages") or []
        if msgs:
            m = msgs[0]
            await self.web.chat_update(
                channel=channel, ts=ts, text=m.get("text", ""), blocks=closed(m.get("blocks") or [], verdict)
            )
        self.posted.close(channel, ts)

    async def thread_notes(self, channel: str, ts: str) -> str:
        """People's replies in a wait's thread, oldest first, as the review notes to send."""
        try:
            r = await self.web.conversations_replies(channel=channel, ts=ts, limit=200)
        except Exception:  # noqa: BLE001 - no history scope: just start with an empty form
            return ""
        notes = [m.get("text", "").strip() for m in (r.get("messages") or [])[1:] if not m.get("bot_id")]
        return "\n".join(f"- {n}" for n in notes if n)[:2900]

    # --- clicks -------------------------------------------------------------------------------------------------

    def allowed(self, user: str) -> bool:
        return not self.approvers or user in self.approvers

    async def decide(self, ref: dict, user: str, decision: str, note: str = "", reset_to: str = "") -> None:
        data = {"gate": ref["step_id"], "attempt": ref["attempt"], "decision": decision, "reset_to": reset_to or None}
        self.db.add_event(ref["slug"], "gate.decided", {**data, "note": note or None, "by": f"slack:{user}"})

    async def answer(self, ref: dict, user: str, answer: str, reset_to: str = "") -> None:
        data = {"step_id": ref["step_id"], "wait": ref["wait"], "answer": answer, "reset_to": reset_to or None}
        self.db.add_event(ref["slug"], "input.provided", {**data, "by": f"slack:{user}"})

    def modal(self, callback: str, title: str, label: str, meta: dict, text: str, rework: str) -> dict:
        def field(block_id: str, lab: str, value: str, optional: bool, multiline: bool) -> dict:
            el = {"type": "plain_text_input", "action_id": "v", "multiline": multiline}
            if value:
                el["initial_value"] = value
            return {
                "type": "input",
                "block_id": block_id,
                "optional": optional,
                "label": {"type": "plain_text", "text": lab},
                "element": el,
            }

        return {
            "type": "modal",
            "callback_id": callback,
            "private_metadata": json.dumps(meta),
            "title": {"type": "plain_text", "text": title},
            "submit": {"type": "plain_text", "text": "Send"},
            "blocks": [
                field("text", label, text, False, True),
                field("reset_to", "Send back to step" + ("" if rework else " (optional)"), rework, not rework, False),
            ],
        }


def values(view: dict) -> tuple[str, str]:
    v = view["state"]["values"]
    return (v["text"]["v"].get("value") or "").strip(), (v["reset_to"]["v"].get("value") or "").strip()


def build_app(bridge: Bridge, bot_token: str):
    from slack_bolt.async_app import AsyncApp

    # Socket Mode carries no HTTP requests to sign, so there is no signing secret to check.
    app = AsyncApp(token=bot_token, request_verification_enabled=False)

    async def refuse(respond) -> None:
        await respond(
            response_type="ephemeral", replace_original=False, text="You're not on the approver list for runs."
        )

    @app.action("approve")
    async def on_approve(ack, body, respond):
        await ack()
        user = body["user"]["id"]
        if not bridge.allowed(user):
            return await refuse(respond)
        await bridge.decide(json.loads(body["actions"][0]["value"]), user, "approved")
        await bridge.update(body["channel"]["id"], body["message"]["ts"], f":white_check_mark: Approved by <@{user}>")

    async def open_form(ack, body, client, respond, callback: str, title: str, label: str) -> None:
        await ack()
        user = body["user"]["id"]
        if not bridge.allowed(user):
            return await refuse(respond)
        channel, ts = body["channel"]["id"], body["message"]["ts"]
        meta = {**json.loads(body["actions"][0]["value"]), "channel": channel, "ts": ts}
        rework = bridge.rework.get(meta["step_id"], "") if callback == "reject_submit" else ""
        notes = await bridge.thread_notes(channel, ts)
        view = bridge.modal(callback, title, label, meta, notes, rework)
        await client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.action("reject")
    async def on_reject(ack, body, client, respond):
        await open_form(ack, body, client, respond, "reject_submit", "Reject", "What needs to change?")

    @app.action("answer")
    async def on_answer(ack, body, client, respond):
        await open_form(ack, body, client, respond, "answer_submit", "Answer", "Your answer")

    @app.view("reject_submit")
    async def on_reject_submit(ack, body, view):
        await ack()
        meta, user = json.loads(view["private_metadata"]), body["user"]["id"]
        note, reset_to = values(view)
        await bridge.decide(meta, user, "rejected", note, reset_to)
        back = f", back to *{reset_to}*" if reset_to else ""
        await bridge.update(meta["channel"], meta["ts"], f":x: Rejected by <@{user}>{back}: {note[:500]}")

    @app.view("answer_submit")
    async def on_answer_submit(ack, body, view):
        await ack()
        meta, user = json.loads(view["private_metadata"]), body["user"]["id"]
        answer, reset_to = values(view)
        await bridge.answer(meta, user, answer, reset_to)
        back = f", back to *{reset_to}*" if reset_to else ""
        await bridge.update(meta["channel"], meta["ts"], f":speech_balloon: <@{user}> answered{back}: {answer[:500]}")

    return app


async def poll(bridge: Bridge) -> None:
    while True:
        try:
            await bridge.sync()
        except Exception as e:  # noqa: BLE001 - Slack or the database hiccuping must not end the bridge
            print(f"{time.strftime('%H:%M:%S')} sync failed: {e}", flush=True)
        await asyncio.sleep(POLL)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="orchestrator-slack", description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--config", help="the driver's config (default: $ORCH_DRIVER_CONFIG, then ~/.orchestrator/driver.yaml)"
    )
    a = p.parse_args(argv)
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    cfg = Config.load(a.config)
    env = os.environ
    approvers = {u for u in env.get("SLACK_APPROVERS", "").split(",") if u}
    rework = json.loads(env.get("SLACK_REWORK") or "{}")

    async def run() -> None:
        from slack_sdk.web.async_client import AsyncWebClient

        web = AsyncWebClient(token=env["SLACK_BOT_TOKEN"])
        bridge = Bridge(cfg, web, env["SLACK_CHANNEL"], approvers, rework)
        app = build_app(bridge, env["SLACK_BOT_TOKEN"])
        await asyncio.gather(poll(bridge), AsyncSocketModeHandler(app, env["SLACK_APP_TOKEN"]).start_async())

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
