import { useEffect, useMemo } from "react";
import {
  BaseEdge, EdgeLabelRenderer, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useReactFlow, useStore,
  type Connection, type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Step, StepState, Workflow } from "../types";
import { roleById } from "../roles";
import { forwardEdges, layout, type Layout } from "../paths";

interface NodeData extends Record<string, unknown> { step: Step; state?: StepState; readOnly: boolean; rich: boolean; dim: boolean; off: boolean }
type SNode = Node<NodeData, "step">;

const Icon = ({ kind }: { kind: Step["kind"] }) => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    {kind === "exec" && <><path d="m3 4 3.5 4L3 12" /><path d="M8 12h5" /></>}
    {kind === "judgment" && <><path d="M8 2.2 9.5 6l3.8 1.5L9.5 9 8 12.8 6.5 9 2.7 7.5 6.5 6Z" /></>}
    {kind === "gate" && <><circle cx="8" cy="5.2" r="2.4" /><path d="M3.2 13c.4-2.6 2.3-4 4.8-4s4.4 1.4 4.8 4" /></>}
  </svg>
);
const Branch = () => (
  <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden><circle cx="4" cy="3.5" r="1.7" /><circle cx="4" cy="12.5" r="1.7" /><circle cx="12" cy="6" r="1.7" /><path d="M4 5.2v5.6M12 7.7c0 2.4-3 2.3-6.2 3.9" /></svg>
);
const Lock = () => (
  <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden><rect x="3" y="7" width="10" height="7" rx="1.8" /><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" /></svg>
);
const Key = () => (
  <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden><circle cx="5.5" cy="10.5" r="2.8" /><path d="m7.6 8.4 5.4-5.4M11 5l1.8 1.8" /></svg>
);
export function Avatar({ role, size = 18 }: { role?: string | null; size?: number }) {
  const r = roleById(role);
  if (!r) return null;
  return <span className="avatar" title={r.name} style={{ ["--c" as string]: r.color, width: size, height: size, fontSize: size * 0.48 }}>{r.initials}</span>;
}

export function RoleChip({ role }: { role?: string | null }) {
  const r = roleById(role);
  if (!r) return s_unassigned;
  return <span className="rolechip" style={{ ["--c" as string]: r.color }}><i />{r.name}</span>;
}
const s_unassigned = <span className="rolechip rolechip--none"><i />No role</span>;

function StepNode({ data, selected }: NodeProps<SNode>) {
  const { step: s, state, readOnly, rich, dim, off } = data;
  const meta =
    s.kind === "gate" ? `show ${(s.show ?? []).join(", ") || "nothing"}`
    : s.kind === "judgment" ? `${s.max_turns ?? "?"} turns · ${s.tools?.length ?? 0} tools`
    : `${s.tools?.length ?? 0} tools`;
  const nr = s.routes?.length ?? 0;
  const chips = (
    <>
      {nr > 0 && <span className="chip chip--route" title={s.routes!.map((r) => (r.when ? `${Object.entries(r.when).map(([k, v]) => `${k} = ${v}`).join(", ")} -> ${r.goto}` : `default -> ${r.goto}`)).join("\n")}><Branch />{nr} route{nr > 1 ? "s" : ""}</span>}
      {s.skip_when_unchanged && <span className="chip chip--skip" title="skip_when_unchanged: the step self-reports skipped when its inputs have not changed">skippable</span>}
      {s.requires && <span className="chip chip--lock"><Lock />{s.requires}</span>}
      {s.kind === "gate" && s.approve_as && <span className="chip chip--key"><Key />{s.approve_as}</span>}
      {s.max_retries != null && <span className="chip">↻ {s.max_retries}</span>}
    </>
  );
  return (
    <div className={`sn sn--${s.kind} ${rich ? "sn--rich" : ""} ${dim ? (off ? "sn--off" : "sn--dim") : ""}`} data-state={state} data-selected={selected || undefined}>
      <Handle type="target" position={Position.Top} id="t" isConnectable={!readOnly} />
      <Handle type="target" position={Position.Right} id="ft" style={{ top: "72%" }} isConnectable={!readOnly} />
      <Handle type="target" position={Position.Left} id="ftl" style={{ top: "72%" }} isConnectable={false} />
      <Handle type="source" position={Position.Right} id="fs" style={{ top: "28%" }} isConnectable={!readOnly} />
      <Handle type="source" position={Position.Left} id="fl" style={{ top: "28%" }} isConnectable={false} />
      <Handle type="source" position={Position.Bottom} id="s" isConnectable={!readOnly} />
      <div className="sn__row">
        <span className="sn__icon"><Icon kind={s.kind} /></span>
        <span className="sn__id">{s.id}</span>
        <span className="sn__badge">{s.kind}</span>
        {state === "done" && <span className="sn__state sn__state--done" aria-label="done">✓</span>}
        {state === "failed" && <span className="sn__state sn__state--failed" aria-label="failed">!</span>}
        {(state === "running" || state === "waiting") && <span className="sn__dot" aria-label={state} />}
      </div>
      <div className="sn__meta">
        {s.kind === "judgment" && <RoleChip role={s.role} />}
        <span className="sn__metatxt">{meta}</span>
        {!rich && chips}
      </div>
      {rich && (off ? <div className="sn__chips sn__chips--off">not on this path</div> : <div className="sn__chips">{chips}</div>)}
    </div>
  );
}

