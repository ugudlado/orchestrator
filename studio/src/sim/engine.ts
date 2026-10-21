import type { Source } from "../connectors";
import { nextIndex, routeLabel } from "../paths";
import type { LiveRun } from "../live/types";
import type { Kind, Step, Workflow } from "../types";

export interface Call {
  n: number; step: string; kind: Kind; attempt: number;
  status: "running" | "success" | "error";
  ticks: number; total: number;
  args: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string; durationMs?: number; role?: string | null;
}
export type RunStatus = "queued" | "running" | "waiting" | "needs_you" | "done" | "failed";
export interface Run {
  id: string; workflow: string; slug: string; title: string; pipelineId: string;
  calls: Call[]; cursor: number; status: RunStatus; tokens: string[];
  retries: Record<string, number>; edges: Record<string, number>;
  elapsed: number; startedAgo: string; source?: Source;
  script: { failOnce: string[]; failAlways: string[]; assign: Record<string, string> };
  taken: Record<string, number>;
  /** set in live mode: the backend run this was converted from */
  live?: LiveRun;
}
export const SEC_PER_TICK = 12;
const ticksFor = (s: Step) => (s.kind === "exec" ? 1 : s.kind === "gate" ? 0 : (s.max_turns ?? 20) <= 15 ? 2 : (s.max_turns ?? 20) <= 40 ? 3 : (s.max_turns ?? 20) <= 60 ? 4 : 5);
export const retryCap = (s: Step) => s.max_retries ?? 3;

function genOuts(step: Step, run: Run, failing: boolean): Record<string, unknown> {
  const o: Record<string, unknown> = {};
  for (const [k, t] of Object.entries(step.out ?? {})) {
    if (t === "enum") {
      const vals = step.enums?.[k] ?? [];
      const bad = step.fail_on?.[k] ?? [];
      o[k] = failing ? bad[0] : vals.find((v) => !bad.includes(v)) ?? vals[0];
      if (k === "complexity") o[k] = "M";
      const as = run.script.assign[`${step.id}.${k}`] ?? run.script.assign[k];
      if (as && !failing && vals.includes(as)) o[k] = as;
    } else if (t === "boolean") o[k] = k === "already_completed" || k === "skipped" ? false : true;
    else if (t === "string") o[k] = k.includes("branch") ? `${run.workflow}/${run.slug.toLowerCase()}` : k.includes("path") ? `.worktrees/${run.slug.toLowerCase()}` : "ok";
    else o[k] = `runs/${run.slug}/${t}`;
  }
  return o;
}

function startCall(run: Run, wf: Workflow, after: { step: string; status: string; outs?: Record<string, unknown> } | null) {
  const step = wf.steps[run.cursor];
  const attempt = (run.retries[step.id] ?? 0) + 1;
  const command = after
    ? `orchestrator next ${wf.name} --after ${after.step} --status ${after.status}`
    : `orchestrator next ${wf.name} --input ticket=${run.slug}`;
  const call: Call = {
    n: run.calls.length + 1, step: step.id, kind: step.kind, attempt, status: "running", ticks: 0,
    total: ticksFor(step), role: step.role,
    args: { command, run: run.slug, after: after?.step ?? null, status: after?.status ?? null, ...(after?.outs ? { outs: after.outs } : {}) },
  };
  run.calls.push(call);
  if (step.requires && !run.tokens.includes(step.requires)) {
    call.status = "error"; call.error = `missing token ${step.requires}`;
    call.result = { answer: "error", error: `requires ${step.requires}` };
    run.status = "needs_you";
  } else run.status = step.kind === "gate" ? "waiting" : "running";
}

export function startRun(wf: Workflow, o: { id: string; slug: string; title: string; pipelineId: string; startedAgo?: string; source?: Source; failOnce?: string[]; failAlways?: string[]; assign?: Record<string, string> }): Run {
  const run: Run = {
    id: o.id, workflow: wf.name, slug: o.slug, title: o.title, pipelineId: o.pipelineId, calls: [], cursor: 0, status: "running",
    tokens: [], retries: {}, edges: {}, elapsed: 0, startedAgo: o.startedAgo ?? "just now", source: o.source,
    script: { failOnce: o.failOnce ?? [], failAlways: o.failAlways ?? [], assign: o.assign ?? {} }, taken: {},
  };
  if (wf.steps.length) startCall(run, wf, null); else run.status = "done";
  return run;
}

