import { useState } from "react";
import type { AppApi } from "../app";
import { Button } from "../affectui/button-clean";
import { DecisionInbox } from "../affectui/decision-inbox-clean";
import { sendAnswer, sendGate } from "../live/api";
import type { LiveComments } from "../live/useComments";
import type { Run } from "../sim/engine";
import type { Workflow } from "../types";
import { clip } from "./DocReview";

const SENT = "Sent, waiting for the run to continue…";
const plural = (n: number) => `${n} comment${n === 1 ? "" : "s"}`;

/** For feedback at a gate: the first step since the previous gate that writes a file this gate shows,
 *  so rework starts where the work was made (design-signoff → design, pr-signoff → implement), not at its reviewer. */
function reworkStep(wf: Workflow, gate: string): string {
  const i = wf.steps.findIndex((s) => s.id === gate);
  const show = wf.steps[i]?.show ?? [];
  let start = 0;
  for (let j = i - 1; j >= 0; j--) if (wf.steps[j].kind === "gate") { start = j + 1; break; }
  const writes = (j: number) => show.some((k) => k in (wf.steps[j].out ?? {}));
  for (let j = start; j < i; j++) if (writes(j)) return wf.steps[j].id;
  for (let j = start - 1; j >= 0; j--) if (writes(j)) return wf.steps[j].id; // e.g. hotfix-signoff → diagnose
  return "";
}

function SendBack({ wf, before, value, onChange, required }: { wf: Workflow; before: string; value: string; onChange: (v: string) => void; required?: boolean }) {
  const ids = wf.steps.slice(0, Math.max(0, wf.steps.findIndex((s) => s.id === before))).map((s) => s.id);
  return (
    <label className="field"><span className="field__l">{required ? "Send back to step" : "Send back to step (optional)"}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Send back to step">
        {/* A gate without on_failure has nowhere to go on its own: the run would stop and ask again. */}
        {!required && <option value="">Let the workflow decide</option>}
        {ids.map((id) => <option key={id} value={id}>{id}</option>)}
      </select></label>
  );
}

