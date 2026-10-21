import { useState } from "react";
import type { AppApi, SettingsTab } from "../app";
import { Button } from "../affectui/button-clean";
import { ACCOUNT_NAME, type Account, type AccountId } from "../accounts";
import { ROLES, TIERS, type Tier } from "../roles";
import { MAIN_PLUGIN, PLUGIN_CATALOG } from "../seed";
import { ConnectorsView } from "./ConnectorsView";

const TABS: { id: SettingsTab; label: string }[] = [
  { id: "models", label: "Model accounts" }, { id: "plugins", label: "Plugins" }, { id: "channels", label: "Channels" }, { id: "connectors", label: "Connectors" }, { id: "agents", label: "Agents" },
];

export function SettingsView({ app }: { app: AppApi }) {
  return (
    <div className="settings">
      <nav className="settings__nav" aria-label="Settings sections">
        <h1>Settings</h1>
        {TABS.map((t) => <button key={t.id} className="snav" data-on={app.settingsTab === t.id || undefined} onClick={() => app.setSettingsTab(t.id)}>{t.label}</button>)}
      </nav>
      <div className="settings__body">
        {app.settingsTab === "models" && <Models app={app} />}
        {app.settingsTab === "plugins" && <Plugins app={app} />}
        {app.settingsTab === "channels" && <Channels app={app} />}
        {app.settingsTab === "connectors" && <ConnectorsView app={app} />}
        {app.settingsTab === "agents" && (
          <div className="conns">
            <div><h1>Agents</h1><p className="muted">Roles, model tiers and the steps each role covers are managed on the Agents tab.</p></div>
            <div className="ccard"><div className="ccard__n">{ROLES.length} roles</div><p className="muted">{ROLES.map((r) => r.name).join(", ")}</p><div><Button variant="primary" onClick={() => app.setView("agents")}>Open Agents</Button></div></div>
          </div>
        )}
      </div>
    </div>
  );
}

function Meter({ label, pct }: { label: string; pct: number }) {
  return (
    <div className="meter" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className="meter__l"><span>{label}</span><span>{pct}% used</span></div>
      <div className="prog__bar"><i style={{ transform: `scaleX(${pct / 100})`, background: pct > 80 ? "var(--danger)" : undefined }} /></div>
    </div>
  );
}

