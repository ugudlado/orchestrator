import type { Route, Step, Workflow } from "./types";

export type PathId = "all" | "feature" | "fix" | "hotfix" | "quick";
export const PATHS: Record<Exclude<PathId, "all">, { label: string; assign: Record<string, string> }> = {
  feature: { label: "Feature", assign: { change_type: "feature" } },
  fix: { label: "Fix", assign: { change_type: "fix", fix_scope: "standard" } },
  hotfix: { label: "Hotfix", assign: { change_type: "fix", fix_scope: "hotfix" } },
  quick: { label: "Quick", assign: { change_type: "quick" } },
};
export const hasRoutes = (wf: Workflow) => wf.steps.some((s) => s.routes?.length);

export function whenMatch(when: Route["when"], outs: Record<string, unknown>): boolean {
  if (!when) return true;
  return Object.entries(when).every(([k, v]) => (Array.isArray(v) ? v.includes(String(outs[k])) : String(outs[k]) === v));
}
export function routeLabel(r: Route): string {
  if (!r.when || !Object.keys(r.when).length) return "default";
  return Object.entries(r.when).map(([k, v]) => `${k} = ${Array.isArray(v) ? v.join(" | ") : v}`).join(", ");
}
/** Index of the step after i: first matching route wins, no match falls through */
export function nextIndex(wf: Workflow, i: number, outs: Record<string, unknown>): { idx: number; route?: Route } {
  const s = wf.steps[i];
  for (const r of s.routes ?? []) {
    if (whenMatch(r.when, outs)) {
      const t = wf.steps.findIndex((x) => x.id === r.goto);
      if (t >= 0) return { idx: t, route: r };
    }
  }
  return { idx: i + 1 };
}
export const endsInDefault = (s: Step) => !!s.routes?.length && !s.routes[s.routes.length - 1].when;

export interface GEdge { id: string; source: string; target: string; kind: "next" | "route"; label?: string }
/** Forward edges: one per route case, plus a fall-through edge only where reachable */
export function forwardEdges(wf: Workflow): GEdge[] {
  const out: GEdge[] = [];
  wf.steps.forEach((s, i) => {
    (s.routes ?? []).forEach((r, k) => {
      if (wf.steps.some((x) => x.id === r.goto)) out.push({ id: `r-${s.id}-${k}`, source: s.id, target: r.goto, kind: "route", label: routeLabel(r) });
    });
    if (i < wf.steps.length - 1 && !endsInDefault(s)) out.push({ id: `n-${s.id}`, source: s.id, target: wf.steps[i + 1].id, kind: "next" });
  });
  return out;
}

export interface PathInfo { ids: string[]; edges: Set<string>; steps: number; gates: number }
export function walkPath(wf: Workflow, assign: Record<string, string>): PathInfo {
  const ids: string[] = []; const edges = new Set<string>();
  let i = 0;
  for (let n = 0; i < wf.steps.length && n < wf.steps.length + 2; n++) {
    const s = wf.steps[i];
    if (ids.includes(s.id)) break;
    ids.push(s.id);
    const { idx } = nextIndex(wf, i, assign);
    if (idx < wf.steps.length) edges.add(`${s.id}->${wf.steps[idx].id}`);
    i = idx;
  }
  return { ids, edges, steps: ids.length, gates: ids.filter((id) => wf.steps.find((s) => s.id === id)?.kind === "gate").length };
}

export interface Layout { pos: Record<string, { x: number; y: number }>; col: Record<string, number>; nodeW: number; nodeH: number; pitch: number; colGap: number; minCol: number; maxCol: number; rich: boolean; height: number }
export function layout(wf: Workflow): Layout {
  const rich = hasRoutes(wf);
  const nodeW = rich ? 240 : 330, nodeH = rich ? 90 : 64, pitch = rich ? 132 : 92, colGap = nodeW + (rich ? 110 : 56);
  const idx = new Map(wf.steps.map((s, i) => [s.id, i]));
  const rank = wf.steps.map((_, i) => (rich ? 0 : i));
  const col: Record<string, number> = {};
  if (rich) {
    const fe = forwardEdges(wf);
    const succ = wf.steps.map(() => [] as number[]);
    for (const e of fe) { const a = idx.get(e.source)!, b = idx.get(e.target)!; if (b > a) succ[a].push(b); }
    wf.steps.forEach((_, i) => succ[i].forEach((b) => (rank[b] = Math.max(rank[b], rank[i] + 1))));
    const sets = Object.values(PATHS).map((p) => new Set(walkPath(wf, p.assign).ids));
    wf.steps.forEach((s) => {
      const [f, x, h, q] = sets.map((st) => st.has(s.id));
      // quick-only steps (quick-signoff) get their own column right of fix/hotfix
      col[s.id] = f && !x && !h && !q ? -1 : !f && !q && (x || h) ? 1 : !f && !x && !h && q ? 2 : 0;
    });
    // pull side-column steps with a single successor down next to it (hotfix-signoff, quick-signoff beside design-signoff)
    for (let i = wf.steps.length - 1; i >= 0; i--) {
      const c = col[wf.steps[i].id];
      if (c < 1 || succ[i].length !== 1) continue;
      const t = rank[succ[i][0]] - 1;
      if (t > rank[i] && !wf.steps.some((o, j) => j !== i && col[o.id] === c && rank[j] === t)) rank[i] = t;
    }
  } else wf.steps.forEach((s) => (col[s.id] = 0));
  const pos: Layout["pos"] = {};
  wf.steps.forEach((s, i) => (pos[s.id] = { x: col[s.id] * colGap, y: rank[i] * pitch }));
  const cs = Object.values(col);
  return { pos, col, nodeW, nodeH, pitch, colGap, minCol: Math.min(0, ...cs), maxCol: Math.max(0, ...cs), rich, height: (Math.max(...rank) + 1) * pitch };
}