function PendingList({ cm, onOpen }: { cm: LiveComments; onOpen?: (f: string) => void }) {
  return (
    <ul className="plist">
      {cm.pending.map((c) => (
        <li key={c.id}>
          <button onClick={() => c.file && onOpen?.(c.file)} disabled={!c.file || !onOpen}>
            <span className="plist__w">{c.file ?? "run"}{c.quote ? "" : " · whole document"}</span>
            {c.quote && <span className="plist__q">“{clip(c.quote, 80)}”</span>}
            <span className="plist__t">{c.text}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Compact bar above the actions: pending comments waiting to go out with the next review. */
export function FeedbackTray({ run, cm, openFile }: { run: Run; cm: LiveComments; openFile: (f: string) => void }) {
  const [open, setOpen] = useState(false);
  const n = cm.pending.length;
  if (!n) return null;
  const waiting = !!run.live?.attention;
  return (
    <div className="tray">
      <div className="tray__h">
        <strong>{plural(n)} ready to send</strong>
        {!waiting && <span className="muted">Comments are sent with your next review</span>}
        <span className="rd__sp" />
        <button className="tray__t" aria-expanded={open} onClick={() => setOpen((v) => !v)}>{open ? "Hide list" : "Show list"}</button>
      </div>
      {open && <PendingList cm={cm} onOpen={openFile} />}
    </div>
  );
}

/** Sends the pending comments with a rejection (gate) or an answer (question). Used in the timeline and in the review overlay. */
export function SendForm({ app, run, wf, cm, sent, setSent, onDone, onCancel, onOpen }: {
  app: AppApi; run: Run; wf: Workflow; cm: LiveComments; sent: string | null; setSent: (k: string | null) => void; onDone?: () => void; onCancel?: () => void; onOpen?: (f: string) => void;
}) {
  const at = run.live!.attention!;
  const gate = at.kind === "gate";
  const key = gate ? `${at.step_id}#${at.attempt}` : `${at.step_id}?${at.wait}`;
  const [text, setText] = useState("");
  // Gates in the pack have no on_failure, so feedback must name where the rework starts.
  const mustSend = gate && !wf.steps.find((s) => s.id === at.step_id)?.on_failure;
  const [back, setBack] = useState(() => (mustSend ? reworkStep(wf, at.step_id) || wf.steps[0]?.id || "" : ""));
  const [err, setErr] = useState("");
  const n = cm.pending.length;
  const ok = !!text.trim() || n > 0 || (!gate && !!back);
  if (sent === key) return <div className="tl__done" role="status">{SENT}</div>;
  const go = () => {
    setErr(""); setSent(key);
    const p = gate
      ? sendGate(run.slug, { gate: at.step_id, attempt: at.attempt, decision: "rejected", note: text.trim() || undefined, reset_to: back || undefined, include_comments: true })
      : sendAnswer(run.slug, { step_id: at.step_id, wait: at.wait, answer: text.trim() || undefined, reset_to: back || undefined, include_comments: true });
    p.then(() => { app.refreshRuns(); cm.refresh(); onDone?.(); }).catch((e: Error) => { setSent(null); setErr(e.message); });
  };
  return (
    <div className="fbbox">
      <label className="field__l" htmlFor={`fb-${key}`}>{gate ? "Overall note (optional)" : "Your answer"}</label>
      <textarea id={`fb-${key}`} autoFocus={gate} rows={3} value={text} onChange={(e) => setText(e.target.value)} aria-label={gate ? "Overall note" : "Your answer"}
        placeholder={gate ? "e.g. Collapse retry loops into one entry" : "Type your answer"} />
      {n > 0 && <><div className="field__l">{plural(n)} will be sent</div><PendingList cm={cm} onOpen={onOpen} /></>}
      <SendBack wf={wf} before={at.step_id} value={back} onChange={setBack} required={mustSend} />
      {err && <p className="ferr" role="alert">{err}</p>}
      <div className="cmt__a">
        {onCancel && <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>}
        <Button size="sm" variant="primary" disabled={!ok} onClick={go}>{gate ? `Send feedback${n ? ` (${plural(n)})` : ""}` : "Send answer"}</Button>
      </div>
    </div>
  );
}

/** Live gate: approve, or send feedback (typed note plus the pending document comments). */
export function LiveGate({ app, run, wf, cm, openFile, sent, setSent }: { app: AppApi; run: Run; wf: Workflow; cm: LiveComments; openFile: (f: string) => void; sent: string | null; setSent: (k: string | null) => void }) {
  const at = run.live!.attention!;
  const key = `${at.step_id}#${at.attempt}`;
  const [err, setErr] = useState("");
  const [fb, setFb] = useState(false);
  const show = Object.entries(at.show ?? {});
  const n = cm.pending.length;
  const approve = () => {
    setErr(""); setSent(key);
    sendGate(run.slug, { gate: at.step_id, attempt: at.attempt, decision: "approved" }).then(() => app.refreshRuns()).catch((e: Error) => { setSent(null); setErr(e.message); });
  };
  return (
    <>
      <div className="gatefiles"><span>Files to review</span>
        {show.length === 0 && <em className="muted">none listed</em>}
        {show.map(([k, p]) => <button key={k} className="tchip" title={k} onClick={() => openFile(p)}>{p}</button>)}
      </div>
      {sent === key ? <div className="tl__done" role="status">{SENT}</div> : (
        <>
          {!fb && <DecisionInbox key={key} decisions={[{ id: key, title: `Approve ${at.step_id}?`, detail: "Open the files above to review and comment, then approve or send feedback.", primary: "Approve", secondary: "Send feedback" }]}
            onDecide={(_, a) => { if (a === "primary") setTimeout(approve, 1250); else setTimeout(() => setFb(true), 1200); }} />}
          {!fb && n > 0 && <p className="muted tray__hint">{n} pending comment{n === 1 ? "" : "s"} will be closed with this approval, not sent. <button className="linkbtn" onClick={() => setFb(true)}>Send them as feedback instead</button></p>}
          {fb && <SendForm app={app} run={run} wf={wf} cm={cm} sent={sent} setSent={setSent} onCancel={() => setFb(false)} onOpen={openFile} />}
        </>
      )}
      {err && <p className="ferr" role="alert">{err}</p>}
    </>
  );
}

/** Live question: the run asked something and waits for an answer; pending comments go along. */
export function LiveQuestion({ app, run, wf, cm, openFile, sent, setSent }: { app: AppApi; run: Run; wf: Workflow; cm: LiveComments; openFile: (f: string) => void; sent: string | null; setSent: (k: string | null) => void }) {
  const at = run.live!.attention!;
  const empty = !at.question?.trim();
  return (
    <div className="lqbox">
      <div className="field__l">{empty ? `${at.step_id} stopped and needs you` : `${at.step_id} is asking`}</div>
      {at.reason && <p className="lq lq--reason">{at.reason}</p>}
      {empty ? <p className="muted tray__hint">Answer to re-run the step with your answer, or send it back to an earlier step.</p> : <p className="lq">{at.question}</p>}
      <SendForm app={app} run={run} wf={wf} cm={cm} sent={sent} setSent={setSent} onOpen={openFile} />
    </div>
  );
}
