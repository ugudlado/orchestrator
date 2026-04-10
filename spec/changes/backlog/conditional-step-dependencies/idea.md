# Conditional Step Dependencies

## Idea
Add a `depends_on` field to step entries in schemas so steps can declare explicit dependencies on other steps' outputs. Currently, step ordering is purely positional (list order in the phase). This works but creates implicit coupling -- if someone reorders steps in a schema, they might break an input dependency (e.g., `create-or-refresh-artifacts` depends on `explore` having produced `discovery.md`). Adding `depends_on: [explore]` makes this explicit. The grammar already has `requires` at the phase level and `requires` on output artifacts -- this extends the pattern to steps.

## Why Now
As the number of schemas grows and `/learn` and `/workflow-improve` modify schemas automatically, positional ordering becomes fragile. The grammar file is the right place to formalize this, and the dispatch loop already reads step contracts sequentially -- adding a dependency check before execution is straightforward.

## Prototype
Grammar extension:
```yaml
step_entry:
  forms:
    - pattern:
        id: string
        depends_on: list<string>   # Step IDs that must have completed first
```

Schema usage:
```yaml
steps:
  - load-project-context
  - explore
  - design-exploration if design
  - id: create-or-refresh-artifacts
    depends_on: [explore]          # explicit: needs discovery.md
```

## Priority
- User value: 5/10
- Strategic fit: 6/10
- Technical leverage: 6/10
- Effort: medium
- **Score: 2.8**
