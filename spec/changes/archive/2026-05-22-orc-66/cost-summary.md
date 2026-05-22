## Executive Summary

| Metric | Value |
| --- | --- |
| Total cost | $161.5567 |
| Input tokens | 216,399 |
| Output tokens | 176,422 |
| Duration | 180.4m |
| Steps | 10 |
| Rework ratio | 0.0% |

## Median Delta

| Metric | This run | Repo median (n=21) | Delta |
| --- | --- | --- | --- |
| Cost    | $161.5567 | $13.1660 | 12.27x |
| Duration | 180.4m | 33.9m | 5.32x |

## Per-Phase

| Phase | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| main | $161.5567 | 216,399 | 176,422 | 180.4m | 10 |

## Per-Agent

| Agent | Cost | Input Tok | Output Tok | Duration | Steps |
| --- | --- | --- | --- | --- | --- |
| architect | $74.6631 | 274 | 91,568 | 51.2m | 1 |
| developer | $66.4076 | 56,870 | 60,278 | 20.7m | 1 |
| discoverer | $2.3428 | 17,849 | 4,114 | 105.1m | 1 |
| inline | $0.0000 | 0 | 0 | 0.0s | 5 |
| reviewer | $17.6632 | 21,406 | 12,462 | 3.4m | 1 |
| workflow-learner | $0.4800 | 120,000 | 8,000 | 0.0s | 1 |

## Per-Model

| Model | Cost | Input Tok | Output Tok | Steps |
| --- | --- | --- | --- | --- |
| claude-opus-4-7 | $158.7339 | 78,550 | 164,308 | 3 |
| claude-sonnet-4-6 | $2.8228 | 137,849 | 12,114 | 2 |
| unknown | $0.0000 | 0 | 0 | 5 |

## Native Tools

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| Bash | 145 | 3.2m | 1.3s | 8.9s |
| Read | 66 | 101.0m | 1.5m | 99.7m |
| Edit | 39 | 10.9s | 0.3s | 0.3s |
| Write | 20 | 5.6s | 0.3s | 0.4s |

## MCP Calls

| Tool | Calls | Total | Avg | Max |
| --- | --- | --- | --- | --- |
| mcp__plugin_claude-mem_mcp-search__get_observations | 1 | 0.3s | 0.3s | 0.3s |
| mcp__plugin_claude-mem_mcp-search__search | 1 | 0.3s | 0.3s | 0.3s |

## Per-Agent Tool Use

### architect

| Tool | Calls |
| --- | --- |
| Read | 27 |
| Bash | 22 |
| Edit | 15 |
| Write | 10 |

### developer

| Tool | Calls |
| --- | --- |
| Bash | 71 |
| Edit | 24 |
| Read | 16 |
| Write | 8 |

### discoverer

| Tool | Calls |
| --- | --- |
| Bash | 27 |
| Read | 18 |
| Write | 1 |
| mcp__plugin_claude-mem_mcp-search__get_observations | 1 |
| mcp__plugin_claude-mem_mcp-search__search | 1 |

### reviewer

| Tool | Calls |
| --- | --- |
| Bash | 25 |
| Read | 5 |
| Write | 1 |

## Anomalies

### Tool not in role

_No anomalies detected._
