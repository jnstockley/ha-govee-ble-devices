# Govee beta Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Adopt the published 1.0.0b3 client safely while preserving brief BLE sessions.

**Architecture:** Home Assistant owns discovery and retry routing. The coordinator owns each complete device transaction, bounded reply collection, cleanup handles, and verification tasks. The published client owns GATT and encrypted session lifecycle through its public API.

**Tech Stack:** Python, Home Assistant Bluetooth/update coordinator, bleak-retry-connector, govee-ble-client 1.0.0b3, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-10-24-govee-beta-design.md`

## Global Constraints

- Brief sessions, a per-device transaction lock, and a 90-second polling interval.
- A 45-second outer setup budget; preserve conservative write pacing.
- Home Assistant owns discovery/routing/slot admission; no independent scanner or fleet lock.
- Only received fields establish observation freshness; retain five-minute freshness limit.
- Cancellation propagates; uncertain controls are never replayed.
- Failed cleanup retains handles, and unload stops and awaits owned work.
- No release publication or claims of hardware certification.

## Review Focus

- Disconnect during query collection must wake the reader and preserve cleanup.
- Invalid or duplicate responses must not falsely complete a snapshot.
- Unload while a control waits for the device lock must prevent a subsequent write.
- Unknown/malformed saved Custom durations must not replace a user's schedule.
- Failed teardown must not lose the only handle capable of freeing the slot.

---

### Task 1: Public connection lifecycle

**Files:** Modify `custom_components/govee_ble_devices/coordinator.py`, `custom_components/govee_ble_devices/manifest.json`; create `tests/conftest.py`, `tests/test_coordinator.py`, and test dependency configuration.

**Interfaces:** Coordinator `_connection() -> GattConnection` supplies a public connector and resolver with model service filtering; session ownership stays inside the coordinator.

- [x] Write tests using the released GattConnection and a fake BLE backend: a successful connect permits GATT I/O, forwards service filters/disconnect callbacks, and refreshes HA's connectable device; loss/unavailable lookup invalidates use.
- [x] Run tests and confirm failure against the current private-field override.
- [x] Pin 1.0.0b3, declare `loggers`, and replace the override with connector injection and 45-second setup/10-second operation budgets. Maintain retained session handles for bounded cleanup retries.
- [x] Run the lifecycle tests; expect passing checks with no real Bluetooth access.

### Task 2: Bounded and prompt response collection

**Files:** Modify `coordinator.py`; extend `tests/test_coordinator.py`.

**Interfaces:** Plain-model collection returns recognized observed fields; H7102 uses one receiver. Both use a bounded response deadline after setup and return partial valid state when a response budget expires.

- [x] Add failing tests for early completion, False/zero, partial timeout, malformed/duplicate replies, H7126 mode hint versus gear precedence, bounded overflow failure, and disconnect wakeup.
- [x] Implement bounded plain buffering with explicit overflow failure, subscribe-before-query ordering, model-specific completion, and receipt timestamps. Preserve independent-device concurrency and write pacing. Log setup/response timings separately without packet data or addresses.
- [x] Add H7102 tests for one receiver, optional fields, invalid frames, deadline, and disconnect.
- [x] Run all tests; expect valid partial data retained, errors bounded, and complete reads returning promptly.

### Task 3: Controls, saved schedule, and unload

**Files:** Modify `coordinator.py`, `custom_components/govee_ble_devices/__init__.py`, and tests.

**Interfaces:** `async_commands(commands)` never retries writes; `async_shutdown()` closes admission, awaits/cancels owned work, and retries retained cleanup. Existing fan command names remain compatible.

- [x] Add failing tests for saved Custom read/validation before write, preservation of marker/segments, refusal of unknown schedule fields, no write replay, provisional state without observation freshness, coalesced verification, transaction serialization, cancellation, failed cleanup retry, and unload during setup/queued control.
- [x] Implement saved Custom restoration using released public APIs; fail safely when exact preservation is unsupported. Track verification tasks and active operations. Close encrypted client before GATT and retain failed handles for subsequent cleanup. Integrate shutdown with successful platform unload.
- [x] Run the full suite; expect no pending owned tasks and no writes after shutdown admission closes.

### Task 4: Documentation and release checks

**Files:** Modify `README.md`, `scripts/check_release.py`, `.github/workflows/validate.yml` (or actual workflow path), and test configuration as needed.

**Interfaces:** Existing release validator additionally imports newly used released API; CI executes offline tests alongside existing style/syntax/release checks.

- [x] Document beta installation, brief-session tradeoffs, provisional controls, logging, and a practical Linux/proxy validation matrix with separate setup/response latency measurements.
- [x] Run full pytest, release validation against installed PyPI beta, compileall, Ruff lint/format, and available HA compatibility checks; record exact outcomes and environment limits.
- [x] Perform one independent review of the final working diff, fix important findings with regression tests, and repeat affected checks.
- [x] Leave changes reviewable in the current workspace without publishing or pushing.
