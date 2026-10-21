import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AppApi, View } from "./app";
import { Button } from "./affectui/button-clean";
import { SegmentedControl } from "./affectui/segmented-control-clean";
import { ROLES, type Tier } from "./roles";
import type { Comment } from "./files";
import { SettingsView } from "./views/SettingsView";
import { SEED_ACCOUNTS, SEED_ROUTING, SEED_RUNNER, type Account } from "./accounts";
import { PLUGIN_CATALOG } from "./seed";
import type { SettingsTab, Slack } from "./app";
import { SlackPreview } from "./views/SlackPreview";
import { MAIN_PLUGIN, SEED_PIPELINES, loadWorkflows, seedRuns } from "./seed";
import { startRun, tick } from "./sim/engine";
import { devAssign } from "./seed";
import type { Pipeline, Step, Workflow } from "./types";
import { defaultRole } from "./roles";
import { NotStudioServer, getHealth, getRuns, getWorkflows } from "./live/api";
import { LIVE_PIPELINE, convertRun } from "./live/convert";
import type { LiveHealth } from "./live/types";
import { AgentsView } from "./views/AgentsView";
import { ChatPanel } from "./views/ChatPanel";
import { RunDetail } from "./views/RunDetail";
import { ProjectsView } from "./views/ProjectsView";
import { RunsView } from "./views/RunsView";
import { WorkflowsView } from "./views/WorkflowsView";

const readTheme = (): boolean => {
  const t = document.documentElement.getAttribute("data-theme");
  if (t === "dark") return true;
  if (t === "light") return false;
  return !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
};

