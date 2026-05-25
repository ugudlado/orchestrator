## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $200.6396 |
| Input tokens | 1,905 |
| Output tokens | 394,306 |
| Duration | 156.4m |
| Steps | 36 |
| Rework ratio | 53.3% |

## Median Delta

| Metric | This run | Repo median (n=14) | Delta |
| --- | --- | --- | --- |
| Cost    | $200.6396 | $34.3042 | 5.85x |
| Duration | 156.4m | 87.8m | 1.78x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $200.6396 | 1,905 | 394,306 | 156.4m | 36 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $5.4533 | 27 | 13,041 | 2.7m | 1 |
| developer | $129.7924 | 1,562 | 251,175 | 118.6m | 26 |
| discoverer | $5.5133 | 28 | 6,803 | 2.1m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $22.8144 | 100 | 49,620 | 8.1m | 1 |
| workflow-learner | $37.0662 | 188 | 73,667 | 24.8m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $200.6396 | 1,905 | 394,306 | 8 |
| none | $0.0000 | 0 | 0 | 21 |
| unknown | $0.0000 | 0 | 0 | 7 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 181 | — | — | — |
| Read | 40 | — | — | — |
| Edit | 39 | — | — | — |
| TaskUpdate | 37 | — | — | — |
| Write | 32 | — | — | — |
| TaskCreate | 21 | — | — | — |
| AskUserQuestion | 8 | — | — | — |
| ToolSearch | 5 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 6 |
| Read | 2 |
| Write | 2 |
| Edit | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 109 |
| Read | 32 |
| Edit | 28 |
| TaskUpdate | 21 |
| Write | 19 |
| TaskCreate | 12 |
| AskUserQuestion | 4 |
| ToolSearch | 3 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 8 |
| Read | 2 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 18 |
| TaskUpdate | 7 |
| Write | 5 |
| TaskCreate | 4 |
| AskUserQuestion | 1 |
| Edit | 1 |
| Read | 1 |
| ToolSearch | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Bash | 40 |
| Edit | 9 |
| TaskUpdate | 9 |
| TaskCreate | 5 |
| Write | 5 |
| AskUserQuestion | 3 |
| Read | 3 |
| ToolSearch | 1 |

## Anomalies

### Tool not in role

- developer used AskUserQuestion (4 calls) — not in declared tools list
- developer used TaskCreate (12 calls) — not in declared tools list
- developer used TaskUpdate (21 calls) — not in declared tools list
- developer used ToolSearch (3 calls) — not in declared tools list
- reviewer used AskUserQuestion (1 calls) — not in declared tools list
- reviewer used TaskCreate (4 calls) — not in declared tools list
- reviewer used TaskUpdate (7 calls) — not in declared tools list
- reviewer used ToolSearch (1 calls) — not in declared tools list
- workflow-learner used AskUserQuestion (3 calls) — not in declared tools list
- workflow-learner used TaskCreate (5 calls) — not in declared tools list
- workflow-learner used TaskUpdate (9 calls) — not in declared tools list
- workflow-learner used ToolSearch (1 calls) — not in declared tools list
