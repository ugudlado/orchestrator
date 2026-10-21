import { useState } from "react";
import { Button } from "../affectui/button-clean";
import { ROLES, TIERS, roleById } from "../roles";
import type { Route, Step, Workflow } from "../types";
import { routeLabel } from "../paths";

const TOOLS = ["fs.read", "fs.write", "fs.list", "shell.run", "shell.test", "git.status", "git.diff", "git.commit", "git.fetch", "git.worktree", "git.push", "net.fetch", "ticket.read", "ticket.write", "forge.pr", "chat.post"];

export interface InspectorProps {
  wf: Workflow;
  step: Step;
  index: number;
  onChange: (patch: Partial<Step>) => void;
  onRename: (id: string) => void;
  onDelete: () => void;
  onMove: (dir: -1 | 1) => void;
}

const Field = ({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) => (
  <label className="field"><span className="field__l">{label}</span>{children}{hint && <span className="field__h">{hint}</span>}</label>
);

export function Inspector({ wf, step: s, index, onChange, onRename, onDelete, onMove }: InspectorProps) {
  const [draftId, setDraftId] = useState(s.id);
  const [prevId, setPrevId] = useState(s.id);
  if (prevId !== s.id) { setPrevId(s.id); setDraftId(s.id); }
  const earlier = wf.steps.slice(0, index);
  const tokens = [...new Set(earlier.filter((x) => x.kind === "gate" && x.approve_as).map((x) => x.approve_as!))];
  const enumKeys = Object.keys(s.enums ?? {});
  const routes = s.routes ?? [];
  const setRoutes = (next: Route[]) => onChange({ routes: next.length ? next : undefined });
  const hasDefault = routes.length > 0 && !routes[routes.length - 1].when;
  const gotoOptions = wf.steps.filter((x) => x.id !== s.id);
  const nextStep = wf.steps[index + 1]?.id ?? gotoOptions[0]?.id ?? "";
  const whenKey = (r: Route) => Object.keys(r.when ?? {})[0];
  const whenVals = (r: Route): string[] => { const v = r.when?.[whenKey(r)]; return v === undefined ? [] : Array.isArray(v) ? v : [v]; };
  const setRow = (i: number, r: Route) => setRoutes(routes.map((x, j) => (j === i ? r : x)));
  const addCase = () => {
    const k = enumKeys[0]; if (!k) return;
    const fresh: Route = { when: { [k]: s.enums![k][0] }, goto: nextStep };
    setRoutes(hasDefault ? [...routes.slice(0, -1), fresh, routes[routes.length - 1]] : [...routes, fresh]);
  };
  const toolList = [...new Set([...TOOLS, ...(s.tools ?? [])])];
  const artifacts = (field: "in" | "out") => (
    <div className="kv">
      {Object.entries(s[field] ?? {}).map(([k, v]) => (
        <div className="kv__row" key={k}>
          <input aria-label={`${field} key`} value={k} onChange={(e) => {
            const nk = e.target.value; if (!nk || (nk !== k && nk in (s[field] ?? {}))) return;
            const next: Record<string, string> = {}; for (const [a, b] of Object.entries(s[field] ?? {})) next[a === k ? nk : a] = b;
            onChange({ [field]: next } as Partial<Step>);
          }} />
          <input aria-label={`${field} value`} value={v} onChange={(e) => {
            const val = e.target.value;
            const patch: Partial<Step> = { [field]: { ...s[field], [k]: val } } as Partial<Step>;
            if (field === "out" && val === "enum" && !s.enums?.[k]) patch.enums = { ...s.enums, [k]: ["pass", "needs_work"] };
            onChange(patch);
          }} />
          <button className="icon-x" aria-label={`remove ${k}`} onClick={() => { const n = { ...s[field] }; delete n[k]; onChange({ [field]: n } as Partial<Step>); }}>×</button>
        </div>
      ))}
      <button className="link" onClick={() => onChange({ [field]: { ...s[field], [field === "in" ? "input" : "output"]: field === "in" ? "input.md" : "output.md" } } as Partial<Step>)}>+ add {field === "in" ? "input" : "output"}</button>
    </div>
  );

  return (
    <div className="insp">
      <div className="insp__head">
        <span className={`kindbadge kindbadge--${s.kind}`}>{s.kind}</span>
        <span className="insp__idx">step {index + 1} of {wf.steps.length}</span>
      </div>
      <Field label="Step id">
        <input value={draftId} onChange={(e) => setDraftId(e.target.value)} onBlur={() => (draftId.trim() && draftId !== s.id ? onRename(draftId.trim()) : setDraftId(s.id))} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
      </Field>

      {s.kind === "gate" ? (
        <>
          <Field label="approve_as (token minted)"><input value={s.approve_as ?? ""} onChange={(e) => onChange({ approve_as: e.target.value })} /></Field>
          <Field label="show artifacts" hint="comma separated">
            <input value={(s.show ?? []).join(", ")} onChange={(e) => onChange({ show: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
          </Field>
        </>
      ) : (
        <>
          {s.kind === "judgment" && (
            <>
              <Field label="Role" hint={roleById(s.role) ? `${TIERS[roleById(s.role)!.tier]}` : "no agent assigned"}>
                <select value={s.role ?? ""} onChange={(e) => onChange({ role: e.target.value || null })}>
                  <option value="">unassigned</option>
                  {ROLES.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                </select>
              </Field>
              <Field label="max_turns"><input type="number" min={1} value={s.max_turns ?? ""} onChange={(e) => onChange({ max_turns: e.target.value ? +e.target.value : null })} /></Field>
            </>
          )}
          <div className="field"><span className="field__l">Tools</span>
            <div className="toolchips">
              {toolList.map((t) => {
                const on = s.tools?.includes(t);
                return <button key={t} type="button" className="tchip" data-on={on || undefined} aria-pressed={!!on}
                  onClick={() => onChange({ tools: on ? s.tools!.filter((x) => x !== t) : [...(s.tools ?? []), t] })}>{t}</button>;
              })}
            </div>
          </div>
          <div className="field"><span className="field__l">Inputs (artifacts in)</span>{artifacts("in")}</div>
          <div className="field"><span className="field__l">Outputs (artifacts out)</span>{artifacts("out")}</div>
          {enumKeys.length > 0 && (
            <div className="field"><span className="field__l">Enums &amp; fail_on</span>
              {enumKeys.map((k) => (
                <div className="enum" key={k}>
                  <div className="enum__k">{k}</div>
                  <input aria-label={`${k} values`} value={s.enums![k].join(", ")} onChange={(e) => onChange({ enums: { ...s.enums, [k]: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) } })} />
                  <div className="toolchips">
                    {s.enums![k].map((v) => {
                      const on = s.fail_on?.[k]?.includes(v);
                      return <button key={v} type="button" className="tchip tchip--warn" data-on={on || undefined} aria-pressed={!!on}
                        onClick={() => { const cur = s.fail_on?.[k] ?? []; const nxt = on ? cur.filter((x) => x !== v) : [...cur, v]; const fo = { ...s.fail_on }; if (nxt.length) fo[k] = nxt; else delete fo[k]; onChange({ fail_on: fo }); }}>
                        {on ? "fail_on · " : ""}{v}</button>;
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
          <button className="link" onClick={() => onChange({ out: { ...s.out, verdict: "enum" }, enums: { ...s.enums, verdict: ["pass", "needs_work"] }, fail_on: { ...s.fail_on, verdict: ["needs_work"] } })}>+ add verdict enum</button>
          <Field label="on_failure" hint="earlier step to route back to">
            <select value={s.on_failure ?? ""} onChange={(e) => onChange({ on_failure: e.target.value || null })}>
              <option value="">none</option>
              {earlier.map((x) => <option key={x.id} value={x.id}>{x.id}</option>)}
            </select>
          </Field>
          <Field label="max_retries"><input type="number" min={0} placeholder="default 3" value={s.max_retries ?? ""} onChange={(e) => onChange({ max_retries: e.target.value === "" ? null : +e.target.value })} /></Field>
          <Field label="requires" hint="token a gate must have minted">
            <select value={s.requires ?? ""} onChange={(e) => onChange({ requires: e.target.value || null })}>
              <option value="">none</option>
              {tokens.map((t) => <option key={t} value={t}>{t}</option>)}
              {s.requires && !tokens.includes(s.requires) && <option value={s.requires}>{s.requires} (not minted upstream)</option>}
            </select>
          </Field>
        </>
      )}
      {s.kind !== "gate" && (
        <label className="check"><input type="checkbox" checked={!!s.skip_when_unchanged} onChange={(e) => onChange({ skip_when_unchanged: e.target.checked || undefined })} />
          <span><code>skip_when_unchanged</code><em>self-reports skipped when inputs have not changed</em></span></label>
      )}
      <div className="field routes" id="routes-section"><span className="field__l">Routes <span className="muted">· evaluated after the step completes, first match wins</span></span>
        {routes.length === 0 && <div className="field__h">No routes. Falls through to {wf.steps[index + 1] ? <code>{wf.steps[index + 1].id}</code> : "the end of the workflow"}.</div>}
        {routes.map((r, i) => (
          <div className="route" key={i} data-def={!r.when || undefined}>
            <div className="route__w">
              {r.when ? (
                <>
                  <span className="route__t">when</span>
                  <select aria-label="route key" value={whenKey(r)} onChange={(e) => setRow(i, { ...r, when: { [e.target.value]: s.enums![e.target.value][0] } })}>
                    {enumKeys.map((k) => <option key={k}>{k}</option>)}
                    {!enumKeys.includes(whenKey(r)) && <option>{whenKey(r)}</option>}
                  </select>
                  <span className="route__t">=</span>
                  <div className="toolchips">
                    {(s.enums?.[whenKey(r)] ?? whenVals(r)).map((v) => {
                      const on = whenVals(r).includes(v);
                      return <button key={v} type="button" className="tchip" data-on={on || undefined} aria-pressed={on}
                        onClick={() => { const nv = on ? whenVals(r).filter((x) => x !== v) : [...whenVals(r), v]; if (!nv.length) return; setRow(i, { ...r, when: { [whenKey(r)]: nv.length === 1 ? nv[0] : nv } }); }}>{v}</button>;
                    })}
                  </div>
                </>
              ) : <span className="route__t">default <em>(no when, must be last)</em></span>}
            </div>
            <div className="route__g">
              <span className="route__t">goto</span>
              <select aria-label="route goto" value={r.goto} onChange={(e) => setRow(i, { ...r, goto: e.target.value })}>
                {gotoOptions.map((x) => <option key={x.id}>{x.id}</option>)}
                {!gotoOptions.some((x) => x.id === r.goto) && <option>{r.goto}</option>}
              </select>
              <button className="icon-x" aria-label={`remove route ${routeLabel(r)}`} onClick={() => setRoutes(routes.filter((_, j) => j !== i))}>×</button>
            </div>
          </div>
        ))}
        <div className="route__add">
          {enumKeys.length > 0 && <button className="link" onClick={addCase}>+ add case</button>}
          {!hasDefault && <button className="link" onClick={() => setRoutes([...routes, { goto: nextStep }])}>+ add default</button>}
        </div>
        {enumKeys.length === 0 && <div className="field__h">Cases match enum outputs. This step has none, so only a default route is possible.</div>}
      </div>
      <div className="insp__actions">
        <Button size="sm" variant="secondary" disabled={index === 0} onClick={() => onMove(-1)}>Move up</Button>
        <Button size="sm" variant="secondary" disabled={index === wf.steps.length - 1} onClick={() => onMove(1)}>Move down</Button>
        <Button size="sm" variant="destructive" onClick={onDelete}>Delete</Button>
      </div>
    </div>
  );
}
