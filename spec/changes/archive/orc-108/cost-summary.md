## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $8.4652 |
| Input tokens | 10,303 |
| Output tokens | 72,837 |
| Duration | 19.9m |
| Steps | 26 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=28) | Delta |
| --- | --- | --- | --- |
| Cost    | $8.4652 | $50.4447 | 0.17x |
| Duration | 19.9m | 47.8m | 0.42x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $8.4652 | 10,303 | 72,837 | 19.9m | 26 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $2.9140 | 93 | 33,866 | 7.6m | 1 |
| developer | $1.5955 | 70 | 6,160 | 1.1m | 10 |
| discoverer | $1.0627 | 35 | 7,487 | 3.3m | 1 |
| ideator | $0.1632 | 7 | 430 | 8.4s | 1 |
| none | $0.0000 | 0 | 0 | 0.0s | 10 |
| reviewer | $1.6047 | 9,991 | 16,970 | 4.1m | 1 |
| ux-reviewer | $0.1596 | 7 | 616 | 6.8s | 1 |
| workflow-learner | $0.9654 | 100 | 7,308 | 3.4m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-sonnet-4-6 | $8.4652 | 10,303 | 72,837 | 16 |
| unknown | $0.0000 | 0 | 0 | 10 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 84 | — | — | — |
| Read | 30 | — | — | — |
| Glob | 13 | — | — | — |
| Write | 4 | — | — | — |
| Edit | 3 | — | — | — |
| Agent | 2 | — | — | — |
| Skill | 2 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 23 |
| Read | 19 |
| Glob | 12 |
| Edit | 3 |
| Write | 2 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 10 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 7 |
| Read | 6 |
| Agent | 1 |
| Write | 1 |

### ideator

| Tool | Calls |
| --- | --- |
| Read | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 37 |
| Read | 2 |
| Write | 1 |

### ux-reviewer

| Tool | Calls |
| --- | --- |
| Bash | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Bash | 6 |
| Read | 2 |
| Skill | 2 |
| Agent | 1 |
| Glob | 1 |

## Anomalies

### Tool not in role

_No anomalies detected._
