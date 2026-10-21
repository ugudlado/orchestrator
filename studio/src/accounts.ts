import type { Tier } from "./roles";
export type AccountId = "claude" | "chatgpt" | "apikey" | "ollama";
export interface Meter { label: string; pct: number }
export interface Account { id: AccountId; connected: boolean; connecting?: boolean; email?: string; plan?: string; detail?: string; meters?: Meter[] }
export const ACCOUNT_NAME: Record<AccountId, string> = { claude: "Claude Code (runner)", chatgpt: "Codex (runner)", apikey: "API key", ollama: "Local (Ollama)" };
export interface Runner { paired: boolean; name: string; version: string; code: string }
export const SEED_RUNNER: Runner = { paired: true, name: "studio-vm", version: "0.3.0", code: "7QK-4MX" };
export function accountLabel(a: Account | undefined, runner: Runner): string {
  if (!a) return "";
  if (a.id === "claude" || a.id === "chatgpt") {
    if (!a.connected || !runner.paired) return "no runner";
    const plan = (a.plan ?? "").replace(/^(Claude|ChatGPT) /, "");
    return `${a.id === "claude" ? "Claude Code" : "Codex"} (${plan}) on ${runner.name}`;
  }
  return a.connected ? (a.id === "apikey" ? "API key" : "Local") : "no account";
}
export const SEED_ACCOUNTS: Account[] = [
  { id: "claude", connected: true, email: "you@example.com", plan: "Claude Max", meters: [{ label: "5-hour window", pct: 38 }, { label: "Weekly", pct: 12 }] },
  { id: "chatgpt", connected: true, email: "you@example.com", plan: "ChatGPT Plus", meters: [{ label: "5-hour window", pct: 21 }, { label: "Weekly", pct: 9 }] },
  { id: "apikey", connected: true, plan: "API key", detail: "Anthropic · sk-ant-…a41f" },
  { id: "ollama", connected: false },
];
export const SEED_ROUTING: Record<Tier, AccountId> = { strong: "claude", code: "claude", standard: "chatgpt", fast: "apikey" };
