## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $62.5319 |
| Input tokens | 314 |
| Output tokens | 156,529 |
| Duration | 30.8m |
| Steps | 15 |
| Rework ratio | 13.1% |

## Median Delta

| Metric | This run | Repo median (n=19) | Delta |
| --- | --- | --- | --- |
| Cost    | $62.5319 | $46.5412 | 1.34x |
| Duration | 30.8m | 51.9m | 0.59x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $62.5319 | 314 | 156,529 | 30.8m | 15 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $8.6860 | 42 | 24,699 | 4.1m | 1 |
| developer | $34.7441 | 168 | 98,796 | 16.5m | 4 |
| discoverer | $8.7907 | 44 | 14,223 | 1.7m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $8.1875 | 44 | 16,606 | 2.0m | 2 |
| workflow-learner | $2.1234 | 16 | 2,205 | 6.5m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $62.5319 | 314 | 156,529 | 8 |
| none | $0.0000 | 0 | 0 | 1 |
| unknown | $0.0000 | 0 | 0 | 6 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 86 | — | — | — |
| Read | 18 | — | — | — |
| Write | 12 | — | — | — |
| Edit | 5 | — | — | — |
| Glob | 5 | — | — | — |
| Agent | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 11 |
| Read | 2 |
| Write | 2 |
| Edit | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 44 |
| Read | 8 |
| Write | 8 |
| Edit | 4 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 8 |
| Read | 6 |
| Glob | 5 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 21 |
| Read | 2 |
| Write | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Bash | 2 |
| Agent | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
