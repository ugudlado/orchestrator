import type { Pipeline } from "./types";
export interface Connector {
  id: string; name: string; kind: "linear" | "github" | "mcp" | "slack"; connected: boolean;
  meta: string[]; events: string[]; actions: string[]; url?: string; auth?: string; tools?: string[];
}
export interface Source { connector: string; label: string; url: string }

export const SEED_CONNECTORS: Connector[] = [
  { id: "linear", name: "Linear", kind: "linear", connected: true, meta: ["Workspace ugudlado", "Team ORC"], events: ["Issue created", "Label added", "Status changed"], actions: ["read issue", "comment", "move status"] },
  { id: "slack", name: "Slack", kind: "slack", connected: true, meta: ["Workspace ugudlado", "App Orchestrator Studio", "#orchestrator"], events: ["Message → Linear ticket", ":bug: reaction on a message"], actions: ["post message", "reply in thread", "approval buttons"] },
  { id: "github", name: "GitHub", kind: "github", connected: true, meta: ["ugudlado/orchestrator", "ugudlado/workflows"], events: ["Issue opened", "PR review", "CI failed"], actions: ["open PR", "comment", "read checks"] },
];

export function fakeTools(name: string): string[] {
  return /zendesk/i.test(name) ? ["search_tickets", "get_ticket", "add_note"] : /jira/i.test(name) ? ["search_issues", "get_issue", "add_comment"] : ["list_items", "get_item", "add_note"];
}
export function fakeEvents(name: string): string[] {
  return /zendesk/i.test(name) ? ["Ticket created", "Ticket updated"] : ["Item created", "Item updated"];
}
export const slugId = (name: string) => name.toLowerCase().replace(/\s*mcp\s*$/i, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "custom";

export function makeSource(conn: string, slug: string): Source {
  const n = slug.match(/\d+/)?.[0] ?? "42";
  if (conn === "slack") return { connector: conn, label: `Slack → Linear ${slug}`, url: `https://linear.app/ugudlado/issue/${slug}` };
  if (conn === "github") return { connector: conn, label: `GitHub #${n}`, url: `https://github.com/ugudlado/orchestrator/issues/${n}` };
  if (conn === "linear") return { connector: conn, label: `Linear ${slug}`, url: `https://linear.app/ugudlado/issue/${slug}` };
  return { connector: conn, label: `${conn[0].toUpperCase()}${conn.slice(1)} ${slug}`, url: `https://acme.${conn}.com/agent/tickets/${n}` };
}

export const connName = (id: string, list: Connector[]) => (id === "manual" ? "Manual" : list.find((c) => c.id === id)?.name ?? id);
export const connKind = (id: string, list: Connector[]) => (id === "manual" ? "manual" : list.find((c) => c.id === id)?.kind ?? "mcp");
export function triggerText(p: Pipeline, list: Connector[]) {
  if (p.connector === "slack") return p.event.startsWith(":bug:") ? `Slack · ${p.event}${p.filter ? ` · ${p.filter}` : ""}` : `Slack → Linear${p.filter ? ` · ${p.filter}` : ""}`;
  return p.connector === "manual" ? "Manual · Start run" : `${connName(p.connector, list)} · ${p.event}${p.filter ? ` · ${p.filter}` : ""}`;
}

export function stepAction(step: string, src?: Source): string | null {
  const c = src?.connector;
  if (step === "open-pr") return "github.open_pr";
  if (!c) return null;
  if (c === "zendesk") return ({ "load-ticket-context": "zendesk.get_ticket", explore: "zendesk.search_tickets", "post-reply": "zendesk.add_note" } as Record<string, string>)[step] ?? null;
  const m: Record<string, [string, string]> = {
    "ticket-start": ["move_status → In Progress", 'comment → "Started"'],
    "ticket-review": ["move_status → In Review", 'comment → "Ready for review"'],
    "ticket-qa": ["move_status → QA", 'comment → "QA passed"'],
    "ticket-done": ["move_status → Done", 'comment → "Closed by PR"'],
  };
  if (step === "load-ticket-context") return `${c}.read_issue`;
  const e = m[step];
  return e ? `${c}.${c === "linear" ? e[0] : e[1]}` : null;
}

export const BINDINGS: Record<string, { provider: string; via: string; command?: string }> = {
  ticket: { provider: "Linear", via: "mcp" },
  forge: { provider: "GitHub", via: "cli", command: "gh" },
  chat: { provider: "Slack", via: "mcp" },
};
export function bindingsFor(tools?: string[]): { role: string; provider: string; via: string }[] {
  const roles = [...new Set((tools ?? []).map((t) => t.split(".")[0]).filter((r) => r in BINDINGS))];
  return roles.map((r) => ({ role: r, provider: BINDINGS[r].provider, via: BINDINGS[r].via }));
}