export function App() {
  const [dark, setDark] = useState(readTheme);
  const [view, setView] = useState<View>("workflows");
  const [workflows, setWorkflows] = useState(loadWorkflows);
  const [selWf, setSelWf] = useState("dev");
  const [selStep, setSelStep] = useState<string | null>(null);
  const seeded = useMemo(() => seedRuns(workflows), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [runs, setRuns] = useState(() => Object.fromEntries(seeded.map((r) => [r.id, r])));
  const [runOrder, setRunOrder] = useState(() => seeded.map((r) => r.id));
  const [pipelines, setPipelines] = useState<Pipeline[]>(SEED_PIPELINES);
  const [openRunId, setOpenRunId] = useState<string | null>(null);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [tiers, setTiers] = useState<Record<string, Tier>>(() => Object.fromEntries(ROLES.map((r) => [r.id, r.tier])));
  const [chatOpen, setChatOpen] = useState(false);
  const [projectFilter, setProjectFilter] = useState<string | null>(null);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("models");
  const [accounts, setAccounts] = useState<Account[]>(SEED_ACCOUNTS);
  const [slack, setSlack] = useState<Slack>({ connected: true, tickets: true, updates: true, gates: true });
  const [tg, setTg] = useState<{ n: number; runId?: string } | null>(null);
  const [runner, setRunner] = useState(SEED_RUNNER);
  const [routing, setRouting] = useState(SEED_ROUTING);
  const [fallback, setFallback] = useState(true);
  const [installedPlugins, setInstalledPlugins] = useState<string[]>([]);
  const [comments, setComments] = useState<Comment[]>([]);
  const [probed, setProbed] = useState(false);
  const [health, setHealth] = useState<LiveHealth | null>(null);
  const [bootErr, setBootErr] = useState<string | null>(null);
  const [inngestErr, setInngestErr] = useState<{ error: string } | null>(null);
  const [serverErr, setServerErr] = useState<string | null>(null);
  const live = !!health;
  const wfRef = useRef(workflows);
  wfRef.current = workflows;
  const userTheme = useRef(false);

  useEffect(() => { document.documentElement.setAttribute("data-theme", dark ? "dark" : "light"); }, [dark]);
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    const h = () => { if (!userTheme.current) setDark(mq.matches); };
    mq?.addEventListener?.("change", h);
    return () => mq?.removeEventListener?.("change", h);
  }, []);

  // Boot: simulation only when there is no Studio server (404, or an answer that is not ours). Any other failure stays live and retries.
  useEffect(() => {
    let off = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const attempt = async () => {
      try {
        const h = await getHealth(10000);
        if (!h.ok) throw new Error(h.inngest_error || "the server says it is not ready");
        const raw = await getWorkflows();
        if (off) return;
        const wfs: Record<string, Workflow> = {};
        for (const [k, w] of Object.entries(raw)) wfs[k] = { ...w, name: w.name ?? k, plugin: MAIN_PLUGIN, steps: (w.steps as Step[]).map((x) => ({ ...x, role: defaultRole(x) })) };
        wfRef.current = wfs;
        setWorkflows(wfs); setSelWf(wfs.dev ? "dev" : Object.keys(wfs)[0] ?? "dev");
        setRuns({}); setRunOrder([]); setPipelines([LIVE_PIPELINE]);
        setBootErr(null); setHealth(h); setProbed(true);
      } catch (e) {
        if (off) return;
        if (e instanceof NotStudioServer) { setProbed(true); return; }
        setBootErr((e as Error).message || "unknown error"); setProbed(true);
        timer = setTimeout(attempt, 5000);
      }
    };
    attempt();
    return () => { off = true; clearTimeout(timer); };
  }, []);

  const polling = useRef(false);
  const pollRuns = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try { await pollOnce(); } finally { polling.current = false; }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const pollOnce = async () => {
    const [h, r] = await Promise.allSettled([getHealth(10000), getRuns()]);
    if (h.status === "fulfilled") { setHealth(h.value); setServerErr(null); }
    else setServerErr((h.reason as Error).message || "unknown error");
    if (r.status === "fulfilled") {
      const rs = r.value.runs.map((x) => convertRun(x, wfRef.current[x.workflow]));
      setRuns(Object.fromEntries(rs.map((x) => [x.id, x]))); setRunOrder(rs.map((x) => x.id));
    }
    const msg = r.status === "rejected" ? (r.reason as Error).message : r.value.stale ? r.value.error : null;
    if (h.status === "fulfilled" && h.value.inngest === false) setInngestErr({ error: h.value.inngest_error || msg || "unreachable" });
    else if (h.status === "fulfilled" && (r.status === "rejected" || r.value.stale)) setInngestErr({ error: msg || "unreachable" });
    else if (h.status === "fulfilled") setInngestErr(null);
  };
  useEffect(() => {
    if (!live) return;
    pollRuns();
    const id = setInterval(pollRuns, 3000);
    return () => clearInterval(id);
  }, [live, pollRuns]);

  useEffect(() => {
    if (!playing || live) return;
    const id = setInterval(() => {
      setRuns((rs) => {
        let changed = false;
        const next = { ...rs };
        for (const r of Object.values(rs)) {
          if (r.status !== "running") continue;
          const wf = wfRef.current[r.workflow];
          if (!wf) continue;
          next[r.id] = tick(r, wf); changed = true;
        }
        return changed ? next : rs;
      });
    }, 900 / speed);
    return () => clearInterval(id);
  }, [playing, speed, live]);

  const api: AppApi = {
    dark, view, setView: (v) => { setView(v); if (v !== "runs") setOpenRunId(null); },
    workflows, setWorkflows, selWf, setSelWf, selStep, setSelStep,
    runs, runOrder, setRuns, setRunOrder, pipelines, setPipelines,
    openRunId, openRun: (id) => { setOpenRunId(id); if (id) setView("runs"); },
    playing, setPlaying, speed, setSpeed,
    tiers, setTier: (role, t) => setTiers((m) => ({ ...m, [role]: t })),
    slack, setSlack, openSlack: (runId) => setTg((t) => ({ n: (t?.n ?? 0) + 1, runId })),
    startRun: (workflow, slug, opts) => {
      const wf = workflows[workflow];
      const id = `r${Date.now()}`;
      let pipe = pipelines.find((p) => p.workflow === workflow);
      if (!pipe) { pipe = { id: `p-manual-${workflow}`, name: `${workflow} (manual)`, workflow }; const np = pipe; setPipelines((p) => [...p, np]); }
      const run = startRun(wf, { id, slug, pipelineId: pipe.id, title: opts?.title ?? "New run", failOnce: workflow === "feature" || workflow === "dev" || workflow === "bug-intake" ? ["code-review"] : [], assign: workflow === "dev" ? devAssign(slug) : undefined });
      setRuns((m) => ({ ...m, [id]: run }));
      setRunOrder((o) => [id, ...o]);
      setOpenRunId(id); setView("runs"); setPlaying(true);
      return id;
    },
    closeChatOnPhone: () => { if (window.innerWidth < 900) setChatOpen(false); },
    projectFilter, setProjectFilter, comments, live, health, refreshRuns: pollRuns,
    settingsTab, setSettingsTab, openSettings: (t) => { setSettingsTab(t); setOpenRunId(null); setView("settings"); },
    runner, setRunner, accounts, setAccounts, routing, setRouting, fallback, setFallback, installedPlugins,
    installPlugin: (id) => {
      const p = PLUGIN_CATALOG.find((x) => x.id === id)!;
      setWorkflows((all) => { const n = { ...all }; p.workflows.forEach((w) => (n[w.name] = structuredClone(w))); return n; });
      setInstalledPlugins((l) => (l.includes(id) ? l : [...l, id]));
    },
    removePlugin: (id) => {
      const p = PLUGIN_CATALOG.find((x) => x.id === id)!;
      setWorkflows((all) => { const n = { ...all }; p.workflows.forEach((w) => { if (!Object.values(runs).some((r) => r.workflow === w.name)) delete n[w.name]; }); return n; });
      setInstalledPlugins((l) => l.filter((x) => x !== id));
      if (p.workflows.some((w) => w.name === selWf)) setSelWf("dev");
    },
    addComment: (c) => setComments((l) => [...l, { ...c, id: `c${Date.now()}${l.length}`, at: "just now" }]),
  };

  const needs = Object.values(runs).filter((r) => r.status === "waiting" || r.status === "needs_you").length;
  const openRun = openRunId ? runs[openRunId] : null;

  if (!probed) return <div className="frame" aria-busy="true" />;
  if (bootErr && !live) return (
    <div className="frame"><div className="shell">
      <header className="top"><div className="brand"><span className="brand__mark" aria-hidden />Orchestrator Studio</div></header>
      <div className="banner" role="alert">Can't reach the Studio server: {bootErr}. Retrying…</div>
    </div></div>
  );
  const banner = !live ? null : serverErr ? `Can't reach the Studio server: ${serverErr}. Retrying…`
    : inngestErr ? `Inngest isn't responding (${inngestErr.error}); showing ${runOrder.length ? "the last known state" : "no runs yet"}` : null;
  return (
    <div className="frame">
      <div className="shell">
        <header className="top">
          <div className="brand"><span className="brand__mark" aria-hidden />Orchestrator Studio</div>
          <SegmentedControl aria-label="Sections" variant="tray" size="md" value={(view === "settings" ? "" : view) as View} onChange={(v) => api.setView(v)}
            options={[{ value: "workflows", label: "Workflows" }, { value: "runs", label: "Runs", count: needs || undefined }, ...(live ? [{ value: "projects" as View, label: "Projects" }] : []), { value: "agents", label: "Agents" }]} />
          <div className="top__sp" />
          <Button size="sm" variant={view === "settings" ? "secondary" : "ghost"} onClick={() => api.openSettings(settingsTab)} aria-label="Settings" aria-pressed={view === "settings"}
            icon={<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden><circle cx="8" cy="8" r="2.2" /><path d="M8 1.5v1.7M8 12.8v1.7M1.5 8h1.7M12.8 8h1.7M3.4 3.4l1.2 1.2M11.4 11.4l1.2 1.2M12.6 3.4l-1.2 1.2M4.6 11.4l-1.2 1.2" /></svg>} />
          <Button size="sm" variant="ghost" onClick={() => { userTheme.current = true; setDark((d) => !d); }} aria-label="Toggle theme">{dark ? "Light" : "Dark"}</Button>
        </header>
        {banner && <div className="banner" role="alert">{banner}</div>}
        <main className="main">
          {view === "workflows" && <WorkflowsView app={api} />}
          {view === "runs" && (openRun && workflows[openRun.workflow] ? <RunDetail app={api} run={openRun} /> : <RunsView app={api} />)}
          {view === "projects" && <ProjectsView app={api} />}
          {view === "agents" && <AgentsView app={api} />}
          {view === "settings" && <SettingsView app={api} />}
        </main>
      </div>
      {!live && tg && <SlackPreview key={tg.n} app={api} runId={tg.runId} onClose={() => setTg(null)} />}
      {!live && <>
      {chatOpen && <div className="chatback" onClick={() => setChatOpen(false)} aria-hidden />}
      <button className="fab" data-open={chatOpen || undefined} onClick={() => setChatOpen(true)} aria-label={needs ? `Open assistant, ${needs} runs waiting on you` : "Open assistant"}>
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M8 1.8 9.6 6l4.2 1.6L9.6 9.2 8 13.4 6.4 9.2 2.2 7.6 6.4 6Z" /></svg>
        <span>Assistant</span>
        {needs > 0 && <b className="fab__badge" title={`${needs} waiting on you`}>{needs}</b>}
      </button>
      <aside className="chatdock" data-open={chatOpen || undefined} aria-label="Assistant" inert={!chatOpen}>
        <ChatPanel app={api} onClose={() => setChatOpen(false)} />
      </aside>
      </>}
    </div>
  );
}
