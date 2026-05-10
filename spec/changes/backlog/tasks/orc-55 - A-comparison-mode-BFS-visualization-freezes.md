---
id: ORC-55
title: 'A* comparison mode: BFS visualization freezes'
status: Backlog
assignee: []
created_date: '2026-05-10 10:24'
labels:
  - algoviz
  - bug
dependencies: []
ordinal: 52000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fix bug where BFS freezes on last frame when A* runs longer. Both share currentStep counter; BFS clamps to bfsSnapshots.length-1. Need separate step counters.
<!-- SECTION:DESCRIPTION:END -->
