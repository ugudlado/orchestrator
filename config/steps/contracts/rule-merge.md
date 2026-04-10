# Rule Merge & Evaluation Contracts

Contracts for how rules are evaluated, merged, and classified at runtime.

---

## Rules-When Evaluation

Schemas use `rules_when:` on step references to inject conditional rules at
runtime. The evaluation protocol:

1. Read `state.yaml.flags` to get resolved flag values.
2. For each key in `rules_when:`:
   - If key matches a flag name and flag is truthy → activate those rules.
   - If key is `not <flag_name>` and flag is falsy (or absent) → activate those rules.
   - If key doesn't match any flag → ignore (no error).
3. Activated rules become **additional** rules for the step, appended after the
   step contract's own `rules:` section.
4. If both a `when:` condition (positive) and `not when:` condition match, this
   is a conflict — only the positive match applies.

## Rule Merge Contract

Every step executes with a **merged rule set** — the union of rules from multiple
sources, deduplicated and filtered by flag conditions. This contract defines the
deterministic algorithm that any agent (or the orchestrator) uses to compute the
merged rules for a given step.

### Rule Source Taxonomy

Rules come from 5 sources, listed in precedence order (highest to lowest):

| Source | Location | Format | Precedence |
|--------|----------|--------|------------|
| Step entry injections | Schema `phases[].steps[]` — `rules_when:` and `extra_rules:` | Plain strings | 1 (highest) |
| Step contract rules | `$ORCHESTRATOR_HOME/config/steps/<step>.yaml` — `rules:` | Plain strings | 2 |
| Phase rules | Schema `phases[].rules:` | Plain strings | 3 |
| Schema rules | Schema top-level `rules:` | Named (`id:`, `rule:`, optional `when:`) | 4 |
| Project rules | `project.yaml` `rules:` | Named (`id:`, `rule:`, optional `when:`) | 5 (lowest) |

### Rule Formats

**Named rules** (schema and project levels):
```yaml
- id: tdd-default
  when: tdd_required        # optional — omit for always-active
  rule: Write failing test before implementation.
```

**Plain string rules** (phase, step contract, and injected):
```yaml
- Keep scope explicit (in-scope and out-of-scope).
- Fix root cause, not symptoms.
```

**Injected rules** (from `rules_when:` and `extra_rules:` on step entries):
- `rules_when:` → conditional on flags, evaluated per § Rules-When Evaluation
- `extra_rules:` → always included

### Merge Algorithm

Given: `state.yaml.flags`, `project.yaml`, schema YAML, current phase, current step.

```
MERGE(flags, project, schema, phase, step_entry, step_contract):

  1. COLLECT named rules:
     a. Start with project.yaml rules[] → named_rules{}  (keyed by id)
     b. For each schema rules[] entry:
        - If same id exists in named_rules → OVERRIDE (schema wins over project)
        - Else → ADD to named_rules
     c. Result: named_rules{} with one entry per unique id

  2. FILTER named rules by when-conditions:
     For each entry in named_rules:
       - If entry has no `when:` → KEEP (always active)
       - If entry has `when: <flag>` and flags[flag] is truthy → KEEP
       - If entry has `when: <flag>` and flags[flag] is falsy → REMOVE
     Result: active_named_rules[]

  3. COLLECT plain rules (no deduplication — accumulate all):
     a. phase_rules[] = schema.phases[current].rules[]  (plain strings)
     b. step_rules[] = step_contract.rules[]  (plain strings)
        FILTER learned rules by repo scope:
        For each rule in step_rules[]:
          If rule has `<!-- learned: ... repo: X -->` metadata:
            If X == $REPO_NAME or X == "*": KEEP
            Else: SKIP (rule is scoped to a different repo)
          If rule has `<!-- learned: ... -->` but no `repo:` field: KEEP (backward compat = universal)
          If rule has no metadata (permanent rule): KEEP
     c. injected_rules[] = evaluate rules_when(step_entry, flags)
        per § Rules-When Evaluation
     d. extra[] = step_entry.extra_rules[]  (always included)

  4. ASSEMBLE merged list in precedence order:
     merged = []
     merged += injected_rules[]     # source 1 (highest)
     merged += extra[]              # source 1
     merged += step_rules[]         # source 2
     merged += phase_rules[]        # source 3
     merged += active_named_rules[] # sources 4+5 (extract rule: text only)

  5. RETURN merged[]
```

