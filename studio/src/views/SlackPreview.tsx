import { useEffect, useRef, useState } from "react";
import type { AppApi } from "../app";
import { decide, fastForward } from "../sim/engine";

type Msg =
  | { id: number; from: "user"; text: string }
  | { id: number; from: "bot"; text: string; card?: { rows: [string, string][] }; files?: string[]; buttons?: { label: string; act: string }[]; chosen?: string }
  | { id: number; from: "typing" };

type NoId<T> = T extends unknown ? Omit<T, "id"> : never;
const Hash = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden><path d="M6.2 2 5 14M11 2l-1.2 12M2.5 5.8h11M2 10.2h11" /></svg>
);
const TITLE = "Coupon stacking double-discounts at checkout";

export function SlackPreview({ app, runId, onClose }: { app: AppApi; runId?: string; onClose: () => void }) {
  const ref = useRef(app); ref.current = app;
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [run, setRun] = useState<string | null>(runId ?? null);
  const [awaiting, setAwaiting] = useState<"feedback" | null>(null);
  const alive = useRef(true);
  const box = useRef<HTMLDivElement>(null);
  const idc = useRef(0);
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const nid = () => ++idc.current;
  const add = (m: NoId<Msg>) => setMsgs((l) => [...l.filter((x) => x.from !== "typing"), { ...m, id: nid() } as Msg]);
  const typing = () => setMsgs((l) => [...l.filter((x) => x.from !== "typing"), { id: nid(), from: "typing" }]);

  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight; }, [msgs]);
  useEffect(() => () => { alive.current = false; }, []);

  const wfOf = (id: string) => ref.current.workflows[ref.current.runs[id]?.workflow];
  const gateMsg = async (id: string) => {
    const r = ref.current.runs[id]; const wf = wfOf(id);
    const gate = wf?.steps[r.cursor];
    add({ from: "bot", text: `${gate.id} needs you · ${r.slug}`, files: ["design.md", "tasks.yaml"], buttons: [{ label: "Approve", act: "approve" }, { label: "Request changes", act: "changes" }] });
  };

  useEffect(() => {
    (async () => {
      if (runId) {
        const r = ref.current.runs[runId];
        if (!r) return;
        await sleep(300);
        add({ from: "bot", text: `${r.slug} · ${r.title}\nStatus: ${r.status === "waiting" ? "waiting on you" : r.status === "needs_you" ? "needs you" : r.status}` });
        if (r.status === "waiting" && ref.current.slack.gates) { await sleep(500); if (alive.current) await gateMsg(runId); }
        return;
      }
      await sleep(500);
      add({ from: "user", text: TITLE });
      await sleep(400); typing(); await sleep(1100);
      if (!alive.current) return;
      add({ from: "bot", text: "Draft Linear issue", card: { rows: [["Title", TITLE], ["Label", "bug"], ["Team", "ORC"]] }, buttons: [{ label: "Create in ORC", act: "create" }, { label: "Edit", act: "edit" }] });
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const press = async (m: Msg, act: string, label: string) => {
    if (m.from !== "bot") return;
    setMsgs((l) => l.map((x) => (x.id === m.id ? ({ ...x, chosen: label } as Msg) : x)));
    const a = ref.current;
    if (act === "edit") { await sleep(300); add({ from: "bot", text: "Send the new title as a message." }); return; }
    if (act === "create") {
      const wfName = a.workflows["bug-intake"] ? "bug-intake" : "bugfix";
      const slug = "ORC-133";
      const id = a.startRun(wfName, slug, { title: TITLE });
      a.setView("runs"); a.openRun(null);
      setRun(id);
      const pipe = a.pipelines.find((p) => p.workflow === wfName);
      await sleep(500); typing(); await sleep(800);
      add({ from: "bot", text: `Created ${slug} · ${pipe?.name ?? "Bug intake"} pipeline started` });
      await sleep(1800);
      if (!alive.current) return;
      a.setRuns((rs) => { const cur = rs[id]; const wf = a.workflows[cur.workflow]; return { ...rs, [id]: fastForward(cur, wf, (r) => r.status === "waiting", false) }; });
      await sleep(200); typing(); await sleep(900);
      await gateMsg(id);
      return;
    }
    if (!run) return;
    if (act === "approve") {
      a.setRuns((rs) => ({ ...rs, [run]: decide(rs[run], wfOf(run), "approve") }));
      await sleep(500); add({ from: "bot", text: "Approved · impl_token granted · implement started" });
    } else if (act === "changes") {
      setAwaiting("feedback"); await sleep(400); add({ from: "bot", text: "What should change? Reply with a message." });
    }
  };

  const send = async (t: string) => {
    const q = t.trim(); if (!q) return;
    setText("");
    add({ from: "user", text: q });
    const a = ref.current;
    if (run && a.runs[run]?.status === "waiting") {
      a.addComment({ runId: run, file: "design.md", line: 0, text: q, owner: "design" });
      a.setRuns((rs) => ({ ...rs, [run]: decide(rs[run], wfOf(run), "changes") }));
      setAwaiting(null);
      await sleep(400); typing(); await sleep(900);
      add({ from: "bot", text: "Sent to Designer as feedback · run reworking design" });
    } else { await sleep(500); add({ from: "bot", text: "Noted. I will pass that along when the run reaches a gate." }); }
  };

  const waiting = run ? ref.current.runs[run]?.status === "waiting" : false;
  const real = msgs.filter((m) => m.from !== "typing");
  const rootId = real[0]?.id;
  const isTyping = msgs.some((m) => m.from === "typing");
  const body = (m: Msg) => m.from === "user" ? <div className="sk__txt">{m.text}</div> : m.from === "bot" ? (
    <>
      <div className="sk__txt">{m.text}</div>
      {m.card && <div className="sk__card">{m.card.rows.map(([k, v]) => <div key={k}><span>{k}</span>{v}</div>)}</div>}
      {m.files?.map((f) => <div className="sk__file" key={f}><span>▤</span>{f}</div>)}
      {m.buttons && <div className="sk__btns">{m.buttons.map((b, i) => (
        <button key={b.label} disabled={!!m.chosen} data-primary={i === 0 || undefined} data-chosen={m.chosen === b.label || undefined} onClick={() => press(m, b.act, b.label)}>{m.chosen === b.label ? `✓ ${b.label}` : b.label}</button>
      ))}</div>}
    </>
  ) : null;
  const row = (m: Msg) => m.from === "typing" ? null : (
    <div key={m.id} className="sk__m">
      <span className={`sk__av sk__av--${m.from}`}>{m.from === "user" ? "M" : <Hash size={14} />}</span>
      <div className="sk__c"><div className="sk__n">{m.from === "user" ? "M" : "Orchestrator Studio"}{m.from === "bot" && <em>APP</em>}<span>{m.from === "user" ? "10:42" : "10:42"}</span></div>{body(m)}</div>
    </div>
  );
  const replies = real.filter((m) => m.id !== rootId);
  return (
    <div className="overlay slov" onClick={onClose}>
      <div className="slwrap" onClick={(e) => e.stopPropagation()}>
        <div className="slack" role="dialog" aria-label="Slack conversation preview">
          <header className="slack__h"><Hash /><strong>orchestrator</strong><span>ugudlado</span><button className="slack__x" onClick={onClose} aria-label="Close preview">×</button></header>
          <div className="slack__body" ref={box}>
            {real[0] && row(real[0])}
            {(replies.length > 0 || isTyping) && (
              <div className="sk__thread">
                <div className="sk__cnt">{replies.length} {replies.length === 1 ? "reply" : "replies"} · Thread</div>
                {replies.map(row)}
                {isTyping && <div className="sk__typing">Orchestrator Studio is typing…</div>}
              </div>
            )}
            {!real[0] && isTyping && <div className="sk__typing">Orchestrator Studio is typing…</div>}
          </div>
          <div className="slack__f">
            {waiting && !awaiting && <button className="tchip" onClick={() => send("split the migration into its own task")}>split the migration into its own task</button>}
            <form onSubmit={(e) => { e.preventDefault(); send(text); }}>
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder={real.length ? "Reply to thread" : "Message #orchestrator"} aria-label="Slack message" />
              <button type="submit" aria-label="Send" disabled={!text.trim()}>Send</button>
            </form>
          </div>
        </div>
        <p className="slcap">Preview only. Each run is a thread; only allow-listed members (M) can act. Replies in the thread become feedback for the agent.</p>
      </div>
    </div>
  );
}
