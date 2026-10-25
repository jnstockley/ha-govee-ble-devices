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
| Preset modes | — | Normal, Sleep, Nature, Custom* | Sleep, Low, High, Custom* |
| Custom block editing | — | Speed and oscillation for 3 blocks; duration for blocks 1–2 | Sleep/Low/High for 3 blocks; duration for blocks 1–2 |
| Apply custom mode button | — | Yes | Yes |
| Oscillation control | — | Yes | — |
| Panel display on/off switch | — | Yes | Yes |
| Filter life sensor | — | — | Yes |
| Reset filter life button | — | — | Yes |

\* The fan's Custom preset reads and reapplies the schedule already stored on the device. To edit it in HA, use the block controls and **Apply custom mode** as described below. H7102 preserves unknown fields and untouched bytes. H7126 rejects schedules whose marker, modes, or durations cannot be preserved by the client's writer. If reading or preserving the saved settings fails, no controls are sent.

### Editing custom blocks

On the H7102 or H7126 device page, edit the **Custom block 1**, **2**, and **3**
controls, then press **Apply custom mode**. Changing a block only stages a local
draft: it sends no Bluetooth controls and leaves the active mode alone. Draft
edits survive Home Assistant restarts and integration reloads.

- H7102: all three blocks have speed **1–8** and oscillation controls.
- H7126: all three blocks have a **Sleep**, **Low**, or **High** mode.
- Blocks 1 and 2 have durations in whole minutes from **1–65534**.
  **Block 3 always runs continuously**, so it has no duration control or
  Continuous switch. Apply always writes block 3 with a continuous duration.

**Apply custom mode saves the blocks and activates Custom.** It powers the
device on if necessary. Immediately before applying, HA reads the saved schedule
and merges your edited fields, preserving untouched speed, oscillation, mode,
and block 1–2 duration changes made through the Govee app. Pressing Apply with
no pending edits activates the saved schedule with block 3 set to continuous.
Choosing the fan's existing Custom preset also activates the device's saved
schedule; it does not submit local draft edits.

HA clears submitted edits only after reading back matching settings and Custom
mode. A timeout, disconnect, or mismatched reply retains the draft and reports
an error; HA does not automatically replay the write. An interrupted Apply may
already have powered on the device or selected Custom, so check its state before
applying again. Edits made during Apply remain pending for your next submission.

Controls without a known value are unavailable until the device returns a saved
schedule. Missing schedule replies do not invalidate otherwise successful power
or sensor readings. Physical round-trip verification of edited schedules is
still required on each model and firmware; offline tests simulate the radio.

## Install

