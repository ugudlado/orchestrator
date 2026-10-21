import { useState } from "react";
import type { AppApi } from "../app";
import { Button } from "../affectui/button-clean";
import { fmtElapsed, type Run } from "../sim/engine";
import { startLiveRun } from "../live/api";

export const STATUS_LABEL: Record<Run["status"], string> = { queued: "queued, waiting for the driver", running: "running", waiting: "waiting on you", needs_you: "needs you", done: "done", failed: "failed" };
export const Chip = ({ status }: { status: Run["status"] }) => <span className={`schip schip--${status}`}>{(status === "running" || status === "queued") && <i />}{STATUS_LABEL[status]}</span>;

export function RunsView({ app }: { app: AppApi }) {
  const [form, setForm] = useState(false);
  const wfNames = Object.keys(app.workflows);
  const [wf, setWf] = useState("dev");
  const [slug, setSlug] = useState("ORC-140");
  const lw = app.health?.workflows ?? [], projects = app.health?.projects ?? (app.health?.repos ?? []).map((path) => ({ path, name: path.split("/").filter(Boolean).pop() ?? path }));
  const nameOf = (path: string | null | undefined) => (path ? projects.find((x) => x.path === path)?.name ?? path.split("/").filter(Boolean).pop() ?? path : "");
  const [lwf, setLwf] = useState(""), [ticket, setTicket] = useState(""), [repo, setRepo] = useState("");
  const [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const filter = app.live && projects.some((x) => x.path === app.projectFilter) ? app.projectFilter : null;
  const runs = app.runOrder.map((id) => app.runs[id]).filter((r) => r && (!filter || r.live?.repo === filter));
  const byPipe = app.pipelines.map((p) => ({ p, rs: runs.filter((r) => r.pipelineId === p.id) }));
  const waiting = runs.filter((r) => r.status === "waiting" || r.status === "needs_you").length;

  return (
    <div className="runs">
      <div className="runs__head">
        <div><h1>Runs</h1><p className="muted">{runs.length} runs{app.live ? "" : ` in ${byPipe.length} pipelines`}{waiting ? ` · ${waiting} need you` : ""}</p></div>
        <div className="runs__btns">{app.live && projects.length > 1 && <select aria-label="Filter by project" value={filter ?? ""} onChange={(e) => app.setProjectFilter(e.target.value || null)}><option value="">All projects</option>{projects.map((x) => <option key={x.path} value={x.path}>{x.name}</option>)}</select>}
        <Button variant="primary" onClick={() => { setErr(""); setForm(true); if (app.live) { setSlug((v) => (v === "ORC-140" ? "" : v)); setLwf((v) => v || lw[0] || ""); setRepo((v) => (projects.some((x) => x.path === v) ? v : filter ?? projects[0]?.path ?? "")); } else setWf(wfNames.includes(wf) ? wf : wfNames[0]); }}>Start run</Button></div>
      </div>
      {byPipe.map(({ p, rs }) => (
        <section className="pipe" key={p.id} aria-label={p.name}>
          <header className="pipe__h">
            <span className="pipe__n">{p.name}</span>
            <span className="pipe__c">{p.workflow} · {rs.length} run{rs.length === 1 ? "" : "s"}</span>
          </header>
          <div className="pipe__rows">
            {rs.length === 0 && <div className="grp__empty">No runs yet. Use Start run to begin one.</div>}
            {rs.map((r) => {
              const total = app.workflows[r.workflow]?.steps.length ?? 1;
              return (
                <div key={r.id} className="runrow" role="button" tabIndex={0} onClick={() => app.openRun(r.id)} onKeyDown={(e) => e.key === "Enter" && app.openRun(r.id)}>
                  <span className="runrow__slug">{r.slug}</span>
                  <span className="runrow__title">{r.title}{r.live?.repo && <span className="projchip" title={r.live.repo}>{nameOf(r.live.repo)}</span>}</span>
                  <span className="runrow__meta">{Math.min(r.cursor + (r.status === "done" ? 0 : 1), total)}/{total} · {fmtElapsed(r.elapsed)} · {r.startedAgo}</span>
                  <Chip status={r.status} />
                </div>
              );
            })}
          </div>
        </section>
      ))}
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
            <label className="field"><span className="field__l">Project</span>
              <select value={repo} onChange={(e) => setRepo(e.target.value)}>{projects.map((x) => <option key={x.path} value={x.path}>{x.name}</option>)}</select></label>
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
