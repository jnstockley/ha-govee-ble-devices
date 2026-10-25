"""Setup must preserve HA retry semantics and durable BLE cleanup ownership."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from govee_ble_client import build_packet
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components import govee_ble_devices as component
from custom_components.govee_ble_devices.const import DOMAIN
from custom_components.govee_ble_devices.custom_schedule import CustomScheduleEditor

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


@pytest.mark.parametrize("foreign_owner", [False, True])
async def test_removed_block_three_controls_are_cleaned_from_registry(
    coordinator, foreign_owner
):
    hass = coordinator.hass
    entry = coordinator.config_entry
    entry.data["model"] = "H7102"
    entry.disabled_by = None
    entry.pref_disable_new_entities = False
    hass.config_entries = SimpleNamespace(async_get_entry=lambda entry_id: entry)
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    registry = er.async_get(hass)
    owner = (
        SimpleNamespace(
            entry_id="other-entry", disabled_by=None, pref_disable_new_entities=False
        )
        if foreign_owner
        else entry
    )
    removed = []
    for domain, key in (
        ("number", "custom_block_3_duration"),
        ("switch", "custom_block_3_continuous"),
    ):
        entity = registry.async_get_or_create(
            domain, DOMAIN, f"{coordinator.address}_{key}", config_entry=owner
        )
        removed.append(entity.entity_id)
    speed = registry.async_get_or_create(
        "number",
        DOMAIN,
        f"{coordinator.address}_custom_block_3_speed",
        config_entry=entry,
    )
    other = registry.async_get_or_create(
        "number", DOMAIN, "OTHER_custom_block_3_duration", config_entry=entry
    )
    foreign_platform = registry.async_get_or_create(
        "number",
        "other_integration",
        f"{coordinator.address}_custom_block_3_duration",
        config_entry=entry,
    )
    component._remove_obsolete_custom_entities(hass, entry)
    assert all(
        (registry.async_get(entity_id) is not None) == foreign_owner
        for entity_id in removed
    )
    assert registry.async_get(speed.entity_id) is not None
    assert registry.async_get(other.entity_id) is not None
    assert registry.async_get(foreign_platform.entity_id) is not None


@pytest.mark.parametrize("invalid_storage", [False, True])
async def test_custom_draft_loads_before_platforms_and_is_removed_with_entry(
    coordinator, radio, monkeypatch, invalid_storage
):
    hass = coordinator.hass
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    entry = coordinator.config_entry
    entry.state = ConfigEntryState.SETUP_IN_PROGRESS
    entry.data["model"] = "H7126"
    saved = CustomScheduleEditor(hass, entry.entry_id, "H7126")
    await saved.async_edit(0, "mode", "sleep")
    if invalid_storage:
        Path(saved.store.path).write_text('{"data": {}}')
    settings = bytes.fromhex("00 02 00140014 03 001e001e 01 ffffffff")

    def respond(client, data):
        if data[:3] == b"\xaa\x05\x02":
            reply(client, build_packet(0xAA, 0x05, b"\x02" + settings))
        elif data[1] == 0x01:
            reply(client, build_packet(0xAA, 0x01, b"\x01"))
        elif data[:3] == b"\xaa\x05\x00":
            reply(client, build_packet(0xAA, 0x05, b"\x00\x01"))
        elif data[:3] == b"\xaa\x05\x01":
            reply(client, build_packet(0xAA, 0x05, b"\x01\x03"))
        elif data[1] == 0x16:
            reply(client, build_packet(0xAA, 0x16, b"\x00"))
        elif data[1] == 0x19:
            reply(client, build_packet(0xAA, 0x19, b"\x00\x00\x00\x00\x00"))

    use_replies(monkeypatch, radio, respond, purifier=True)
    forwarded = []

    async def forward(hass_entry, platforms):
        forwarded.extend(platforms)
        assert hass_entry.runtime_data.custom_editor.value(0, "mode") == (
            "low" if invalid_storage else "sleep"
        )
        assert hass_entry.runtime_data.custom_editor.value(1, "mode") == "high"

    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=forward,
        async_unload_platforms=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        component.bluetooth, "async_rediscover_address", lambda *_: None
    )
    try:
        assert await component.async_setup_entry(hass, entry)
        assert "number" in forwarded and "select" in forwarded
        assert await component.async_unload_entry(hass, entry)
        restored = CustomScheduleEditor(hass, entry.entry_id, "H7126")
        await restored.async_load()
        assert restored.value(0, "mode") == (None if invalid_storage else "sleep")
        await component.async_remove_entry(hass, entry)
        empty = CustomScheduleEditor(hass, entry.entry_id, "H7126")
        await empty.async_load()
        assert not empty.snapshot().edits
        assert not radio.clients[0].is_connected
    finally:
        if hasattr(entry, "runtime_data"):
            await entry.runtime_data.async_shutdown()
