# Tasks -- HL-276: Track per-tool-use breakdown in agent usage metrics

- [x] T-1: Update CONVENTIONS.md State Field Registry to document `tools:` sub-map under `step_history[].usage`
  Verify: The `step_history[].usage` row in CONVENTIONS.md includes `tools: { ToolName: N, ... }` in its description. The State Updates example block includes the `tools:` field.

- [x] T-2: Extend SKILL.md dispatch loop instruction to write `tools:` map after each agent step
  Verify: SKILL.md lines 109-110 area includes instruction to count tool invocations by type name and write `tools:` map under `usage:`. Instruction states sum of tools values must equal tool_uses.
  depends: T-1

- [x] T-3: Write a test state.yaml fixture with `tools:` sub-maps for verifying compute-swe-metrics.sh changes
  Verify: A fixture file exists at `config/scripts/test-fixtures/state-with-tools.yaml` containing at least two step_history entries with `tools:` maps, one entry without `tools:` (backward compat), and one entry with `tools: {}` (zero tool calls). The fixture has known expected values for per_agent_tools aggregation.

- [x] T-4: Extend compute-swe-metrics.sh per-agent awk block to aggregate `tools:` maps using SUBSEP keys
  Verify: Running `compute-swe-metrics.sh` against the T-3 fixture produces output containing `per_agent_tools` with correct per-agent tool breakdowns. Entries without `tools:` are silently skipped. The `per_agent_tools` value is a valid JSON object string.
  depends: T-3

- [x] T-5: Add `per_agent_tools` field to compute-swe-metrics.sh YAML output block
  Verify: The script output includes a `per_agent_tools:` line in the YAML metrics block, formatted as a single-quoted JSON string (same convention as `per_agent_tokens`). Running against the T-3 fixture shows the correct aggregated values. Running against a state.yaml with no `tools:` fields produces `per_agent_tools: '{}'`.
  depends: T-4

- [x] T-6: Update compute-swe-metrics.yaml step contract instruction 3a to include `per_agent_tools`
  Verify: The compute-swe-metrics.yaml instruction 3a mentions `per_agent_tools` with its format: JSON object keyed by agent name, each value a map of tool type to count.
  depends: T-5

- [x] T-7: Update grammar.yaml swe_metrics section to document `per_agent_tools`
  Verify: grammar.yaml swe_metrics section includes a `per_agent_tools` entry with a comment describing its format (JSON object keyed by agent name, values are tool-type-to-count maps).
  depends: T-5
