import { useMemo, useState } from "react";
import type { AppApi } from "../app";
import { Button } from "../affectui/button-clean";
import { SegmentedControl } from "../affectui/segmented-control-clean";
import { Legend, WorkflowGraph } from "../graph/WorkflowGraph";
import { PATHS, hasRoutes, walkPath, type PathId } from "../paths";
import { MAIN_PLUGIN, MINE } from "../seed";
import type { Kind, Step, Workflow } from "../types";
import { toYaml, validate, type Issue } from "../yaml";
import { Inspector } from "./Inspector";

function YamlView({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const lines = text.replace(/\n$/, "").split("\n");
  return (
    <div className="yaml">
      <button className="yaml__copy" onClick={() => { try { navigator.clipboard?.writeText(text); } catch { /* ignore */ } setCopied(true); setTimeout(() => setCopied(false), 1200); }}>{copied ? "Copied" : "Copy"}</button>
      <pre aria-label="Generated workflow YAML">
        {lines.map((l, i) => {
          const m = l.match(/^(\s*(?:- )?)([A-Za-z_]+)(:)(.*)$/);
          return <div key={i} className="yaml__l">{l.startsWith("#") ? <span className="y-c">{l}</span> : m ? <>{m[1]}<span className="y-k">{m[2]}</span>{m[3]}<span className="y-v">{m[4]}</span></> : <span className="y-v">{l}</span>}</div>;
        })}
      </pre>
    </div>
  );
}

const blank = (kind: Kind, taken: Set<string>): Step => {
  let n = 1; const base = kind === "gate" ? "new-gate" : `new-${kind}`;
  while (taken.has(`${base}-${n}`)) n++;
  const id = `${base}-${n}`;
  if (kind === "gate") return { id, kind, show: [], approve_as: `${id.replace(/-/g, "_")}_token` };
  return { id, kind, role: kind === "judgment" ? "teamlead" : null, max_turns: kind === "judgment" ? 20 : null, tools: ["fs.read"], in: {}, out: {}, enums: {}, fail_on: {}, on_failure: null, requires: null, max_retries: null };
};

export function WorkflowsView({ app }: { app: AppApi }) {
  const { workflows, selWf, selStep } = app;
  const wf: Workflow | undefined = workflows[selWf] ?? Object.values(workflows)[0];
  const [tab, setTab] = useState<"graph" | "yaml">("graph");
  const [addOpen, setAddOpen] = useState(false);
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [notice, setNotice] = useState("");
  const [path, setPath] = useState<PathId>("all");
  const rich = wf ? hasRoutes(wf) : false;
  const pathInfos = useMemo(() => (wf && rich ? Object.fromEntries(Object.entries(PATHS).map(([k, p]) => [k, walkPath(wf, p.assign)])) : {}), [wf, rich]);
  const activePath = rich && path !== "all" ? pathInfos[path] : null;
  const activeIds = useMemo(() => (activePath ? new Set(activePath.ids) : null), [activePath]);
  const yaml = useMemo(() => (wf ? toYaml(wf) : ""), [wf]);
  const step = wf?.steps.find((s) => s.id === selStep) ?? null;
  const usedByRuns = wf ? Object.values(app.runs).some((r) => r.workflow === wf.name) : false;

  const groups = useMemo(() => {
    const g = new Map<string, Workflow[]>([[MAIN_PLUGIN, []]]);
    Object.values(workflows).forEach((w) => { const k = w.plugin ?? MINE; (g.get(k) ?? g.set(k, []).get(k)!).push(w); });
    if (!g.has(MINE)) g.set(MINE, []);
    return [...g.entries()];
  }, [workflows]);

  const edit = (fn: (w: Workflow) => Workflow) => { if (!wf) return; app.setWorkflows((all) => ({ ...all, [wf.name]: fn(all[wf.name]) })); setIssues(null); };
  const patchStep = (id: string, patch: Partial<Step>) => edit((w) => ({ ...w, steps: w.steps.map((s) => (s.id === id ? { ...s, ...patch } : s)) }));

  const addStep = (kind: Kind) => {
    if (!wf) return;
    const ns = blank(kind, new Set(wf.steps.map((s) => s.id)));
    const at = step ? wf.steps.findIndex((s) => s.id === step.id) + 1 : wf.steps.length;
    edit((w) => ({ ...w, steps: [...w.steps.slice(0, at), ns, ...w.steps.slice(at)] }));
    app.setSelStep(ns.id); setAddOpen(false);
  };
  const newWorkflow = () => {
    let n = 1; while (workflows[`workflow-${n}`]) n++;
    const name = `workflow-${n}`;
    const first = blank("exec", new Set()); first.id = "check-rerun";
    app.setWorkflows((all) => ({ ...all, [name]: { name, version: 1, plugin: MINE, inputs: { ticket: { type: "string" } }, steps: [first] } }));
    app.setSelWf(name); app.setSelStep(null); setIssues(null); setTab("graph");
  };
  const del = () => {
    if (!step) return;
    edit((w) => ({ ...w, steps: w.steps.filter((s) => s.id !== step.id).map((s) => {
      const routes = s.routes?.filter((r) => r.goto !== step.id);
      return { ...s, on_failure: s.on_failure === step.id ? null : s.on_failure, routes: routes?.length ? routes : undefined };
    }) }));
    app.setSelStep(null);
  };
  const rename = (id: string) => {
    if (!step || wf!.steps.some((s) => s.id === id)) return;
    edit((w) => ({ ...w, steps: w.steps.map((s) => ({ ...s, id: s.id === step.id ? id : s.id, on_failure: s.on_failure === step.id ? id : s.on_failure, routes: s.routes?.map((r) => (r.goto === step.id ? { ...r, goto: id } : r)) })) }));
    app.setSelStep(id);
  };
  const move = (dir: -1 | 1) => {
    if (!step) return;
    edit((w) => { const a = [...w.steps]; const i = a.findIndex((s) => s.id === step.id); const j = i + dir; if (j < 0 || j >= a.length) return w; [a[i], a[j]] = [a[j], a[i]]; return { ...w, steps: a }; });
  };
  const validateNow = () => {
    if (!wf) return;
    const is = validate(wf);
    setIssues(is);
  };
  const errs = issues?.filter((i) => i.level === "error") ?? [];
  const warns = issues?.filter((i) => i.level === "warn") ?? [];

  if (!wf) return null;
  return (
    <div className="wfv">
      <aside className="wfv__list" aria-label="Workflows">
        <div className="wfv__listhead">
          <Button size="sm" variant="primary" onClick={newWorkflow}>New workflow</Button>
          <button className="link" onClick={() => app.openSettings("plugins")}>Manage plugins</button>
        </div>
        {groups.map(([plugin, ws]) => (
          <div className="grp" key={plugin}>
            <div className="grp__h">{plugin}{plugin === MAIN_PLUGIN && <span className="grp__src">ugudlado/workflows</span>}</div>
            {ws.length === 0 && <div className="grp__empty">Nothing here yet. Create one or ask the assistant.</div>}
            {ws.map((w) => (
              <button key={w.name} className="wfi" data-on={w.name === wf.name || undefined} onClick={() => { app.setSelWf(w.name); app.setSelStep(null); setIssues(null); setPath("all"); }}>
                <span className="wfi__n">{w.name}{w.name === "dev" && <em className="wfi__def">default</em>}{w.trigger && <em className="wfi__def wfi__trig">{w.trigger}</em>}</span>
                <span className="wfi__m">v{w.version} · {w.steps.length} steps · {w.steps.filter((s) => s.kind === "gate").length} gates</span>
              </button>
            ))}
          </div>
        ))}
      </aside>

      <section className="wfv__center">
        <div className="toolbar">
          <div className="toolbar__name"><span className="toolbar__wf">{wf.name}</span><span className="toolbar__v">v{wf.version} · {wf.plugin ?? MINE}{wf.trigger ? ` · ${wf.trigger}` : ""}</span></div>
          <SegmentedControl size="sm" variant="tray" aria-label="Graph or YAML" value={tab} onChange={(v) => setTab(v)} options={[{ value: "graph", label: "Graph" }, { value: "yaml", label: "YAML" }]} />
          <div className="toolbar__sp" />
          <Button size="sm" variant="secondary" onClick={() => setAddOpen((o) => !o)}>Add step</Button>
          <Button size="sm" variant="primary" onClick={validateNow}>Validate</Button>
        </div>
        {addOpen && (
          <div className="addrow" role="group" aria-label="Step kind">
            <span>Insert {step ? `after ${step.id}` : "at end"}:</span>
            {(["judgment", "exec", "gate"] as Kind[]).map((k) => <Button key={k} size="sm" variant="secondary" onClick={() => addStep(k)}>{k}</Button>)}
            <Button size="sm" variant="ghost" onClick={() => setAddOpen(false)}>Cancel</Button>
          </div>
        )}
        {issues && (
          <div className="vres" data-level={errs.length ? "error" : warns.length ? "warn" : "ok"} role="status">
            <strong>{errs.length ? `${errs.length} error${errs.length > 1 ? "s" : ""}` : "Valid"}{warns.length ? ` · ${warns.length} warning${warns.length > 1 ? "s" : ""}` : ""}</strong>
            {!errs.length && <span> · {wf.steps.length} steps, dry-run of `orchestrator next {wf.name}` reached done in {wf.steps.length + 2} calls</span>}
            {issues.map((i, k) => <div key={k} className="vres__i">{i.level === "error" ? "error" : "warn"}: {i.msg}</div>)}
          </div>
        )}
        {notice && <div className="hint" role="status">{notice}</div>}
        {rich && tab === "graph" && (
          <div className="pathbar">
            <SegmentedControl size="sm" variant="tray" aria-label="Highlight a path" value={path} onChange={(v) => setPath(v as PathId)}
              options={[{ value: "all", label: "All paths" }, { value: "feature", label: "Feature" }, { value: "fix", label: "Fix" }, { value: "hotfix", label: "Hotfix" }, { value: "quick", label: "Quick" }]} />
            <Legend />
          </div>
        )}
        <div className="graphbox">
          {tab === "graph" ? (
            <WorkflowGraph wf={wf} selectedId={selStep} onSelect={app.setSelStep} dark={app.dark} activeIds={activeIds} activeEdges={activePath?.edges ?? null}
              onSetFailure={(src, tgt) => patchStep(src, { on_failure: tgt })} onNotice={(m) => { setNotice(m); setTimeout(() => setNotice(""), 3500); }} />
          ) : <YamlView text={yaml} />}
        </div>
        <p className="foot">Engine: <code>orchestrator next</code> (same core as the CLI). Drag from a step's right-hand handle to an earlier step to set <code>on_failure</code>.</p>
      </section>

      <aside className="wfv__insp" aria-label="Inspector">
        {rich && (
          <div className="pathcard" data-on={activePath ? "" : undefined}>
            {activePath ? (
              <>
                <div className="pathcard__h">{PATHS[path as Exclude<PathId, "all">].label} · {activePath.steps} steps · {activePath.gates} gate{activePath.gates === 1 ? "" : "s"}</div>
                <div className="pathcard__s">{Object.entries(PATHS[path as Exclude<PathId, "all">].assign).map(([k, v]) => `${k} = ${v}`).join(", ")} · {wf.steps.length - activePath.steps} of {wf.steps.length} steps not on this path</div>
              </>
            ) : (
              <>
                <div className="pathcard__h">{Object.keys(PATHS).length} paths through {wf.name}</div>
                <div className="pathcard__s">{(Object.entries(PATHS) as [Exclude<PathId, "all">, (typeof PATHS)["feature"]][]).map(([k, p]) => `${p.label} ${pathInfos[k].steps}`).join(" · ")} steps. Pick a path above to trace it.</div>
              </>
            )}
          </div>
        )}
        {step ? (
          <Inspector wf={wf} step={step} index={wf.steps.findIndex((s) => s.id === step.id)} onChange={(p) => patchStep(step.id, p)} onRename={rename} onDelete={del} onMove={move} />
        ) : (
          <div className="insp__empty">
            <h3>Inspector</h3>
            {wf.summary && <p>{wf.summary}</p>}
            <p>Select a step in the graph to edit its kind, artifacts, tools, enums, <code>on_failure</code>, <code>max_retries</code> and <code>requires</code>.</p>
            {usedByRuns && <p className="muted">This workflow has runs, so its name is locked.</p>}
          </div>
        )}
      </aside>

    </div>
  );
}
