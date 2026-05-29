## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $61.4157 |
| Input tokens | 304 |
| Output tokens | 176,898 |
| Duration | 34.2m |
| Steps | 22 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=23) | Delta |
| --- | --- | --- | --- |
| Cost    | $61.4157 | $61.4157 | 1.00x |
| Duration | 34.2m | 51.9m | 0.66x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $61.4157 | 304 | 176,898 | 34.2m | 22 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $5.2401 | 26 | 18,807 | 2.4m | 1 |
| developer | $31.4405 | 156 | 112,842 | 14.5m | 6 |
| discoverer | $10.3150 | 50 | 19,892 | 2.7m | 4 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $11.4323 | 56 | 22,835 | 5.7m | 4 |
| workflow-learner | $2.9878 | 16 | 2,522 | 8.8m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $61.4157 | 304 | 176,898 | 10 |
| none | $0.0000 | 0 | 0 | 6 |
| unknown | $0.0000 | 0 | 0 | 6 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 89 | — | — | — |
| Read | 16 | — | — | — |
| Write | 16 | — | — | — |
| Agent | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 6 |
| Read | 2 |
| Write | 2 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 36 |
| Read | 12 |
| Write | 12 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 25 |
| Read | 1 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 21 |
| Write | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Agent | 1 |
| Bash | 1 |
| Read | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
