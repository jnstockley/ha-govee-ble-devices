# Govee BLE Devices for Home Assistant

A local Bluetooth custom integration using [`govee-ble-client`](https://github.com/jnstockley/govee_ble_client). Home Assistant owns discovery and Bluetooth adapters; the client supplies model-specific GATT communication. No Govee cloud account is needed.

## Supported devices

The matrix lists the features exposed in Home Assistant by this integration. **—** means the feature is not exposed for that model.

| Feature | H5179 temperature & humidity sensor | H7102 tower fan | H7126 air purifier |
| --- | --- | --- | --- |
| Temperature sensor | Yes | — | — |
| Humidity sensor | Yes | — | — |
| Battery level sensor | Yes | — | — |
| Power on/off | — | Yes | Yes |
| Fan speed control | — | 8 speeds through HA's percentage control | Low/High presets |
| Preset modes | — | Normal, Sleep, Nature | Sleep, Low, High, Custom* |
| Oscillation control | — | Yes | — |
| Panel display on/off switch | — | Yes | Yes |
| Filter life sensor | — | — | Yes |
| Reset filter life button | — | — | Yes |

\* H7126 Custom mode reads and reapplies the schedule already stored on the device. If its marker, modes, or durations cannot be preserved by the client's writer, the action fails before sending controls. Editing the schedule is not exposed. H7102 Custom mode is also omitted because its settings contain fields that are not fully mapped in the client.

## Install

1. Install and configure [HACS](https://www.hacs.xyz/docs/use/download/prerequisites/) in Home Assistant.
2. In HACS, open the three-dot menu, choose **Custom repositories**, add `https://github.com/jnstockley/ha-govee-ble-devices`, and select **Integration** as the type.
3. Open **Govee BLE Devices** in HACS and select **Download**. For prerelease versions, enable prereleases for this repository or select the version explicitly. Restart Home Assistant after the download.
4. In **Settings → Devices & services → Add integration**, search for **Govee BLE Devices** and select a discovered device. Repeat for each additional device, including devices of the same model.

The device must be advertising within range of a Home Assistant Bluetooth adapter or a connectable Bluetooth proxy. Passive-only proxies cannot establish the GATT connections these models require. Home Assistant must be able to install `govee-ble-client==1.0.0b3` from PyPI.

HACS tracks the repository and exposes an update entity in Home Assistant. When a newer release is available, install it through the HACS update entity or HACS dashboard, then restart Home Assistant to load the new Python code. Prerelease updates are shown when prereleases are enabled for this repository. HACS handles future integration file downloads; no manual copying is needed. [HACS update entity documentation](https://www.hacs.xyz/docs/use/entities/update/)

## Data and availability

The integration polls every 90 seconds using brief connections, freeing adapter/proxy slots between transactions. Home Assistant owns discovery, adapter routing, and connection admission. Each session resolves a fresh connectable device and uses Home Assistant's BLE retry connector through the client's public connection API, with model service filtering, a 45-second overall GATT setup budget, and 10-second operation/cleanup budgets. H7102 encryption has a separate eight-second handshake budget. Cleanup can extend elapsed time beyond a setup or response deadline.

Query writes and response collection share a four-second budget after setup. Complete reads return as soon as the expected fields arrive; missing fields can use the remaining budget. Invalid frames are ignored, duplicate replies do not complete a snapshot, and plain-device notification buffers are capped at 64 frames. Overflow or disconnect fails the transaction. H7126 saved gear replies establish active mode only when the mode-category reply confirms gear mode.

ATT write modes are explicit: H7126 and encrypted H7102 use write-without-response; H5179 retains response writes when its current characteristic supports them, otherwise write-without-response. Existing profile validation checks the connected GATT layout before queries or controls.

H7126 control sessions stay open for 300 ms after the final write so queued write-without-response data can transmit before disconnecting. Immediate teardown on macOS Core Bluetooth was observed to drop controls. This settling period does not confirm application; the subsequent readback remains authoritative.

A failed poll marks the device's entities unavailable; individual fields also become unavailable when they have not received a valid update for five minutes. The last valid values are kept internally so a malformed or incomplete response cannot replace good data. Only received observations refresh field timestamps. Commands are serialized with polling per device; separate devices can progress independently. Completed control writes update displayed values provisionally and trigger one coalesced readback after a one-second delay. A write does not restore availability or confirm an applied setting. Ambiguous write failures also schedule readback; controls are never replayed automatically. Unload cancels and awaits active and queued operations, then closes encryption and GATT. Failed cleanup handles are retained for retry before another session or during unload. Failed initial setup also retains its cleanup owner across Home Assistant setup retries.

These choices follow the beta's [application guide](https://github.com/jnstockley/govee_ble_client/blob/v1.0.0b3/docs/APP_INTEGRATION.md) and [BLE review](https://github.com/jnstockley/govee_ble_client/blob/v1.0.0b3/docs/BLE_BEST_PRACTICES.md). Brief sessions conserve slots but require another connection and, for H7102, another handshake for each transaction. Persistent sessions and shorter pacing require measurements on the target adapters before adoption.

Enable debug logging from the integration's Home Assistant device/service page, or configure:

```yaml
logger:
  logs:
    custom_components.govee_ble_devices: debug
    govee_ble_client: debug
```

The manifest declares the library logger so Home Assistant's integration debug controls include it. Integration DEBUG records separate GATT setup, encrypted setup, and response-collection durations. Return logging to normal after diagnosis. The integration's timing messages omit addresses and packet data; Home Assistant and backend exception logs may still include identifying information.

Some device firmware may respond slowly or differ from the captured profiles. Report the model, firmware, affected entity, and redacted debug logs when opening an issue. Bluetooth addresses and raw packet bytes can identify a device, so redact them before sharing.

## Development

The integration is pure Python under `custom_components/govee_ble_devices`. With Python 3.14 in a virtual environment, install `python -m pip install -r requirements-test.txt`, then run:

```sh
python scripts/check_release.py --install-requirement
python -m pytest -q
python -m compileall -q custom_components scripts tests
ruff check custom_components scripts tests
ruff format --check custom_components scripts tests
```

Offline tests use the published client and Home Assistant coordinator, replacing radio I/O and the encrypted wire transport with controlled doubles. They cover lifecycle, bounded replies, partial state, saved Custom settings, uncertain writes, readback, and unload. CI also runs hassfest and HACS validation. The test environment pins Home Assistant 2026.10.3 and its Bluetooth import dependencies; update those pins together when changing the compatibility target.

### Local Home Assistant on macOS

Run a separate development instance using the Mac's Bluetooth adapter. This follows Home Assistant's [manual development environment guidance](https://contributors.home-assistant.io/core/development/environment/), using the same Home Assistant version as the offline tests. HACS is not needed: the instance loads the integration directly from this checkout.

Install the native media libraries used by Home Assistant's frontend dependencies with [Homebrew](https://brew.sh/):

```sh
brew install ffmpeg jpeg-turbo
```

Then, from the repository root, with `python3` pointing to Python 3.14:

```sh
python3 --version
python3 -m venv .venv-ha
.venv-ha/bin/python -m pip install -r requirements-test.txt
.venv-ha/bin/python scripts/check_release.py --install-requirement
mkdir -p .ha-dev/custom_components
cp -n dev/configuration.yaml .ha-dev/configuration.yaml
ln -s ../../custom_components/govee_ble_devices .ha-dev/custom_components/govee_ble_devices
```

The `ln` command is a one-time setup step; an existing link does not need replacing. The existing `.venv/` is independent of this environment. `.venv-ha/` and `.ha-dev/` are ignored by Git; the latter holds local credentials, configuration, logs, and the database. Keep it private. `dev/configuration.yaml` is the tracked seed; edit `.ha-dev/configuration.yaml` for local settings.

Check configuration and start Home Assistant:

```sh
.venv-ha/bin/hass --script check_config -c .ha-dev
.venv-ha/bin/hass -c .ha-dev
```

The first run downloads additional requirements for the enabled Home Assistant integrations and needs internet access. Open **http://localhost:8123**, create a local owner account, then go to **Settings → Devices & services → Add integration → Govee BLE Devices**. Choose a supported device within Bluetooth range of the Mac. With no device in range, the integration reports that no devices were found; it does not create simulated entities. The web server listens only on `127.0.0.1:8123`. The seed enables the frontend, integration configuration, API, history, and Bluetooth, without unrelated default integrations or automatic LAN proxy discovery.

Allow Bluetooth access if macOS prompts. If startup exits with `SIGKILL` or scanning fails, check **System Settings → Privacy & Security → Bluetooth** for the app launching Python (such as Terminal or your editor), enable access, and restart from that app. Home Assistant documents this macOS permission requirement in its development guide.

To stop the development server, press **Ctrl+C** in the terminal running Home Assistant and wait for shutdown to finish.

If it is running in the background or that terminal is unavailable, find the process listening on the development port from another terminal:

```sh
lsof -nP -iTCP:8123 -sTCP:LISTEN
```

Use the PID from that output in the commands below, replacing `12345` with the actual PID. Check that the command belongs to this checkout's `.venv-ha/bin/hass -c .ha-dev` instance before stopping it:

```sh
ps -p 12345 -o command=
kill -TERM 12345
```

`SIGTERM` requests a graceful shutdown, allowing Home Assistant to close Bluetooth connections and its database. Wait for shutdown to finish; rerun the `lsof` command to confirm nothing is listening on port 8123.

After changing Python code, stop and run the same start command again; the symlink loads the working source without copying files or publishing a release. Restart after manifest changes as well so Home Assistant can install changed requirements. When the repository's test pins change, rerun the dependency installation and release validation commands.

Do not add production ESPHome Bluetooth proxies to the development instance: their Bluetooth subscription serves one client, and another subscriber can interrupt production. See the [ESPHome proxy implementation](https://api-docs.esphome.io/bluetooth__proxy_8cpp_source). Even with separate adapters, two Home Assistant instances polling the same Govee device can compete for connections. Use a spare device, or temporarily disable that device's integration entry in production while testing, then re-enable it afterward.

Run the offline checks with this environment:

```sh
.venv-ha/bin/python scripts/check_release.py --install-requirement
.venv-ha/bin/python -m pytest -q
.venv-ha/bin/python -m compileall -q custom_components scripts tests
.venv-ha/bin/ruff check custom_components scripts tests
.venv-ha/bin/ruff format --check custom_components scripts tests
```

The local instance verifies UI/setup and Mac Bluetooth behavior. Also perform the Linux/BlueZ and active-proxy hardware checks below before a release that changes Bluetooth behavior.

### Hardware validation before deployment

Offline tests cannot establish RF latency, battery life, proxy compatibility, or firmware behavior. On a Linux/BlueZ host and an active Bluetooth proxy, test mixed supported models and include:

| Scenario | Check |
| --- | --- |
| Cold discovery and simultaneous startup | Devices eventually connect without a competing scanner; one slow device does not block other coordinators |
| Proxy slot exhaustion | Other devices recover as slots become available; brief sessions release their slots |
| Device loss and adapter failover | Entities become unavailable, recover on later polls, and use fresh HA device routing |
| Repeated controls and readback | Observed settings replace provisional values; uncertain controls are not replayed |
| Unload during setup or writes | No surviving verification/receiver tasks or occupied connection slots |
| Long-duration sensor run | Five-minute field freshness and acceptable battery behavior |

Record GATT setup, encrypted setup, and query-to-matching-reply p50/p95 separately, along with failure/recovery counts, notification overflows, and remaining tasks/slots after unload. Repeat representative checks on the deployed x86 or ARM64 host. This change has no hardware certification implied by its offline tests.

### Dependency updates

[Renovate](https://github.com/apps/renovate) is configured in `renovate.json`. Install or enable the Renovate GitHub App for this repository after merging the configuration to the default branch. It opens dependency update pull requests for GitHub Actions and the Python packages in `custom_components/govee_ble_devices/manifest.json`'s `requirements` array, using its built-in [Home Assistant manifest manager](https://docs.renovatebot.com/modules/manager/homeassistant-manifest/). Review and merge these pull requests through the existing validation workflow, then follow the release steps below.

### Publishing an update

1. Update `version` in `custom_components/govee_ble_devices/manifest.json` and the changelog or release notes. If the client version changes, update its pinned `requirements` entry and the installation note above.
2. Push the changes to the default branch and confirm the **Validate** workflow passes.
3. Create and push a tag matching the manifest version, prefixed with `v` (for example, `v1.0.0b4`). The tag workflow validates the version and publishes the matching GitHub Release. Beta or release-candidate versions become GitHub prereleases.

HACS needs a public GitHub repository with a short description, topics, Issues enabled, and at least one GitHub Release for version-based update notifications. The repository's HACS custom-repository URL is the GitHub URL in the installation steps; it does not need to be listed in HACS's default catalog. [HACS publishing requirements](https://www.hacs.xyz/docs/publish/start/)
