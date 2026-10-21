import type { Run } from "./sim/engine";
import type { Connector, Source } from "./connectors";
export interface Slack { connected: boolean; tickets: boolean; updates: boolean; gates: boolean }
import type { Comment } from "./files";
import type { Pipeline, Workflow } from "./types";
import type { Tier } from "./roles";
import type { LiveHealth } from "./live/types";
import type { Account, AccountId, Runner } from "./accounts";

export type View = "workflows" | "runs" | "agents" | "settings"
export type SettingsTab = "models" | "plugins" | "channels" | "connectors" | "agents";
export interface AppApi {
  dark: boolean;
  view: View;
  setView: (v: View) => void;
  workflows: Record<string, Workflow>;
  setWorkflows: (fn: (w: Record<string, Workflow>) => Record<string, Workflow>) => void;
  selWf: string;
  setSelWf: (n: string) => void;
  selStep: string | null;
  setSelStep: (id: string | null) => void;
  runs: Record<string, Run>;
  runOrder: string[];
  setRuns: (fn: (r: Record<string, Run>) => Record<string, Run>) => void;
  setRunOrder: (fn: (o: string[]) => string[]) => void;
  pipelines: Pipeline[];
  setPipelines: (fn: (p: Pipeline[]) => Pipeline[]) => void;
  openRunId: string | null;
  openRun: (id: string | null) => void;
  playing: boolean;
  setPlaying: (b: boolean) => void;
  speed: number;
  setSpeed: (n: number) => void;
  tiers: Record<string, Tier>;
  setTier: (role: string, t: Tier) => void;
  startRun: (workflow: string, slug: string, opts?: { source?: Source; title?: string }) => string;
  slack: Slack;
  setSlack: (fn: (t: Slack) => Slack) => void;
  openSlack: (runId?: string) => void;
  closeChatOnPhone: () => void;
  settingsTab: SettingsTab;
  openSettings: (t: SettingsTab) => void;
  setSettingsTab: (t: SettingsTab) => void;
  runner: Runner;
  setRunner: (fn: (r: Runner) => Runner) => void;
  accounts: Account[];
  setAccounts: (fn: (a: Account[]) => Account[]) => void;
  routing: Record<Tier, AccountId>;
  setRouting: (fn: (r: Record<Tier, AccountId>) => Record<Tier, AccountId>) => void;
  fallback: boolean;
  setFallback: (b: boolean) => void;
  installedPlugins: string[];
  installPlugin: (id: string) => void;
  removePlugin: (id: string) => void;
  connectors: Connector[];
  setConnectors: (fn: (c: Connector[]) => Connector[]) => void;
  /** true when a studio backend answered /api/health */
  live: boolean;
  health: LiveHealth | null;
  /** live mode: re-poll /api/runs now */
  refreshRuns: () => void;
  comments: Comment[];
  addComment: (c: Omit<Comment, "id" | "at">) => void;
}
