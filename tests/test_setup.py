"""Setup must preserve HA retry semantics and durable BLE cleanup ownership."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from govee_ble_client import build_packet
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components import govee_ble_devices as component

from .helpers import reply, use_replies


async def test_failed_first_refresh_retains_owner_and_retries_cleanup_before_setup(
    coordinator, radio, monkeypatch
):
    hass = coordinator.hass
    entry = coordinator.config_entry
    entry.state = ConfigEntryState.SETUP_IN_PROGRESS
    forward = AsyncMock()
    hass.config_entries = SimpleNamespace(async_forward_entry_setups=forward)
    failed = False

    def respond(client, data):
        nonlocal failed
        if not failed:
            failed = True
            client.disconnect_failures = 20
            raise OSError("query failed with pending teardown")
        if data[1] == 0x0A:
            reply(client, build_packet(0xAA, 0x0A, b"\x10\x27\x88\x13"))
        else:
            reply(client, build_packet(0xAA, 0x08, b"\x50"))

    use_replies(monkeypatch, radio, respond)
    try:
        # Cleanup must not replace ConfigEntryNotReady with UpdateFailed.
        with pytest.raises(ConfigEntryNotReady):
            await component.async_setup_entry(hass, entry)
        owner = entry.runtime_data
        assert owner._sessions
        # Model HA draining its unload callback stack after failed setup.
        while entry.unload_callbacks:
            try:
                await entry.unload_callbacks.pop()()
            except UpdateFailed:
                pass
        with pytest.raises(ConfigEntryNotReady):
            await component.async_setup_entry(hass, entry)
        assert entry.runtime_data is owner
        assert len(radio.clients) == 1
        assert not forward.called
        radio.clients[0].disconnect_failures = 0
        assert await component.async_setup_entry(hass, entry)
        assert entry.runtime_data is not owner
        assert not radio.clients[0].is_connected
        assert len(radio.clients) == 2
        assert forward.call_count == 1
    finally:
        for client in radio.clients:
            client.disconnect_failures = 0
        for callback in entry.unload_callbacks:
            await callback()
        if hasattr(entry, "runtime_data"):
            await entry.runtime_data.async_shutdown()
