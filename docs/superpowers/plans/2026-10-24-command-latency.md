# Command Latency Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline and finish with independent review.

**Goal:** Reduce avoidable control queue latency and identify remaining radio/setup costs.

**Architecture:** A per-coordinator admission condition serializes complete
transactions, with FIFO control and read queues and control priority.
Existing session/client lifecycle remains unchanged. Privacy-safe DEBUG
records measure actual elapsed phases.

**Tech Stack:** Python 3.14, asyncio, Home Assistant, govee-ble-client 1.0.0b4, pytest.

**Spec:** `docs/superpowers/specs/2026-10-24-command-latency-design.md`

## Global Constraints

- No active-transaction interruption or uncertain-control replay.
- Preserve brief sessions, pacing, retry/setup/response/cleanup budgets.
- Preserve cancellation, unload, observation freshness, and per-device isolation.
- Diagnostics omit addresses, arguments, raw payloads, and encryption material.

## Review Focus

- Cancellation of the highest priority waiter must wake background readers.
- FIFO controls must survive interleaved arrivals of background reads.
- Unload during queue admission or cleanup must leave no owned operation behind.
- Setup and control failures must produce timings without leaking backend text.
- A busy device must not block transactions on another device.

### Task 1: Prioritize controls

**Files:** Modify `custom_components/govee_ble_devices/coordinator.py`, `tests/test_coordinator.py`.

**Interfaces:** `_transaction(*, control: bool = False)` grants one complete
transaction at a time. `async_commands` requests control priority; polling
and verification use the default background class.

- [x] Add event-driven regressions for control-over-read priority, FIFO controls,
  no active-read interruption, cancellation, shutdown, and device isolation.
- [x] Run those tests and observe priority failure against the current FIFO lock.
- [x] Implement admission with an asyncio condition and per-class FIFO tokens.
- [x] Run the complete offline suite and confirm no lifecycle regressions.

### Task 2: Measure latency and document behavior

**Files:** Modify coordinator, regression tests, and `README.md`.

**Interfaces:** DEBUG phase records distinguish queue, GATT, encryption,
control submission, cleanup, and total transaction elapsed time and outcome.

- [x] Add regressions for separate setup timing, queue wait, failed/cancelled
  transactions, and private-data exclusion; run and observe failures.
- [x] Add timing records in finally blocks where needed, without changing
  transaction behavior or exception propagation.
- [x] Update README with control priority and how to interpret timings.
- [x] Run pytest, compileall, Ruff lint/format, release validation, diff checks.
- [x] Review the final diff independently and address actionable findings.

No dependency change, release, commit, or deployment is part of this task.

## Results

74 offline tests passed. Independent review found no actionable correctness
or reliability issues. Hardware latency measurements remain pending; existing
local development logs contained H7126 samples only. No API change is needed
for control admission or independent phase timing.
