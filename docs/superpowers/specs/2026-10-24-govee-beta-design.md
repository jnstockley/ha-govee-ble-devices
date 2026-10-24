# Govee BLE client beta migration

## Intent and scope

Upgrade the Home Assistant integration from `govee-ble-client==1.0.0b2` to the
published, non-yanked `1.0.0b3` beta. Preserve supported entities and brief
per-device sessions while adopting the released lifecycle and application
guidance. Improve bounded response handling, cleanup, and control readback.
Offline verification cannot establish radio performance or production hardware
reliability; document the remaining direct-adapter and active-proxy checks.

## Approaches

- **Brief sessions with public connection hooks (selected by the user):** free
  proxy slots between transactions, retain per-device serialization, and avoid
  private transport fields. Connections and H7102 handshakes remain a latency
  cost on each transaction.
- **Persistent mains-powered sessions:** potentially faster commands, but reserve
  proxy slots and require receiver/recovery workers and hardware measurements.
  Defer this policy.

## Design

1. Pin the beta in the manifest, declare the library logger, and update install
   and behavior documentation. Leave integration release publication to the
   existing release workflow; this task does not publish a release.
2. Replace the private-field `RetryGattConnection.connect()` override with a
   public `GattConnection` connector and fresh connectable-device resolver.
   Forward disconnect callbacks and profile service filters to Home Assistant's
   retry connector. Use a 45-second outer setup budget and bounded GATT operations
   as recommended by the released integration recipe. Home Assistant continues
   to own discovery, routing, and adapter-slot admission.
3. Retain the per-device lock across complete poll/control transactions, with
   independent devices free to progress concurrently. Preserve conservative
   write pacing and the current 90-second polling interval; do not introduce
   unsupported throughput tuning, a fleet-wide lock, or an independent scanner.
4. Bound plain notification buffering and define overflow as a failed read.
   Subscribe before querying, preserve valid non-None fields (including False
   and zero), and complete reads when required responses arrive within one
   response budget. Distinguish H7126 mode hint and gear replies; do not treat an
   arbitrary notification or write acknowledgement as a complete snapshot.
   Use one H7102 receiver and preserve its optional-field behavior.
5. Preserve cancellation and never replay controls after ambiguous delivery.
   Retain failed cleanup handles for a later cleanup attempt. Stop scheduling
   work and cancel/await owned verification tasks during unload, then close
   encrypted sessions and disconnect GATT. Keep cleanup bounded and report
   failures without logging raw payloads.
6. Preserve H7126 saved Custom settings by reading and validating the saved
   schedule before selecting it. Never call the library's fixed-schedule helper
   for this integration's Custom preset. If saved settings cannot be safely
   represented without changing them, fail the action rather than substitute
   settings. Document that control writes are provisional until observed
   readback; only received fields establish observation freshness.
7. Add safe DEBUG timing for setup and response collection separately. Retain
   last valid observations on malformed/partial replies and the five-minute
   field freshness limit. Translate transport failures at the HA boundary.

## Verification

Add offline regression coverage for public connector arguments and disconnect
tracking; complete, partial, malformed, overflowed, and interrupted reads;
per-device serialization; cancellation/failed cleanup/unload; saved Custom
preservation; and command readback without write replay. Exercise the actual
published package rather than assuming the sibling checkout is the release.
Run release validation, dependency import checks, syntax compilation, Ruff,
and the regression suite. Run HA compatibility checks where the available
Python/environment supports them and report limits explicitly.

Before deployment, verify Linux/BlueZ and an active proxy with mixed models:
startup contention, slot exhaustion, loss/failover/recovery, unload during
setup/write, and repeated controls. Record setup and matching-response p50/p95,
recovery counts, queue overflow, and leaked tasks/slots. Hardware testing is
outside what offline checks can prove.

## Sources

- https://pypi.org/project/govee-ble-client/1.0.0b3/
- https://github.com/jnstockley/govee_ble_client/releases/tag/v1.0.0b3
- https://github.com/jnstockley/govee_ble_client/blob/v1.0.0b3/docs/APP_INTEGRATION.md
- https://github.com/jnstockley/govee_ble_client/blob/v1.0.0b3/docs/BLE_BEST_PRACTICES.md
- https://contributors.home-assistant.io/core/integrations/bluetooth/
