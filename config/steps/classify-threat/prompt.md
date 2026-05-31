# Classify Threat

## Inputs

- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/alert.json` — original alert
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/triage.md` — triage verdict and initial scope
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/logs/raw-logs.jsonl` — SIEM/firewall logs
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/logs/network-flows.json` — network flow records
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/logs/endpoint-telemetry.json` — EDR telemetry

## Outputs

- `classification.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/classification.md`
- Fields: threat category, MITRE ATT&CK techniques, IOCs, blast radius, confidence

## Instructions

1. Read all evidence files listed in Inputs.
2. Classify the threat into a primary category:
   - Ransomware / Extortion
   - Data Exfiltration
   - Business Email Compromise (BEC)
   - Phishing / Credential Harvesting
   - Malware Infection (specify family if identifiable)
   - Insider Threat
   - Unauthorized Access / Privilege Escalation
   - Denial of Service
   - Supply Chain Compromise
   - Other (describe)
3. Map observed behaviours to **MITRE ATT&CK techniques** (Tactic → Technique ID + name).
4. Extract **Indicators of Compromise (IOCs)**:
   - Malicious IPs, domains, URLs
   - File hashes (MD5/SHA256), filenames, paths
   - Registry keys, scheduled tasks, persistence mechanisms
   - Compromised accounts
5. Determine **blast radius**:
   - Which systems are confirmed affected?
   - Which systems may be affected (lateral movement paths)?
   - Is there evidence of data exfiltration? If so, what data types?
6. State confidence level: High / Medium / Low, with justification.
7. Write `classification.md` with sections:
   - ## Threat Category
   - ## MITRE ATT&CK Mapping  (table: Tactic | Technique ID | Technique Name | Evidence)
   - ## Indicators of Compromise  (subsections: IPs, Domains, File Hashes, Accounts)
   - ## Blast Radius
   - ## Confidence & Gaps

## Verify

- `classification.md` exists and is non-empty
- At least one MITRE ATT&CK technique is identified or explicitly noted as unidentifiable
- IOC section contains at least one entry or explicitly states "no IOCs extracted"

Return COMPLETION:
COMPLETION:
  step_id: classify-threat
  status: completed
  outputs:
    classification_path: <path to classification.md>
    threat_category: <primary category>
    confidence: <High|Medium|Low>
