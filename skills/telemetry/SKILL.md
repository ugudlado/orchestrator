---
name: telemetry
description: "Show workflow metrics dashboard. Use when user says \"telemetry\", \"show metrics\", \"workflow health\", \"dashboard\"."
user-invocable: true
args:
  - name: scope
    description: >
      What to show: "recent" (last 5 features, default), "all" (all features).
    required: false
  - name: fleet
    description: >
      Pass `--fleet` to show metrics across all repos (cross-repo view).
      Default (no flag) shows only the current repo.
    required: false
---

## Invocation

Runs the operator workflow via the orchestrator CLI (step: `render-telemetry`).

Default params live in `config/steps/render-telemetry/contract.yaml`. Override
by exporting env vars before invoke (same names as contract `params` keys).

## Variables

```
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
ORCHESTRATOR_CLI=${ORCHESTRATOR_CLI:-$(command -v orchestrator || echo "$REPO_ROOT/bin/orchestrator")}
```

## Execution

1. Parse `$ARGUMENTS` for `--fleet` and scope (`recent` default, `all` when requested).
2. Export overrides when the user asked for non-default behavior:

```bash
export TELEMETRY_SCOPE="${SCOPE:-recent}"
[ -n "$FLEET_FLAG" ] && export TELEMETRY_FLEET=1
# TELEMETRY_FEATURES_LIMIT, TELEMETRY_TREND_LIMIT, etc. — only when changing contract defaults
```

3. Run and show stdout:

```bash
"$ORCHESTRATOR_CLI" telemetry
```

4. If exit code is non-zero, report the error. If output says no archived metrics, explain that completing a feature workflow populates metrics.

Do not invoke `metrics-query.sh` directly — the step owns metrics queries.