function finish(run: Run, wf: Workflow) {
  const step = wf.steps[run.cursor];
  const call = run.calls[run.calls.length - 1];
  const hasFail = step.kind === "judgment" && step.fail_on && Object.keys(step.fail_on).length > 0;
  const failing = !!hasFail && (run.script.failAlways.includes(step.id) || (run.script.failOnce.includes(step.id) && !(run.retries[step.id] > 0)));
  const outs = genOuts(step, run, failing);
  call.durationMs = Math.max(call.ticks, 1) * SEC_PER_TICK * 1000;
  if (failing) {
    run.retries[step.id] = (run.retries[step.id] ?? 0) + 1;
    const key = Object.keys(step.fail_on!)[0];
    call.status = "error"; call.error = `${key}=${String(outs[key])} (fail_on)`;
    const target = step.on_failure ? wf.steps.findIndex((s) => s.id === step.on_failure) : -1;
    if (target < 0 || run.retries[step.id] >= retryCap(step)) {
      call.result = { answer: "needs_you", step: step.id, outs, reason: target < 0 ? "no on_failure target" : `max_retries exhausted (${run.retries[step.id]}/${retryCap(step)})` };
      run.status = "needs_you";
      return;
    }
    call.result = { answer: "ready", status: "failed", outs, retries: `${run.retries[step.id]}/${retryCap(step)}`, next: step.on_failure };
    run.edges[`${step.id}->${step.on_failure}`] = (run.edges[`${step.id}->${step.on_failure}`] ?? 0) + 1;
    run.cursor = target;
    startCall(run, wf, { step: step.id, status: "failed", outs });
    return;
  }
  call.status = "success";
  const nx = nextIndex(wf, run.cursor, outs);
  const last = nx.idx >= wf.steps.length;
  call.result = { answer: last ? "done" : "ready", outs, ...(last ? {} : { next: wf.steps[nx.idx].id }), ...(nx.route ? { route: routeLabel(nx.route) } : {}) };
  if (nx.idx < wf.steps.length) run.taken[`${step.id}->${wf.steps[nx.idx].id}`] = (run.taken[`${step.id}->${wf.steps[nx.idx].id}`] ?? 0) + 1;
  run.cursor = nx.idx;
  if (run.cursor >= wf.steps.length) { run.status = "done"; call.result = { answer: "done", outs }; } else startCall(run, wf, { step: step.id, status: "success", outs });
}

export function tick(prev: Run, wf: Workflow): Run {
  if (prev.status !== "running") return prev;
  const run = structuredClone(prev);
  const call = run.calls[run.calls.length - 1];
  call.ticks++; run.elapsed += SEC_PER_TICK;
  if (call.ticks >= call.total) finish(run, wf);
  return run;
}

export function decide(prev: Run, wf: Workflow, answer: "approve" | "changes"): Run {
  if (prev.status !== "waiting") return prev;
  const run = structuredClone(prev);
  const step = wf.steps[run.cursor];
  const call = run.calls[run.calls.length - 1];
  call.durationMs = Math.max(call.ticks, 1) * SEC_PER_TICK * 1000;
  if (answer === "approve") {
    const tok = step.approve_as ?? "token";
    run.tokens.push(tok);
    call.status = "success";
    const nx = nextIndex(wf, run.cursor, {});
    call.result = { answer: nx.idx >= wf.steps.length ? "done" : "ready", outs: { [tok]: "granted", approved_by: "you" }, ...(nx.idx < wf.steps.length ? { next: wf.steps[nx.idx].id } : {}) };
    if (nx.idx < wf.steps.length) run.taken[`${step.id}->${wf.steps[nx.idx].id}`] = (run.taken[`${step.id}->${wf.steps[nx.idx].id}`] ?? 0) + 1;
    run.cursor = nx.idx;
    if (run.cursor >= wf.steps.length) run.status = "done"; else startCall(run, wf, { step: step.id, status: "success", outs: { [tok]: "granted" } });
  } else {
    call.status = "error"; call.error = "changes requested";
    let t = -1;
    for (let i = run.cursor - 1; i >= 0; i--) if (step.show && wf.steps[i].out && step.show[0] in (wf.steps[i].out ?? {})) { t = i; break; }
    if (t < 0) t = Math.max(0, run.cursor - 1);
    call.result = { answer: "ready", status: "changes_requested", next: wf.steps[t].id };
    run.cursor = t;
    startCall(run, wf, { step: step.id, status: "changes_requested" });
  }
  return run;
}

/** needs_you: reset the retry budget and route the run back to the on_failure target */
export function resume(prev: Run, wf: Workflow): Run {
  if (prev.status !== "needs_you") return prev;
  const run = structuredClone(prev);
  const last = run.calls[run.calls.length - 1];
  const stepId = last.step;
  const step = wf.steps.find((s) => s.id === stepId)!;
  run.retries[stepId] = 0;
  run.script.failAlways = run.script.failAlways.filter((x) => x !== stepId);
  const target = step.on_failure ? wf.steps.findIndex((s) => s.id === step.on_failure) : wf.steps.findIndex((s) => s.id === stepId);
  run.cursor = Math.max(0, target);
  startCall(run, wf, { step: stepId, status: "resumed" });
  return run;
}

export function fastForward(run: Run, wf: Workflow, until: (r: Run) => boolean, autoApprove = true): Run {
  let r = run;
  for (let i = 0; i < 4000 && !until(r); i++) {
    if (r.status === "waiting") { if (!autoApprove) break; r = decide(r, wf, "approve"); }
    else if (r.status === "running") r = tick(r, wf);
    else break;
  }
  return r;
}

export function fmtElapsed(sec: number) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
