import type { Workflow } from "../types";
import type { LiveComment, LiveHealth, LiveRunList } from "./types";

async function req<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json", "X-Studio": "1" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new Error((j as { error?: string } | null)?.error ?? `${r.status} ${r.statusText}`);
  return j as T;
}

/** The answer at /api/health did not come from a Studio server (404, or not JSON: static hosting). The app falls back to simulation only for this. */
export class NotStudioServer extends Error {}
export async function getHealth(timeoutMs = 10000): Promise<LiveHealth> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch("/api/health", { signal: ctl.signal });
    if (r.status === 404) throw new NotStudioServer("no /api/health here");
    const j = (await r.json().catch(() => null)) as (LiveHealth & { error?: string }) | null;
    if (!r.ok) throw new Error(j?.error ?? `${r.status} ${r.statusText}`.trim());
    if (!j || typeof j !== "object" || typeof j.ok !== "boolean") throw new NotStudioServer("/api/health is not a Studio server");
    return j;
  } catch (e) {
    if (ctl.signal.aborted) throw new Error(`no answer after ${Math.round(timeoutMs / 1000)} s`);
    throw e;
  } finally { clearTimeout(t); }
}
export const getWorkflows = () => req<Record<string, Workflow>>("/api/workflows");
export const getRuns = () => req<Partial<LiveRunList>>("/api/runs").then((d): LiveRunList => ({ runs: d.runs ?? [], stale: !!d.stale, error: d.error ?? null }));
export const startLiveRun = (b: { workflow: string; slug: string; ticket: string; repo: string }) => req<{ ok: true }>("/api/runs", b);
export const sendGate = (slug: string, b: { gate: string; attempt?: number; decision: "approved" | "rejected"; note?: string; reset_to?: string; include_comments?: boolean }) => req<{ ok: true }>(`/api/runs/${encodeURIComponent(slug)}/gate`, b);
export const sendAnswer = (slug: string, b: { step_id: string; wait?: number; answer?: string; reset_to?: string; include_comments?: boolean }) => req<{ ok: true }>(`/api/runs/${encodeURIComponent(slug)}/answer`, b);
export const getArtifact = async (slug: string, path: string): Promise<string> => {
  const r = await fetch(`/api/runs/${encodeURIComponent(slug)}/artifacts/${path.split("/").map(encodeURIComponent).join("/")}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.text();
};
const cbase = (slug: string) => `/api/runs/${encodeURIComponent(slug)}/comments`;
export const getComments = (slug: string) => req<{ comments: LiveComment[] }>(cbase(slug)).then((d) => d.comments);
export const addLiveComment = (slug: string, b: { text: string; file?: string; quote?: string; line?: number }) => req<LiveComment>(cbase(slug), b);
export const deleteLiveComment = (slug: string, id: string) => req<{ ok: true }>(`${cbase(slug)}/${encodeURIComponent(id)}/delete`, {});

/** Re-check one project's MCP servers now. The server takes up to about 90 seconds to answer. */
export const checkProject = (path: string) => req<{ ok: true }>("/api/projects/check", { path });
