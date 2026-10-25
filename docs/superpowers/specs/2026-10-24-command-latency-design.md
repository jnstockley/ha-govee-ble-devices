# Reduce command queue latency

The user reports that H7102 sometimes takes several seconds to physically
respond to HA controls, and authorizes the recommended conservative changes.

## Approach

Give queued controls priority over queued background polls and verification
reads, preserving FIFO order within each class. Never interrupt an active
transaction. Keep brief sessions, existing query/control spacing, retry
connector, deadlines, cleanup, and no automatic replay of uncertain writes.
The installed client's public API is sufficient for these changes.

Record DEBUG durations for queue wait, independent GATT/encryption setup,
control submission, cleanup, and complete transactions. Use only model,
operation category, outcome, and timings; omit addresses, command arguments,
raw packets, exception text, and encryption material.

Connection reuse can avoid setup for successive commands but occupies an
adapter slot and requires hardware recovery measurements. Defer it until
timing evidence identifies setup as the dominant cost. Reducing pacing or
timeouts risks missed writes or false failures and is outside this change.

## Acceptance

- Controls overtake queued reads, but never active reads or earlier controls.
- Cancelled queued/active operations cannot strand the admission gate.
- Shutdown cancels all queued/active work and retains cleanup ownership.
- Separate devices progress independently. Observation freshness and readback
  remain authoritative. Failed controls are never replayed.
- Timing records include failed/cancelled operations without private data.
- Run the offline suite, syntax, Ruff, and release validation; document that
  physical latency and adapter/proxy reliability need hardware verification.
