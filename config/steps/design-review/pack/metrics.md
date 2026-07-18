# Design Review Step Metrics

Metric keys: `gate_discipline`, `finding_actionability`

Use these metrics only for step-specific scenarios. Base reviewer metrics are
scored separately on base scenarios.

| Metric                  | 10 looks like                                                                                                         | 0 looks like                                                                            |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `gate_discipline`       | Deterministic gate runs first; needs_work returns status failed with refresh_artifacts; caps applied (crit→4, imp→7). | Scoring past a failed gate, needs_work as completed, editing artifacts, wrong caps.     |
| `finding_actionability` | Each finding names the AC, task id, or section at fault with concrete guidance; no style nitpicks.                    | Vague findings, subjective preferences flagged, or findings without a repair direction. |
