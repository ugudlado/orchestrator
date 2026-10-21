import { useEffect, useMemo, useRef, useState } from "react";
import type { AppApi } from "../app";
import { Button } from "../affectui/button-clean";
import { DecisionInbox } from "../affectui/decision-inbox-clean";
import { NotificationStack } from "../affectui/notification-stack-clean";
import { SegmentedControl } from "../affectui/segmented-control-clean";
import { ThinkingLoader } from "../affectui/thinking-loader-clean";
import { ToolCallCard, ToolCallGroup } from "../affectui/tool-call-card-clean";
import { Legend, WorkflowGraph } from "../graph/WorkflowGraph";
import { hasRoutes, walkPath } from "../paths";
import { TIERS, roleById } from "../roles";
import { decide, fmtElapsed, resume, retryCap, type Call, type Run } from "../sim/engine";
import type { Step, StepState } from "../types";
import { Chip } from "./RunsView";
import { FilesPanel } from "./FilesPanel";
import { filesFor } from "../files";
import { accountLabel } from "../accounts";
import { FeedbackTray, LiveGate, LiveQuestion } from "./LiveActions";
import { DocOverlay } from "./DocReview";
import { useLiveComments } from "../live/useComments";

function phasesFor(s: Step): string[] {
  const p: string[] = [];
  const first = Object.values(s.in ?? {})[0];
  p.push(first ? `Reading ${first}` : "Reading ticket context");
  if (s.tools?.includes("shell.test")) p.push("Running tests");
  else if (s.tools?.includes("shell.run")) p.push("Running commands");
  if (s.tools?.includes("git.diff")) p.push("Inspecting the diff");
  const o = Object.entries(s.out ?? {}).find(([, t]) => t.includes("."));
  p.push(o ? `Writing ${o[1]}` : "Recording outputs");
  if (Object.values(s.out ?? {}).includes("enum")) p.push("Settling on a verdict");
  return p;
}