function Models({ app }: { app: AppApi }) {
  const get = (id: AccountId) => app.accounts.find((a) => a.id === id)!;
  const set = (id: AccountId, p: Partial<Account>) => app.setAccounts((l) => l.map((a) => (a.id === id ? { ...a, ...p } : a)));
  const pair = () => {
    app.setRunner((r) => ({ ...r, paired: true }));
    set("claude", { connected: true, plan: "Claude Max", meters: [{ label: "5-hour window", pct: 38 }, { label: "Weekly", pct: 12 }] });
    set("chatgpt", { connected: true, plan: "ChatGPT Plus", meters: [{ label: "5-hour window", pct: 21 }, { label: "Weekly", pct: 9 }] });
  };
  const disconnect = () => { app.setRunner((r) => ({ ...r, paired: false })); set("claude", { connected: false }); set("chatgpt", { connected: false }); };
  const out = (id: AccountId) => set(id, { connected: false, detail: undefined });
  const [prov, setProv] = useState("Anthropic");
  const [key, setKey] = useState("");
  const [oll, setOll] = useState("http://localhost:11434");
  const [copied, setCopied] = useState(false);
  const r = app.runner;
  const anyConnected = r.paired || app.accounts.some((a) => a.connected);
  const cmd = `npx orchestrator-runner connect ${r.code}`;

  const tool = (id: "claude" | "chatgpt", name: string, via: string) => {
    const a = get(id); const on = a.connected && r.paired;
    return (
      <article className="ccard" key={id} style={on ? undefined : { opacity: 0.7 }}>
        <header className="ccard__h"><span className={`cmark cmark--${id}`}>{name[0]}</span><div><div className="ccard__n">{name}</div><div className="ccard__k">{via}</div></div>
          <span className={`schip ${on ? "schip--done" : ""}`}>{on ? `signed in · ${a.plan}` : r.paired ? "not signed in" : "runner offline"}</span></header>
        {on ? (<>{a.meters?.map((m) => <Meter key={m.label} {...m} />)}</>) : <p className="muted">{r.paired ? <>Run <code>{id === "claude" ? "claude" : "codex"}</code> on your runner to sign in</> : "Pair a runner to detect this tool."}</p>}
      </article>
    );
  };
  const api = get("apikey"), ol = get("ollama");
  return (
    <div className="conns">
      <div><h1>Model accounts</h1><p className="muted">Studio runs agents through a runner on your machine or your own VM. It uses the AI tools you're already signed in to there; Studio never sees your login.</p></div>
      {!anyConnected && <div className="vres" data-level="warn" role="status">No runner connected. Workflows cannot run until you pair a runner or add an API key.</div>}

      <section className="ccard" aria-label="Your runner">
        <div className="ccard__n">Your runner</div>
        <p className="muted">Deployment: Studio, queue and runner on {r.name} · webhooks via Tailscale Funnel (/webhooks only)</p>
        {r.paired ? (
          <div className="runner"><span className="runner__dot" /><div className="runner__t"><strong>{r.name}</strong> · always on · tailnet · v{r.version}</div><Button size="sm" variant="secondary" onClick={disconnect}>Disconnect runner</Button></div>
        ) : (
          <>
            <p className="muted">No runner paired. On your VM or machine, run:</p>
            <div className="cmdbox"><code>{cmd}</code><Button size="sm" variant="secondary" onClick={() => { try { navigator.clipboard?.writeText(cmd); } catch { /* ignore */ } setCopied(true); setTimeout(() => setCopied(false), 1200); }}>{copied ? "Copied" : "Copy"}</Button></div>
            <div><Button size="sm" variant="ghost" onClick={pair}>Simulate pairing</Button></div>
          </>
        )}
      </section>

      <div className="field__l">Detected on your runner</div>
      <div className="conns__grid">
        {tool("claude", "Claude Code", "Subscription via the vendor's own CLI")}
        {tool("chatgpt", "Codex", "Subscription via the vendor's own CLI")}
        <article className="ccard" style={{ opacity: 0.7 }}>
          <header className="ccard__h"><span className="cmark cmark--ollama">G</span><div><div className="ccard__n">Gemini CLI</div><div className="ccard__k">Detected</div></div><span className="schip">not signed in</span></header>
          <p className="muted">Run <code>gemini</code> on your runner to sign in</p>
        </article>
        <article className="ccard" style={{ opacity: 0.7 }}>
          <header className="ccard__h"><span className="cmark cmark--ollama">C</span><div><div className="ccard__n">GitHub Copilot CLI</div><div className="ccard__k">Not detected</div></div><span className="schip">not installed</span></header>
          <p className="muted">Not installed on your runner</p>
        </article>
      </div>

      <div className="field__l">Other model sources</div>
      <div className="conns__grid">
        <article className="ccard">
          <header className="ccard__h"><span className="cmark cmark--mcp">K</span><div><div className="ccard__n">Use an API key</div><div className="ccard__k">For hosted runs and teams · Anthropic, OpenAI or OpenRouter</div></div>{api.connected && <span className="schip schip--done">connected</span>}</header>
          {api.connected ? (<><div className="ccard__meta"><span>{api.detail}</span><span>Billed per token by the provider</span></div><div><Button size="sm" variant="secondary" onClick={() => out("apikey")}>Remove key</Button></div></>) : (
            <form className="ccard__form" onSubmit={(e) => { e.preventDefault(); if (key.trim()) { set("apikey", { connected: true, plan: "API key", detail: `${prov} · ${key.trim().slice(0, 6)}…${key.trim().slice(-4)}` }); setKey(""); } }}>
              <select aria-label="Provider" value={prov} onChange={(e) => setProv(e.target.value)}><option>Anthropic</option><option>OpenAI</option><option>OpenRouter</option></select>
              <input aria-label="API key" type="password" placeholder="Paste API key" value={key} onChange={(e) => setKey(e.target.value)} />
              <Button type="submit" variant="primary" disabled={!key.trim()}>Save key</Button>
            </form>)}
        </article>
        <article className="ccard">
          <header className="ccard__h"><span className="cmark cmark--ollama">O</span><div><div className="ccard__n">Local model (Ollama)</div><div className="ccard__k">Optional, runs on your machine</div></div>{ol.connected && <span className="schip schip--done">connected</span>}</header>
          {ol.connected ? (<><div className="ccard__meta"><span>{ol.detail}</span></div><div><Button size="sm" variant="secondary" onClick={() => out("ollama")}>Disconnect</Button></div></>) : (
            <form className="ccard__form" onSubmit={(e) => { e.preventDefault(); set("ollama", { connected: true, plan: "Local", detail: `${oll} · llama3.1:8b` }); }}>
              <input aria-label="Ollama URL" value={oll} onChange={(e) => setOll(e.target.value)} />
              <Button type="submit" variant="secondary">Connect</Button>
            </form>)}
        </article>
      </div>

      <section className="ccard" aria-label="Routing">
        <div className="ccard__n">Routing</div>
        <table className="rtable">
          <thead><tr><th>Tier</th><th>Model</th><th>Roles</th><th>Account</th></tr></thead>
          <tbody>
            {(Object.keys(TIERS) as Tier[]).map((t) => (
              <tr key={t}>
                <td><strong>{t}</strong></td><td className="mono">{TIERS[t]}</td>
                <td>{ROLES.filter((x) => app.tiers[x.id] === t).map((x) => x.name).join(", ") || <span className="muted">none</span>}</td>
                <td><select aria-label={`Account for ${t}`} value={app.routing[t]} onChange={(e) => app.setRouting((rt) => ({ ...rt, [t]: e.target.value as AccountId }))}>
                  {(Object.keys(ACCOUNT_NAME) as AccountId[]).filter((id) => id !== "ollama" || get("ollama").connected).map((id) => <option key={id} value={id}>{ACCOUNT_NAME[id]}{get(id).connected && (r.paired || (id !== "claude" && id !== "chatgpt")) ? "" : " (not connected)"}</option>)}
                </select></td>
              </tr>
            ))}
          </tbody>
        </table>
        <label className="toggle"><input type="checkbox" checked={app.fallback} onChange={(e) => app.setFallback(e.target.checked)} /><span>Fall back to API key when the subscription limit is hit</span></label>
        <p className="muted">Subscription usage runs through each vendor's own CLI on your runner, under your own login and plan terms. Hosted runs use API keys.</p>
      </section>
    </div>
  );
}