interface FwdData extends Record<string, unknown> { kind: "next" | "route"; label?: string; dim: boolean; taken: number }
function FwdEdge({ sourceX: sx, sourceY: sy, targetX: tx, targetY: ty, markerEnd, data, selected }: EdgeProps<Edge<FwdData>>) {
  const R = 8, dir = tx > sx ? 1 : -1;
  let d: string, lx: number, ly: number;
  if (Math.abs(sx - tx) < 1) { d = `M ${sx} ${sy} L ${tx} ${ty}`; lx = sx; ly = (sy + ty) / 2; }
  else {
    const y1 = ty - sy > 60 ? ty - 24 : (sy + ty) / 2;
    d = `M ${sx} ${sy} L ${sx} ${y1 - R} Q ${sx} ${y1} ${sx + dir * R} ${y1} L ${tx - dir * R} ${y1} Q ${tx} ${y1} ${tx} ${y1 + R} L ${tx} ${ty}`;
    lx = sx + (tx - sx) * 0.66; ly = y1;
  }
  const kind = data?.kind ?? "next";
  return (
    <>
      <BaseEdge path={d} markerEnd={markerEnd} className={`fe fe--${kind} ${data?.taken ? "fe--taken" : ""} ${data?.dim ? "fe--dim" : ""} ${selected ? "fe--sel" : ""}`} interactionWidth={10} />
      {kind === "route" && data?.label && (
        <EdgeLabelRenderer>
          <div className={`rt-label ${data.label === "default" ? "rt-label--def" : ""} ${data.taken ? "rt-label--taken" : ""} ${data.dim ? "rt-label--dim" : ""}`} style={{ transform: `translate(-50%,-50%) translate(${lx}px,${ly}px)` }}>{data.label}</div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

interface BackData extends Record<string, unknown> { span: number; hits: number; run: boolean; mode: "same" | "left" | "right"; dim: boolean }
function BackEdge({ sourceX, sourceY, targetX, targetY, markerEnd, data, selected }: EdgeProps<Edge<BackData>>) {
  const span = data?.span ?? 1, hits = data?.hits ?? 0, run = !!data?.run, mode = data?.mode ?? "same";
  let d: string, lx: number, ly: number;
  if (mode === "same") {
    const o = Math.min(58 + span * 8, 104);
    d = `M ${sourceX} ${sourceY} C ${sourceX + o} ${sourceY}, ${targetX + o} ${targetY}, ${targetX} ${targetY}`;
    lx = sourceX + 52; ly = sourceY;
  } else {
    const o = Math.abs(sourceX - targetX) / 2, k = mode === "left" ? -1 : 1;
    d = `M ${sourceX} ${sourceY} C ${sourceX + k * o} ${sourceY}, ${targetX - k * o} ${targetY}, ${targetX} ${targetY}`;
    lx = (sourceX + targetX) / 2; ly = (sourceY + targetY) / 2;
  }
  const idle = run && !hits;
  return (
    <>
      <BaseEdge path={d} markerEnd={markerEnd} className={`be ${hits ? "be--hit" : ""} ${idle ? "be--idle" : ""} ${data?.dim ? "be--dim" : ""} ${selected ? "be--sel" : ""}`} interactionWidth={16} />
      <EdgeLabelRenderer>
        <div className={`be-label ${hits ? "be-label--hit" : ""} ${idle ? "be-label--idle" : ""} ${data?.dim ? "be-label--dim" : ""}`} style={{ transform: `translate(-50%,-50%) translate(${lx}px,${ly}px)` }}>
          on failure{hits > 0 ? ` ×${hits}` : ""}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

export function Legend({ compact }: { compact?: boolean }) {
  return (
    <div className={`glegend ${compact ? "glegend--c" : ""}`} aria-label="Edge legend">
      <span><svg width="26" height="8" aria-hidden><path d="M1 4h24" className="lg lg--next" /></svg>next (fall-through)</span>
      <span><svg width="26" height="8" aria-hidden><path d="M1 4h24" className="lg lg--route" /></svg>route</span>
      <span><svg width="26" height="8" aria-hidden><path d="M1 4h24" className="lg lg--back" /></svg>on_failure</span>
    </div>
  );
}

const nodeTypes = { step: StepNode };
const edgeTypes = { back: BackEdge, fwd: FwdEdge };

function Viewport({ L, followIdx, followY, resetKey }: { L: Layout; followIdx: number; followY: number; resetKey: string }) {
  const rf = useReactFlow();
  const w = useStore((s) => s.width);
  const h = useStore((s) => s.height);
  useEffect(() => {
    if (!w || !h) return;
    const spanW = L.nodeW + (L.maxCol - L.minCol) * L.colGap + (L.maxCol === 0 ? 110 : 24);
    const zf = (w - 16) / spanW;
    const z = L.rich ? Math.max(0.5, Math.min(0.95, zf)) : Math.min(0.95, (w - 16) / (L.nodeW + 150));
    const cx = L.rich ? (L.minCol * L.colGap + spanW / 2 - (L.maxCol === 0 ? 55 : 12)) : L.nodeW / 2 + 65;
    const centre = zf < 0.5 ? L.nodeW / 2 + 40 : cx;
    const x = w / 2 - centre * z;
    if (followIdx < 0) rf.setViewport({ x, y: 14, zoom: z });
    else rf.setViewport({ x, y: Math.min(14, h / 2 - (followY + L.nodeH / 2) * z), zoom: z }, { duration: 500 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [w > 0, h > 0, resetKey, followIdx]);
  return null;
}

export interface GraphProps {
  wf: Workflow;
  readOnly?: boolean;
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
  onSetFailure?: (src: string, tgt: string | null) => void;
  onNotice?: (msg: string) => void;
  states?: Record<string, StepState>;
  edgeHits?: Record<string, number>;
  followId?: string | null;
  dark?: boolean;
  /** steps on the highlighted path; others are dimmed */
  activeIds?: Set<string> | null;
  /** "src->tgt" forward edges on the highlighted path */
  activeEdges?: Set<string> | null;
  /** run view: forward edges the run took, "src->tgt" -> count */
  taken?: Record<string, number>;
  /** run view: label dimmed steps "not on this path" */
  offLabel?: boolean;
}

function Inner({ wf, readOnly = false, selectedId, onSelect, onSetFailure, onNotice, states, edgeHits, followId, dark, activeIds, activeEdges, taken, offLabel }: GraphProps) {
  const idx = useMemo(() => new Map(wf.steps.map((s, i) => [s.id, i])), [wf.steps]);
  const L = useMemo(() => layout(wf), [wf]);
  const nodes: SNode[] = useMemo(
    () => wf.steps.map((s) => ({
      id: s.id, type: "step", position: L.pos[s.id], width: L.nodeW, height: L.nodeH,
      data: { step: s, state: states?.[s.id], readOnly, rich: L.rich, dim: !!activeIds && !activeIds.has(s.id), off: !!offLabel && !!activeIds && !activeIds.has(s.id) },
      selected: s.id === selectedId, draggable: false, deletable: false,
    })),
    [wf.steps, L, states, readOnly, selectedId, activeIds, offLabel],
  );
  const edges: Edge[] = useMemo(() => {
    const e: Edge[] = [];
    const arrow = (color: string, n = 13) => ({ type: MarkerType.ArrowClosed, width: n, height: n, color });
    if (L.rich) {
      for (const g of forwardEdges(wf)) {
        const key = `${g.source}->${g.target}`;
        e.push({
          id: g.id, source: g.source, target: g.target, sourceHandle: "s", targetHandle: "t", type: "fwd", selectable: false, focusable: false, deletable: false,
          data: { kind: g.kind, label: g.label, dim: !!activeEdges && !activeEdges.has(key), taken: taken?.[key] ?? 0 },
          markerEnd: arrow(g.kind === "route" ? "var(--accent)" : "var(--faint)"),
        });
      }
    } else {
      wf.steps.forEach((s, i) => {
        if (i < wf.steps.length - 1)
          e.push({ id: `l-${s.id}`, source: s.id, target: wf.steps[i + 1].id, sourceHandle: "s", targetHandle: "t", type: "straight", selectable: false, focusable: false, deletable: false, markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 }, className: "le" });
      });
    }
    wf.steps.forEach((s, i) => {
      const t = s.on_failure ? idx.get(s.on_failure) : undefined;
      if (t === undefined || t >= i) return;
      const cs = L.col[s.id], ct = L.col[s.on_failure!];
      const mode = ct < cs ? "left" : ct > cs ? "right" : "same";
      e.push({
        id: `b-${s.id}`, source: s.id, target: s.on_failure!,
        sourceHandle: mode === "left" ? "fl" : "fs", targetHandle: mode === "right" ? "ftl" : "ft", type: "back", reconnectable: !readOnly, deletable: !readOnly,
        data: { span: Math.round((L.pos[s.id].y - L.pos[s.on_failure!].y) / L.pitch) || 1, hits: edgeHits?.[`${s.id}->${s.on_failure}`] ?? 0, run: !!states, mode, dim: !!activeIds && !(activeIds.has(s.id) && activeIds.has(s.on_failure!)) },
        markerEnd: arrow("var(--warn-line)", 12),
      });
    });
    return e;
  }, [wf, L, idx, edgeHits, states, readOnly, activeEdges, activeIds, taken]);

  const connect = (c: Connection) => {
    const si = idx.get(c.source), ti = idx.get(c.target);
    if (si === undefined || ti === undefined) return;
    if (ti < si) { onSetFailure?.(c.source, c.target); onNotice?.(`on_failure of ${c.source} set to ${c.target}`); }
    else onNotice?.("Forward edges come from routes. Add a route case in the inspector, or use move up / down to reorder.");
  };
  const followIdx = followId ? idx.get(followId) ?? -1 : -1;
  const followY = followId && L.pos[followId] ? L.pos[followId].y : 0;

  return (
    <ReactFlow
      nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      nodesDraggable={false} nodesConnectable={!readOnly} elementsSelectable
      onNodeClick={(_, n) => onSelect?.(n.id)} onPaneClick={() => onSelect?.(null)}
      onEdgeClick={(_, e) => e.type === "back" && onSelect?.(e.source)}
      onConnect={connect}
      onReconnect={(old, c) => { if (!readOnly && idx.get(c.target)! < idx.get(old.source)!) onSetFailure?.(old.source, c.target); }}
      onEdgesDelete={(es) => es.forEach((e) => e.type === "back" && onSetFailure?.(e.source, null))}
      panOnScroll zoomOnScroll={false} zoomOnPinch minZoom={0.25} maxZoom={1.5}
      colorMode={dark ? "dark" : "light"}
      defaultViewport={{ x: 20, y: 14, zoom: 0.8 }}
    >
      <Viewport L={L} followIdx={readOnly ? followIdx : -1} followY={followY} resetKey={wf.name} />
    </ReactFlow>
  );
}

export function WorkflowGraph(p: GraphProps) {
  return <ReactFlowProvider><Inner {...p} /></ReactFlowProvider>;
}
