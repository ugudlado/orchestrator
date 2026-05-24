## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $49.4831 |
| Input tokens | 10,778 |
| Output tokens | 167,773 |
| Duration | 87.2m |
| Steps | 12 |
| Rework ratio | 4.3% |

## Median Delta

| Metric | This run | Repo median (n=22) | Delta |
| --- | --- | --- | --- |
| Cost    | $49.4831 | $14.3754 | 3.44x |
| Duration | 87.2m | 37.1m | 2.35x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $49.4831 | 10,778 | 167,773 | 87.2m | 12 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $16.6551 | 73 | 20,444 | 5.3m | 1 |
| developer | $25.2457 | 713 | 112,200 | 61.8m | 2 |
| discoverer | $1.4237 | 9,778 | 4,931 | 4.0m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 5 |
| reviewer | $3.4773 | 137 | 19,096 | 9.9m | 2 |
| workflow-learner | $2.6813 | 77 | 11,102 | 6.2m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $16.6551 | 73 | 20,444 | 1 |
| claude-sonnet-4-6 | $32.8281 | 10,705 | 147,329 | 6 |
| unknown | $0.0000 | 0 | 0 | 5 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 324 | 5.5m | 1.0s | 9.4s |
| Read | 172 | 46.9s | 0.3s | 0.7s |
| Edit | 133 | 37.8s | 0.3s | 0.4s |
| Write | 20 | 6.0s | 0.3s | 0.3s |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Read | 11 |
| Bash | 4 |
| Write | 2 |
| Edit | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 189 |
| Read | 130 |
| Edit | 127 |
| Write | 15 |

### discoverer

| Tool | Calls |
| --- | --- |
| Read | 15 |
| Bash | 8 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 87 |
| Read | 8 |
| Write | 2 |
| Edit | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Bash | 36 |
| Read | 8 |
| Edit | 4 |

## Anomalies

### Tool not in role

_No anomalies detected._
