## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $42.2641 |
| Input tokens | 254 |
| Output tokens | 85,938 |
| Duration | 30.0m |
| Steps | 14 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=13) | Delta |
| --- | --- | --- | --- |
| Cost    | $42.2641 | $32.0323 | 1.32x |
| Duration | 30.0m | 70.6m | 0.43x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $42.2641 | 254 | 85,938 | 30.0m | 14 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $5.8652 | 35 | 12,920 | 2.8m | 1 |
| developer | $23.4607 | 140 | 51,680 | 11.2m | 4 |
| discoverer | $5.8345 | 33 | 8,806 | 1.7m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $5.3099 | 33 | 10,304 | 2.2m | 1 |
| workflow-learner | $1.7939 | 13 | 2,228 | 12.1m | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $42.2641 | 254 | 85,938 | 8 |
| unknown | $0.0000 | 0 | 0 | 6 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 88 | — | — | — |
| Write | 12 | — | — | — |
| Read | 8 | — | — | — |
| Edit | 5 | — | — | — |
| Agent | 1 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 13 |
| Write | 2 |
| Edit | 1 |
| Read | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 52 |
| Write | 8 |
| Edit | 4 |
| Read | 4 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 14 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 9 |
| Read | 3 |
| Write | 1 |

### workflow-learner

| Tool | Calls |
| --- | --- |
| Agent | 1 |

## Anomalies

### Tool not in role

- workflow-learner used Agent (1 calls) — not in declared tools list
