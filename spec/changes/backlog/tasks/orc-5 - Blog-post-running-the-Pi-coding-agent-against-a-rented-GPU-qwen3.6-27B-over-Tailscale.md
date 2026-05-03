---
id: ORC-5
title: >-
  Blog post: running the Pi coding agent against a rented GPU (qwen3.6 27B over
  Tailscale)
status: To Do
assignee: []
created_date: '2026-05-03 10:55'
updated_date: '2026-05-03 11:00'
labels:
  - slug-blog-post-pi-coding-agent-on-rented-gpu
  - feature
  - score-6.0
  - recurrence-1
dependencies: []
priority: low
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
<!-- migrated from spec/changes/backlog.md slug: blog-post-pi-coding-agent-on-rented-gpu -->

**Original score:** 6.0 | **Recurrence:** 1

## Idea

Write a public blog post on the end-to-end setup: Pi coding agent (`~/.pi/agent/models.json` config) pointed at a Vast.ai vLLM pod serving Qwen3.6-27B-AWQ-INT4 over Tailscale, with `hopper` providing the rental harness and the cost cap. Audience: people who want a personal coding agent without paying API rates.

The setup itself is small (~3 lines of JSON), but the supporting context is the interesting part: why 27B AWQ-INT4 specifically, why Vast vs RunPod for the cost target, how Tailscale removes the port-forwarding pain, and how the supervisor keeps you under $100/month even when you forget to terminate.

## Notes

- Blog post lives outside this repo (wherever your blog publishes from). This ticket is just to track the writing intent.
- Defer until cost-analysis.md and setup-termius.md ship in hopper — they're the reference material the post would link to.

---

## Sources

- Pi coding agent: https://shittycodingagent.ai (public URL)
- Pi config path: `~/.pi/agent/models.json`
- `~/code/hopper/docs/cost-analysis.md` (when written)
- `~/code/hopper/docs/setup-termius.md` (when written)
- Vast spike data — VAST_AUTOMATION_LESSONS.md and VAST_SPIKE_TEST_SCENARIOS.md in hopper repo

## Why bother

- The `hopper` README documents *the harness*; this post documents *one specific way to use it* — Pi as the agent. Worth the separation: harness instructions stay generic, agent-specific posts can be added per agent over time.
- Pi-specific config is small enough to be a setup note, but the *whole story* (model choice, cost math, mobile workflow via Termius, Cline as a sibling option) is blog-shaped.
- Concrete usage data from spike sessions (B2 architect test, C2 long-context review per memory obs 16180-16181) gives the post real benchmarks rather than vibes.
<!-- SECTION:DESCRIPTION:END -->