function Channels({ app }: { app: AppApi }) {
  const t = app.slack;
  const [token, setToken] = useState("");
  const tog = (k: "tickets" | "updates" | "gates", label: string) => (
    <label className="toggle"><input type="checkbox" checked={t[k]} onChange={(e) => app.setSlack((x) => ({ ...x, [k]: e.target.checked }))} /><span>{label}</span></label>
  );
  return (
    <div className="conns">
      <div><h1>Channels</h1><p className="muted">The same assistant is reachable from the web app and from Slack.</p></div>
      <div className="conns__grid">
        <article className="ccard">
          <header className="ccard__h"><span className="cmark">W</span><div><div className="ccard__n">Web assistant</div><div className="ccard__k">Floating Assistant on every screen</div></div><span className="schip schip--done">always on</span></header>
          <p className="muted">Draft workflows, answer questions about runs and approve gates from the web app.</p>
        </article>
        <article className="ccard">
          <header className="ccard__h"><span className="cmark cmark--slack">S</span><div><div className="ccard__n">Slack</div><div className="ccard__k">Workspace app</div></div>{t.connected && <span className="schip schip--done">connected</span>}</header>
          {t.connected ? (
            <>
              <div className="ccard__meta"><span>Workspace <strong>ugudlado</strong> · app <strong>Orchestrator Studio</strong></span><span>Channel #orchestrator</span><span>Mode: Socket Mode (no public endpoint)</span><span>Only allow-listed members can act. Allowed: M</span></div>
              {tog("tickets", "Create Linear tickets from messages (confirm first)")}
              {tog("updates", "Run updates")}
              {tog("gates", "Gate approvals")}
              <div className="runner"><Button size="sm" variant="primary" onClick={() => app.openSlack()}>Preview conversation</Button><Button size="sm" variant="secondary" onClick={() => app.setSlack((x) => ({ ...x, connected: false }))}>Disconnect</Button></div>
            </>
          ) : (
            <form className="ccard__form" onSubmit={(e) => { e.preventDefault(); if (token.trim()) { app.setSlack((x) => ({ ...x, connected: true })); setToken(""); } }}>
              <input aria-label="Bot token" type="password" placeholder="App-level token + bot token (xapp-… / xoxb-…)" value={token} onChange={(e) => setToken(e.target.value)} />
              <Button type="submit" variant="primary" disabled={!token.trim()}>Connect Slack</Button>
              <p className="muted">Invite the app to #orchestrator and send it a message to link your account.</p>
            </form>
          )}
        </article>
      </div>
    </div>
  );
}

