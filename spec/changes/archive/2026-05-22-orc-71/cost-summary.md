## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $102.6688 |
| Input tokens | 94,416 |
| Output tokens | 96,231 |
| Duration | 60.6m |
| Steps | 10 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=22) | Delta |
| --- | --- | --- | --- |
| Cost    | $102.6688 | $13.3640 | 7.68x |
| Duration | 60.6m | 35.1m | 1.73x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $102.6688 | 94,416 | 96,231 | 60.6m | 10 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $23.8607 | 108 | 24,273 | 27.8m | 1 |
| developer | $33.4026 | 13,142 | 36,891 | 10.6m | 1 |
| discoverer | $1.9501 | 8,352 | 5,228 | 12.6m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 5 |
| reviewer | $15.8322 | 168 | 18,262 | 4.3m | 1 |
| workflow-learner | $27.6231 | 72,646 | 11,577 | 5.3m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $100.7187 | 86,064 | 91,003 | 4 |
| claude-sonnet-4-6 | $1.9501 | 8,352 | 5,228 | 1 |
| unknown | $0.0000 | 0 | 0 | 5 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 120 | 1.3m | 0.6s | 8.1s |
| Read | 59 | 6.2m | 6.3s | 5.9m |
| Edit | 47 | 9.0m | 11.5s | 39.5s |
| Write | 7 | 2.1s | 0.3s | 0.3s |
| Skill | 1 | 0.2s | 0.2s | 0.2s |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Read | 20 |
| Edit | 10 |
| Bash | 3 |
| Write | 2 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 41 |
| Edit | 16 |
| Read | 13 |
| Write | 3 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 15 |
| Read | 11 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 44 |
| Read | 4 |
| Write | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Edit | 21 |
| Bash | 17 |
| Read | 11 |
| Skill | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Skill (1 calls) — not in declared tools list
