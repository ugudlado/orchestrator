## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $117.4645 |
| Input tokens | 599 |
| Output tokens | 361,916 |
| Duration | 58.4m |
| Steps | 17 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=12) | Delta |
| --- | --- | --- | --- |
| Cost    | $117.4645 | $25.2171 | 4.66x |
| Duration | 58.4m | 87.8m | 0.67x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $117.4645 | 599 | 361,916 | 58.4m | 17 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $10.1366 | 51 | 31,991 | 5.2m | 1 |
| developer | $81.0930 | 408 | 255,928 | 41.4m | 8 |
| discoverer | $5.9617 | 38 | 10,015 | 1.5m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 5 |
| reviewer | $10.1366 | 51 | 31,991 | 5.2m | 1 |
| workflow-learner | $10.1366 | 51 | 31,991 | 5.2m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $117.4645 | 599 | 361,916 | 12 |
| unknown | $0.0000 | 0 | 0 | 5 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 104 | — | — | — |
| Read | 93 | — | — | — |
| Write | 22 | — | — | — |
| Edit | 12 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 9 |
| Read | 8 |
| Write | 2 |
| Edit | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 72 |
| Read | 64 |
| Write | 16 |
| Edit | 8 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 5 |
| Read | 5 |
| Edit | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 9 |
| Read | 8 |
| Write | 2 |
| Edit | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Bash | 9 |
| Read | 8 |
| Write | 2 |
| Edit | 1 |

## Anomalies

### Tool not in role

- discoverer used Edit (1 calls) — not in declared tools list
