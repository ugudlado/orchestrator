import type { Step } from "./types";
export const TIERS = {
  strong: "claude-fable-5-1",
  standard: "claude-sonnet-5",
  fast: "claude-haiku-4-5",
  code: "claude-opus-5",
} as const;
export type Tier = keyof typeof TIERS;
export interface Role {
  id: string;
  name: string;
  initials: string;
  color: string;
  blurb: string;
  tier: Tier;
}
export const ROLES: Role[] = [
  { id: "designer", name: "Designer", initials: "De", color: "#7c5cff", tier: "strong", blurb: "Turns discovery into a design and a task list." },
  { id: "explorer", name: "Explorer", initials: "Ex", color: "#0f9aa8", tier: "fast", blurb: "Reads the codebase and tickets, maps what already exists." },
  { id: "implementer", name: "Implementer", initials: "Im", color: "#0060e2", tier: "code", blurb: "Writes the change task by task, runs tests, commits." },
  { id: "reviewer", name: "Reviewer", initials: "Rv", color: "#c27a0a", tier: "strong", blurb: "Reviews designs, diffs and prototypes and returns a verdict." },
  { id: "teamlead", name: "Team lead", initials: "Tl", color: "#1a9a52", tier: "standard", blurb: "Keeps tickets, learnings and hand-offs in order." },
  { id: "uxdesigner", name: "UX designer", initials: "Ux", color: "#d6336c", tier: "standard", blurb: "Prototypes the interface and critiques the flow." },
];
export const roleById = (id?: string | null) => ROLES.find((r) => r.id === id);
const MAP: Record<string, string> = {
  design: "designer", explore: "explorer", diagnose: "explorer", implement: "implementer",
  "code-review": "reviewer", "design-review": "reviewer", "ux-critique": "reviewer", "verify-changes": "reviewer",
  "ux-design": "uxdesigner", learn: "teamlead", "human-review": "teamlead",
  "load-ticket-context": "teamlead", "ticket-start": "teamlead", "ticket-review": "teamlead", "ticket-qa": "teamlead",
  "ticket-done": "teamlead", "open-pr": "implementer",
  "intake-research": "explorer", "source-search": "explorer", "synthesize-findings": "designer", "fact-check": "reviewer",
  storyline: "designer", "choose-deliverables": "teamlead", deliverables: "uxdesigner",
};
export const defaultRole = (s: Step): string | null => (s.kind === "judgment" ? MAP[s.id] ?? "teamlead" : null);
