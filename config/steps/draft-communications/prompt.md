# Draft Stakeholder Communications

## Inputs

- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/triage.md` — severity and initial verdict
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/classification.md` — threat type and blast radius
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/forensics.md` — root cause, timeline, data impact

## Outputs

- `communications/exec-summary.md` — non-technical executive summary
- `communications/technical-report.md` — detailed technical report for security/IT teams
- `communications/customer-notice.md` — customer-facing notice (draft; may not be required)

All files written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/communications/`.

## Instructions

1. Read all inputs to understand the full incident picture.
2. Create `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/communications/` directory.

### Executive Summary (`exec-summary.md`)
- Audience: C-suite, board, non-technical leadership
- Length: 1–2 pages maximum
- Tone: clear, calm, factual — avoid jargon
- Content:
  - What happened (plain English, one paragraph)
  - Business impact (systems affected, data at risk, operations disrupted)
  - What we did (containment and response actions taken)
  - Current status (contained / under investigation / resolved)
  - Next steps and timeline
  - Do NOT include technical details, tool names, or IOCs

### Technical Report (`technical-report.md`)
- Audience: security engineers, IT ops, CISO
- Length: comprehensive — include all relevant technical detail
- Content:
  - Incident timeline (reference forensics.md timeline)
  - Attack vector and root cause
  - Systems and accounts affected
  - Containment actions taken (with timestamps if available)
  - Evidence collected and chain of custody notes
  - IOCs (all IPs, domains, hashes, account names)
  - MITRE ATT&CK mapping
  - Eradication steps completed vs. pending
  - Recommended hardening actions

### Customer Notice (`customer-notice.md`)
- Audience: affected customers (external)
- Draft only — mark clearly as **DRAFT — LEGAL REVIEW REQUIRED**
- Include: what data may have been affected, what we are doing, what customers should do
- Omit: internal system names, tool names, attacker attribution
- If forensics.md indicates no customer data was affected, write a short note explaining
  that customer notice is not required and why.

## Verify

- `exec-summary.md` exists, is non-technical, and is under 800 words
- `technical-report.md` exists and contains IOC section
- `customer-notice.md` exists (even if it states notice not required)

Return COMPLETION:
COMPLETION:
  step_id: draft-communications
  status: completed
  outputs:
    exec_summary_path: <path>
    technical_report_path: <path>
    customer_notice_path: <path>
    customer_data_affected: <true|false>
