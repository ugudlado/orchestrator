import { useEffect, useRef, useState } from "react";
import type { AppApi } from "../app";
import { Button } from "../affectui/button-clean";
import { MessageActions } from "../affectui/message-actions-clean";
import { SmartRecommendation, type Recommendation } from "../affectui/smart-recommendation-clean";
import { ThinkingLoader } from "../affectui/thinking-loader-clean";
import { ToolCallCard, type ToolCallStatus } from "../affectui/tool-call-card-clean";
import { ROLES, defaultRole } from "../roles";
import { BUG_PIPELINE, SUPPORT_PIPELINE, bugIntakeRuns, bugIntakeWorkflow, supportRuns, supportTriageWorkflow } from "../seed";
import { fakeEvents, fakeTools } from "../connectors";
import { validate } from "../yaml";

export const BUG_PROMPT = "Every incoming bug report should be triaged, reproduced, fixed and opened as a PR";
export const ZD_PROMPT = "Triage support tickets from Zendesk";

type Item =
  | { k: "user"; id: string; text: string }
  | { k: "text"; id: string; text: string }
  | { k: "think"; id: string; phases: string[]; done: boolean }
  | { k: "tool"; id: string; name: string; summary: string; status: ToolCallStatus; args: unknown; result?: unknown }
  | { k: "rec"; id: string; options: Recommendation[]; title: string }
  | { k: "links"; id: string; links: { label: string; act: () => void }[] }
  | { k: "prompt"; id: string; text: string };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let uid = 0;
const nid = () => `m${++uid}`;

