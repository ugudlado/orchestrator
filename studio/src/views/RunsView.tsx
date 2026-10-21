import { useState } from "react";
import type { AppApi } from "../app";
import { Button } from "../affectui/button-clean";
import { fmtElapsed, type Run } from "../sim/engine";
import { startLiveRun } from "../live/api";
import { connKind, connName, triggerText } from "../connectors";

export const STATUS_LABEL: Record<Run["status"], string> = { queued: "queued, waiting for the driver", running: "running", waiting: "waiting on you", needs_you: "needs you", done: "done", failed: "failed" };
export const Chip = ({ status }: { status: Run["status"] }) => <span className={`schip schip--${status}`}>{(status === "running" || status === "queued") && <i />}{STATUS_LABEL[status]}</span>;

export function RunsView({ app }: { app: AppApi }) {
  const [form, setForm] = useState(false);
  const [pform, setPform] = useState(false);
  const [pn, setPn] = useState("");
  const [pwf, setPwf] = useState("dev");
  const [pc, setPc] = useState("linear");
  const [pe, setPe] = useState("Issue created");
  const [pf, setPf] = useState("label = bug");
  const wfNames = Object.keys(app.workflows);
  const [wf, setWf] = useState("dev");
  const [slug, setSlug] = useState("ORC-140");
  const lw = app.health?.workflows ?? [], lr = app.health?.repos ?? [];
  const [lwf, setLwf] = useState(""), [ticket, setTicket] = useState(""), [repo, setRepo] = useState("");
  const [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const runs = app.runOrder.map((id) => app.runs[id]).filter(Boolean);
  const byPipe = app.pipelines.map((p) => ({ p, rs: runs.filter((r) => r.pipelineId === p.id) }));
  const waiting = runs.filter((r) => r.status === "waiting" || r.status === "needs_you").length;

  return (
    <div className="runs">
      <div className="runs__head">
        <div><h1>Runs</h1><p className="muted">{runs.length} runs in {byPipe.length} pipelines{waiting ? ` · ${waiting} need you` : ""}</p></div>
        <div className="runs__btns">{!app.live && <Button variant="secondary" onClick={() => { setPform(true); setPwf(app.workflows[pwf] ? pwf : Object.keys(app.workflows)[0]); }}>New pipeline</Button>}
        <Button variant="primary" onClick={() => { setErr(""); setForm(true); if (app.live) { setSlug((v) => (v === "ORC-140" ? "" : v)); setLwf((v) => v || lw[0] || ""); setRepo((v) => v || lr[0] || ""); } else setWf(wfNames.includes(wf) ? wf : wfNames[0]); }}>Start run</Button></div>
      </div>
      {byPipe.map(({ p, rs }) => (
        <section className="pipe" key={p.id} aria-label={p.name}>
          <header className="pipe__h">
            <span className="pipe__n">{p.name}</span>
            <span className={`cbadge cbadge--${connKind(p.connector, app.connectors)}`}>{connName(p.connector, app.connectors)}</span>
            <span className="pipe__t">{triggerText(p, app.connectors)}</span>
            <span className="pipe__c">{p.workflow} · {rs.length} run{rs.length === 1 ? "" : "s"}</span>
          </header>
          <div className="pipe__rows">
            {rs.length === 0 && <div className="grp__empty">No runs yet. The next matching event starts one.</div>}
            {rs.map((r) => {
              const total = app.workflows[r.workflow]?.steps.length ?? 1;
              return (
                <div key={r.id} className="runrow" role="button" tabIndex={0} onClick={() => app.openRun(r.id)} onKeyDown={(e) => e.key === "Enter" && app.openRun(r.id)}>
                  <span className="runrow__slug">{r.slug}</span>
                  <span className="runrow__title">{r.title}{r.source && <a className={`srcchip srcchip--${connKind(r.source.connector, app.connectors)}`} href={r.source.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{r.source.label} ↗</a>}</span>
                  <span className="runrow__meta">{Math.min(r.cursor + (r.status === "done" ? 0 : 1), total)}/{total} · {fmtElapsed(r.elapsed)} · {r.startedAgo}</span>
                  <Chip status={r.status} />
                </div>
              );
            })}
          </div>
        </section>
      ))}
      {pform && (
        <div className="overlay" onClick={() => setPform(false)}>
          <form className="panel panel--sm" role="dialog" aria-label="New pipeline" onClick={(e) => e.stopPropagation()} onSubmit={(e) => {
            e.preventDefault();
            const c = app.connectors.find((x) => x.id === pc)!;
            app.setPipelines((l) => [...l, { id: `p-${Date.now()}`, name: pn.trim() || `${pwf} pipeline`, workflow: pwf, connector: pc, event: pe || c.events[0], filter: pf.trim() }]);
            setPform(false); setPn("");
          }}>
            <div className="panel__h"><h2>New pipeline</h2></div>
            <p className="muted">A standing pipeline starts one run of the workflow for every matching event.</p>
            <label className="field"><span className="field__l">Name</span><input value={pn} onChange={(e) => setPn(e.target.value)} placeholder="Bug intake" /></label>
            <label className="field"><span className="field__l">Workflow</span><select value={pwf} onChange={(e) => setPwf(e.target.value)}>{wfNames.map((n) => <option key={n}>{n}</option>)}</select></label>
            <div className="field__l">Trigger: connector + event + filter</div>
            <select aria-label="Connector" value={pc} onChange={(e) => { setPc(e.target.value); setPe(app.connectors.find((x) => x.id === e.target.value)!.events[0]); }}>{app.connectors.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
            <select aria-label="Event" value={pe} onChange={(e) => setPe(e.target.value)}>{app.connectors.find((x) => x.id === pc)?.events.map((ev) => <option key={ev}>{ev}</option>)}</select>
            <input aria-label="Filter" value={pf} onChange={(e) => setPf(e.target.value)} placeholder="label = bug" />
            <div className="panel__a"><Button type="button" variant="ghost" onClick={() => setPform(false)}>Cancel</Button><Button type="submit" variant="primary">Create pipeline</Button></div>
          </form>
        </div>
      )}
      {form && app.live && (
        <div className="overlay" onClick={() => setForm(false)}>
          <form className="panel panel--sm" role="dialog" aria-label="Start run" onClick={(e) => e.stopPropagation()} onSubmit={(e) => {
            e.preventDefault();
            if (!slug.trim() || !ticket.trim() || !lwf || !repo || busy) return;
            setBusy(true); setErr("");
            startLiveRun({ workflow: lwf, slug: slug.trim(), ticket: ticket.trim(), repo }).then(() => { setForm(false); setSlug(""); setTicket(""); app.refreshRuns(); }).catch((x: Error) => setErr(x.message)).finally(() => setBusy(false));
          }}>
            <div className="panel__h"><h2>Start run</h2></div>
            <label className="field"><span className="field__l">Workflow</span>
              <select value={lwf} onChange={(e) => setLwf(e.target.value)}>{lw.map((n) => <option key={n}>{n}</option>)}</select></label>
            <label className="field"><span className="field__l">Slug (letters, digits, -)</span>
              <input value={slug} onChange={(e) => setSlug(e.target.value.replace(/[^A-Za-z0-9-]/g, ""))} placeholder="add-dark-mode" autoFocus /></label>
            <label className="field"><span className="field__l">What to build</span>
              <textarea rows={4} value={ticket} onChange={(e) => setTicket(e.target.value)} placeholder="Describe the change in a few sentences" /></label>
            <label className="field"><span className="field__l">Repo</span>
              <select value={repo} onChange={(e) => setRepo(e.target.value)}>{lr.map((n) => <option key={n}>{n}</option>)}</select></label>
            {err && <p className="ferr" role="alert">{err}</p>}
            <div className="panel__a"><Button type="button" variant="ghost" onClick={() => setForm(false)}>Cancel</Button><Button type="submit" variant="primary" disabled={busy || !slug.trim() || !ticket.trim()}>{busy ? "Starting…" : "Start"}</Button></div>
          </form>
        </div>
      )}
      {form && !app.live && (
        <div className="overlay" onClick={() => setForm(false)}>
          <form className="panel panel--sm" role="dialog" aria-label="Start run" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); if (!slug.trim()) return; app.startRun(wf, slug.trim()); setForm(false); }}>
            <div className="panel__h"><h2>Start run</h2></div>
            <label className="field"><span className="field__l">Workflow</span>
              <select value={wf} onChange={(e) => setWf(e.target.value)}>{wfNames.map((n) => <option key={n}>{n}</option>)}</select></label>
            <label className="field"><span className="field__l">Ticket / slug</span>
              <input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="ORC-140" autoFocus /></label>
            <p className="muted mono">orchestrator next {wf} --input ticket={slug || "…"}</p>
            <div className="panel__a"><Button type="button" variant="ghost" onClick={() => setForm(false)}>Cancel</Button><Button type="submit" variant="primary">Start</Button></div>
          </form>
        </div>
      )}
    </div>
  );
}
