---
id: ORC-45
title: 'HL-293: Fix estimate-cost.sh state.yaml path and preview-route integration'
status: Backlog
assignee: []
created_date: '2026-05-08 12:05'
labels:
  - bug
  - orchestrator
dependencies: []
references:
  - >-
    https://linear.app/home-labs-experiments/issue/HL-293/fix-estimate-costsh-stateyaml-path-and-preview-route-integration
priority: low
ordinal: 42000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
preview-route always returns {status: estimate_unavailable} because estimate-cost.sh expects state.yaml at $WORKFLOW_DIR/state.yaml, but the state move put it at ~/.workflows/<slug>/state.yaml. Fix the estimator to accept the state.yaml path as argument or env var, and update all callers accordingly.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 estimate-cost.sh accepts state.yaml path as argument or WORKFLOW_STATE_YAML env var
- [ ] #2 preview-route returns a cost estimate instead of estimate_unavailable
- [ ] #3 All callers of estimate-cost.sh pass the correct path
<!-- AC:END -->
