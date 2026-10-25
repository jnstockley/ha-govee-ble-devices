# Speed Burst Implementation Plan

**Spec:** `docs/superpowers/specs/2026-10-24-speed-bursts-design.md`

**Goal:** Submit the newest unsent H7102 speed target once per consecutive burst.

**Architecture:** A small batch object holds live caller targets and terminal
outcome. Callers use existing control admission; the first admitted caller
performs the write, others consume its result. Freeze selection after setup,
before control submission. Non-speed controls delimit batches.

- [x] Add radio-boundary tests for setup bursts and queued bursts; observe failure.
- [x] Extract execution inside existing transaction admission, and add batch
  registration, cancellation removal, outcome propagation, and target selection.
- [x] Add regressions for power/preset ordering, post-submission requests, queued
  target cancellation, active cancellation/failure, shutdown, and no replay.
- [x] Document behavior, run the full suite and project checks, and obtain
  independent review before reporting completion.

No dependency/API change, release publication, deployment, or log publication.

## Verification

91 offline tests passed. Ruff lint/format, syntax compilation, release validation
with the installed requirement, and diff checks passed. Independent review found
no actionable issues. The reproduced burst uses one speed-4 write rather than
three sessions; hardware latency and any separate 62% incident remain unverified.
