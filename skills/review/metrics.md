# Run Phase Review Step Metrics

Metric keys: `verification_evidence`, `gate_integrity`

Use these metrics only for step-specific scenarios. Base reviewer metrics are
scored separately on base scenarios.

| Metric                  | 10 looks like                                                                                                                  | 0 looks like                                                                                         |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `verification_evidence` | verify_commands actually run; ACs verified with fresh evidence (N/N counts, re-run searches); spot audit of recorded evidence. | Scores without running commands, trusts stale counts or self-reported evidence unchecked.            |
| `gate_integrity`        | Pending tasks → incomplete_phase + status failed; quarantine → critical cap; fix tasks minimal, sequential, status pending.    | Advances with pending tasks or unresolved criticals, needs_work as completed, scope-creep fix tasks. |