### Precedence Semantics

- **Named rules**: Deduplicated by `id`. Higher-precedence source wins on collision
  (schema overrides project). Within the same source, original order preserved.
- **Plain/injected rules**: Never deduplicated. All accumulate. Two identical strings
  from different sources both appear in the merged list.
- **Output order**: Highest precedence first (injected → step → phase → named).
  Within each source, original declaration order preserved.

### Example

Given:
- project.yaml: `[{id: evidence-based, rule: "Show output"}, {id: tdd, when: tdd_required, rule: "Write tests first"}]`
- schema rules: `[{id: tdd, when: tdd_required, rule: "Write failing test before impl"}]`
- phase rules: `["Keep scope explicit"]`
- step contract rules: `["Verify every criterion"]`
- step entry extra_rules: `["Fix root cause"]`
- flags: `{tdd_required: false}`

Merge result:
```
1. "Fix root cause"              # extra_rules (source 1)
2. "Verify every criterion"      # step contract (source 2)
3. "Keep scope explicit"         # phase (source 3)
4. "Show output"                 # project named, id: evidence-based (source 5, no when → active)
```

Note: `tdd` rule is REMOVED because `tdd_required` is false. Schema's version
overrode project's version (same id), but both are filtered out by the when-condition.

---

## Change Type Detection

The orchestrator classifies each change as "code" or "config_docs" to adapt
agent spawning, TDD applicability, and review behavior. This prevents false
expectations (e.g., TDD for YAML-only changes) and allows efficient inline
execution for non-code changes without violating flag contracts.

### Extension Classification

| Category | Extensions |
|----------|-----------|
| Code | `.ts`, `.tsx`, `.js`, `.jsx`, `.py`, `.rs`, `.go`, `.java`, `.rb`, `.swift`, `.kt`, `.c`, `.cpp`, `.h`, `.cs`, `.vue`, `.svelte` |
| Config/Docs | `.yaml`, `.yml`, `.json`, `.toml`, `.md`, `.mdx`, `.txt`, `.css`, `.scss`, `.html`, `.xml`, `.env`, `.sh`, `.bash`, `.zsh` |
| Unknown | Any extension not in either list → treat as **code** (conservative) |

### Detection Algorithm

```
DETECT_CHANGE_TYPE(tasks_md):
  1. Parse all `Files:` fields from tasks.md
  2. Extract file extensions from each path
  3. Classify each extension per table above
  4. If ALL extensions are config/docs → change_type = "config_docs"
     If ANY extension is code or unknown → change_type = "code"
  5. Write change_type to state.yaml
```

### Flag Adaptation Rules

When `change_type = "config_docs"`:

| Flag | Adaptation | Rationale |
|------|-----------|-----------|
| `agents` | Steps with `agent: developer` MAY execute inline instead of spawning. Log `agent: inline (config_docs)` in step_history. | No benefit to spawning a developer agent for YAML/markdown edits. |
| `agents` | Steps with `agent: reviewer` MAY execute inline instead of spawning. Log `agent: inline (config_docs)` in step_history. | Structural review is faster inline for non-code. |
| `tdd_required` | Effective value becomes `false` regardless of flag setting. Tasks omit RED/GREEN/REFACTOR pattern. Log adaptation in state.yaml. | No code to test — TDD is meaningless. |
| `auto_approve_phases` | No change — phases still need signoff per flag. | Signoff is about scope control, not code quality. |

When `change_type = "code"`: No adaptations — all flags apply as-is.

### State Recording

When change type causes flag adaptation, record it in state.yaml:

```yaml
change_type: config_docs
flag_adaptations:
  - flag: tdd_required
    original: true
    effective: false
    reason: "config_docs change — no code to test"
  - flag: agents
    original: true
    effective: true
    note: "agents flag honored but developer/reviewer steps may execute inline"
```
