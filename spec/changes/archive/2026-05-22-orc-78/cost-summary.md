## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $7.0515 |
| Input tokens | 123,116 |
| Output tokens | 29,433 |
| Duration | 6.3m |
| Steps | 11 |
| Rework ratio | 1.8% |

## Median Delta

| Metric | This run | Repo median (n=21) | Delta |
| --- | --- | --- | --- |
| Cost    | $7.0515 | $12.4173 | 0.57x |
| Duration | 6.3m | 33.9m | 0.18x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $7.0515 | 123,116 | 29,433 | 6.3m | 11 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $5.1692 | 56 | 12,313 | 3.4m | 1 |
| developer | $0.5490 | 123,000 | 12,000 | 0.0s | 2 |
| discoverer | $1.3333 | 60 | 5,120 | 2.9m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 4 |
| reviewer | $0.0000 | 0 | 0 | 0.0s | 2 |
| workflow-learner | $0.0000 | 0 | 0 | 0.0s | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $5.1692 | 56 | 12,313 | 1 |
| claude-sonnet-4-6 | $1.8823 | 123,060 | 17,120 | 5 |
| unknown | $0.0000 | 0 | 0 | 5 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 35 | 9.4s | 0.3s | 0.3s |
| Read | 14 | 3.7s | 0.3s | 0.3s |
| Write | 3 | 0.9s | 0.3s | 0.3s |
| Edit | 1 | 0.3s | 0.3s | 0.3s |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 11 |
| Write | 2 |
| Edit | 1 |
| Read | 1 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 24 |
| Read | 13 |
| Write | 1 |

## Anomalies

### Tool not in role

_No anomalies detected._
