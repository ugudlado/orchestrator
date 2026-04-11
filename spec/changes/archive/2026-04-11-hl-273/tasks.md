# Tasks -- Track per-agent token attribution

- [x] T-1: Add usage field to step_history format in CONVENTIONS.md
  Add an optional `usage` field to the standard step_history entry format showing
  `{ total_tokens: N, tool_uses: N, duration_ms: N }`. Add `step_history[].usage`
  to the State Field Registry table with format documentation. Note that this field
  is only populated for agent-spawned steps (steps with `agent:` field).
  Verify: CONVENTIONS.md step_history entry format shows usage field; State Field
  Registry table includes step_history[].usage entry.

- [x] T-2: Update compute-swe-metrics.yaml instruction for per-agent extraction
  Add a step to the instruction block that reads step_history from state.yaml,
  groups usage by agent type, and includes a `per_agent_tokens` object in the JSONL
  output with per-agent totals (e.g., `{ developer: { total_tokens: N, steps: N },
  reviewer: { total_tokens: N, steps: N } }`).
  Verify: compute-swe-metrics.yaml instruction references step_history usage
  extraction and per_agent_tokens JSONL field.
  depends: T-1

- [x] T-3: Implement per-agent extraction in compute-swe-metrics.sh
  Add shell logic to parse step_history entries from state.yaml, extract usage
  fields grouped by agent, and output the per_agent_tokens JSON object. Handle
  missing usage fields gracefully (skip entries without usage).
  Verify: compute-swe-metrics.sh contains logic to extract usage from step_history;
  entries without usage field are skipped; output includes per_agent_tokens in JSON.
  depends: T-2