1. Install and configure [HACS](https://www.hacs.xyz/docs/use/download/prerequisites/) in Home Assistant.
2. In HACS, open the three-dot menu, choose **Custom repositories**, add `https://github.com/jnstockley/ha-govee-ble-devices`, and select **Integration** as the type.
3. Open **Govee BLE Devices** in HACS and select **Download**. For prerelease versions, enable prereleases for this repository or select the version explicitly. Restart Home Assistant after the download.
4. In **Settings → Devices & services → Add integration**, search for **Govee BLE Devices** and select a discovered device. Repeat for each additional device, including devices of the same model.

The device must be advertising within range of a Home Assistant Bluetooth adapter or a connectable Bluetooth proxy. Passive-only proxies cannot establish the GATT connections these models require. Home Assistant must be able to install `govee-ble-client==1.0.0b4` from PyPI.

HACS tracks the repository and exposes an update entity in Home Assistant. When a newer release is available, install it through the HACS update entity or HACS dashboard, then restart Home Assistant to load the new Python code. Prerelease updates are shown when prereleases are enabled for this repository. HACS handles future integration file downloads; no manual copying is needed. [HACS update entity documentation](https://www.hacs.xyz/docs/use/entities/update/)

### Icons and logos

The integration ships its brand icon in `brand/icon.png` (256×256)
and `brand/icon@2x.png` (512×512). Home Assistant 2026.3 and newer serves these
locally for integration setup, integration pages, and other brand displays.
Logo and dark-theme requests fall back to the same artwork, which works on
both backgrounds. Separate duplicate logo files are unnecessary. See
[Home Assistant branding documentation](https://contributors.home-assistant.io/core/integrations/brands/).

Entity controls use function-specific icons (filter, display, oscillation,
schedule duration, speed, mode, and Apply). Temperature, humidity, battery,
and the fan use Home Assistant's built-in device-class/platform icons.
Restart Home Assistant after installing an update to load icon metadata, and
refresh the browser if it still shows cached icons.

Some HACS versions still request custom integration images from the public
brands CDN instead of Home Assistant's local brand API. This causes the
"icon not available" image in the HACS dashboard and its update entity even
when the integration's local images work. This is tracked upstream in
[hacs/integration#5223](https://github.com/hacs/integration/issues/5223) and
[hacs/integration#5402](https://github.com/hacs/integration/issues/5402).
Keep HACS up to date; adding an image URL to `hacs.json` does not change those
requests.

For the **update entity only**, a supported temporary workaround is to copy
`custom_components/govee_ble_devices/brand/icon.png` to
`/config/www/govee_ble_devices/icon.png` (create the directories), then merge
this into `configuration.yaml` under your existing `homeassistant:` section:

```yaml
homeassistant:
  customize:
    update.govee_ble_devices_update:  # Replace with your actual HACS update entity ID.
      entity_picture: /local/govee_ble_devices/icon.png
```

Restart Home Assistant after changing the configuration. Find the actual entity
ID under **Settings → Devices & services → Entities**. This uses Home Assistant's
[entity customization](https://www.home-assistant.io/docs/configuration/customizing-entities/)
and keeps the image local. It fixes the picture on update cards/dialogs that use
the entity picture; it does not change the HACS dashboard's independent image
requests. Remove the customization once your HACS version supports local brands.

## Data and availability

The integration polls every 90 seconds by default. To change one device's interval, open **Settings → Devices & services → Govee BLE Devices**, select the configure/options gear for that device's integration entry, and set **Polling interval** to a whole number from **5 to 3600 seconds**. Each device has its own saved interval; existing entries stay at 90 seconds until changed. Saving a changed interval reloads only that entry, with no Home Assistant restart required. This is an integration setting and does not create an entity. Shorter intervals help pick up changes made through the mobile app or physical controls sooner, but increase Bluetooth activity and battery use, especially for the H5179 sensor. The interval is a scheduling setting, not a guarantee of update latency: connection, read, and cleanup time also contribute.

Polling uses brief connections, freeing adapter/proxy slots between transactions. Home Assistant owns discovery, adapter routing, and connection admission. Each session resolves a fresh connectable device and uses Home Assistant's BLE retry connector through the client's public connection API, with model service filtering, a 45-second overall GATT setup budget, and 10-second operation/cleanup budgets. H7102 encryption has a separate eight-second handshake budget. Cleanup can extend elapsed time beyond a setup or response deadline.

Query writes and response collection share a four-second budget after setup. Complete reads return as soon as the expected fields arrive; missing fields can use the remaining budget. Invalid frames are ignored, duplicate replies do not complete a snapshot, and plain-device notification buffers are capped at 64 frames. Overflow or disconnect fails the transaction. H7126 saved gear replies establish active mode only when the mode-category reply confirms gear mode.

ATT write modes are explicit: H7126 and encrypted H7102 use write-without-response; H5179 retains response writes when its current characteristic supports them, otherwise write-without-response. Existing profile validation checks the connected GATT layout before queries or controls.

H7126 control sessions stay open for 300 ms after the final write so queued write-without-response data can transmit before disconnecting. Immediate teardown on macOS Core Bluetooth was observed to drop controls. This settling period does not confirm application; the subsequent readback remains authoritative.

A failed poll marks the device's entities unavailable; individual fields also become unavailable when they have not received a valid update for five minutes or twice the selected polling interval, whichever is longer. The last valid values are kept internally so a malformed or incomplete response cannot replace good data. Only received observations refresh field timestamps. Commands are serialized with polling per device; separate devices can progress independently. Queued controls run before queued polls or verification reads, preserving arrival order among controls. An active transaction finishes, including cleanup, before the next starts. This reduces avoidable waiting during repeated adjustments; the first command after idle still needs a connection and, for H7102, an encryption handshake. Completed control writes update displayed values provisionally and trigger one coalesced readback after a one-second delay. A write does not restore availability or confirm an applied setting. Ambiguous write failures also schedule readback; controls are never replayed automatically. Unload cancels and awaits active and queued operations, then closes encryption and GATT. Failed cleanup handles are retained for retry before another session or during unload. Failed initial setup also retains its cleanup owner across Home Assistant setup retries.

These choices follow the beta's [application guide](https://github.com/jnstockley/govee_ble_client/blob/v1.0.0b3/docs/APP_INTEGRATION.md) and [BLE review](https://github.com/jnstockley/govee_ble_client/blob/v1.0.0b3/docs/BLE_BEST_PRACTICES.md). Brief sessions conserve slots but require another connection and, for H7102, another handshake for each transaction. Persistent sessions and shorter pacing require measurements on the target adapters before adoption.

Consecutive H7102 speed-only requests that arrive before a write is submitted
share a single transaction. The newest live target is selected after connection
and encryption setup; superseded requests complete with that shared write's
result. This avoids reconnecting for every intermediate or duplicate HomeKit
slider request. Power, preset, oscillation, display, and composite commands
preserve their ordering and separate speed batches. A request arriving after
submission starts a new transaction. Cancelling a queued request removes its
target; a failed or cancelled writer fails its followers without replaying the
write. Readback remains authoritative.

Enable debug logging from the integration's Home Assistant device/service page, or configure:

```yaml
logger:
  logs:
    custom_components.govee_ble_devices: debug
    govee_ble_client: debug
```

The manifest declares the library logger so Home Assistant's integration debug controls include it. Integration DEBUG records `BLE timing` messages with model, operation category, phase, elapsed seconds, and outcome (`ok`, `failed`, or `cancelled`):

- `queue`: time waiting for admission; a control waiting here is blocked by an active transaction or earlier controls.
- `gatt_setup` and `encrypted_setup`: independent connection and handshake durations. The encryption duration does not include GATT setup.
- `controls`: control submission, including any saved-Custom preflight, command spacing, and H7126 settling. This is transport timing, not proof of physical application.
- `cleanup`: encryption/GATT teardown, including failed attempts.
- `transaction`: total elapsed time including queue wait, setup, operations, and cleanup. It contains the other phases; do not add it to their durations.

Response-collection durations are logged separately. A long setup phase points to connection/handshake costs; shortening the one-second post-command verification delay does not speed up the fan's initial response. An active read with missing replies can still use its four-second response budget before a queued command starts. Return logging to normal after diagnosis. The integration's timing messages omit addresses, arguments, packet data, and exception text; Home Assistant and backend exception logs may still include identifying information.

Some device firmware may respond slowly or differ from the captured profiles. Report the model, firmware, affected entity, and redacted debug logs when opening an issue. Bluetooth addresses and raw packet bytes can identify a device, so redact them before sharing.

### HomeKit fan speed troubleshooting

H7102 has eight discrete speeds: 50% selects speed 4, and a displayed 62%
represents speed 5 (62.5% before integer rounding). A 50% request should not
be converted to speed 5. HomeKit Bridge forwards speed changes through HA's
fan percentage action.

If the fan physically stays at its old speed, enable debug logging for this
integration and HomeKit Bridge. After installing the diagnostic changes,
change from 75% to 50% once in Apple Home, wait for it to settle, and repeat
the same change directly in HA. Avoid dragging through several intermediate
values during this comparison. Look for these integration DEBUG messages:

- `Fan percentage request model=H7102 percentage=50`: the request reached the
  fan entity, before any queue or connection wait.
- `Fan speed submitted model=H7102 speed=4`: the client write returned. This
  does not prove that the proxy transmitted it or the fan applied it.
- `Fan speed observed model=H7102 speed=4`: a subsequent read reported speed 4.
  An observed speed 5 means that read still reported the old 62% setting.

Use the timestamps, `BLE timing` phases, and HomeKit's incoming speed log to
distinguish missing bridge requests, queue/setup delay, write failures, and
differing readback. Include HA Core and proxy firmware versions with redacted
logs. The integration's speed traces contain numeric percentage/speed values,
not addresses or raw packets. Restore normal logging after reproduction.

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
