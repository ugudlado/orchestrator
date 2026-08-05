---
name: synthesize-findings
description: "Synthesize web search results into a structured research findings report. Use after search-web has produced sources.json."
user-invocable: true
---

# Synthesize Findings

**Intent:** Turn raw search results into a tight, source-backed research report.

## Inputs

- `spec/changes/<slug>/topic.md` — the normalized research topic.
- `spec/changes/<slug>/sources.json` — Tavily results: `{topic, results: [{title, url, content}]}`.

## Outputs

- `spec/changes/<slug>/findings.md` — the research report.

## Instructions

1. Read `topic.md` and `sources.json`.
2. Write `findings.md` with:
   - **Summary** — 2–3 sentences answering the topic directly.
   - **Key Findings** — bullet list, each backed by a source URL.
   - **Sources** — numbered list of titles + URLs used.
3. If the search returned no usable results, say so plainly in Summary and list
   the topic as "unresolved" — do not invent facts.

## Verify

- `findings.md` exists under the change dir.
- Every Key Finding cites at least one source from `sources.json`.
- No fabricated URLs — only URLs present in the sources file.

Return a COMPLETION block on stdout with `{"status": "completed", "outputs": {"findings_file": "<abs path>"}}`.
