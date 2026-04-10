---
name: diagram
description: "Generate visual diagrams (flowchart, sequence, class, state, ER, C4) via draw.io. This skill should be used when the user says 'draw a diagram', 'visualize the architecture', 'create a flowchart', 'sequence diagram for X', 'show me the class hierarchy', or asks for any visual representation of code or systems."
user-invocable: true
args:
  - name: type
    description: "Diagram type: flowchart, sequence, class, state, er, c4 (optional — auto-detected)"
    required: false
  - name: subject
    description: What to diagram (optional)
    required: false
---

## Execution

1. Parse `$ARGUMENTS` for diagram type and subject
2. Analyze the codebase to produce diagram content
3. Render via draw.io MCP (mermaid or CSV format)
4. Present the diagram to the user
