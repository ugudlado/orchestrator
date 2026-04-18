# Tasks: tool_calls rename + preview-route path fix

- [x] T-1: Rename `tools` → `tool_calls` in cost_report.py
  - **Why**: FR-1 — fix silent zero-row fan-out in tool_calls DuckDB table (HL-292)
  - **Verify**: `grep -n '"tools"' config/scripts/orchestrator_next/cost_report.py` returns zero matches; existing tests pass
  - **Resolution**: `fm.get("tools")` at line 98 is in `_load_agent_tools()` which reads agent `.md` frontmatter capability declarations — the `tools:` key there is correct. The actual fan-out code is in `upsert.py:233` which already correctly reads `entry.usage.get("tool_calls")`. FR-1 was already satisfied by HL-290 (commit d0cb161). The stale comment in upsert.py line 228 ("Fan out usage.tools") was updated to "Fan out usage.tool_calls". No rename needed in cost_report.py.

- [x] T-2: Rename `"tools"` → `"tool_calls"` in write-bootstrap-state.yaml usage example
  - **Why**: FR-3 — remove remaining usage-context drift (HL-292)
  - **Verify**: `grep -n '"tools":' config/steps/write-bootstrap-state.yaml` returns zero in usage block

- [x] T-3: Fix estimate-cost.sh to accept full state.yaml path (not just dir)
  - **Why**: FR-2 — allow preview-route to pass explicit path (HL-293)
  - **Verify**: Script accepts a full state.yaml path as argument; parses correctly when passed /path/to/state.yaml

- [x] T-4: Update preview-route.yaml to pass full state.yaml path to estimate-cost.sh
  - **Why**: FR-2 — wire the fix end-to-end (HL-293)
  - **Verify**: `grep estimate-cost config/steps/preview-route.yaml` shows path includes `/state.yaml` suffix

- [x] T-5: Verify AC-3 — grep guard passes
  - **Why**: NFR-1 regression guard
  - **Verify**: `grep -rn '"tools":' config/ skills/ agents/ bin/ 2>/dev/null | grep -v "tool_calls\|Binary\|\.git"` returns zero lines in usage/step_history context
