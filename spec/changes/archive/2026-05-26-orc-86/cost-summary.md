## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $62.6026 |
| Input tokens | 317 |
| Output tokens | 179,413 |
| Duration | 30.1m |
| Steps | 15 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=15) | Delta |
| --- | --- | --- | --- |
| Cost    | $62.6026 | $36.5761 | 1.71x |
| Duration | 30.1m | 70.6m | 0.43x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $62.6026 | 317 | 179,413 | 30.1m | 15 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $7.9858 | 39 | 27,865 | 3.8m | 1 |
| developer | $31.9430 | 156 | 111,460 | 15.4m | 4 |
| discoverer | $7.0387 | 34 | 11,821 | 1.7m | 2 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $13.8498 | 75 | 26,756 | 4.3m | 1 |
| workflow-learner | $1.7853 | 13 | 1,511 | 4.9m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $62.6026 | 317 | 179,413 | 8 |
| unknown | $0.0000 | 0 | 0 | 7 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 75 | — | — | — |
| Edit | 26 | — | — | — |
| Read | 21 | — | — | — |
| Write | 14 | — | — | — |
| Grep | 3 | — | — | — |
| Agent | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 7 |
| Edit | 5 |
| Read | 3 |
| Write | 2 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 28 |
| Edit | 20 |
| Read | 12 |
| Write | 8 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 7 |
| Grep | 3 |
| Read | 3 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 33 |
| Read | 3 |
| Write | 3 |
| Edit | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Agent | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
