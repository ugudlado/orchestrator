---
id: ORC-30
title: Pricing lookup tolerant of model date-suffixes (ISSUE-23)
status: Done
assignee: []
created_date: '2026-05-03 10:56'
updated_date: '2026-05-10 16:37'
labels:
  - slug-pricing-date-suffix-lookup
  - bug
  - score-5.8
  - recurrence-1
dependencies: []
priority: low
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: pricing-date-suffix-lookup -->

**Original score:** 5.8 | **Recurrence:** 1

## Idea

Anthropic returns model IDs with date suffixes in JSONLs (e.g. `claude-haiku-4-5-20251001`, future: `claude-sonnet-4-7-20260315`). `config/pricing.yaml` lists unstamped keys. Today the lookup misses, falls through to the `default` block (opus-tier), and overstates cost ~4× for haiku and ~5× for sonnet.

Two clean options:

1. **Strip date suffix in `_compute_cost_usd`** — regex `-\d{8}$` before the pricing lookup. One-line change. Covers all current and future Anthropic dated IDs.
2. **`aliases:` block in pricing.yaml** — explicit mapping `claude-haiku-4-5-20251001: claude-haiku-4-5`. More explicit but requires pricing.yaml edit every time Anthropic ships a dated alias.

Recommended: option 1. Simpler, future-proof, zero maintenance.

## Why Now

Already partially fixed for claude-haiku-4-5-20251001 via explicit alias in 190df05, but the pattern will recur for every future dated release. A 1-line regex strip prevents the next five instances of this bug.

## Prototype

```python
# in _compute_cost_usd, before the pricing.models lookup:
import re
base_model = re.sub(r'-\d{8}$', '', model_id)
price = (pricing.get("models") or {}).get(model_id) or \
        (pricing.get("models") or {}).get(base_model)
```

## Priority

- User value: 4/10
- Strategic fit: 6/10 (pricing-accuracy hygiene)
- Technical leverage: 9/10 (one line, permanent fix)
- Effort: XS
- **Score: 5.8**

## Source

spec/changes/archive/2026-04-19-live-telemetry-and-repeat-until-enforcement/retro.md §ISSUE-23

---
<!-- SECTION:DESCRIPTION:END -->
