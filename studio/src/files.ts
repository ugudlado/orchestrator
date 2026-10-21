import type { Run } from "./sim/engine";
import type { Workflow } from "./types";

export interface FileInfo { name: string; owner: string; writers: string[]; version: number; lastStep: string; lastN: number }
export interface Comment { id: string; runId: string; file: string; line: number; text: string; owner: string; at: string }

export function filesFor(run: Run, wf: Workflow): FileInfo[] {
  if (run.live) {
    const h = run.live.history;
    const mentions = (e: (typeof h)[number], name: string) => Object.values(e.outs ?? {}).some((v) => typeof v === "string" && (v === name || v.endsWith(`/${name}`)));
    return run.live.artifacts.map((a, i) => {
      const hs = h.filter((e) => mentions(e, a.name));
      const last = hs[hs.length - 1]?.step ?? "";
      return { name: a.name, owner: last, writers: [...new Set(hs.map((e) => e.step))], version: Math.max(1, hs.length), lastStep: last, lastN: i };
    });
  }
  const m = new Map<string, FileInfo>();
  for (const c of run.calls) {
    if (c.status === "running") continue;
    const step = wf.steps.find((s) => s.id === c.step);
    for (const [, t] of Object.entries(step?.out ?? {})) {
      if (!/\.[a-z]+$/i.test(t)) continue;
      const f = m.get(t) ?? { name: t, owner: c.step, writers: [], version: 0, lastStep: c.step, lastN: c.n };
      f.version++; f.lastStep = c.step; f.lastN = c.n;
      if (!f.writers.includes(c.step)) f.writers.push(c.step);
      m.set(t, f);
    }
  }
  return [...m.values()];
}

export function contentFor(name: string, run: Run, version: number): string {
  const t = run.title, id = run.slug;
  switch (name) {
    case "ticket-context.md": return `# ${id}: ${t}
Source: ${run.source?.label ?? id} · Priority: High · Reporter: dana@ugudlado

## Summary
Users cannot tell what a run is doing without opening it. This ticket asks for: ${t}.

## Acceptance criteria
- A change is visible within 2 seconds of the underlying event
- Works for runs that are paused at a gate
- No regression in the existing runs list

## Open questions
- None. Reporter confirmed scope in the ticket thread.
`;
    case "discovery.md": return `# Discovery for ${id}

## Where this lives
- The run list is rendered by RunsView and reads from the run store
- Status changes already emit events on the run bus (run.step.started, run.step.finished)
- The gate UI subscribes to the same bus, so there is a pattern to follow

## Constraints
- The engine is stateless, so the feed must be derived from recorded next calls
- Events arrive out of order after a retry loop; sort by call number

## Risks
- A noisy feed during retry loops (code-review can loop up to 8 times)
`;
    case "design.md": return `# Design: ${t}

## Approach
Derive the feed from the recorded \`orchestrator next\` calls. Each call becomes one feed entry with a step id, a status and a duration.

## Components
- FeedList: renders entries newest first
- FeedEntry: step id, role, status chip, duration
- useRunFeed: subscribes to the run bus and folds events into entries

## Failure handling
- Retry loops collapse into one entry with a count (code-review x3)
- A needs_you answer pins to the top of the feed
${version > 1 ? `
## Revision ${version}
- Added the collapse rule for retry loops after design-review flagged noise
- Pinned needs_you entries to the top
` : ""}`;
    case "tasks.yaml": return `tasks:
  - id: T1
    title: Add useRunFeed hook
    files: [src/feed/useRunFeed.ts]
    done: ${version > 1}
  - id: T2
    title: Render FeedList and FeedEntry
    files: [src/feed/FeedList.tsx, src/feed/FeedEntry.tsx]
    done: false
  - id: T3
    title: Collapse retry loops into one entry
    files: [src/feed/collapse.ts]
    done: false
  - id: T4
    title: Tests for ordering and collapse
    files: [src/feed/feed.test.ts]
    done: false
complexity: M
`;
    case "code-review.md": return `# Code review: ${id}
Verdict: ${version > 1 ? "pass" : "needs_work"}

## Findings
${version > 1 ? "- No blocking findings. Retry-loop collapse covered by tests." : `- useRunFeed leaks its subscription when the run changes (missing cleanup)
- Collapse rule drops the last attempt's error message
- No test covers out-of-order events`}

## Tests
- shell.test: ${version > 1 ? "42 passed" : "39 passed, 1 failed (feed.test.ts: ordering)"}
`;
    case "verify-changes.md": return `# Verification: ${id}
- Pulled latest main, rebased cleanly
- Ran the full suite: 42 passed
- Verdict: pass
`;
    case "ux-prototype.html": return `<section class="feed">
  <h3>Run feed</h3>
  <ul>
    <li data-status="done">explore · 36s</li>
    <li data-status="running">design · running</li>
  </ul>
</section>
`;
    case "ux-artifacts.yaml": return `screens:
  - name: run-feed
    states: [empty, running, needs_you]
critique:
  contrast: ok
  keyboard: needs focus ring on entries
`;
    case "reply.md": return `Hi there,

Thanks for flagging "${t}". I checked your account and can see the failed export.

## What happened
The export timed out on a large invoice batch.

## What we did
We re-ran the export on our side and attached the CSV. A fix for large batches ships this week.

Best,
Support
`;
    default: return `# ${name}\n\nGenerated by the run ${id}. Content is recorded in the run store.\n`;
  }
}
