import type { Workflow } from "../types";
import type { LiveHealth, LiveRun } from "./types";

async function req<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json", "X-Studio": "1" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new Error((j as { error?: string } | null)?.error ?? `${r.status} ${r.statusText}`);
  return j as T;
}

export const getHealth = () => req<LiveHealth>("/api/health");
export const getWorkflows = () => req<Record<string, Workflow>>("/api/workflows");
export const getRuns = () => req<{ runs: LiveRun[] }>("/api/runs").then((d) => d.runs);
export const startLiveRun = (b: { workflow: string; slug: string; ticket: string; repo: string }) => req<{ ok: true }>("/api/runs", b);
export const sendGate = (slug: string, b: { gate: string; attempt?: number; decision: "approved" | "rejected"; note?: string; reset_to?: string }) => req<{ ok: true }>(`/api/runs/${encodeURIComponent(slug)}/gate`, b);
export const sendAnswer = (slug: string, b: { step_id: string; wait?: number; answer: string; reset_to?: string }) => req<{ ok: true }>(`/api/runs/${encodeURIComponent(slug)}/answer`, b);
export const getArtifact = async (slug: string, path: string): Promise<string> => {
  const r = await fetch(`/api/runs/${encodeURIComponent(slug)}/artifacts/${path.split("/").map(encodeURIComponent).join("/")}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.text();
};
