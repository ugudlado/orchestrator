import { useState } from "react";
import type { AppApi } from "../app";
import { Button } from "../affectui/button-clean";
import { BINDINGS, fakeEvents, fakeTools, slugId } from "../connectors";

export function ConnectorsView({ app }: { app: AppApi }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("https://");
  const [auth, setAuth] = useState("oauth");
  const [err, setErr] = useState("");
  const add = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setErr("Give the connector a name.");
    if (!/^https:\/\/.+\/mcp\/?$/.test(url.trim())) return setErr("Server URL must look like https://host/mcp");
    const id = slugId(name);
    if (app.connectors.some((c) => c.id === id)) return setErr("A connector with that name exists.");
    app.setConnectors((l) => [...l, { id, name: name.trim(), kind: "mcp", connected: true, meta: [url.trim(), `Auth: ${auth}`], events: fakeEvents(name), actions: [], url: url.trim(), auth, tools: fakeTools(name) }]);
    setName(""); setUrl("https://"); setErr("");
  };
  return (
    <div className="conns">
      <div><h1>Connectors</h1><p className="muted">Where tickets come from and where results go. Pipelines trigger from a connector event.</p></div>
      <div className="conns__grid">
        {app.connectors.map((c) => (
          <article className="ccard" key={c.id}>
            <header className="ccard__h">
              <span className={`cmark cmark--${c.kind}`}>{c.name[0]}</span>
              <div><div className="ccard__n">{c.name}</div><div className="ccard__k">{c.kind === "mcp" ? "Custom MCP server" : "Built-in connector"}</div></div>
              <span className="schip schip--done">{c.connected ? "connected" : "disconnected"}</span>
            </header>
            <div className="ccard__meta">{c.meta.map((m) => <span key={m}>{m}</span>)}</div>
            <div className="field__l">Events (triggers)</div>
            <div className="toolchips">{c.events.map((e) => <span key={e} className="tchip tchip--warn" data-on>{e}</span>)}</div>
            {c.tools ? (<><div className="field__l">Tools discovered</div><div className="toolchips">{c.tools.map((t) => <span key={t} className="tchip" data-on>{t}</span>)}</div></>)
              : (<><div className="field__l">Actions</div><div className="toolchips">{c.actions.map((t) => <span key={t} className="tchip">{t}</span>)}</div></>)}
          </article>
        ))}
        <form className="ccard ccard--add" onSubmit={add}>
          <div className="ccard__n">Add custom connector</div>
          <p className="muted">MCP server. Non-coding intake, e.g. support tickets, forms, docs.</p>
          <label className="field"><span className="field__l">Name</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Zendesk MCP" /></label>
          <label className="field"><span className="field__l">Server URL</span><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://host/mcp" /></label>
          <label className="field"><span className="field__l">Auth</span>
            <select value={auth} onChange={(e) => setAuth(e.target.value)}><option value="none">none</option><option value="bearer">bearer token</option><option value="oauth">OAuth</option></select></label>
          {err && <div className="vres" data-level="error">{err}</div>}
          <Button type="submit" variant="primary">Add connector</Button>
        </form>
      </div>
      <section className="bind" aria-label="Tool bindings">
        <h2>Tool bindings</h2>
        <p className="muted">Steps call neutral tools such as <code>ticket.read</code>. Each role resolves to one provider. From <code>.orchestrator/bindings.yaml</code>.</p>
        <table className="bind__t">
          <thead><tr><th>Role</th><th>Tools</th><th>Provider</th><th>Via</th></tr></thead>
          <tbody>
            {Object.entries(BINDINGS).map(([role, b]) => (
              <tr key={role}>
                <td><code>{role}</code></td>
                <td><code>{role}.*</code></td>
                <td>{b.provider}</td>
                <td><span className="tchip" data-on>{b.via}{b.command ? ` · ${b.command}` : ""}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
