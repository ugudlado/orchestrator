## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $137.7846 |
| Input tokens | 651 |
| Output tokens | 382,203 |
| Duration | 54.1m |
| Steps | 19 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=21) | Delta |
| --- | --- | --- | --- |
| Cost    | $137.7846 | $62.5319 | 2.20x |
| Duration | 54.1m | 54.1m | 1.00x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $137.7846 | 651 | 382,203 | 54.1m | 19 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $11.7034 | 54 | 34,767 | 4.6m | 1 |
| developer | $105.3304 | 486 | 312,903 | 41.2m | 9 |
| discoverer | $11.1377 | 54 | 17,895 | 2.2m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $7.8367 | 44 | 14,880 | 2.5m | 1 |
| workflow-learner | $1.7764 | 13 | 1,758 | 3.7m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $137.7846 | 651 | 382,203 | 13 |
| unknown | $0.0000 | 0 | 0 | 6 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 143 | — | — | — |
| Read | 108 | — | — | — |
| Write | 22 | — | — | — |
| Edit | 10 | — | — | — |
| Glob | 5 | — | — | — |
| Agent | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 11 |
| Read | 10 |
| Write | 2 |
| Edit | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 99 |
| Read | 90 |
| Write | 18 |
| Edit | 9 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 17 |
| Read | 8 |
| Glob | 5 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 16 |
| Write | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Agent | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
