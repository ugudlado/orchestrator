---
name: systematic-debugging
description: "Systematic debugging — reproduce, trace, hypothesize, verify. This skill should be used when the user says 'debug this', 'why is this failing', 'trace the error', 'find the root cause', or encounters a bug, test failure, or unexpected behavior that needs methodical investigation."
user-invocable: true
args:
  - name: issue
    description: Description of the bug or failing test (optional)
    required: false
---

## Scope

This skill owns the **trace → hypothesize** middle of root-cause investigation.
It is invoked at T-2 of the bugfix task plan (`tasks.yaml`),
_after_ the bug is reproducible (T-1) and _before_ the root cause is written to
`diagnosis.md` (T-3, owned by `config/steps/diagnose.yaml`).

It does not reproduce the bug (T-1's job) and does not author the diagnosis
document (T-3's job). It produces the evidence chain those steps depend on.

Premise: a reliable reproduction already exists. If it does not, stop and
report that T-1 is incomplete — do not guess at causes from a flaky repro.

## Execution

Parse `$ARGUMENTS` for the bug description, failing test, or symptom. Then run
the four phases in order. Do not skip ahead — a fix proposed before Phase 3 is
complete is a guess.

### Phase 1 — Reproduce (confirm, don't perform)

Confirm the reproduction from T-1 triggers reliably in this session. Capture the
exact command and its observed-vs-expected output. If it does not reproduce,
stop here and report — there is nothing to trace.

### Phase 2 — Trace

Follow the data and control flow from symptom backward toward origin. Gather
evidence at each hop; do not theorize yet.

- Start at the observable failure (stack trace, assertion, wrong output) and
  walk upstream through callers, not downstream from a suspected cause.
- At each step record the _actual_ value/state vs the _expected_ one, with a
  concrete `file:line` reference.
- Inspect the boundary where actual first diverges from expected. That boundary,
  not the crash site, is where the root cause lives.
- Prefer evidence (logs, prints, a debugger, a narrowed test) over reasoning
  about what the code "should" do.

### Phase 3 — Hypothesize

Form one falsifiable hypothesis of the root cause: a specific `file:line` plus
why that code produces the observed divergence. State what you would expect to
change if the hypothesis were true.

If more than one cause is plausible, rank them and test the cheapest-to-falsify
first. A hypothesis you cannot state as a testable prediction is not yet a
hypothesis — return to Phase 2.

### Phase 4 — Verify

Prove the hypothesis before proposing any fix:

- Make the smallest possible intervention that the hypothesis predicts will
  change behavior, and confirm it does (and that reverting it restores the bug).
- Confirm the cause explains _all_ observed symptoms, not just the loudest one.
- Distinguish root cause from symptom: if the "cause" is itself caused by
  something upstream, keep tracing.

## Output

Present to the user (and, in a bugfix workflow, hand to T-3 for `diagnosis.md`):

- **Symptom** — the observable failure, from the reproduction.
- **Root cause** — `file:line` and why that code is wrong (mechanism, not
  restatement of the symptom).
- **Evidence** — the trace chain and the Phase 4 verification that confirms it.
- **Proposed fix direction** — one sentence targeting the root cause, not the
  symptom. Not an implementation.

Do not write `diagnosis.md` from this skill — that is `diagnose.yaml`'s
contract. Stop at the evidence-backed root cause.
