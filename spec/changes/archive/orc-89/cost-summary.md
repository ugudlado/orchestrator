## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $93.4157 |
| Input tokens | 444 |
| Output tokens | 284,710 |
| Duration | 55.9m |
| Steps | 20 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=21) | Delta |
| --- | --- | --- | --- |
| Cost    | $93.4157 | $48.5427 | 1.92x |
| Duration | 55.9m | 51.9m | 1.08x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $93.4157 | 444 | 284,710 | 55.9m | 20 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $8.2466 | 38 | 29,400 | 5.6m | 4 |
| developer | $57.7262 | 266 | 205,800 | 39.2m | 7 |
| discoverer | $12.8734 | 67 | 23,524 | 2.7m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $11.6783 | 58 | 23,818 | 3.3m | 1 |
| workflow-learner | $2.8912 | 15 | 2,168 | 5.1m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $93.4157 | 444 | 284,710 | 11 |
| none | $0.0000 | 0 | 0 | 3 |
| unknown | $0.0000 | 0 | 0 | 6 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 139 | — | — | — |
| Read | 53 | — | — | — |
| Write | 35 | — | — | — |
| Grep | 16 | — | — | — |
| Glob | 7 | — | — | — |
| Agent | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 13 |
| Read | 5 |
| Write | 4 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 91 |
| Read | 35 |
| Write | 28 |

### discoverer

| Tool | Calls |
| --- | --- |
| Grep | 16 |
| Bash | 12 |
| Glob | 7 |
| Read | 3 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 23 |
| Read | 9 |
| Write | 2 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Agent | 1 |
| Read | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
