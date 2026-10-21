import raw from "./data/workflows.json";
import { defaultRole } from "./roles";
import { makeSource } from "./connectors";
import { decide, fastForward, startRun, type Run } from "./sim/engine";
import type { Pipeline, Step, Workflow } from "./types";

export const MAIN_PLUGIN = "workflows";
export const MINE = "My workflows";

export function loadWorkflows(): Record<string, Workflow> {
  const out: Record<string, Workflow> = {};
  for (const [k, w] of Object.entries(raw as unknown as Record<string, Workflow>)) {
    const steps = (w.steps as Step[]).map((s) => ({ ...s, role: defaultRole(s) }));
    out[k] = { ...w, plugin: MAIN_PLUGIN, steps };
  }
  return out;
}

const ex = (id: string, tools = ["fs.read", "shell.run"], out: Record<string, string> = {}): Step => ({ id, kind: "exec", tools, in: {}, out, enums: {}, fail_on: {} });
const jd = (id: string, role: string, turns: number, ins: Record<string, string>, out: Record<string, string>, extra: Partial<Step> = {}): Step => ({
  id, kind: "judgment", role, max_turns: turns, tools: ["fs.read", "fs.write", "shell.run"], in: ins, out, enums: {}, fail_on: {}, ...extra,
});
const verdict = { enums: { verdict: ["pass", "needs_work"] }, fail_on: { verdict: ["needs_work"] } };
const gate = (id: string, show: string[], as: string): Step => ({ id, kind: "gate", show, approve_as: as });
const wf = (name: string, plugin: string, steps: Step[]): Workflow => ({ name, version: 1, plugin, inputs: { ticket: { type: "string" } }, steps });

export interface PluginDef { id: string; name: string; source: string; blurb: string; workflows: Workflow[] }
export const PLUGIN_CATALOG: PluginDef[] = [
  {
    id: "release-train", name: "release-train", source: "github.com/acme-eng/release-train", blurb: "Cut releases, keep the changelog, ship canaries.",
    workflows: [
      wf("cut-release", "release-train", [ex("check-rerun"), jd("collect-changes", "explorer", 20, {}, { changes: "changes.md" }), jd("draft-notes", "designer", 20, { changes: "changes.md" }, { notes: "release-notes.md" }),
        gate("release-signoff", ["notes"], "release_token"), { ...ex("tag-release", ["git.tag"]), requires: "release_token" }]),
      wf("changelog", "release-train", [jd("collect-changes", "explorer", 20, {}, { changes: "changes.md" }), jd("write-changelog", "designer", 25, { changes: "changes.md" }, { changelog: "CHANGELOG.md" }),
        jd("changelog-review", "reviewer", 15, { changelog: "CHANGELOG.md" }, { verdict: "enum" }, { ...verdict, on_failure: "write-changelog", max_retries: 3 })]),
      wf("deploy-canary", "release-train", [ex("build"), ex("deploy-canary-step", ["shell.run"]), jd("watch-metrics", "reviewer", 30, {}, { verdict: "enum" }, { enums: { verdict: ["healthy", "regressed"] }, fail_on: { verdict: ["regressed"] }, on_failure: "deploy-canary-step", max_retries: 2 }),
        gate("promote-signoff", ["metrics"], "promote_token"), { ...ex("promote", ["shell.run"]), requires: "promote_token" }]),
    ],
  },
  {
    id: "docs-pack", name: "docs-pack", source: "github.com/acme-eng/docs-pack", blurb: "Keep docs in step with the code that changed.",
    workflows: [
      wf("doc-update", "docs-pack", [ex("load-diff", ["git.diff"], { diff: "diff.patch" }), jd("write-docs", "designer", 30, { diff: "diff.patch" }, { docs: "docs-update.md" }), jd("link-check", "reviewer", 10, { docs: "docs-update.md" }, { verdict: "enum" }, { ...verdict, on_failure: "write-docs", max_retries: 3 })]),
      wf("doc-review", "docs-pack", [jd("read-docs", "explorer", 20, {}, { notes: "doc-notes.md" }), jd("review-docs", "reviewer", 25, { notes: "doc-notes.md" }, { verdict: "enum" }, { ...verdict, on_failure: "read-docs", max_retries: 2 }), gate("docs-signoff", ["notes"], "docs_token")]),
    ],
  },
  {
    id: "incident-pack", name: "incident-pack", source: "github.com/acme-eng/incident-pack", blurb: "Triage an incident and write the postmortem.",
    workflows: [wf("incident-triage", "incident-pack", [jd("gather-signals", "explorer", 25, {}, { signals: "signals.md" }), jd("hypothesize", "designer", 25, { signals: "signals.md" }, { hypothesis: "hypothesis.md" }), gate("page-signoff", ["hypothesis"], "page_token")])],
  },
];

