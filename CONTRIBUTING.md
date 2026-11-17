# Contributing

Use Python 3.14. Run `scripts/setup`, then the validation commands in the
[README](README.md#development). For live macOS Bluetooth testing, follow the
[local development instructions](README.md#local-home-assistant-on-macos).

Create a branch from `main`, add regression tests for changed behavior, update
documentation, and open a pull request against
[jnstockley/ha-govee-ble-devices](https://github.com/jnstockley/ha-govee-ble-devices/pulls).
Describe the problem, resulting behavior, tests run, and any hardware limitations.
Keep the `govee_ble_devices` domain and existing entity IDs stable. Use HA's Bluetooth
manager instead of starting a separate scanner. Preserve cancellation and clean
up pending tasks and connections on setup failure, unload, and shutdown.
Serialize complete transactions per device and never automatically replay
uncertain control writes.

Use Ruff for formatting and linting; `scripts/lint` checks without modifying files.
To format, run `.venv-ha/bin/ruff format custom_components scripts tests`.
Tests must run without hardware; mock radio boundaries rather than HA itself when
possible. Device tests should record model, firmware, HA version and Bluetooth
adapter/proxy. Follow the README hardware validation matrix for Bluetooth or proxy changes.
Redact Bluetooth addresses, credentials, raw packets, and encryption material
from logs; restore normal logging after diagnosis. Keep `.ha-dev` private.

Dependency changes must update the manifest pin and installation documentation.
Release tags must match the integration version prefixed by `v`; CI publishes a
prerelease for alpha, beta and RC versions. See the README release checklist.

Report reproducible bugs through the
[issue templates](https://github.com/jnstockley/ha-govee-ble-devices/issues/new/choose).
Contributions are licensed under the repository's [MIT License](LICENSE), including
the copyright notice.
