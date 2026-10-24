"""Real HA/client objects with Bluetooth replaced at the radio boundary."""

import asyncio
from types import SimpleNamespace

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.govee_ble_devices import coordinator as integration
from custom_components.govee_ble_devices.coordinator import GoveeBLECoordinator

from .helpers import RadioClient


@pytest.fixture
async def coordinator(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    unload_callbacks = []
    entry = SimpleNamespace(
        title="Test device",
        data={"address": "AA:BB:CC:DD:EE:FF", "model": "H5179"},
        async_on_unload=unload_callbacks.append,
        unload_callbacks=unload_callbacks,
        pref_disable_polling=False,
        state=ConfigEntryState.LOADED,
        async_create_background_task=lambda hass, coro, **kwargs: asyncio.create_task(
            coro, name=kwargs["name"]
        ),
    )
    result = GoveeBLECoordinator(hass, entry)
    yield result
    await result.async_shutdown()


@pytest.fixture
def device():
    return BLEDevice("AA:BB:CC:DD:EE:FF", "Govee test", {})


@pytest.fixture
def radio(monkeypatch, device):
    clients = []
    resolved = []

    def lookup(hass, address, *, connectable):
        assert connectable is True
        resolved.append(device)
        return device

    async def establish(client_class, native_device, name, **kwargs):
        client = client_class(native_device, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(integration.bluetooth, "async_ble_device_from_address", lookup)
    monkeypatch.setattr(integration, "BleakClientWithServiceCache", RadioClient)
    monkeypatch.setattr(integration, "establish_connection", establish)
    return SimpleNamespace(clients=clients, resolved=resolved)
