import type { Route, Step, Workflow } from "./types";
import { endsInDefault } from "./paths";
const list = (a: string[]) => `[${a.join(", ")}]`;
const map = (o: Record<string, string>) => `{ ${Object.entries(o).map(([k, v]) => `${k}: ${v}`).join(", ")} }`;
const wval = (v: string | string[]) => (Array.isArray(v) ? `[${v.join(", ")}]` : v);
export function routeLine(r: Route): string {
  const w = r.when && Object.keys(r.when).length ? `when: { ${Object.entries(r.when).map(([k, v]) => `${k}: ${wval(v)}`).join(", ")} }, ` : "";
  return `{ ${w}goto: ${r.goto} }`;
}
export function stepLines(s: Step): string[] {
  const rl = (s.routes ?? []).map((r) => `      - ${routeLine(r)}`);
  if (s.kind === "gate") {
    if (!rl.length) return [`  - { gate: ${s.id}, show: ${list(s.show ?? [])}, approve_as: ${s.approve_as ?? "token"} }`];
    return [`  - gate: ${s.id}`, `    show: ${list(s.show ?? [])}`, `    approve_as: ${s.approve_as ?? "token"}`, "    routes:", ...rl];
  }
  const extra: string[] = [];
  if (s.on_failure) extra.push(`    on_failure: ${s.on_failure}`);
  if (s.requires) extra.push(`    requires: ${s.requires}`);
  if (s.max_retries != null) extra.push(`    max_retries: ${s.max_retries}`);
  if (rl.length) extra.push("    routes:", ...rl);
  return extra.length ? [`  - id: ${s.id}`, ...extra] : [`  - ${s.id}`];
}
export function toYaml(wf: Workflow): string {
  const L: string[] = [];
  L.push(`name: ${wf.name}`, `version: ${wf.version}`, "inputs:");
  for (const [k, v] of Object.entries(wf.inputs)) L.push(`  ${k}: { type: ${v.type} }`);
  L.push("steps:");
  for (const s of wf.steps) L.push(...stepLines(s));
  const defs = wf.steps.filter((s) => s.kind !== "gate");
  if (defs.length) {
    L.push("", "# step definitions (steps/<id>.yaml)", "definitions:");
    for (const s of defs) {
      L.push(`  ${s.id}:`, `    kind: ${s.kind}`);
      if (s.role) L.push(`    role: ${s.role}`);
      if (s.max_turns != null) L.push(`    max_turns: ${s.max_turns}`);
      if (s.skip_when_unchanged) L.push("    skip_when_unchanged: true");
      if (s.tools?.length) L.push(`    tools: ${list(s.tools)}`);
      L.push(`    in: ${s.in && Object.keys(s.in).length ? map(s.in) : "{}"}`);
      L.push(`    out: ${s.out && Object.keys(s.out).length ? map(s.out) : "{}"}`);
      if (s.enums && Object.keys(s.enums).length) L.push(`    enums: { ${Object.entries(s.enums).map(([k, v]) => `${k}: ${list(v)}`).join(", ")} }`);
      if (s.fail_on && Object.keys(s.fail_on).length) L.push(`    fail_on: { ${Object.entries(s.fail_on).map(([k, v]) => `${k}: ${list(v)}`).join(", ")} }`);
    }
  }
  return L.join("\n") + "\n";
}
export interface Issue { level: "error" | "warn"; msg: string }
export function validate(wf: Workflow): Issue[] {
  const out: Issue[] = [];
  const idx = new Map(wf.steps.map((s, i) => [s.id, i]));
  const seen = new Set<string>();
  const minted = new Set<string>();
  wf.steps.forEach((s, i) => {
    if (seen.has(s.id)) out.push({ level: "error", msg: `duplicate step id "${s.id}"` });
    seen.add(s.id);
    if (s.on_failure) {
      const t = idx.get(s.on_failure);
      if (t === undefined) out.push({ level: "error", msg: `${s.id}: on_failure "${s.on_failure}" does not exist` });
      else if (t >= i) out.push({ level: "error", msg: `${s.id}: on_failure must point to an earlier step` });
    }
    (s.routes ?? []).forEach((r, k) => {
      if (!idx.has(r.goto)) out.push({ level: "error", msg: `${s.id}: route goto "${r.goto}" does not exist` });
      if (!r.when && k < (s.routes?.length ?? 0) - 1) out.push({ level: "error", msg: `${s.id}: a default route (no when) must be last` });
      if (s.kind === "gate" && r.when) out.push({ level: "error", msg: `${s.id}: a gate may only have a default route` });
    });
    if (s.routes?.length && !endsInDefault(s) && i === wf.steps.length - 1) out.push({ level: "warn", msg: `${s.id}: last step has routes but nothing to fall through to` });
    if (s.requires && !minted.has(s.requires)) out.push({ level: "error", msg: `${s.id}: requires "${s.requires}" but no earlier gate approves as it` });
    if (s.kind === "gate") {
      if (!s.approve_as) out.push({ level: "error", msg: `${s.id}: gate has no approve_as token` });
      else minted.add(s.approve_as);
    }
    if (s.fail_on && Object.keys(s.fail_on).length && !s.on_failure) out.push({ level: "warn", msg: `${s.id}: fail_on set but no on_failure target; a failing verdict will end in needs_you` });
    if (s.on_failure && s.max_retries == null) out.push({ level: "warn", msg: `${s.id}: on_failure loop without max_retries (default 3 applies)` });
    if (s.kind === "judgment" && !s.role) out.push({ level: "warn", msg: `${s.id}: no role assigned` });
  });
  if (!wf.steps.length) out.push({ level: "error", msg: "workflow has no steps" });
  return out;
}
