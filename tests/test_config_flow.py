"""Device selection must only offer supported devices that can be added."""

from unittest.mock import AsyncMock

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.govee_ble_devices.config_flow import GoveeBLEConfigFlow
from custom_components.govee_ble_devices.const import DOMAIN


@pytest.fixture
async def flow(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    result = GoveeBLEConfigFlow()
    result.hass = hass
    result.handler = DOMAIN
    result.context = {"source": "user"}
    return result


def add_entry(flow, address, *, source="user", domain=DOMAIN):
    """Register a stored entry without setting up Bluetooth hardware."""
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=domain,
        title="Govee H5179",
        data={"address": address, "model": "H5179"},
        source=source,
        unique_id=address,
        options={},
        discovery_keys={},
        subentries_data=(),
    )
    flow.hass.config_entries._entries[entry.entry_id] = entry


def advertisement(address, *, name="Govee H5179", connectable=True):
    return bluetooth.BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="local",
        device=BLEDevice(address, name, {}),
        advertisement=None,
        connectable=connectable,
        time=0,
        tx_power=None,
    )


@pytest.fixture
def scanner(monkeypatch):
    devices = []
    monkeypatch.setattr(
        bluetooth, "async_discovered_service_info", lambda hass: devices
    )
    monkeypatch.setattr(bluetooth, "async_request_active_scan", AsyncMock())
    return devices


@pytest.mark.parametrize("stored_address", ["AA:BB:CC:DD:EE:01", "aa:bb:cc:dd:ee:01"])
async def test_picker_excludes_configured_devices(flow, scanner, stored_address):
    add_entry(flow, stored_address)
    scanner.extend(
        [advertisement("aa:bb:cc:dd:ee:01"), advertisement("AA:BB:CC:DD:EE:02")]
    )

    result = await flow.async_step_user()

    assert result["type"] == "form"
    selector = next(iter(result["data_schema"].schema.values()))
    assert selector.config["options"] == [
        {
            "value": "AA:BB:CC:DD:EE:02",
            "label": "Govee Temperature & Humidity Sensor (H5179) (AA:BB:CC:DD:EE:02)",
        }
    ]


@pytest.mark.parametrize("has_configured_device", [False, True])
async def test_no_new_devices_aborts(flow, scanner, has_configured_device):
    if has_configured_device:
        add_entry(flow, "AA:BB:CC:DD:EE:01")
        scanner.append(advertisement("AA:BB:CC:DD:EE:01"))

    result = await flow.async_step_user()

    assert result["type"] == "abort"
    assert result["reason"] == "no_devices_found"


@pytest.mark.parametrize(
    ("broadcast_name", "friendly_name"),
    [
        ("GVH5179_1234", "Govee Temperature & Humidity Sensor (H5179)"),
        ("GV5179_1234", "Govee Temperature & Humidity Sensor (H5179)"),
        ("GVH7102_1234", "Govee Tower Fan (H7102)"),
        ("GVH7126_1234", "Govee Air Purifier (H7126)"),
    ],
)
async def test_setup_shows_friendly_model_names(
    flow, scanner, broadcast_name, friendly_name
):
    scanner.append(advertisement("AA:BB:CC:DD:EE:01", name=broadcast_name))

    result = await flow.async_step_user()

    selector = next(iter(result["data_schema"].schema.values()))
    assert selector.config["options"] == [
        {
            "value": "AA:BB:CC:DD:EE:01",
            "label": f"{friendly_name} (AA:BB:CC:DD:EE:01)",
        }
    ]

    result = await flow.async_step_user({"address": "AA:BB:CC:DD:EE:01"})

    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"name": friendly_name}
    assert flow.context["title_placeholders"] == {"name": friendly_name}


async def test_scan_can_find_new_device_when_only_configured_device_is_cached(
    flow, scanner, monkeypatch
):
    add_entry(flow, "AA:BB:CC:DD:EE:01")
    scanner.append(advertisement("AA:BB:CC:DD:EE:01"))

    async def active_scan(hass, duration):
        scanner.append(advertisement("AA:BB:CC:DD:EE:02"))

    monkeypatch.setattr(bluetooth, "async_request_active_scan", active_scan)

    result = await flow.async_step_user()

    assert result["type"] == "form"
    selector = next(iter(result["data_schema"].schema.values()))
    assert [option["value"] for option in selector.config["options"]] == [
        "AA:BB:CC:DD:EE:02"
    ]


@pytest.mark.parametrize(
    ("source", "domain"), [("ignore", DOMAIN), ("user", "other_integration")]
)
async def test_picker_allows_ignored_devices_and_other_domains(
    flow, scanner, source, domain
):
    add_entry(flow, "AA:BB:CC:DD:EE:01", source=source, domain=domain)
    scanner.append(advertisement("AA:BB:CC:DD:EE:01"))

    result = await flow.async_step_user()

    assert result["type"] == "form"


async def test_picker_still_excludes_unsupported_and_nonconnectable_devices(
    flow, scanner
):
    scanner.extend(
        [
            advertisement("AA:BB:CC:DD:EE:01", name="Unknown device"),
            advertisement("AA:BB:CC:DD:EE:02", connectable=False),
        ]
    )

    result = await flow.async_step_user()

    assert result["type"] == "abort"
    assert result["reason"] == "no_devices_found"
