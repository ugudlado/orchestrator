---
name: synthesize-findings
description: "Research a topic with the agent's own web search/browse tools and write a source-backed findings report."
user-invocable: true
---

# Synthesize Findings

**Intent:** Answer the research topic with a tight, source-backed report.
You own discovery — use the web search / browse / fetch tools available in
this agent runtime. Do **not** expect a pre-built `sources.json` or Tavily key.

## Inputs

Session workspace is `$WORKTREE_PATH` / `$ORCHESTRATOR_WORKFLOW_DIR`
(`.orchestrator/sessions/<session_id>/` for ACP research). Prefer these files:

- `$WORKTREE_PATH/intake.json` — structured intake (topic, audience, depth).
- `$WORKTREE_PATH/topic.md` — human-readable brief from intake-research.

If only legacy `spec/changes/<slug>/topic.md` exists (non-session runs), use that.

## Outputs

Under the same session workspace (or legacy change dir if that is where intake lived):

- `findings.md` — required research report.
- `sources.md` — optional short citation ledger (title + URL per source you
  actually used). Prefer this over inventing a Tavily-shaped JSON.

## Verify

- `findings.md` exists under the session workspace (or legacy change dir).
- Every Key Finding cites a URL that appears in Sources.
- No fabricated URLs.

## Instructions

1. Read `intake.json` and/or `topic.md`. Note **Topic**, **Audience**, and **Depth**.
2. Search the web (or browse docs) with whatever tools you have — pick the
   right tool for the job (site search, docs fetch, general web search). Prefer
   primary/docs sources over SEO fluff. Match depth/audience (ops guide vs brief).
3. Write `findings.md` with:
   - **Summary** — 2–3 sentences answering the topic directly.
   - **Key Findings** — bullet list; each bullet cites at least one real URL.
   - **Sources** — numbered list of titles + URLs you used.
4. If search/browse yields nothing usable, say so plainly in Summary, mark the
   topic **unresolved**, and do **not** invent facts or URLs.

Return a COMPLETION block on stdout:

```text
COMPLETION:
  step_id: synthesize-findings
  status: completed
  outputs:
    findings_file: <abs path to findings.md>
    reason: >
      <what you searched, what you wrote, and why the step can advance>
```
