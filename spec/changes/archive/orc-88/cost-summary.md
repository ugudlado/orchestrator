## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $48.5427 |
| Input tokens | 271 |
| Output tokens | 122,024 |
| Duration | 22.2m |
| Steps | 17 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=19) | Delta |
| --- | --- | --- | --- |
| Cost    | $48.5427 | $48.5427 | 1.00x |
| Duration | 22.2m | 51.9m | 0.43x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $48.5427 | 271 | 122,024 | 22.2m | 17 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $5.9400 | 33 | 18,119 | 2.8m | 1 |
| developer | $23.7598 | 132 | 72,476 | 11.3m | 4 |
| discoverer | $8.8317 | 36 | 12,348 | 1.6m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $8.0097 | 42 | 17,052 | 1.5m | 1 |
| workflow-learner | $2.0015 | 28 | 2,029 | 5.0m | 4 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $48.5427 | 271 | 122,024 | 8 |
| none | $0.0000 | 0 | 0 | 3 |
| unknown | $0.0000 | 0 | 0 | 6 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 67 | — | — | — |
| Read | 19 | — | — | — |
| Write | 12 | — | — | — |
| Grep | 6 | — | — | — |
| Edit | 5 | — | — | — |
| Agent | 1 | — | — | — |
| Skill | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 10 |
| Read | 2 |
| Write | 2 |
| Edit | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 40 |
| Read | 8 |
| Write | 8 |
| Edit | 4 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 6 |
| Grep | 6 |
| Read | 2 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 11 |
| Read | 7 |
| Write | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Agent | 1 |
| Skill | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
- workflow-learner used Skill (1 calls) — not in declared tools list
