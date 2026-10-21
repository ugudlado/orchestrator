import type { Call, Run } from "../sim/engine";
import type { Pipeline, Workflow } from "../types";
import type { LiveHistoryEntry, LiveRun } from "./types";

export const LIVE_PIPELINE: Pipeline = { id: "p-live", name: "Runs on the box", workflow: "dev" };

const ms = (t: string | null) => (t ? Date.parse(t) : NaN);
export function ago(t: string | null, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - ms(t)) / 1000));
  if (!isFinite(s)) return "";
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} d ago`;
}
const strip = (o: Record<string, unknown>) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "" && !(typeof v === "object" && !Object.keys(v as object).length)));

export function convertRun(lr: LiveRun, wf: Workflow | undefined): Run {
  const steps = wf?.steps ?? [];
  const stepOf = (id: string) => steps.find((s) => s.id === id);
  const calls: Call[] = lr.history.map((h: LiveHistoryEntry, i) => {
    const s = stepOf(h.step);
    const ok = h.status === "completed";
    const rejected = !ok && h.gate === "rejected";
    return {
      n: i + 1, step: h.step, kind: h.kind ?? s?.kind ?? "exec", attempt: h.attempt, status: ok ? "success" : "error", ticks: 1, total: 1,
      args: strip({ attempt: h.attempt, route: h.route, model: h.model }),
      result: strip({ outs: strip(h.outs ?? {}), summary: h.summary, cost_usd: h.cost_usd, ...(rejected ? { note: h.note } : {}) }),
      error: ok ? undefined : rejected ? "changes requested" : h.note || (h.exit_code !== undefined ? `exit ${h.exit_code}` : "failed"),
      role: s?.role,
    };
  });
  const pend = lr.attention ? { step: lr.attention.step_id, kind: lr.attention.kind === "gate" ? "gate" as const : stepOf(lr.attention.step_id)?.kind ?? "judgment", attempt: lr.attention.attempt ?? 1 }
    : lr.current ? { step: lr.current.step, kind: lr.current.kind, attempt: 1 + lr.history.filter((h) => h.step === lr.current!.step).length } : null;
  if (pend) calls.push({ n: calls.length + 1, step: pend.step, kind: pend.kind, attempt: pend.attempt, status: "running", ticks: 0, total: 1, args: { attempt: pend.attempt }, role: stepOf(pend.step)?.role });
  const lastStep = calls[calls.length - 1]?.step;
  const cursor = lr.status === "done" ? steps.length : Math.max(0, steps.findIndex((s) => s.id === lastStep));
  const edges: Record<string, number> = {};
  for (const h of lr.history) { const f = stepOf(h.step)?.on_failure; if (h.status === "failed" && f) edges[`${h.step}->${f}`] = (edges[`${h.step}->${f}`] ?? 0) + 1; }
  const taken: Record<string, number> = {};
  calls.forEach((c, i) => { if (i) { const k = `${calls[i - 1].step}->${c.step}`; taken[k] = (taken[k] ?? 0) + 1; } });
  const assign: Record<string, string> = {};
  for (const h of lr.history) for (const [k, v] of Object.entries(h.outs ?? {})) if (typeof v === "string" && stepOf(h.step)?.enums?.[k]?.includes(v)) { assign[k] = v; assign[`${h.step}.${k}`] = v; }
  const start = ms(lr.started_at), end = lr.ended_at ? ms(lr.ended_at) : Date.now();
  return {
    id: `live:${lr.slug}`, workflow: lr.workflow, slug: lr.slug, title: lr.title, pipelineId: LIVE_PIPELINE.id, calls, cursor, status: lr.status, tokens: lr.tokens,
    retries: {}, edges, elapsed: isFinite(start) ? Math.max(0, Math.round((end - start) / 1000)) : 0, startedAgo: ago(lr.started_at),
    script: { failOnce: [], failAlways: [], assign }, taken, live: lr,
  };
}
