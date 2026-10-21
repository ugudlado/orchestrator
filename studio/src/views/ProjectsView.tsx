import type { AppApi } from "../app";
import { Button } from "../affectui/button-clean";

const SAMPLE = `{"mcpServers": {"linear": {"command": "npx", "args": ["-y", "<server package>"], "env": {"LINEAR_API_KEY": "\${LINEAR_API_KEY}"}}}}`;

export function ProjectsView({ app }: { app: AppApi }) {
  const projects = app.health?.projects;
  const runs = Object.values(app.runs);
  return (
    <div className="projs">
      <div><h1>Projects</h1><p className="muted">Each project is a folder the agents work in. Its <code>.mcp.json</code> gives the agents in its runs their tools.</p></div>
      {!projects && <p className="muted">Projects come from the Studio server. Start the server to see them here.</p>}
      {projects && projects.length === 0 && <p className="muted">The server has no projects yet.</p>}
      {projects?.map((p) => {
        const n = runs.filter((r) => r.live?.repo === p.path).length;
        return (
          <article className="ccard" key={p.path} aria-label={p.name}>
            <header className="ccard__h">
              <div><div className="ccard__n">{p.name}</div><div className="ccard__k mono">{p.path}</div></div>
              <Button size="sm" variant="secondary" disabled={!n} onClick={() => { app.setProjectFilter(p.path); app.setView("runs"); }}>{n} run{n === 1 ? "" : "s"}</Button>
            </header>
            <div className="field__l">Agent tools</div>
            {p.has_mcp_json ? (
              p.mcp.length === 0 ? <p className="muted">.mcp.json lists no servers.</p> : (
                <ul className="mcpl">
                  {p.mcp.map((m) => (
                    <li key={m.name}>
                      <div className="mcpl__h"><strong>{m.name}</strong><span className="tchip" data-on>{m.type}</span><code className="mcpl__w">{m.what}</code></div>
                      {m.missing.length > 0 && <p className="mcpl__warn" role="note">Needs {m.missing.join(", ")} in .orchestrator/mcp.env; until then runs start without this server.</p>}
                    </li>
                  ))}
                </ul>
              )
            ) : (
              <div className="mcpl__empty">
                <p>Add a <code>.mcp.json</code> to this folder to give the agents in its runs their tools. Claude Code and Codex both get the same servers.</p>
                <pre className="mcpl__code"><code>{SAMPLE}</code></pre>
                <p className="muted">Put secrets in <code>.orchestrator/mcp.env</code> (KEY=value, keep it out of git). Agents never see these values; only the MCP servers do.</p>
              </div>
            )}
            {p.has_bindings && <p className="muted">This project also has a <code>.orchestrator/bindings.yaml</code>.</p>}
          </article>
        );
      })}
    </div>
  );
}
