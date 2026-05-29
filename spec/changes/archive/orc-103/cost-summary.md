## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $147.8366 |
| Input tokens | 406,831 |
| Output tokens | 435,975 |
| Duration | 99.5m |
| Steps | 15 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=25) | Delta |
| --- | --- | --- | --- |
| Cost    | $147.8366 | $61.4157 | 2.41x |
| Duration | 99.5m | 51.9m | 1.92x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $147.8366 | 406,831 | 435,975 | 99.5m | 15 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $19.7849 | 46,072 | 64,450 | 13.5m | 1 |
| developer | $98.9246 | 230,360 | 322,250 | 67.3m | 5 |
| discoverer | $10.3990 | 31,367 | 18,300 | 4.6m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $14.7787 | 57,593 | 25,093 | 5.8m | 1 |
| workflow-learner | $3.9494 | 41,439 | 5,882 | 8.2m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-8 | $147.8366 | 406,831 | 435,975 | 9 |
| unknown | $0.0000 | 0 | 0 | 6 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 178 | — | — | — |
| Read | 40 | — | — | — |
| Edit | 30 | — | — | — |
| Write | 14 | — | — | — |
| Grep | 2 | — | — | — |
| Agent | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 25 |
| Edit | 5 |
| Read | 3 |
| Write | 2 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 125 |
| Edit | 25 |
| Read | 15 |
| Write | 10 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 13 |
| Read | 8 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Read | 13 |
| Bash | 12 |
| Grep | 2 |
| Write | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Bash | 3 |
| Agent | 1 |
| Read | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
