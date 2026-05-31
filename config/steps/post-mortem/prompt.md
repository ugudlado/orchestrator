# Post-Mortem

## Inputs

- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/alert.json` — original alert
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/triage.md` — triage verdict
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/classification.md` — threat classification
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/forensics.md` — forensic analysis and root cause
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/containment-plan.md` — planned containment actions
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/containment/` — actual containment results
- `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/communications/` — stakeholder communications

## Outputs

- `post-mortem.md` written to `$WORKTREE_ARTIFACT_DIR/$CHANGE_ID/post-mortem.md`

## Instructions

Conduct a **blameless post-mortem** — the goal is systemic improvement, not assigning fault to individuals.

1. Read all inputs to understand the full incident lifecycle.
2. Write `post-mortem.md` with the following sections:

### ## Incident Summary
One paragraph: what happened, when, what was affected, how it was resolved.

### ## Timeline of Key Events
Table: Timestamp | Event | Who / What | Phase (Detection / Triage / Containment / Forensics / Recovery)
Include first detection, escalation points, containment actions, and resolution.

### ## Root Cause Analysis
- Immediate cause (the specific action or failure that triggered the incident)
- Contributing factors (the conditions that allowed it to happen)
- Why those conditions existed (process, tooling, or configuration gaps)
Use the "5 Whys" approach if applicable.

### ## What Went Well
Bullet list of things that worked: fast detection, effective containment, good communication, etc.

### ## What Could Have Gone Better
Bullet list of gaps: slow detection, unclear escalation paths, missing playbooks, tool failures.

### ## Detection Gaps
Specific signals that were available but not alerted on. For each gap:
- What signal existed
- Why it was not detected
- What detection rule or threshold would catch it next time

### ## Action Items
Table: Priority | Action | Owner Role | Due (relative: "within 1 week", "within 30 days") | Success Criteria

Prioritise by risk reduction impact. Include:
- Short-term (patch, rotate, harden) — within 1 week
- Medium-term (new detection rules, playbook updates) — within 30 days
- Long-term (architectural changes, training) — within 90 days

### ## Playbook Updates Required
List any incident response playbooks that need to be created or updated based on this incident.

## Verify

- `post-mortem.md` exists and contains all seven required sections
- Action Items table has at least one entry with a priority and due date
- Detection Gaps section is non-empty
- Document is blameless in tone (no individual names assigned fault)

Return COMPLETION:
COMPLETION:
  step_id: post-mortem
  status: completed
  outputs:
    post_mortem_path: <path to post-mortem.md>
    action_item_count: <number of action items>
    detection_gap_count: <number of detection gaps identified>
