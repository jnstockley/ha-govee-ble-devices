# Per-device polling interval

Status: approved by the user on 2026-10-25.

## Intent

Let users tune synchronization speed separately for each Govee device through
its integration options. Faster reads help Home Assistant reflect changes made
outside HA. Slower reads can reduce Bluetooth traffic and battery use.

## Approaches

- Numeric interval in seconds (recommended): precise control using one standard
  Home Assistant options field. Bounds prevent invalid or extreme values.
- Presets: simpler selection, but users cannot choose another useful interval.

The user approved numeric input and the behavior below after reviewing this
design.

## Proposed behavior

- Add a **Polling interval** numeric field, in whole seconds, under each entry's
  integration options. Do not create a device entity.
- Accept 5–3600 seconds. Preserve the current 90-second default for new and
  existing entries until the user saves a different interval.
- Persist the interval in that config entry's options. Display its saved value
  when reopening the form, and preserve unrelated options when saving.
- Use Home Assistant's `OptionsFlowWithReload` so saving changed options reloads
  only that entry. The new coordinator reads the saved interval and performs
  the normal initial refresh. No Home Assistant restart is required.
- Retain brief BLE sessions, transaction serialization, existing control
  priority, cleanup, and one-second command verification.
- Keep field freshness at five minutes or twice the selected polling interval,
  whichever is longer. Only valid received observations refresh timestamps;
  a failed poll still marks entities unavailable immediately.
- Explain in the form and README that shorter intervals increase Bluetooth
  activity and battery consumption; connection/read time also contributes to
  actual update latency. Five seconds is an allowed setting, not a hardware
  performance guarantee.

## Scope and validation

Update constants, options flow, coordinator interval/freshness handling, English
strings/translations, and README. Cover default and saved options, numeric
validation, independent device intervals, saving/reopening, entry reload, and
freshness at long intervals with offline regressions. Run repository checks.

No persistent-notification implementation, cloud integration, new entity,
dependency change, or release/version bump is included. Earlier notification
investigation notes are independent of this feature. Linux/proxy behavior and
battery impact remain hardware validation items.