export function bugIntakeWorkflow(src: Record<string, Workflow>): Workflow {
  const bf = src.bugfix.steps;
  const pick = (id: string, patch: Partial<Step> = {}) => ({ ...structuredClone(bf.find((s) => s.id === id)!), ...patch });
  const steps: Step[] = [
    pick("load-ticket-context"), pick("diagnose"), pick("design"), pick("design-review"), pick("design-signoff"),
    pick("implement"), pick("code-review"), pick("open-pr", { requires: null }),
  ];
  return { name: "bug-intake", version: 1, plugin: MINE, inputs: { ticket: { type: "string" } }, steps };
}

export const DEV_FEATURE = { change_type: "feature" };
export const DEV_HOTFIX = { change_type: "fix", fix_scope: "hotfix", "ux-critique.verdict": "skipped" };
export const DEV_FIX = { change_type: "fix", fix_scope: "standard", "ux-critique.verdict": "skipped" };
export const DEV_QUICK = { change_type: "quick", "ux-critique.verdict": "skipped" };
/** Which dev path a new run takes, picked from its ticket key so the demo shows all four */
export const devAssign = (slug: string): Record<string, string> => (/^HOT/i.test(slug) ? DEV_HOTFIX : /^(QUICK|QK)/i.test(slug) ? DEV_QUICK : /^(BUG|FIX)/i.test(slug) ? DEV_FIX : DEV_FEATURE);

export const SEED_PIPELINES: Pipeline[] = [
  { id: "p-dev", name: "Dev delivery", workflow: "dev", connector: "linear", event: "Status changed", filter: "status = Ready for dev" },
  { id: "p-feature", name: "Feature delivery", workflow: "feature", connector: "linear", event: "Issue created", filter: "label = feature" },
  { id: "p-bugfix", name: "Bug fixes", workflow: "bugfix", connector: "github", event: "Issue opened", filter: "label = bug" },
  { id: "p-research", name: "Research briefs", workflow: "research", connector: "linear", event: "Status changed", filter: "status = Research" },
];

