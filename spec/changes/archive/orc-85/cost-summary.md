## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $103.2363 |
| Input tokens | 38,552 |
| Output tokens | 232,579 |
| Duration | 48.5m |
| Steps | 16 |
| Rework ratio | 0.8% |

## Median Delta

| Metric | This run | Repo median (n=16) | Delta |
| --- | --- | --- | --- |
| Cost    | $103.2363 | $49.5894 | 2.08x |
| Duration | 48.5m | 64.5m | 0.75x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $103.2363 | 38,552 | 232,579 | 48.5m | 16 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $10.7181 | 58 | 29,408 | 4.3m | 1 |
| developer | $58.9095 | 323 | 146,755 | 24.6m | 5 |
| discoverer | $15.0200 | 67 | 22,734 | 3.9m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $16.8171 | 38,091 | 31,923 | 7.3m | 2 |
| workflow-learner | $1.7716 | 13 | 1,759 | 8.4m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $103.2363 | 38,552 | 232,579 | 10 |
| unknown | $0.0000 | 0 | 0 | 6 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 188 | — | — | — |
| Read | 26 | — | — | — |
| Write | 15 | — | — | — |
| Grep | 8 | — | — | — |
| Edit | 7 | — | — | — |
| Glob | 2 | — | — | — |
| Agent | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 18 |
| Read | 3 |
| Write | 2 |
| Edit | 1 |
| Grep | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 112 |
| Read | 12 |
| Write | 10 |
| Edit | 5 |
| Grep | 4 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 18 |
| Read | 11 |
| Grep | 3 |
| Glob | 2 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 40 |
| Write | 2 |
| Edit | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Agent | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
