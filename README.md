# Govee BLE Devices for Home Assistant

A local Bluetooth custom integration using [`govee-ble-client`](https://github.com/jnstockley/govee_ble_client). Home Assistant owns discovery and Bluetooth adapters; the client supplies model-specific GATT communication. No Govee cloud account is needed.

## Supported devices

| Model | Entities |
| --- | --- |
| H5179 thermo-hygrometer | Temperature, humidity, battery |
| H7102 tower fan | Fan power, Normal/Sleep/Nature presets, speeds 1–8, oscillation, display switch |
| H7126 air purifier | Fan power, Sleep/Low/High/Custom presets, display switch, filter life sensor, filter reset button |

H7126 Custom mode selects the schedule already stored on the device. Editing the schedule is not exposed. H7102 Custom mode is also omitted because its settings contain fields that are not fully mapped in the client.

## Install

1. Install and configure [HACS](https://www.hacs.xyz/docs/use/download/prerequisites/) in Home Assistant.
2. In HACS, open the three-dot menu, choose **Custom repositories**, add `https://github.com/jnstockley/ha-govee-ble-devices`, and select **Integration** as the type.
3. Open **Govee BLE Devices** in HACS and select **Download**. For the current `1.0.0b2` prerelease, enable prereleases for this repository or select that version explicitly. Restart Home Assistant after the download.
4. In **Settings → Devices & services → Add integration**, search for **Govee BLE Devices** and select a discovered device. Repeat for each additional device, including devices of the same model.

The device must be advertising within range of a Home Assistant Bluetooth adapter or a connectable Bluetooth proxy. Passive-only proxies cannot establish the GATT connections these models require. Home Assistant must be able to install `govee-ble-client==1.0.0b2` from PyPI.

HACS tracks the repository and exposes an update entity in Home Assistant. When a newer release is available, install it through the HACS update entity or HACS dashboard, then restart Home Assistant to load the new Python code. Prerelease updates are shown when prereleases are enabled for this repository. HACS handles future integration file downloads; no manual copying is needed. [HACS update entity documentation](https://www.hacs.xyz/docs/use/entities/update/)

## Data and availability

The integration polls every 90 seconds using short-lived connections. Invalid notification frames are ignored. A failed poll marks the device's entities unavailable; individual fields also become unavailable when they have not received a valid update for five minutes. The last valid values are kept internally so a brief malformed or incomplete response cannot replace good data. Commands are serialized with polling and followed by a fresh readback.

Some device firmware may respond slowly or differ from the captured profiles. Report the model, firmware, affected entity, and redacted debug logs when opening an issue. Bluetooth addresses and raw packet bytes can identify a device, so redact them before sharing.

## Development

The integration is pure Python under `custom_components/govee_ble_devices`. Run `python scripts/check_release.py`, `python -m compileall -q custom_components`, and `ruff check custom_components scripts` before pushing. CI also verifies that the pinned PyPI dependency can be installed, runs hassfest and HACS validation, and checks code formatting. Live behavior requires supported BLE hardware and a Home Assistant instance.

### Publishing an update

1. Update `version` in `custom_components/govee_ble_devices/manifest.json` and the changelog or release notes. If the client version changes, update its pinned `requirements` entry and the installation note above.
2. Push the changes to the default branch and confirm the **Validate** workflow passes.
3. Create and push a tag matching the manifest version, prefixed with `v` (for example, `v1.0.0b2`). The tag workflow validates the version and publishes the matching GitHub Release. Beta or release-candidate versions become GitHub prereleases.

HACS needs a public GitHub repository with a short description, topics, Issues enabled, and at least one GitHub Release for version-based update notifications. The repository's HACS custom-repository URL is the GitHub URL in the installation steps; it does not need to be listed in HACS's default catalog. [HACS publishing requirements](https://www.hacs.xyz/docs/publish/start/)
