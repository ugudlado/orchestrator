"""Slack bridge for the Inngest driver: gates and questions become Slack messages
with buttons, and a click becomes the event the waiting run listens for.

    python -m spike.inngest_driver.slack_bridge

Socket Mode, so the machine needs no inbound port. One process holds two
connections: an Inngest app (function `Post to Slack`, triggered by
`orchestrator/attention` from driver.py) and Slack's Socket Mode websocket.

Env: SLACK_BOT_TOKEN (xoxb-, scopes chat:write), SLACK_APP_TOKEN (xapp-, connections:write),
SLACK_CHANNEL (channel id; invite the bot), SLACK_APPROVERS (optional comma-separated
Slack user ids; when set, only they can decide), plus the INNGEST_* variables the driver uses.

Clicks:
    gate      Approve -> gate.decided approved; Reject -> modal (note, optional reset_to) -> rejected
    question  Answer -> modal (answer, optional reset_to) -> input.provided
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import inngest
from inngest.connect import connect
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

CHANNEL = os.environ["SLACK_CHANNEL"]
APPROVERS = {u for u in os.environ.get("SLACK_APPROVERS", "").split(",") if u}
SNIPPET = 2500  # characters of each shown artifact; Slack caps a section at 3000

client = inngest.Inngest(app_id="orchestrator-slack", is_production=os.environ.get("INNGEST_DEV") != "1")
# Socket Mode carries no HTTP requests to sign, so there is no signing secret to check.
slack = AsyncApp(token=os.environ["SLACK_BOT_TOKEN"], request_verification_enabled=False)

TITLES = {
    "gate": ":raising_hand: *{slug}* is waiting for your sign-off at *{step_id}*",
    "question": ":speech_balloon: *{slug}* has a question at *{step_id}*",
    "finished": ":checkered_flag: *{slug}* finished: *{status}*",
}


def _artifact(artifacts_dir: str, name: str, path: str) -> dict:
    p = Path(path) if Path(path).is_absolute() else Path(artifacts_dir) / path
    try:
        text = p.read_text(errors="replace")
    except OSError:
        text = f"(not found: {p})"
    if len(text) > SNIPPET:
        text = text[:SNIPPET] + f"\n… ({len(text) - SNIPPET} more characters in {p.name})"
    # Artifacts are agent output: show them as code, so Slack renders no links or mentions.
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"*{name}*\n```{text.replace('```', '` ` `')}```"}}


def _blocks(d: dict) -> list[dict]:
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": TITLES[d["kind"]].format(**d)}}]
    if d["kind"] == "gate":
        blocks += [_artifact(d["artifacts_dir"], n, p) for n, p in (d.get("show") or {}).items()][:8]
    if d["kind"] == "question" and d.get("question"):
        blocks.append({"type": "section", "text": {"type": "plain_text", "text": d["question"][:2900]}})
    ref = json.dumps({"slug": d["slug"], "step_id": d.get("step_id")})
    if d["kind"] == "gate":
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "approve",
                        "value": ref,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject…"},
                        "style": "danger",
                        "action_id": "reject",
                        "value": ref,
                    },
                ],
            }
        )
    elif d["kind"] == "question":
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Answer…"},
                        "style": "primary",
                        "action_id": "answer",
                        "value": ref,
                    }
                ],
            }
        )
    return blocks


@client.create_function(
    fn_id="post-to-slack", name="Post to Slack", trigger=inngest.TriggerEvent(event="orchestrator/attention")
)
async def post_to_slack(ctx: inngest.Context) -> dict:
    d = ctx.event.data

    async def post() -> str:
        r = await slack.client.chat_postMessage(channel=CHANNEL, text=TITLES[d["kind"]].format(**d), blocks=_blocks(d))
        return r["ts"]

    return {"ts": await ctx.step.run("post", post)}


def _allowed(user: str) -> bool:
    return not APPROVERS or user in APPROVERS


async def _close(body: dict, verdict: str) -> None:
    """Swap the buttons for who decided what, so nobody clicks twice."""
    msg = body["message"]
    blocks = [b for b in msg["blocks"] if b["type"] != "actions"]
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": verdict}]})
    await slack.client.chat_update(channel=body["channel"]["id"], ts=msg["ts"], text=msg.get("text", ""), blocks=blocks)


@slack.action("approve")
async def on_approve(ack, body, respond):
    await ack()
    user = body["user"]["id"]
    if not _allowed(user):
        await respond(
            response_type="ephemeral", replace_original=False, text="You're not on the approver list for runs."
        )
        return
    ref = json.loads(body["actions"][0]["value"])
    await client.send(
        inngest.Event(
            name="orchestrator/gate.decided",
            data={"slug": ref["slug"], "gate": ref["step_id"], "decision": "approved", "by": user},
        )
    )
    await _close(body, f":white_check_mark: Approved by <@{user}>")


def _modal(callback: str, title: str, label: str, meta: dict) -> dict:
    return {
        "type": "modal",
        "callback_id": callback,
        "private_metadata": json.dumps(meta),
        "title": {"type": "plain_text", "text": title},
        "submit": {"type": "plain_text", "text": "Send"},
        "blocks": [
            {
                "type": "input",
                "block_id": "text",
                "label": {"type": "plain_text", "text": label},
                "element": {"type": "plain_text_input", "action_id": "v", "multiline": True},
            },
            {
                "type": "input",
                "block_id": "reset_to",
                "optional": True,
                "label": {"type": "plain_text", "text": "Send back to step (optional, e.g. design)"},
                "element": {"type": "plain_text_input", "action_id": "v"},
            },
        ],
    }


async def _open(ack, body, client_, callback: str, title: str, label: str) -> None:
    await ack()
    user = body["user"]["id"]
    if not _allowed(user):
        return
    meta = {**json.loads(body["actions"][0]["value"]), "channel": body["channel"]["id"], "ts": body["message"]["ts"]}
    await client_.views_open(trigger_id=body["trigger_id"], view=_modal(callback, title, label, meta))


@slack.action("reject")
async def on_reject(ack, body, client):
    await _open(ack, body, client, "reject_submit", "Reject", "What needs to change?")


@slack.action("answer")
async def on_answer(ack, body, client):
    await _open(ack, body, client, "answer_submit", "Answer", "Your answer")


def _values(view: dict) -> tuple[str, str]:
    v = view["state"]["values"]
    return (v["text"]["v"]["value"] or "").strip(), (v["reset_to"]["v"].get("value") or "").strip()


async def _finish_modal(meta: dict, verdict: str) -> None:
    hist = await slack.client.conversations_history(channel=meta["channel"], latest=meta["ts"], inclusive=True, limit=1)
    msg = hist["messages"][0]
    await _close({"message": msg, "channel": {"id": meta["channel"]}}, verdict)


@slack.view("reject_submit")
async def on_reject_submit(ack, body, view):
    await ack()
    meta, user = json.loads(view["private_metadata"]), body["user"]["id"]
    note, reset_to = _values(view)
    data = {"slug": meta["slug"], "gate": meta["step_id"], "decision": "rejected", "note": note, "by": user}
    if reset_to:
        data["reset_to"] = reset_to
    await client.send(inngest.Event(name="orchestrator/gate.decided", data=data))
    await _finish_modal(
        meta, f":x: Rejected by <@{user}>" + (f", back to *{reset_to}*" if reset_to else "") + f": {note[:500]}"
    )


@slack.view("answer_submit")
async def on_answer_submit(ack, body, view):
    await ack()
    meta, user = json.loads(view["private_metadata"]), body["user"]["id"]
    answer, reset_to = _values(view)
    data = {"slug": meta["slug"], "step_id": meta["step_id"], "answer": answer, "by": user}
    if reset_to:
        data["reset_to"] = reset_to
    await client.send(inngest.Event(name="orchestrator/input.provided", data=data))
    await _finish_modal(
        meta,
        f":speech_balloon: <@{user}> answered" + (f", back to *{reset_to}*" if reset_to else "") + f": {answer[:500]}",
    )


async def main() -> None:
    conn = connect([(client, [post_to_slack])])
    await asyncio.gather(conn.start(), AsyncSocketModeHandler(slack, os.environ["SLACK_APP_TOKEN"]).start_async())


if __name__ == "__main__":
    asyncio.run(main())
