## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $103.6044 |
| Input tokens | 429,372 |
| Output tokens | 227,573 |
| Duration | 63.9m |
| Steps | 16 |
| Rework ratio | 7.0% |

## Median Delta

| Metric | This run | Repo median (n=1) | Delta |
| --- | --- | --- | --- |
| Cost    | $103.6044 | $103.6044 | 1.00x |
| Duration | 63.9m | 63.9m | 1.00x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $103.6044 | 429,372 | 227,573 | 63.9m | 16 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $11.2041 | 35,129 | 28,636 | 7.0m | 1 |
| developer | $60.1602 | 223,891 | 144,417 | 34.5m | 5 |
| discoverer | $7.2854 | 42,120 | 10,871 | 3.3m | 2 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $15.3436 | 83,375 | 29,873 | 6.6m | 1 |
| workflow-learner | $9.6111 | 44,857 | 13,776 | 12.6m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-8 | $103.6044 | 429,372 | 227,573 | 9 |
| none | $0.0000 | 0 | 0 | 1 |
| unknown | $0.0000 | 0 | 0 | 6 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 67 | — | — | — |
| Read | 62 | — | — | — |
| Edit | 27 | — | — | — |
| Write | 12 | — | — | — |
| Glob | 7 | — | — | — |
| Grep | 5 | — | — | — |
| Agent | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Read | 8 |
| Edit | 5 |
| Bash | 4 |
| Write | 2 |
| Glob | 1 |
| Grep | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Read | 39 |
| Bash | 33 |
| Edit | 21 |
| Write | 9 |
| Glob | 4 |
| Grep | 4 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 9 |
| Read | 3 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 17 |
| Read | 7 |
| Edit | 1 |
| Write | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Read | 5 |
| Bash | 4 |
| Glob | 2 |
| Agent | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
