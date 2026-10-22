"""The Slack bridge against a real driver run (stub agents) and a fake Slack client."""

from __future__ import annotations

import asyncio
import os

import pytest

from orchestrator_driver.config import Config
from orchestrator_driver.slack import Bridge, blocks, read_doc


class FakeSlack:
    """Records calls; messages live in a dict so chat_update / conversations_history round-trip."""

    def __init__(self, upload_error: str | None = None, replies: list[dict] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.msgs: dict[str, dict] = {}
        self.upload_error, self.replies = upload_error, replies or []

    async def chat_postMessage(self, **kw):
        self.calls.append(("post", kw))
        ts = str(len(self.calls))
        self.msgs[ts] = {"ts": ts, "text": kw.get("text"), "blocks": kw.get("blocks") or []}
        return {"channel": kw["channel"], "ts": ts}

    async def chat_update(self, **kw):
        self.calls.append(("update", kw))
        if kw["ts"] not in self.msgs:
            raise RuntimeError("message_not_found")
        self.msgs[kw["ts"]].update(text=kw["text"], blocks=kw["blocks"])

    async def conversations_replies(self, **kw):
        return {"messages": [self.msgs[kw["ts"]], *self.replies]}

    async def files_upload_v2(self, **kw):
        self.calls.append(("upload", kw))
        if self.upload_error:
            raise RuntimeError(self.upload_error)

    def of(self, kind: str) -> list[dict]:
        return [kw for k, kw in self.calls if k == kind]


def bridge(env, web: FakeSlack, **kw) -> Bridge:
    return Bridge(Config.load(env.config, env={}), web, "C1", kw.get("approvers", set()), {})


def sync(b: Bridge) -> None:
    asyncio.run(b.sync())


def test_gate_posted_once_with_docs_then_closed_when_decided_elsewhere(env):
    env.submit("sl")
    env.start()
    g = env.at_gate("sl", "design-signoff")["waiting"]
    web = FakeSlack()
    b = bridge(env, web)
    sync(b)
    sync(b)  # a second look posts nothing new
    posts = web.of("post")
    assert len(posts) == 1 and "design-signoff" in posts[0]["text"]
    assert [a["action_id"] for a in posts[0]["blocks"][-2]["elements"]] == ["approve", "reject"]
    assert {u["title"] for u in web.of("upload")} == set(g["show"])  # every shown doc, whole, in the thread
    assert all(u["thread_ts"] == "1" for u in web.of("upload"))
    env.cli("decide", "sl", "design-signoff", g["attempt"], "approved")  # decided outside Slack
    env.at_gate("sl", "pr-signoff")
    sync(b)
    first = web.msgs["1"]
    assert not any(bl["type"] == "actions" for bl in first["blocks"])  # buttons gone
    assert "outside Slack" in first["blocks"][-1]["elements"][0]["text"]
    assert any("pr-signoff" in p["text"] for p in web.of("post"))


def test_click_inserts_the_drivers_own_event_and_a_restart_does_not_repost(env):
    env.submit("click")
    env.start()
    g = env.at_gate("click", "design-signoff")["waiting"]
    web = FakeSlack()
    b = bridge(env, web)
    sync(b)
    ref = {"slug": "click", "step_id": "design-signoff", "attempt": g["attempt"], "wait": None}
    assert b.decide(ref, "U1", "approved")
    env.at_gate("click", "pr-signoff")
    assert not b.decide(ref, "U1", "approved")  # a late click finds the gate visit settled: nothing sent  # the worker took the Slack approval like any other
    b2 = bridge(env, web)  # a restarted bridge keeps its memory in slack.db
    sync(b2)
    assert sum("design-signoff" in p["text"] for p in web.of("post")) == 1


def test_thread_replies_become_the_notes_and_missing_upload_scope_falls_back(env):
    env.submit("notes")
    env.start()
    env.at_gate("notes", "design-signoff")
    web = FakeSlack(
        upload_error="missing_scope",
        replies=[{"text": "tighten the error case", "user": "U1"}, {"text": "bot echo", "bot_id": "B1"}],
    )
    b = bridge(env, web)
    sync(b)
    assert len(web.of("upload")) == 1 and not b.can_upload  # stopped after the first refusal
    assert any("files:write" in p["text"] for p in web.of("post"))
    assert asyncio.run(b.thread_notes("C1", "1")) == "- tighten the error case"


def test_first_start_does_not_announce_old_finished_runs(env):
    env.submit("old", workflow="research")
    env.start()
    for _ in range(2):
        g = env.wait("old", lambda r: r["status"] in ("waiting", "done"))
        if g["status"] == "done":
            break
        env.cli("decide", "old", g["waiting"]["step_id"], g["waiting"]["attempt"], "approved")
    env.wait("old", lambda r: r["status"] == "done")
    web = FakeSlack()
    sync(bridge(env, web))
    assert web.of("post") == []


def test_docs_only_from_under_the_runs_root_and_never_through_a_link(tmp_path):
    root = tmp_path / "runs"
    (root / "r" / "artifacts").mkdir(parents=True)
    doc = root / "r" / "artifacts" / "design.md"
    doc.write_text("# design\n" + "x" * 3000)
    secret = tmp_path / "secret.txt"
    secret.write_text("token")
    (root / "r" / "artifacts" / "link.md").symlink_to(secret)
    assert read_doc(str(doc), root).startswith("# design")
    assert read_doc(str(secret), root) is None
    assert read_doc(str(root / "r" / "artifacts" / "link.md"), root) is None
    assert read_doc("design.md", root) is None  # relative paths aren't trusted
    w = {"kind": "gate", "step_id": "g", "attempt": 1, "show": {"design": str(doc), "x": str(secret)}}
    text = " ".join(bl["text"]["text"] for bl in blocks("r", w, root) if bl["type"] == "section")
    assert "whole design.md is in the thread" in text and "token" not in text


def test_app_builds_with_its_handlers(env):
    pytest.importorskip("slack_bolt")
    assert asyncio.run(_build(env)) is not None


async def _build(env):
    from orchestrator_driver.slack import build_app

    return build_app(bridge(env, FakeSlack()), "xoxb-test")


def test_doc_whose_folder_became_a_link_is_refused(tmp_path, monkeypatch):
    """The folder check and the open aren't atomic: the opened file's real path is checked too."""
    root = tmp_path / "runs"
    (root / "r" / "artifacts").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "design.md").write_text("secret")
    real_open = os.open

    def swap_then_open(p, *a, **kw):  # the agent swaps the folder for a link between check and open
        art = root / "r" / "artifacts"
        if art.is_dir() and not art.is_symlink():
            art.rmdir()
            art.symlink_to(outside)
        return real_open(p, *a, **kw)

    monkeypatch.setattr(os, "open", swap_then_open)
    assert read_doc(str(root / "r" / "artifacts" / "design.md"), root) is None


def test_a_failed_edit_is_not_retried_every_tick(env):
    env.submit("gone")
    env.start()
    g = env.at_gate("gone", "design-signoff")["waiting"]
    web = FakeSlack()
    b = bridge(env, web)
    sync(b)
    del web.msgs["1"]  # someone deleted the gate message
    env.cli("decide", "gone", "design-signoff", g["attempt"], "approved")
    env.at_gate("gone", "pr-signoff")
    sync(b)
    sync(b)
    assert len(web.of("update")) == 1  # tried once, then let go


def test_hint_says_cut_when_uploads_are_off(tmp_path):
    root = tmp_path / "runs"
    (root / "r").mkdir(parents=True)
    doc = root / "r" / "design.md"
    doc.write_text("y" * 3000)
    w = {"kind": "gate", "step_id": "g", "attempt": 1, "show": {"design": str(doc), "bad": 5}}
    text = " ".join(bl["text"]["text"] for bl in blocks("r", w, root, uploads=False) if bl["type"] == "section")
    assert "is in the thread" not in text and "cut" in text and "isn't a readable file" in text
