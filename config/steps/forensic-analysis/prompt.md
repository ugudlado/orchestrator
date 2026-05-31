# Forensic Analysis

## Inputs

- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/logs/raw-logs.jsonl` — SIEM/firewall logs
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/logs/network-flows.json` — network flow records
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/logs/endpoint-telemetry.json` — EDR process/file/registry events
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/classification.md` — threat classification and IOCs
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/containment/` — results of containment actions

## Outputs

- `forensics.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/forensics.md`
- Fields: attack timeline, root cause, persistence mechanisms, attacker TTPs, data impact, eradication checklist

## Instructions

1. Read all evidence files. Correlate events across log sources to build a unified picture.
2. Reconstruct the **attack timeline** in chronological order:
   - Initial access vector (how did the attacker get in?)
   - Execution and persistence (what did they run? what did they install?)
   - Privilege escalation (what privileges did they gain?)
   - Lateral movement (which systems did they pivot to?)
   - Impact (data accessed, exfiltrated, encrypted, or destroyed)
3. Identify the **root cause**: the specific vulnerability, misconfiguration, or human action that enabled initial access.
4. List all **persistence mechanisms** found (scheduled tasks, registry run keys, cron jobs, backdoor accounts, web shells, etc.).
5. Map the full kill chain to **MITRE ATT&CK** (expand on the classification step with specific sub-techniques and evidence citations).
6. Assess **data impact**: what data was accessed or exfiltrated? What is the regulatory/legal exposure?
7. Produce an **eradication checklist**: specific actions required to fully remove the attacker from the environment.
8. Write `forensics.md` with sections:
   - ## Attack Timeline  (table: Timestamp | System | Event | Evidence Source)
   - ## Root Cause
   - ## Persistence Mechanisms
   - ## Full MITRE ATT&CK Kill Chain  (table: Tactic | Sub-Technique | Evidence | Eradication Action)
   - ## Data Impact Assessment
   - ## Eradication Checklist  (checkbox list)

## Verify

- `forensics.md` exists and is non-empty
- Attack Timeline contains at least one entry
- Eradication Checklist contains at least one item
- Root Cause section is non-empty

Return COMPLETION:
COMPLETION:
  step_id: forensic-analysis
  status: completed
  outputs:
    forensics_path: <path to forensics.md>
    root_cause_summary: <one-sentence summary>
    eradication_items: <count of checklist items>
