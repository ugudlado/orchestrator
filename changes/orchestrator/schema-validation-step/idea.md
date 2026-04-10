# Schema Self-Validation on Load

## Idea
When the orchestrate skill loads a schema YAML (SKILL.md section 1, step 3), validate it against `grammar.yaml` before proceeding. Currently, malformed schemas (missing `phases`, invalid `step_entry` forms, typo in a flag name) are only caught when execution hits the broken part -- sometimes deep into a multi-hour workflow. A validation pass at load time would catch: (1) missing required fields per grammar, (2) step references that don't have a matching contract YAML in `config/steps/`, (3) agent references in step contracts that don't have a matching `.md` in `agents/`, (4) flag references in `if`/`if not` conditions that aren't declared in `defaults` or `flags`. This is essentially the "deep doctor" applied to a single schema at runtime.

## Why Now
The grammar file defines the full structural contract but nothing enforces it. As `/learn` and `/workflow-improve` modify schemas automatically, the risk of introducing structural errors increases. Catching them at load time (before worktree creation, agent spawning, etc.) is much cheaper than catching them mid-workflow.

## Prototype
Error output example:
```
[orchestrate] Schema validation failed for feature.yaml:
  - Step "desgin-exploration" (phase: specify) has no matching contract in config/steps/
  - Flag "tdd" referenced in step condition but not declared in defaults or flags
  - Phase "implment" referenced in requires but does not exist
Aborting workflow. Fix schema and retry.
```

## Priority
- User value: 6/10
- Strategic fit: 7/10
- Technical leverage: 5/10
- Effort: medium
- **Score: 3.0**