export function seedRuns(w: Record<string, Workflow>): Run[] {
  const mk = (name: string, o: Parameters<typeof startRun>[1]) => startRun(w[name], o);
  const uxi = w.dev.steps.findIndex((s) => s.id === "ux-design");
  const r131 = fastForward(mk("dev", { id: "r131", slug: "ORC-131", title: "Run status feed", pipelineId: "p-dev", source: makeSource("linear", "ORC-131"), startedAgo: "2 min ago", failOnce: ["code-review"], assign: DEV_FEATURE }), w.dev, (r) => r.cursor >= uxi);
  const hot7 = fastForward(mk("dev", { id: "r-hot7", slug: "HOT-7", title: "Checkout 500 on empty cart", pipelineId: "p-dev", source: makeSource("linear", "HOT-7"), startedAgo: "6 min ago", assign: DEV_HOTFIX }), w.dev, (r) => r.status === "waiting");
  const orc140 = fastForward(mk("dev", { id: "r-orc140", slug: "ORC-140", title: "Fix typo in billing email", pipelineId: "p-dev", source: makeSource("linear", "ORC-140"), startedAgo: "3 min ago", assign: DEV_QUICK }), w.dev, (r) => r.status === "waiting");
  const r124 = fastForward(mk("feature", { id: "r124", slug: "ORC-124", title: "Export audit log as CSV", pipelineId: "p-feature", source: makeSource("linear", "ORC-124"), startedAgo: "yesterday", failOnce: ["code-review"] }), w.feature, () => false);
  const r128 = fastForward(mk("bugfix", { id: "r128", slug: "ORC-128", title: "Coupon stacking double-discounts shipping", pipelineId: "p-bugfix", source: { connector: "github", label: "GitHub #128", url: "https://github.com/ugudlado/orchestrator/issues/128" }, startedAgo: "1 h ago", failAlways: ["code-review"] }), w.bugfix, () => false);
  const r122 = fastForward(mk("bugfix", { id: "r122", slug: "ORC-122", title: "Null session crash on logout", pipelineId: "p-bugfix", startedAgo: "2 days ago" }), w.bugfix, () => false);
  const r42 = fastForward(mk("research", { id: "r42", slug: "RES-42", title: "Competitor pricing scan", pipelineId: "p-research", source: makeSource("linear", "RES-42"), startedAgo: "3 days ago", failOnce: ["fact-check"] }), w.research, () => false);
  return [r131, hot7, orc140, r128, r124, r122, r42];
}

export const BUG_PIPELINE: Pipeline = { id: "p-bug-intake", name: "Bug intake", workflow: "bug-intake", connector: "slack", event: "Message → Linear ticket", filter: "label = bug" };
export function bugIntakeRuns(w: Workflow): Run[] {
  const mk = (id: string, slug: string, title: string, ago: string) => startRun(w, { id, slug, title, pipelineId: BUG_PIPELINE.id, source: makeSource("slack", slug), startedAgo: ago, failOnce: ["code-review"] });
  const a = fastForward(mk("rb214", "BUG-214", "Avatar upload fails over 5 MB", "just now"), w, (r) => r.cursor >= 2);
  const b = fastForward(mk("rb212", "BUG-212", "Timezone off by one in weekly digest", "8 min ago"), w, (r) => r.status === "waiting");
  const c = fastForward(mk("rb209", "BUG-209", "Search ignores archived filter", "yesterday"), w, () => false);
  return [a, b, c];
}
export { decide };

export function supportTriageWorkflow(src: Record<string, Workflow>): Workflow {
  const f = src.feature.steps;
  const pick = (id: string) => structuredClone(f.find((s) => s.id === id)!);
  const steps: Step[] = [
    pick("load-ticket-context"), pick("explore"),
    jd("draft-reply", "designer", 25, { ticket: "ticket-context.md", discovery: "discovery.md" }, { reply: "reply.md" }),
    gate("reply-signoff", ["reply"], "reply_token"),
    { ...ex("post-reply", ["shell.run"], { posted: "boolean" }), requires: "reply_token" },
  ];
  return { name: "support-triage", version: 1, plugin: MINE, inputs: { ticket: { type: "string" } }, steps };
}
export const SUPPORT_PIPELINE: Pipeline = { id: "p-support", name: "Support triage", workflow: "support-triage", connector: "zendesk", event: "Ticket created", filter: "priority != urgent" };
export function supportRuns(w: Workflow): Run[] {
  const mk = (id: string, slug: string, title: string, ago: string) => startRun(w, { id, slug, title, pipelineId: SUPPORT_PIPELINE.id, source: makeSource("zendesk", slug), startedAgo: ago });
  return [
    fastForward(mk("rz4821", "ZD-4821", "Cannot export invoices to CSV", "just now"), w, (r) => r.status === "waiting"),
    fastForward(mk("rz4818", "ZD-4818", "SSO login loops back to sign-in", "yesterday"), w, () => false),
  ];
}
