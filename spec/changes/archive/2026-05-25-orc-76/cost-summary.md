## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $42.4621 |
| Input tokens | 7,166 |
| Output tokens | 164,113 |
| Duration | 93.1m |
| Steps | 36 |
| Rework ratio | 4.7% |

## Median Delta

| Metric | This run | Repo median (n=23) | Delta |
| --- | --- | --- | --- |
| Cost    | $42.4621 | $15.1887 | 2.80x |
| Duration | 93.1m | 38.0m | 2.45x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $42.4621 | 7,166 | 164,113 | 93.1m | 36 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $9.8686 | 47 | 14,426 | 5.5m | 1 |
| developer | $26.4680 | 1,471 | 124,187 | 73.5m | 21 |
| discoverer | $2.5878 | 71 | 13,083 | 8.4m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 10 |
| reviewer | $1.3810 | 109 | 3,994 | 2.5m | 1 |
| workflow-learner | $2.1567 | 5,468 | 8,423 | 3.3m | 2 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| __default__ | $0.1650 | 5,000 | 1,200 | 1 |
| claude-opus-4-7 | $9.8686 | 47 | 14,426 | 1 |
| claude-sonnet-4-6 | $32.4285 | 2,119 | 148,487 | 24 |
| unknown | $0.0000 | 0 | 0 | 10 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 281 | 6.4m | 1.4s | 11.7s |
| Read | 231 | 1.0m | 0.3s | 1.4s |
| Edit | 82 | 21.5s | 0.3s | 0.3s |
| Write | 34 | 9.2s | 0.3s | 0.4s |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Read | 10 |
| Bash | 5 |
| Write | 2 |
| Edit | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 214 |
| Read | 179 |
| Edit | 76 |
| Write | 29 |

### discoverer

| Tool | Calls |
| --- | --- |
| Read | 25 |
| Bash | 22 |
| Write | 2 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 18 |
| Read | 3 |
| Write | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Bash | 22 |
| Read | 14 |
| Edit | 5 |

## Anomalies

### Tool not in role

_No anomalies detected._
