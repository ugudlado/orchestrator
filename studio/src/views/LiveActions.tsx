import { useState } from "react";
import type { AppApi } from "../app";
import { Button } from "../affectui/button-clean";
import { DecisionInbox } from "../affectui/decision-inbox-clean";
import { sendAnswer, sendGate } from "../live/api";
import type { Run } from "../sim/engine";
import type { Workflow } from "../types";

const SENT = "Sent, waiting for the run to continue…";

function SendBack({ wf, before, value, onChange }: { wf: Workflow; before: string; value: string; onChange: (v: string) => void }) {
  const ids = wf.steps.slice(0, Math.max(0, wf.steps.findIndex((s) => s.id === before))).map((s) => s.id);
  return (
    <label className="field"><span className="field__l">Send back to step (optional)</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Send back to step">
        <option value="">Let the workflow decide</option>
        {ids.map((id) => <option key={id} value={id}>{id}</option>)}
      </select></label>
  );
}

/** Live gate: approve, or request changes with a note (typed text plus line comments on this run's files). */
export function LiveGate({ app, run, wf, openFile }: { app: AppApi; run: Run; wf: Workflow; openFile: (f: string) => void }) {
  const at = run.live!.attention!;
  const key = `${at.step_id}#${at.attempt}`;
  const [sent, setSent] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [fb, setFb] = useState(false);
  const [text, setText] = useState("");
  const [back, setBack] = useState("");
  const show = Object.entries(at.show ?? {});
  const post = (b: { decision: "approved" | "rejected"; note?: string; reset_to?: string }) => {
    setErr(""); setSent(key);
    sendGate(run.slug, { gate: at.step_id, attempt: at.attempt, ...b }).then(() => app.refreshRuns()).catch((e: Error) => { setSent(null); setErr(e.message); });
  };
  const note = () => [text.trim(), ...app.comments.filter((c) => c.runId === run.id).map((c) => `${c.file}${c.line ? `:${c.line}` : ""}: ${c.text}`)].filter(Boolean).join("\n");
  const n = app.comments.filter((c) => c.runId === run.id).length;
  return (
    <>
      <div className="gatefiles"><span>Files to review</span>
        {show.length === 0 && <em className="muted">none listed</em>}
        {show.map(([k, p]) => <button key={k} className="tchip" title={k} onClick={() => openFile(p)}>{p}</button>)}
      </div>
      {sent === key ? <div className="tl__done" role="status">{SENT}</div> : (
        <>
          <DecisionInbox key={key} decisions={[{ id: key, title: `Approve ${at.step_id}?`, detail: "Open the files above to review and comment, then approve or ask for changes.", primary: "Approve", secondary: "Request changes" }]}
            onDecide={(_, a) => { if (a === "primary") setTimeout(() => post({ decision: "approved" }), 1250); else setTimeout(() => setFb(true), 1200); }} />
          {fb && (
            <div className="fbbox">
              <label className="field__l" htmlFor="fb">What should change? Sent to the agent as the review note{n ? `, with ${n} line comment${n > 1 ? "s" : ""}` : ""}.</label>
              <textarea id="fb" autoFocus rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Collapse retry loops into one entry" />
              <SendBack wf={wf} before={at.step_id} value={back} onChange={setBack} />
              <div className="cmt__a"><Button size="sm" variant="primary" disabled={!note()} onClick={() => post({ decision: "rejected", note: note(), reset_to: back || undefined })}>Send and request changes</Button></div>
            </div>
          )}
        </>
      )}
      {err && <p className="ferr" role="alert">{err}</p>}
    </>
  );
}

/** Live question: the run asked something and waits for a free-text answer. */
export function LiveQuestion({ app, run, wf }: { app: AppApi; run: Run; wf: Workflow }) {
  const at = run.live!.attention!;
  const key = `${at.step_id}?${at.wait}`;
  const [sent, setSent] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const [text, setText] = useState("");
  const [back, setBack] = useState("");
  return (
    <div className="fbbox" key={key}>
      <div className="field__l">{at.step_id} is asking</div>
      <p className="lq">{at.question}</p>
      {sent === key ? <div className="tl__done" role="status">{SENT}</div> : (
        <>
          <textarea aria-label="Your answer" rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Type your answer" />
          <SendBack wf={wf} before={at.step_id} value={back} onChange={setBack} />
          <div className="cmt__a"><Button size="sm" variant="primary" disabled={!text.trim()} onClick={() => {
            setErr(""); setSent(key);
            sendAnswer(run.slug, { step_id: at.step_id, wait: at.wait, answer: text.trim(), reset_to: back || undefined }).then(() => app.refreshRuns()).catch((e: Error) => { setSent(null); setErr(e.message); });
          }}>Send</Button></div>
        </>
      )}
      {err && <p className="ferr" role="alert">{err}</p>}
    </div>
  );
}
