"""Polling options must be validated, independent, and applied by entry reload."""

from datetime import timedelta
from time import monotonic
from unittest.mock import Mock

import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.govee_ble_devices.config_flow import GoveeBLEConfigFlow
from custom_components.govee_ble_devices.const import DOMAIN
from custom_components.govee_ble_devices.coordinator import GoveeBLECoordinator


def stored_entry(hass, *, options=None, address="AA:BB:CC:DD:EE:01"):
    """Register a real config entry without starting Bluetooth discovery."""
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Test Govee device",
        data={"address": address, "model": "H5179"},
        source="user",
        unique_id=address,
        options=options or {},
        discovery_keys={},
        subentries_data=(),
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


@pytest.fixture
async def hass(tmp_path):
    result = HomeAssistant(str(tmp_path))
    result.config_entries = ConfigEntries(result, {})
    yield result
    await result.async_block_till_done()


def options_flow(hass, entry):
    assert GoveeBLEConfigFlow.async_supports_options_flow(entry)
    flow = GoveeBLEConfigFlow.async_get_options_flow(entry)
    flow.hass = hass
    flow.handler = entry.entry_id
    return flow


@pytest.mark.parametrize("options, expected", [({}, 90), ({"poll_interval": 15}, 15)])
async def test_options_form_displays_default_or_saved_interval(hass, options, expected):
    entry = stored_entry(hass, options=options)
    form = await options_flow(hass, entry).async_step_init()

    assert form["type"] == "form"
    assert form["step_id"] == "init"
    assert form["data_schema"]({}) == {"poll_interval": expected}


@pytest.mark.parametrize("interval", [5, 15, 90, 3600, 30.0])
async def test_valid_polling_options_preserve_other_settings(hass, interval):
    entry = stored_entry(hass, options={"unrelated": "keep"})
    result = await options_flow(hass, entry).async_step_init(
        {"poll_interval": interval}
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {"unrelated": "keep", "poll_interval": int(interval)}
    assert type(result["data"]["poll_interval"]) is int


@pytest.mark.parametrize(
    "interval", [0, 4, 3601, 15.5, float("nan"), float("inf"), True, None, "bad"]
)
async def test_invalid_polling_input_does_not_finish_options_flow(hass, interval):
    entry = stored_entry(hass)
    result = await options_flow(hass, entry).async_step_init(
        {"poll_interval": interval}
    )

    assert result["type"] == "form"
    assert result["errors"] == {"poll_interval": "invalid_poll_interval"}
    assert entry.options == {}


async def test_saving_options_persists_and_reloads_only_selected_device(
    hass, monkeypatch
):
    entry = stored_entry(hass)
    other = stored_entry(hass, options={"poll_interval": 60}, address="OTHER")
    reloads = []
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reloads.append)
    monkeypatch.setattr(hass.config_entries._store, "async_delay_save", Mock())
    flow = options_flow(hass, entry)
    result = await flow.async_step_init({"poll_interval": 15})

    await hass.config_entries.options.async_finish_flow(flow, result)

    assert entry.options == {"poll_interval": 15}
    assert other.options == {"poll_interval": 60}
    assert reloads == [entry.entry_id]
    reopened = await options_flow(hass, entry).async_step_init()
    assert reopened["data_schema"]({}) == {"poll_interval": 15}
    unchanged_flow = options_flow(hass, entry)
    unchanged = await unchanged_flow.async_step_init({"poll_interval": 15})
    await hass.config_entries.options.async_finish_flow(unchanged_flow, unchanged)
    assert reloads == [entry.entry_id]


@pytest.mark.parametrize(
    "options, expected",
    [
        ({}, 90),
        ({"poll_interval": 5}, 5),
        ({"poll_interval": 3600}, 3600),
        ({"poll_interval": 30.0}, 30),
        ({"poll_interval": 0}, 90),
        ({"poll_interval": 15.5}, 90),
        ({"poll_interval": "bad"}, 90),
        ({"poll_interval": float("nan")}, 90),
    ],
)
async def test_coordinator_uses_saved_interval_or_safe_default(hass, options, expected):
    coordinator = GoveeBLECoordinator(hass, stored_entry(hass, options=options))
    try:
        assert coordinator.update_interval == timedelta(seconds=expected)
    finally:
        await coordinator.async_shutdown()


async def test_device_poll_intervals_are_independent(hass):
    fast = GoveeBLECoordinator(hass, stored_entry(hass, options={"poll_interval": 15}))
    slow = GoveeBLECoordinator(
        hass, stored_entry(hass, options={"poll_interval": 600}, address="OTHER")
    )
    try:
        assert fast.update_interval == timedelta(seconds=15)
        assert slow.update_interval == timedelta(seconds=600)
    finally:
        await fast.async_shutdown()
        await slow.async_shutdown()


@pytest.mark.parametrize(
    "interval, age, expected",
    [(15, 299, True), (15, 301, False), (600, 601, True), (600, 1201, False)],
)
async def test_field_freshness_allows_long_intervals_without_changing_receipt_age(
    hass, interval, age, expected
):
    coordinator = GoveeBLECoordinator(
        hass, stored_entry(hass, options={"poll_interval": interval})
    )
    received = monotonic() - age
    coordinator._seen["power_state"] = received
    try:
        assert coordinator.fresh("power_state") is expected
        assert coordinator._seen["power_state"] == received
        assert not coordinator.fresh("never_seen")
        coordinator.last_update_success = False
        assert not coordinator.fresh("power_state")
    finally:
        await coordinator.async_shutdown()
