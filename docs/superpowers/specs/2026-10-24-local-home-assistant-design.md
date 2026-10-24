# Local Home Assistant testing environment

Run this checkout's custom integration in a separate local Home Assistant instance before publishing releases.

## Approach

Use a native Python 3.14 virtual environment at `.venv-ha/`, installing the repository's test requirements (Home Assistant 2026.10.3) and the client pinned by the integration manifest. Preserve the existing `.venv/`, which currently uses Python 3.15.

Keep runtime configuration, credentials, database, and logs in an ignored `.ha-dev/` directory. Link `.ha-dev/custom_components/govee_ble_devices` to the integration source so a Home Assistant restart loads edits without copying or publishing. Provide repeatable setup, startup, configuration checking, and offline test commands in the README. Bind the local web interface to localhost on port 8123. Keep normal logging as the default.

Use the Mac's Bluetooth adapter. macOS Bluetooth authorization may require user action. Do not connect production proxies to this instance. Do not automatically configure devices, create an owner account, or change production Home Assistant. Devices polled by both instances may experience connection contention; hardware verification remains a separate manual step.

## Approaches considered

- Native Python: selected following the user's acceptance of Mac Bluetooth; permits local adapter access and avoids sharing production proxies.
- Docker Compose: reproducible runtime, but cannot directly use the Mac's Bluetooth and would require a separate available proxy.
- Full Core development container: useful for Core contribution and debugging, but adds a Core checkout and has the same Mac hardware constraint.

## Verification

Verify the installed Home Assistant version and client, run the existing offline suite and release/style/syntax checks, check the local Home Assistant configuration, and start the instance to verify its onboarding HTTP endpoint and integration discovery. Report Bluetooth permission or hardware limitations separately from offline results. Runtime data must remain ignored by Git.
