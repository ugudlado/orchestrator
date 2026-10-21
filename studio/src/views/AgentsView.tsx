import type { AppApi } from "../app";
import { Avatar } from "../graph/WorkflowGraph";
import { ROLES, TIERS, type Tier } from "../roles";

export function AgentsView({ app }: { app: AppApi }) {
  return (
    <div className="agents">
      <div className="agents__head">
        <div className="stack" aria-hidden>{ROLES.map((r) => <Avatar key={r.id} role={r.id} size={30} />)}</div>
        <div><h1>Agent roster</h1><p className="muted">Judgment steps are run by a role. Each role runs on a model tier; change a tier and every step it covers follows.</p></div>
      </div>
      <div className="agents__grid">
        {ROLES.map((r) => {
          const tier = app.tiers[r.id];
          const covered = Object.values(app.workflows).flatMap((w) => w.steps.filter((s) => s.role === r.id).map((s) => ({ w: w.name, s: s.id })));
          return (
            <article className="acard" key={r.id}>
              <header className="acard__h"><Avatar role={r.id} size={34} /><div><div className="acard__n">{r.name}</div><div className="acard__b">{r.blurb}</div></div></header>
              <label className="field"><span className="field__l">Model tier</span>
                <select value={tier} onChange={(e) => app.setTier(r.id, e.target.value as Tier)}>
                  {(Object.keys(TIERS) as Tier[]).map((t) => <option key={t} value={t}>{t} · {TIERS[t]}</option>)}
                </select></label>
              <div className="field__l">Covers {covered.length} step{covered.length === 1 ? "" : "s"}</div>
              <div className="acard__steps">
                {covered.map((c) => (
                  <button key={c.w + c.s} className="tchip" onClick={() => { app.setSelWf(c.w); app.setSelStep(c.s); app.setView("workflows"); }}>{c.s}<em>{c.w}</em></button>
                ))}
                {covered.length === 0 && <span className="muted">No steps yet.</span>}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
