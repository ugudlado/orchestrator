/** Shapes served by spike/inngest_driver/studio_server.py. Keep in step with it. */

export type LiveStatus = "queued" | "running" | "waiting" | "needs_you" | "done" | "failed";

export interface LiveHistoryEntry {
  step: string;
  attempt: number;
  kind?: "exec" | "judgment" | "gate";
  /** completed | failed for steps; gates record completed (approved) or failed (rejected) */
  status: string;
  route?: string;
  gate?: "approved" | "rejected" | null;
  note?: string | null;
  exit_code?: number;
  model?: string | null;
  cost_usd?: number | null;
  /** the step's own one-line summary of what it did (judgment steps) */
  summary?: string | null;
  /** recorded outputs: enum values, artifact paths */
  outs?: Record<string, unknown>;
  /** last lines an exec step printed */
  stdout_tail?: string;
}

export interface LiveAttention {
  kind: "gate" | "question";
  step_id: string;
  /** gate visit number; a gate decision must echo it */
  attempt?: number;
  /** question number; an answer must echo it */
  wait?: number;
  /** gate: artifact name -> path relative to the run's artifacts dir */
  show?: Record<string, string>;
  question?: string;
  /** why the engine stopped here (e.g. retries exhausted on a step); may be all there is when `question` is empty */
  reason?: string;
  at: string;
}

export interface LiveRun {
  slug: string;
  workflow: string;
  title: string;
  ticket: string;
  status: LiveStatus;
  /** what the driver returned when it ended: done, error, gate_timeout, refused, loop_guard */
  outcome: string | null;
  started_at: string | null;
  ended_at: string | null;
  inngest: { run_id: string | null; status: string | null; trace_url: string | null };
  history: LiveHistoryEntry[];
  /** the step running right now, if any */
  current: { step: string; kind: "exec" | "judgment" } | null;
  attention: LiveAttention | null;
  tokens: string[];
  artifacts: { name: string; size: number; mtime: string }[];
  cost_usd: number;
  /** the project path the run ran against */
  repo: string | null;
}

export type LiveMcpState = "connected" | "needs_auth" | "failed" | "pending";
export interface LiveMcp {
  name: string; type: "stdio" | "http" | "sse" | (string & {}); what: string;
  /** result of the last Claude Code check; absent when the server has not been checked yet */
  state?: LiveMcpState;
  /** the CLI's own text for the state, e.g. "Failed to connect: ENOENT ..." */
  detail?: string;
  /** env var names the server's config refers to that are not set */
  missing?: string[];
}
export interface LiveProject { name: string; path: string; mcp: LiveMcp[];
  /** unix seconds of the last check; null when never checked */
  mcp_checked_at?: number | null;
  /** why the check could not run, e.g. the claude CLI was not found */
  mcp_error?: string | null; has_mcp_json: boolean; has_codex_config: boolean; has_bindings: boolean }
export interface LiveHealth { ok: boolean; inngest: boolean; inngest_error?: string | null; ui_url: string; repos: string[]; projects?: LiveProject[]; workflows: string[]; runs_root?: string; pack?: string }

export interface LiveRunList { runs: LiveRun[]; stale: boolean; error: string | null }

/** A review comment on a run: on a whole artifact (no quote) or a passage of it. Kept by the server until sent with a gate rejection or an answer, or closed (not sent) by an approval. */
export interface LiveComment {
  id: string;
  file: string | null;
  quote: string | null;
  line: number | null;
  text: string;
  at: string;
  sent: null | { via: "gate" | "answer" | "approve"; step: string; attempt?: number; wait?: number; at: string };
}
