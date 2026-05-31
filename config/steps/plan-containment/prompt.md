# Plan Containment

## Inputs

- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/classification.md` — threat classification, IOCs, blast radius
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/triage.md` — severity and initial scope

## Outputs

- `containment-plan.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/containment-plan.md`
- Structured sections that downstream shell steps parse directly

## Instructions

1. Read `classification.md` and `triage.md`.
2. Decide **which containment actions are necessary and proportionate** given the threat category, severity, and blast radius. Consider:
   - Will blocking IPs cause business disruption? Are those IPs shared infrastructure?
   - Will host isolation prevent ongoing damage vs. destroy forensic evidence?
   - Which accounts are confirmed compromised vs. merely suspicious?
3. Produce a containment plan with the following **exactly-formatted sections** (downstream shell steps parse these sections):

```
## Block IPs
- <ip-or-cidr>
- <ip-or-cidr>
(list each IP/CIDR on its own line with a leading dash)

## Isolate Hosts
- `<hostname-or-endpoint-id>`
- `<hostname-or-endpoint-id>`
(list each in backticks on its own line)

## Revoke Credentials
- `<username-or-account-id>`
- `<username-or-account-id>`
(list each in backticks on its own line)

## Rationale
<explain the reasoning for each action and any omissions>

## Risks & Caveats
<note business impact, any actions deferred and why>
```

4. If a section has no actions, write `(none)` under that heading — do not omit the heading.
5. Prioritise actions by damage reduction impact: credential revocation and host isolation typically take priority over IP blocks (attackers rotate IPs; compromised accounts persist).

## Verify

- `containment-plan.md` exists and contains all four required headings
- Each action list entry uses the exact format parseable by block-ips, isolate-hosts, revoke-credentials scripts
- Rationale section is non-empty

Return COMPLETION:
COMPLETION:
  step_id: plan-containment
  status: completed
  outputs:
    plan_path: <path to containment-plan.md>
    ip_count: <number of IPs to block>
    host_count: <number of hosts to isolate>
    credential_count: <number of credentials to revoke>