export function ChatPanel({ app, onClose }: { app: AppApi; onClose: () => void }) {
  const ref = useRef(app);
  ref.current = app;
  const [items, setItems] = useState<Item[]>([
    { k: "text", id: "g1", text: "Hi, I'm the orchestrator assistant. Describe a recurring problem and I'll draft a workflow, assign an agent roster, then keep running that pipeline for every request." },
    { k: "prompt", id: "g2", text: BUG_PROMPT },
    { k: "prompt", id: "g3", text: ZD_PROMPT },
  ]);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => { const el = box.current; if (el) el.scrollTop = el.scrollHeight; }, [items]);

  const push = (i: Item) => setItems((l) => [...l, i]);
  const patch = (id: string, p: Partial<Item>) => setItems((l) => l.map((x) => (x.id === id ? ({ ...x, ...p } as Item) : x)));
  const say = (t: string) => push({ k: "text", id: nid(), text: t });
  const think = async (phases: string[], ms = 1500) => {
    const id = nid(); push({ k: "think", id, phases, done: false }); await sleep(ms); patch(id, { done: true });
  };
  const tool = async (name: string, summary: string, args: unknown, result: unknown, ms = 900, during?: () => Promise<void> | void) => {
    const id = nid();
    push({ k: "tool", id, name, summary, status: "running", args });
    if (during) await during();
    await sleep(ms);
    patch(id, { status: "success", result });
  };
  const openView = (v: "workflows" | "runs") => { ref.current.setView(v); ref.current.openRun(null); ref.current.closeChatOnPhone(); };

  async function bugScript() {
    const a = () => ref.current;
    if (a().workflows["bug-intake"]) { say("bug-intake already exists in My workflows. Open it from the Workflows screen to edit it."); return; }
    await think(["Reading the problem", "Reusing steps from bugfix", "Drafting the workflow"], 1800);
    say("That is a bugfix loop with a human design gate. I'll draft it as `bug-intake` from the existing bugfix steps and show it in the graph as I go.");
    const full = bugIntakeWorkflow(a().workflows);
    const bare = { ...full, steps: full.steps.map((s) => ({ ...s, role: null })) };
    await tool("draft_workflow", "bug-intake · 8 steps", { name: "bug-intake", from: "bugfix", steps: full.steps.map((s) => s.id) },
      { workflow: "bug-intake", plugin: "My workflows", steps: 8, gates: 1, loops: ["design-review→design", "implement→design", "code-review→implement"] }, 500, async () => {
        a().setView("workflows"); a().openRun(null);
        for (let i = 1; i <= bare.steps.length; i++) {
          a().setWorkflows((w) => ({ ...w, "bug-intake": { ...bare, steps: bare.steps.slice(0, i) } }));
          a().setSelWf("bug-intake");
          await sleep(260);
        }
      });
    const roles: Record<string, string> = {};
    full.steps.forEach((s) => { const r = defaultRole(s); if (r) roles[s.id] = r; });
    roles["open-pr"] = "implementer";
    await tool("assign_roster", `${Object.keys(roles).length} steps → ${new Set(Object.values(roles)).size} roles`, { workflow: "bug-intake", roles }, { assigned: roles }, 900, async () => {
      await sleep(300);
      a().setWorkflows((w) => ({ ...w, "bug-intake": { ...w["bug-intake"], steps: w["bug-intake"].steps.map((s) => ({ ...s, role: roles[s.id] ?? s.role })) } }));
    });
    const issues = validate({ ...full, steps: full.steps.map((s) => ({ ...s, role: roles[s.id] ?? s.role })) });
    await tool("validate_workflow", issues.length ? `${issues.length} warnings` : "valid", { workflow: "bug-intake" }, { ok: !issues.some((i) => i.level === "error"), warnings: issues.map((i) => i.msg) }, 700);
    say("Roster: Explorer diagnoses, Designer writes the design, Reviewer checks design and code, Implementer builds and opens the PR, Teamlead handles the ticket. The design-signoff gate holds the impl_token, so nothing is implemented without your approval.");
    push({
      k: "rec", id: nid(), title: "Next step",
      options: [
        { id: "pipeline", label: "Standing pipeline", reason: "Run this for every new ticket. Trigger: Slack → Linear · label = bug; each message becomes a Linear ticket and starts its own run of bug-intake.", signal: 3 },
        { id: "once", label: "One run on BUG-214", reason: "Try the workflow on a single ticket first.", signal: 2 },
        { id: "draft", label: "Keep as draft", reason: "Leave it in My workflows without starting anything.", signal: 1 },
      ],
    });
  }

  async function zdScript() {
    const a = () => ref.current;
    await think(["Reading the problem", "Checking connectors", "Drafting the workflow"], 1800);
    if (!a().connectors.some((c) => c.id === "zendesk")) {
      say("I don't see a Zendesk connector yet. Support tickets aren't code, so I'll add Zendesk as a custom MCP connector.");
      await tool("add_connector", "Zendesk MCP · custom MCP", { name: "Zendesk MCP", url: "https://acme.zendesk.com/mcp", auth: "oauth" }, { connected: true, tools_discovered: fakeTools("Zendesk") }, 1200, () => {
        a().setConnectors((l) => [...l, { id: "zendesk", name: "Zendesk MCP", kind: "mcp", connected: true, meta: ["https://acme.zendesk.com/mcp", "Auth: OAuth"], events: fakeEvents("Zendesk"), actions: [], url: "https://acme.zendesk.com/mcp", auth: "oauth", tools: fakeTools("Zendesk") }]);
      });
    }
    if (a().workflows["support-triage"]) { say("support-triage already exists in My workflows."); return; }
    say("Now a non-coding workflow: read the ticket, look for similar ones, draft a reply, and wait for your sign-off before anything is posted.");
    const full = supportTriageWorkflow(a().workflows);
    const bare = { ...full, steps: full.steps.map((s) => ({ ...s, role: null })) };
    await tool("draft_workflow", "support-triage · 5 steps", { name: "support-triage", steps: full.steps.map((s) => s.id) }, { workflow: "support-triage", plugin: "My workflows", steps: 5, gates: 1 }, 500, async () => {
      a().setView("workflows"); a().openRun(null);
      for (let i = 1; i <= bare.steps.length; i++) {
        a().setWorkflows((w) => ({ ...w, "support-triage": { ...bare, steps: bare.steps.slice(0, i) } }));
        a().setSelWf("support-triage");
        await sleep(260);
      }
    });
    const roles: Record<string, string> = { "load-ticket-context": "teamlead", explore: "explorer", "draft-reply": "designer" };
    await tool("assign_roster", "3 steps → 3 roles", { workflow: "support-triage", roles }, { assigned: roles }, 800, async () => {
      await sleep(300);
      a().setWorkflows((w) => ({ ...w, "support-triage": { ...w["support-triage"], steps: w["support-triage"].steps.map((s) => ({ ...s, role: roles[s.id] ?? s.role })) } }));
    });
    const issues = validate({ ...full, steps: full.steps.map((s) => ({ ...s, role: roles[s.id] ?? s.role })) });
    await tool("validate_workflow", issues.length ? `${issues.length} warnings` : "valid", { workflow: "support-triage" }, { ok: true, warnings: issues.map((i) => i.msg) }, 700);
    say("The reply is drafted by the Designer role but never posted without your approval: post-reply requires the reply_token minted at reply-signoff.");
    push({
      k: "rec", id: nid(), title: "Next step",
      options: [
        { id: "zd:pipeline", label: "Zendesk pipeline", reason: "Run this for every new ticket. Trigger: Zendesk · Ticket created · priority != urgent.", signal: 3 },
        { id: "zd:once", label: "One run on ZD-4821", reason: "Try it on a single ticket first.", signal: 2 },
        { id: "zd:draft", label: "Keep as draft", reason: "Leave it in My workflows.", signal: 1 },
      ],
    });
  }

  async function zdAccept(o: Recommendation) {
    const a = () => ref.current;
    if (o.id === "zd:draft") { say("Kept support-triage as a draft in My workflows."); return; }
    const w = a().workflows["support-triage"];
    if (o.id === "zd:once") {
      await tool("start_run", "support-triage · ZD-4821", { workflow: "support-triage", ticket: "ZD-4821" }, { run: "ZD-4821", answer: "ready" }, 600);
      const id = a().startRun("support-triage", "ZD-4821");
      say("Started ZD-4821.");
      push({ k: "links", id: nid(), links: [{ label: "Open run", act: () => { a().openRun(id); a().closeChatOnPhone(); } }] });
      return;
    }
    await tool("create_pipeline", "Support triage · Zendesk · Ticket created · priority != urgent", { name: SUPPORT_PIPELINE.name, workflow: "support-triage", trigger: { connector: "zendesk", event: "Ticket created", filter: "priority != urgent" } }, { pipeline: SUPPORT_PIPELINE.id, enabled: true }, 800, () => {
      if (!a().pipelines.some((p) => p.id === SUPPORT_PIPELINE.id)) a().setPipelines((p) => [SUPPORT_PIPELINE, ...p]);
    });
    const rs = supportRuns(w);
    await tool("start_run", "2 tickets matched the trigger", { workflow: "support-triage", tickets: rs.map((r) => r.slug) }, { started: rs.map((r) => ({ run: r.slug, status: r.status })) }, 900, () => {
      a().setRuns((m) => { const n = { ...m }; rs.forEach((r) => (n[r.id] = r)); return n; });
      a().setRunOrder((o2) => [...rs.map((r) => r.id), ...o2.filter((x) => !rs.some((r) => r.id === x))]);
    });
    say("Pipeline added. ZD-4821 has a drafted reply waiting at reply-signoff; ZD-4818 was already answered.");
    push({ k: "links", id: nid(), links: [{ label: "Open Runs", act: () => openView("runs") }, { label: "Review ZD-4821", act: () => { a().openRun("rz4821"); a().closeChatOnPhone(); } }] });
  }

  async function accept(o: Recommendation) {
    const a = () => ref.current;
    setBusy(true);
    try {
      if (o.id.startsWith("zd:")) { await zdAccept(o); return; }
      if (o.id === "draft") { say("Kept bug-intake as a draft in My workflows."); return; }
      const w = a().workflows["bug-intake"];
      if (o.id === "once") {
        await tool("start_run", "bug-intake · BUG-214", { workflow: "bug-intake", ticket: "BUG-214" }, { run: "BUG-214", answer: "ready", step: "load-ticket-context" }, 600);
        const id = a().startRun("bug-intake", "BUG-214");
        say("Started BUG-214. I'll stop at design-signoff for your approval.");
        push({ k: "links", id: nid(), links: [{ label: "Open run", act: () => { a().setView("runs"); a().openRun(id); a().closeChatOnPhone(); } }] });
        return;
      }
      await tool("create_pipeline", "Bug intake · Slack → Linear · label = bug", { name: BUG_PIPELINE.name, workflow: "bug-intake", trigger: { connector: "slack", event: "Message → Linear ticket", filter: "label = bug" } }, { pipeline: BUG_PIPELINE.id, enabled: true }, 800, () => {
        if (!a().pipelines.some((p) => p.id === BUG_PIPELINE.id)) a().setPipelines((p) => [BUG_PIPELINE, ...p]);
      });
      const rs = bugIntakeRuns(w);
      await tool("start_run", "3 tickets matched the trigger", { workflow: "bug-intake", tickets: rs.map((r) => r.slug) }, { started: rs.map((r) => ({ run: r.slug, status: r.status })) }, 900, () => {
        a().setRuns((m) => { const n = { ...m }; rs.forEach((r) => (n[r.id] = r)); return n; });
        a().setRunOrder((o2) => [...rs.map((r) => r.id), ...o2.filter((x) => !rs.some((r) => r.id === x))]);
      });
      say("Pipeline added. BUG-212 is waiting at design-signoff, BUG-214 is exploring, and BUG-209 already finished with a PR. Every bug message in #orchestrator will become a Linear ticket and start a run on its own.");
      push({ k: "links", id: nid(), links: [{ label: "Open Runs", act: () => openView("runs") }, { label: "Approve BUG-212", act: () => { a().setView("runs"); a().openRun("rb212"); a().closeChatOnPhone(); } }] });
    } finally { setBusy(false); }
  }

  async function whyReview() {
    const a = ref.current;
    const run = a.openRunId ? a.runs[a.openRunId] : null;
    if (!run) { say("Open a run first and ask again, I answer from its recorded history. I can see " + Object.values(a.runs).filter((r) => r.status === "needs_you").map((r) => r.slug).join(", ") + " is stuck."); return; }
    await think(["Reading run history"], 900);
    const fails = run.calls.filter((c) => c.step === "code-review" && c.status === "error");
    await tool("read_run_history", `${run.slug} · ${run.calls.length} calls`, { run: run.slug, step: "code-review" }, { failures: fails.length, last: fails[fails.length - 1]?.result ?? null }, 500);
    if (!fails.length) { say(`code-review has not failed in ${run.slug} so far. ${run.calls.some((c) => c.step === "code-review") ? "Its verdict was pass." : "It has not run yet."}`); return; }
    const l = fails[fails.length - 1];
    const v = (l.result?.outs as Record<string, unknown> | undefined)?.verdict;
    const stuck = run.status === "needs_you";
    say(`code-review (attempt ${l.attempt}, call #${l.n}) recorded verdict=${String(v)}. verdict is in fail_on, so the engine routed the run back to implement via on_failure (retries ${String(l.result?.retries ?? `${run.retries["code-review"]}`)}). ${stuck ? "Retries are exhausted, so the driver returned needs_you. Reset retries from the notice on the timeline to continue." : "On the next pass the reviewer returned pass and the run moved on."} The full verdict is in code-review.md under runs/${run.slug}/.`);
  }

  async function needsMe() {
    const rs = Object.values(ref.current.runs).filter((r) => r.status === "waiting" || r.status === "needs_you");
    await think(["Scanning runs"], 700);
    say(rs.length ? `${rs.length} run${rs.length > 1 ? "s" : ""} need you: ${rs.map((r) => `${r.slug} (${r.status === "waiting" ? "approve the gate" : "retries exhausted"})`).join(", ")}.` : "Nothing is waiting on you right now.");
  }

  async function send(t: string) {
    const q = t.trim();
    if (!q || busy) return;
    setText(""); setBusy(true);
    push({ k: "user", id: nid(), text: q });
    try {
      const a = ref.current;
      if (/zendesk|support ticket/i.test(q)) await zdScript();
      else if (/bug|triag|incoming|report/i.test(q) && /every|each|all|pipeline|should/i.test(q)) await bugScript();
      else if (/why/i.test(q) && /review|fail/i.test(q)) await whyReview();
      else if (/need|waiting|stuck|me\b/i.test(q)) await needsMe();
      else {
        await think(["Thinking"], 800);
        say(a.view === "workflows" ? `You're on ${a.selWf}${a.selStep ? ` (${a.selStep})` : ""}. I can draft a new workflow from a problem statement, tweak roles or explain a step. Try: "${BUG_PROMPT}".`
          : a.view === "runs" ? "I can start a run, or tell you what needs you. Try \"What needs me right now?\"."
          : "Roles map judgment steps to model tiers. Change a tier on a card and every step it covers follows.");
      }
    } finally { setBusy(false); }
  }

  const ctx = app.view === "runs" && app.openRunId && app.runs[app.openRunId] ? `Run ${app.runs[app.openRunId].slug}` : app.view === "workflows" ? `Workflow ${app.selWf}` : app.view === "runs" ? "Runs" : app.view === "settings" ? "Settings" : "Agents";
  const suggestions = app.view === "workflows" ? [BUG_PROMPT, ZD_PROMPT] : app.openRunId ? ["Why did code-review fail?", "What needs me right now?"] : ["What needs me right now?"];

  return (
    <div className="chat">
      <header className="chat__h">
        <div><div className="chat__t">Assistant</div><div className="chat__c">Context: {ctx}</div></div>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close assistant">Close</Button>
      </header>
      <div className="chat__body" ref={box} aria-live="polite">
        {!(app.runner.paired || app.accounts.some((a) => a.connected)) && (
          <div className="msg"><SmartRecommendation key={app.view} title="Connect a runner to run workflows" defaultOpen
            options={[{ id: "connect", label: "Runner setup", reason: "Pair a runner on your machine or VM, or add an API key for hosted runs. Without one no step can run.", signal: 3 }]}
            onAccept={() => { app.openSettings("models"); app.closeChatOnPhone(); }} /></div>
        )}
        {items.map((m) => {
          switch (m.k) {
            case "user": return <div key={m.id} className="msg msg--user">{m.text}</div>;
            case "text": return (
              <div key={m.id} className="msg msg--bot" data-ui-message>
                <p>{m.text}</p>
                <MessageActions text={m.text} actions={["copy", "regenerate", "feedback"]} />
              </div>);
            case "think": return <div key={m.id} className="msg"><ThinkingLoader phases={m.phases} interval={650} done={m.done} doneLabel={(s) => `Thought for ${s}s`} size="sm" /></div>;
            case "tool": return <div key={m.id} className="msg"><ToolCallCard name={m.name} summary={m.summary} status={m.status} args={m.args} result={m.result} /></div>;
            case "rec": return <div key={m.id} className="msg"><SmartRecommendation title={m.title} options={m.options} onAccept={accept} /></div>;
            case "links": return <div key={m.id} className="msg msg--links">{m.links.map((l) => <Button key={l.label} size="sm" variant="secondary" onClick={l.act}>{l.label}</Button>)}</div>;
            case "prompt": return <button key={m.id} className="msg promptbtn" disabled={busy} onClick={() => send(m.text)}><span>Try this</span>{m.text}</button>;
          }
        })}
      </div>
      <div className="chat__f">
        <div className="chat__sug">{suggestions.map((s) => <button key={s} className="tchip" disabled={busy} onClick={() => send(s)}>{s.length > 46 ? s.slice(0, 44) + "…" : s}</button>)}</div>
        <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="chat__form">
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask or describe a problem…" aria-label="Message" />
          <Button type="submit" size="sm" variant="primary" disabled={busy || !text.trim()}>Send</Button>
        </form>
        <button className="chat__also" onClick={() => { app.openSettings("channels"); app.closeChatOnPhone(); }}>
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden><path d="M6.2 2 5 14M11 2l-1.2 12M2.5 5.8h11M2 10.2h11" /></svg>Also in Slack</button>
      </div>
    </div>
  );
}
export { ROLES };