export function RunDetail({ app, run }: { app: AppApi; run: Run }) {
  const wf = app.workflows[run.workflow];
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const last = run.calls[run.calls.length - 1];
  const lv = run.live;
  const attn = lv?.attention ?? null;
  const [tab, setTab] = useState<"timeline" | "files">("timeline");
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [overlay, setOverlay] = useState<string | null>(null);
  const [sentKey, setSentKey] = useState<string | null>(null);
  const cm = useLiveComments(run.slug, !!lv);
  const [fb, setFb] = useState<number | null>(null);
  const [fbText, setFbText] = useState("");

  const states = useMemo(() => {
    const m: Record<string, StepState> = {};
    wf.steps.forEach((s) => (m[s.id] = "pending"));
    for (const c of run.calls) m[c.step] = c.status === "success" ? "done" : c.status === "error" ? "failed" : run.status === "waiting" ? "waiting" : "running";
    return m;
  }, [run.calls, run.status, wf]);

  useEffect(() => {
    const el = scroller.current;
    if (!el || tab !== "timeline") return;
    const go = () => { if (stick.current) el.scrollTop = el.scrollHeight; };
    go();
    const ts = [250, 600, 1000].map((d) => setTimeout(go, d));
    return () => ts.forEach(clearTimeout);
  }, [run.calls.length, run.status, last?.ticks, tab]);

  const groups = useMemo(() => {
    const g: Call[][] = [[]];
    for (const c of run.calls) { g[g.length - 1].push(c); if (c.kind === "gate" && c.status !== "running") g.push([]); }
    return g.filter((x) => x.length);
  }, [run.calls]);

  const rich = hasRoutes(wf);
  const activeIds = useMemo(() => {
    if (!rich) return null;
    const a = new Set(walkPath(wf, run.script.assign).ids);
    run.calls.forEach((c) => a.add(c.step));
    return a;
  }, [rich, wf, run.script.assign, run.calls]);
  const activeEdges = useMemo(() => {
    if (!rich) return null;
    const e = new Set(walkPath(wf, run.script.assign).edges);
    Object.keys(run.taken).forEach((k) => e.add(k));
    return e;
  }, [rich, wf, run.script.assign, run.taken]);
  const step = wf.steps[Math.min(run.cursor, wf.steps.length - 1)];
  const pct = run.status === "done" ? 100 : Math.round((run.cursor / wf.steps.length) * 100);
  const failures = Object.values(run.edges).reduce((a, b) => a + b, 0);
  const outFile = (k: string) => wf.steps.flatMap((x) => Object.entries(x.out ?? {})).find(([a]) => a === k)?.[1];
  const gateStep = run.status === "waiting" ? wf.steps[run.cursor] : null;

  const card = (c: Call) => {
    if (lv) {
      const sm = wf.steps.find((x) => x.id === c.step)?.summary;
      const ps = typeof c.result?.summary === "string" ? c.result.summary : "";
      return <ToolCallCard key={c.n} name={c.step} status={c.status} args={c.args} result={c.result} error={c.error} duration={c.durationMs}
        summary={`${sm || c.kind}${ps && ps !== sm ? ` · ${ps}` : ""}${c.attempt > 1 ? ` · attempt ${c.attempt}` : ""}${c.kind === "gate" && c.status === "running" ? " · waiting for approval" : ""}`} />;
    }
    const r = roleById(c.role);
    const accId = r ? app.routing[app.tiers[r.id]] : null;
    const acc = accId ? app.accounts.find((x) => x.id === accId) : null;
    const accName = acc ? accountLabel(acc, app.runner) : "";
    return (
      <ToolCallCard key={c.n} name={c.step} status={c.status} args={c.args} result={c.result} error={c.error} duration={c.durationMs}
        summary={`${c.kind}${r ? ` · ${r.name}${accName ? ` · ${accName}` : ""}` : ""}${c.attempt > 1 ? ` · attempt ${c.attempt}` : ""}${c.kind === "gate" && c.status === "running" ? " · waiting for approval" : ""}${c.result?.answer ? ` → ${c.result.answer}` : ""}`} />
    );
  };

  return (
    <div className="rd">
      <div className="rd__head">
        <div className="rd__top">
          <Button size="sm" variant="ghost" onClick={() => app.openRun(null)}>← Runs</Button>
          <span className="rd__wf">{run.workflow}</span>
          <span className="rd__slug">{run.slug}</span>
          <span className="rd__title">{run.title}</span>
          <Chip status={run.status} />
        </div>
        <div className="rd__ctl">
          <span className="rd__elapsed" aria-label="Elapsed">{fmtElapsed(run.elapsed)} elapsed</span>
          {lv && lv.cost_usd > 0 && <span className="rd__cost" aria-label="Total cost">${lv.cost_usd.toFixed(2)}</span>}
          {lv && lv.outcome && lv.outcome !== "done" && <span className="rd__outcome">outcome: {lv.outcome}</span>}
          <span className="rd__sp" />
          {lv ? lv.inngest.trace_url && <a className="rd__trace" href={lv.inngest.trace_url} target="_blank" rel="noreferrer">View trace in Inngest ↗</a> : <>
          <Button size="sm" variant="ghost" onClick={() => app.openSlack(run.id)}>Open in Slack</Button>
          <Button size="sm" variant="secondary" onClick={() => app.setPlaying(!app.playing)} aria-label={app.playing ? "Pause simulation" : "Play simulation"}>{app.playing ? "Pause" : "Play"}</Button>
          <SegmentedControl size="sm" variant="tray" aria-label="Speed" value={String(app.speed)} onChange={(v) => app.setSpeed(+v)} options={[{ value: "1", label: "1×" }, { value: "2", label: "2×" }, { value: "4", label: "4×" }]} /></>}
        </div>
        <div className="prog" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Run progress">
          <div className="prog__bar"><i style={{ transform: `scaleX(${pct / 100})` }} /></div>
          <div className="prog__l"><span>{run.status === "done" ? "Complete" : `Step ${run.cursor + 1} of ${wf.steps.length} · ${step.id}`}</span><span>{pct}%{failures ? ` · ${failures} loop${failures > 1 ? "s" : ""}` : ""}</span></div>
        </div>
      </div>

      <div className={`rd__body ${rich ? "rd__body--rich" : ""}`}>
        <div className="rd__graph" aria-label="Run graph">
          {rich && <div className="rd__legend"><Legend compact /><span className="rd__path">{Object.entries(run.script.assign).filter(([k]) => !k.includes(".")).map(([k, v]) => `${k} = ${v}`).join(" · ")}</span></div>}
          <WorkflowGraph wf={wf} readOnly states={states} edgeHits={run.edges} followId={step.id} dark={app.dark} activeIds={activeIds} activeEdges={activeEdges} taken={run.taken} offLabel />
        </div>
        <div className="rd__right">
        <div className="rd__tabs"><SegmentedControl size="sm" variant="underline" aria-label="Timeline or files" value={tab} onChange={(v) => { setTab(v); if (v === "timeline") stick.current = true; }}
          options={[{ value: "timeline", label: "Timeline" }, { value: "files", label: "Files", count: filesFor(run, wf).length }]} /></div>
        <div className="rd__tl" ref={scroller} onWheel={(e) => { if (e.deltaY < 0) stick.current = false; }} onTouchMove={() => { stick.current = false; }} onScroll={(e) => { const el = e.currentTarget; if (el.scrollHeight - el.scrollTop - el.clientHeight < 40) stick.current = true; }}>
          {tab === "files" && <FilesPanel run={run} wf={wf} openFile={openFile} setOpenFile={setOpenFile} comments={app.comments} addComment={(c) => app.addComment({ ...c, runId: run.id })} cm={cm} expand={setOverlay} />}
          {tab === "timeline" && <div className="tl__h">Timeline · <code>orchestrator next</code> calls</div>}
          {tab === "timeline" && groups.map((g, gi) => {
            const lab = `${g[0].step} → ${g[g.length - 1].step} · ${g.length} step${g.length > 1 ? "s" : ""}`;
            return (
              <ToolCallGroup key={gi} label={lab} runningLabel={`Running ${g[g.length - 1].step}…`} defaultOpen={gi === groups.length - 1}>
                {g.map(card)}
              </ToolCallGroup>
            );
          })}
          {tab === "timeline" && lv && run.status === "running" && lv.current && (
            <div className="tl__think"><ThinkingLoader key={last?.n} phases={[`Running ${wf.steps.find((x) => x.id === lv.current!.step)?.summary || lv.current.step}…`]} /></div>
          )}
          {tab === "timeline" && !lv && run.status === "running" && last?.kind === "judgment" && (
            <div className="tl__think">
              <ThinkingLoader key={last?.n} phases={phasesFor(step)} phase={Math.min(Math.floor((last.ticks / last.total) * phasesFor(step).length), phasesFor(step).length - 1)} />
              <span className="muted">{roleById(step.role)?.name ?? "agent"} · {roleById(step.role) ? TIERS[roleById(step.role)!.tier] : ""} · max_turns {step.max_turns}</span>
            </div>
          )}
          {tab === "timeline" && lv && run.status === "queued" && <div className="tl__done" role="status">Queued. The event was accepted; no driver has picked this run up yet.</div>}
          {tab === "timeline" && lv && <FeedbackTray run={run} cm={cm} openFile={setOverlay} />}
          {tab === "timeline" && lv && run.status === "waiting" && attn?.kind === "gate" && <LiveGate key={`${attn.step_id}#${attn.attempt}`} app={app} run={run} wf={wf} cm={cm} openFile={setOverlay} sent={sentKey} setSent={setSentKey} />}
          {tab === "timeline" && lv && run.status === "needs_you" && attn?.kind === "question" && <LiveQuestion key={`${attn.step_id}?${attn.wait}`} app={app} run={run} wf={wf} cm={cm} openFile={setOverlay} sent={sentKey} setSent={setSentKey} />}
          {tab === "timeline" && lv && (run.status === "needs_you" || run.status === "failed") && !attn && (
            <div className="tl__done" role="status">{run.status === "failed" ? "Run failed" : "Run needs you"} · outcome: {lv.outcome ?? "unknown"}{lv.inngest.trace_url && <> · <a href={lv.inngest.trace_url} target="_blank" rel="noreferrer">View trace in Inngest ↗</a></>}</div>
          )}
          {tab === "timeline" && !lv && gateStep && (
            <>
              <div className="gatefiles"><span>Files to review</span>
                {filesFor(run, wf).filter((f) => (gateStep.show ?? []).some((k) => outFile(k) === f.name))
                  .map((f) => (
                  <button key={f.name} className="tchip" onClick={() => { setTab("files"); setOpenFile(f.name); }}>{f.name}<em>v{f.version}</em></button>
                ))}
              </div>
              <DecisionInbox key={last?.n} decisions={[{
                id: `g${last.n}`, title: `Approve ${gateStep.id}?`,
                detail: `Open the files above to review and comment. Approving mints ${gateStep.approve_as}.`,
                primary: "Approve", secondary: "Request changes",
              }]} onDecide={(_, a) => {
                if (a === "primary") setTimeout(() => app.setRuns((rs) => ({ ...rs, [run.id]: decide(rs[run.id], wf, "approve") })), 1250);
                else setTimeout(() => setFb(last.n), 1200);
              }} />
              {fb === last.n && (
                <div className="fbbox">
                  <label className="field__l" htmlFor="fb">What should change? This is attached as a comment for the agent.</label>
                  <textarea id="fb" autoFocus rows={3} value={fbText} onChange={(e) => setFbText(e.target.value)} placeholder="e.g. Collapse retry loops into one entry" />
                  <div className="cmt__a"><Button size="sm" variant="primary" onClick={() => {
                    const ofile = filesFor(run, wf).find((f) => f.name === outFile(gateStep.show?.[0] ?? ""));
                    if (fbText.trim()) app.addComment({ runId: run.id, file: ofile?.name ?? "design.md", line: 0, text: fbText.trim(), owner: ofile?.owner ?? "design" });
                    setFb(null); setFbText("");
                    app.setRuns((rs) => ({ ...rs, [run.id]: decide(rs[run.id], wf, "changes") }));
                  }}>Send and request changes</Button></div>
                </div>
              )}
            </>
          )}
          {tab === "timeline" && !lv && run.status === "needs_you" && (
            <NotificationStack key={last?.n} notices={[{
              id: `n${last.n}`, badge: "needs_you", pattern: "rays", title: `${last.step} needs you`,
              text: `Retries exhausted (${run.retries[last.step] ?? 0}/${retryCap(wf.steps.find((s) => s.id === last.step)!)}). Last verdict: ${String(last.error ?? "failed")}. The driver stopped and returned answer: needs_you.`,
              action: { label: "Reset retries and continue", onClick: () => app.setRuns((rs) => ({ ...rs, [run.id]: resume(rs[run.id], wf) })) },
            }]} />
          )}
          {tab === "timeline" && run.status === "done" && <div className="tl__done">Run complete · {lv ? `outcome: ${lv.outcome ?? "done"}` : "answer: done"} · {run.calls.length} calls · {fmtElapsed(run.elapsed)}</div>}
        </div>
        </div>
      </div>
      {lv && overlay && <DocOverlay app={app} run={run} wf={wf} cm={cm} file={overlay} setFile={setOverlay} onClose={() => setOverlay(null)} sent={sentKey} setSent={setSentKey} />}
    </div>
  );
}
