# Coalesce unsent H7102 speed requests

The supplied log shows HomeKit requesting 100%, 50%, and 50% within 512 ms.
All reach the integration. Three separate GATT/encryption sessions submit
speeds 8, 4, and 4, then a fourth session confirms speed 4. The first speed-4
submission is about three seconds after its request. No rejected write or
speed-5 readback appears in this capture.

Consecutive H7102 speed-only requests may share a batch until its writer
finishes setup and is ready to submit a control. Select the newest remaining
request at that point. Successful followers complete without opening another
session. Their success means the shared write completed, not device application.
Power, preset, oscillation, display, and composite requests close the current
batch to later requests, preserving order across those commands. Requests after
submission start another batch; submitted writes are never replayed or changed.

Each caller remains independently tracked by transaction admission. Cancelling
a queued caller removes its target, so a cancelled newest target cannot replace
the previous target. If the active writer fails or is cancelled, followers fail
without retrying a potentially delivered write. Unload must cancel all callers
and retain existing session cleanup. Use existing verification and freshness
semantics and project values from the target actually submitted.

No debounce timer, permanent connection, adapter override, or client API change
is needed. Verify bursts during setup and queueing, cancellation of queued and
active callers, write errors/no replay, power/preset barriers, requests after
submission, and authoritative readback. Offline tests cannot prove radio delivery.
