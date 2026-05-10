---
id: ORC-53
title: Typed configuration dataclasses — replace module constants
status: Backlog
assignee: []
created_date: '2026-05-10 10:24'
labels:
  - algotrade
dependencies: []
priority: high
ordinal: 50000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Replace 166 module-level constants in settings.py with typed dataclasses in DI container. Currently 36 files import raw globals; makes test mocking fragile.
<!-- SECTION:DESCRIPTION:END -->
