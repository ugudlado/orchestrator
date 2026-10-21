import { useEffect, useRef, useState } from "react";
import type { AppApi } from "../app";
import { makeSource } from "../connectors";
import { decide, fastForward } from "../sim/engine";

type Msg =
  | { id: number; from: "user"; text: string }
  | { id: number; from: "bot"; text: string; card?: { rows: [string, string][] }; files?: string[]; buttons?: { label: string; act: string }[]; chosen?: string }
  | { id: number; from: "typing" };

type NoId<T> = T extends unknown ? Omit<T, "id"> : never;
const Plane = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" aria-hidden><path d="M14 2 1.8 7l3.9 1.6L7 13l2-2.7 3 2.3z" /><path d="m5.7 8.6 4.6-3.4" /></svg>
);
const TITLE = "Coupon stacking double-discounts at checkout";

export function TelegramPreview({ app, runId, onClose }: { app: AppApi; runId?: string; onClose: () => void }) {
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
      const id = a.startRun(wfName, slug, { title: TITLE, source: makeSource("telegram", slug) });
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
  return (
    <div className="overlay tgov" onClick={onClose}>
      <div className="tgwrap" onClick={(e) => e.stopPropagation()}>
        <div className="phone" role="dialog" aria-label="Telegram conversation preview">
          <header className="phone__h"><span className="phone__av"><Plane /></span><div><div className="phone__n">orchestrator_m_bot</div><div className="phone__s">bot</div></div><button className="phone__x" onClick={onClose} aria-label="Close preview">×</button></header>
          <div className="phone__body" ref={box}>
            {msgs.map((m) => m.from === "typing" ? <div key={m.id} className="tg tg--bot tg--typing"><i /><i /><i /></div> : (
              <div key={m.id} className={`tg tg--${m.from}`}>
                {m.from === "user" ? m.text : (
                  <>
                    <div className="tg__t">{m.text}</div>
                    {m.card && <div className="tg__card">{m.card.rows.map(([k, v]) => <div key={k}><span>{k}</span>{v}</div>)}</div>}
                    {m.files?.map((f) => <div className="tg__file" key={f}><span>▤</span>{f}</div>)}
                    {m.buttons && <div className="tg__btns">{m.buttons.map((b) => (
                      <button key={b.label} disabled={!!m.chosen} data-chosen={m.chosen === b.label || undefined} onClick={() => press(m, b.act, b.label)}>{m.chosen === b.label ? `✓ ${b.label}` : b.label}</button>
                    ))}</div>}
                  </>
                )}
              </div>
            ))}
          </div>
          <div className="phone__f">
            {waiting && !awaiting && <button className="tchip" onClick={() => send("split the migration into its own task")}>split the migration into its own task</button>}
            <form onSubmit={(e) => { e.preventDefault(); send(text); }}>
              <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Message" aria-label="Telegram message" />
              <button type="submit" aria-label="Send" disabled={!text.trim()}><Plane /></button>
            </form>
          </div>
        </div>
        <p className="tgcap">Preview only. Messages from allow-listed user M reach the same assistant as the web app.</p>
      </div>
    </div>
  );
}
