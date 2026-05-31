# Triage Alert

## Inputs

- Alert record at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/alert.json`

## Outputs

- `triage.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/triage.md`
- Fields: verdict, severity, initial scope, recommended next actions, analyst notes

## Instructions

1. Read the alert at `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/alert.json`.
2. Determine whether this is a **true positive** or **false positive** based on:
   - Alert type, rule name, confidence score
   - Source/destination IPs, ports, hostnames
   - Time of day, user context, asset criticality
   - Historical context (if available in the alert metadata)
3. Assign a severity: **Critical / High / Medium / Low / Informational**.
   - Critical: active breach, data exfiltration in progress, ransomware detonating
   - High: confirmed malicious activity, lateral movement, privilege escalation
   - Medium: suspicious behaviour requiring investigation
   - Low / Informational: likely benign but worth logging
4. Define the **initial scope**: which hosts, accounts, and network segments appear involved.
5. Recommend the next actions for the analyst (collect evidence, escalate, close as FP, etc.).
6. Write `triage.md` to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/triage.md` with sections:
   - ## Verdict  (True Positive / False Positive / Unknown — needs investigation)
   - ## Severity
   - ## Initial Scope  (bullet list: hosts, IPs, accounts)
   - ## Rationale  (why this verdict and severity)
   - ## Recommended Next Actions

## Verify

- `triage.md` exists and is non-empty
- Verdict is one of: True Positive, False Positive, Unknown
- Severity is one of: Critical, High, Medium, Low, Informational
- Initial Scope lists at least one entity or explicitly states "scope unknown"

Return COMPLETION:
COMPLETION:
  step_id: triage-alert
  status: completed
  outputs:
    triage_path: <path to triage.md>
    verdict: <True Positive|False Positive|Unknown>
    severity: <Critical|High|Medium|Low|Informational>
