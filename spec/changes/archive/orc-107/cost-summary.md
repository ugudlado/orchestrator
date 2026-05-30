## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $5.0903 |
| Input tokens | 181 |
| Output tokens | 42,898 |
| Duration | 20.9m |
| Steps | 22 |
| Rework ratio | 32.7% |

## Median Delta

| Metric | This run | Repo median (n=27) | Delta |
| --- | --- | --- | --- |
| Cost    | $5.0903 | $52.3468 | 0.10x |
| Duration | 20.9m | 48.5m | 0.43x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $5.0903 | 181 | 42,898 | 20.9m | 22 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $0.8419 | 26 | 13,107 | 2.3m | 1 |
| developer | $0.4729 | 21 | 1,278 | 15.5s | 3 |
| discoverer | $1.2354 | 42 | 10,018 | 2.2m | 1 |
| ideator | $0.1593 | 7 | 454 | 10.1s | 1 |
| none | $0.0000 | 0 | 0 | 0.0s | 12 |
| reviewer | $1.6663 | 57 | 13,192 | 5.0m | 2 |
| ux-reviewer | $0.1576 | 7 | 426 | 5.2s | 1 |
| workflow-learner | $0.5570 | 21 | 4,423 | 10.8m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-sonnet-4-6 | $5.0903 | 181 | 42,898 | 9 |
| unknown | $0.0000 | 0 | 0 | 13 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 78 | — | — | — |
| Read | 12 | — | — | — |
| Write | 4 | — | — | — |
| Edit | 2 | — | — | — |
| Glob | 2 | — | — | — |
| Agent | 1 | — | — | — |
| Skill | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 5 |
| Read | 4 |
| Write | 2 |
| Edit | 1 |
| Glob | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 3 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 30 |
| Read | 1 |
| Write | 1 |

### ideator

| Tool | Calls |
| --- | --- |
| Read | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 37 |
| Read | 4 |
| Edit | 1 |
| Write | 1 |

### ux-reviewer

| Tool | Calls |
| --- | --- |
| Bash | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Bash | 2 |
| Read | 2 |
| Agent | 1 |
| Glob | 1 |
| Skill | 1 |

## Anomalies

### Tool not in role

_No anomalies detected._
