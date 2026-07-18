# Diagnose Step Metrics

Metric keys: `root_cause_evidence`, `diagnosis_discipline`

Use these metrics only for diagnose-specific scenarios. Base explorer metrics are
scored separately on base scenarios.

| Metric                 | 10 looks like                                                                                                             | 0 looks like                                                                               |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `root_cause_evidence`  | Runnable reproduction captured, call chain actually read, exact file:line divergence identified with why-it's-wrong.      | Root cause guessed from symptoms, prose-only repro, or no line-level evidence.             |
| `diagnosis_discipline` | No fix proposed; impact assessed via callers/tests; pattern bugs cataloged with fresh full-tree counts; questions listed. | Proposes fixes, skips impact analysis, trusts stale counts, or hides unresolved questions. |