function Plugins({ app }: { app: AppApi }) {
  const [note, setNote] = useState<string | null>(null);
  const count = (name: string) => Object.values(app.workflows).filter((w) => w.plugin === name).length;
  const installed = [{ id: MAIN_PLUGIN, name: MAIN_PLUGIN, source: "github.com/ugudlado/workflows", version: "1.4.0", builtin: true },
    ...PLUGIN_CATALOG.filter((p) => app.installedPlugins.includes(p.id)).map((p) => ({ id: p.id, name: p.name, source: p.source, version: "1.0.0", builtin: false }))];
  const avail = PLUGIN_CATALOG.filter((p) => !app.installedPlugins.includes(p.id));
  return (
    <div className="conns">
      <div><h1>Plugins</h1><p className="muted">A plugin (pack) is a git repo of workflows and agent roles.</p></div>
      <div className="field__l">Installed</div>
      <div className="conns__list">
        {installed.map((p) => (
          <div className="plug" key={p.id}>
            <div className="plug__main"><div className="plug__n">{p.name} <span className="frow__v">v{p.version}</span></div><div className="plug__s"><code>{p.source}</code> · {count(p.name)} workflows</div>{note === p.id && <div className="plug__s" role="status">Up to date (v{p.version})</div>}</div>
            <Button size="sm" variant="secondary" onClick={() => setNote(p.id)}>Update</Button>
            <Button size="sm" variant="destructive" disabled={p.builtin} title={p.builtin ? "Runs use this plugin" : undefined} onClick={() => app.removePlugin(p.id)}>Remove</Button>
          </div>
        ))}
      </div>
      <div className="field__l">Available</div>
      <div className="conns__list">
        {avail.length === 0 && <p className="muted">Everything in the catalog is installed.</p>}
        {avail.map((p) => (
          <div className="plug" key={p.id}>
            <div className="plug__main"><div className="plug__n">{p.name}</div><div className="plug__w">{p.workflows.map((w) => w.name).join(", ")}</div><div className="plug__s">{p.blurb} · <code>{p.source}</code></div></div>
            <Button size="sm" variant="primary" onClick={() => app.installPlugin(p.id)}>Install</Button>
          </div>
        ))}
      </div>
    </div>
  );
}
