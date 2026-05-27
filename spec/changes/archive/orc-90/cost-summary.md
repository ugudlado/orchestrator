## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $52.3468 |
| Input tokens | 246 |
| Output tokens | 117,261 |
| Duration | 19.9m |
| Steps | 18 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=23) | Delta |
| --- | --- | --- | --- |
| Cost    | $52.3468 | $52.3468 | 1.00x |
| Duration | 19.9m | 51.9m | 0.38x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $52.3468 | 246 | 117,261 | 19.9m | 18 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $6.5735 | 32 | 17,770 | 3.1m | 1 |
| developer | $26.2941 | 128 | 71,080 | 12.4m | 4 |
| discoverer | $11.4148 | 52 | 18,442 | 2.9m | 4 |
| inline | $0.0000 | 0 | 0 | 0.0s | 6 |
| reviewer | $8.0644 | 33 | 9,968 | 1.5m | 1 |
| workflow-learner | $0.0000 | 1 | 1 | 0.0s | 2 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $52.3467 | 245 | 117,260 | 7 |
| claude-sonnet-4-6 | $0.0000 | 1 | 1 | 1 |
| none | $0.0000 | 0 | 0 | 3 |
| unknown | $0.0000 | 0 | 0 | 7 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 47 | — | — | — |
| Read | 35 | — | — | — |
| Write | 12 | — | — | — |
| Grep | 10 | — | — | — |
| Edit | 5 | — | — | — |

## MCP Calls

_No MCP calls._

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Bash | 6 |
| Read | 4 |
| Write | 2 |
| Edit | 1 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 24 |
| Read | 16 |
| Write | 8 |
| Edit | 4 |

### discoverer

| Tool | Calls |
| --- | --- |
| Grep | 10 |
| Read | 9 |
| Bash | 7 |
| Write | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 10 |
| Read | 6 |
| Write | 1 |

## Anomalies

### Tool not in role

_No anomalies detected._
