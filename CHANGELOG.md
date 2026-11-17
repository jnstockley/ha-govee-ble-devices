# Changelog

## Unreleased

- Add development setup, run, and lint helpers matching the Daybetter integration.
- Add contributor guidelines and GitHub bug and feature request forms.

## 1.0.1

- Require Home Assistant Core 2026.6.0 for on-demand Bluetooth discovery.
- Align the test environment and HACS minimum with the supported HA release.

## 1.0.0

- Support H5179 temperature and humidity sensors, H7102 tower fans, and H7126
  air purifiers through local Bluetooth and `govee-ble-client`.
- Provide staged Custom block editing with verified Apply and per-device
  polling intervals.
- Serialize device transactions, retain drafts after uncertain writes, and
  clean up sessions on failed setup and unload.
- Include offline regression tests, release validation, HACS branding, and
  CI-gated GitHub releases.

Earlier beta changes are recorded in the
[GitHub releases](https://github.com/jnstockley/ha-govee-ble-devices/releases).
