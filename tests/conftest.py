"""Real HA/client objects with Bluetooth replaced at the radio boundary."""

import asyncio
from types import SimpleNamespace

import pytest
from bleak.backends.device import BLEDevice
from govee_ble_client import build_packet
from govee_ble_client.profiles import h7102
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.govee_ble_devices import coordinator as integration
from custom_components.govee_ble_devices.coordinator import GoveeBLECoordinator

from .helpers import RadioClient, use_replies


@pytest.fixture
async def coordinator(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    unload_callbacks = []
    entry = SimpleNamespace(
        entry_id="test-device-entry",
        title="Test device",
        data={"address": "AA:BB:CC:DD:EE:FF", "model": "H5179"},
        options={},
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


@pytest.fixture
def encrypted(coordinator, radio, monkeypatch):
    coordinator.model = "H7102"
    use_replies(monkeypatch, radio, lambda *_: None, purifier=True)
    transports = []

    class Transport:
        """Decoded encrypted-stream double; real H7102Client parses every frame."""

        custom_replies = ()

        def __init__(self, connection, **kwargs):
            self.connection = connection
            self.queue = asyncio.Queue()
            self.established = False
            self.receiving = False
            self.closed = False
            self.writes = []
            self.replies = [
                b"bad",
                build_packet(0xAA, 0x01, b"\x00"),
                build_packet(0xAA, 0x05, b"\x00\x01"),
                build_packet(0xAA, 0x05, b"\x01\x05"),
                build_packet(0xAA, 0x16, b"\x00"),
                build_packet(0xAA, 0x1F, b"\x01\x00"),
            ]
            transports.append(self)

        async def connect(self, **kwargs):
            self.established = True

        async def close(self):
            assert self.connection.is_connected  # Encryption closes before GATT.
            self.established = False
            self.closed = True

        async def write(self, frame):
            self.writes.append(frame)
            if frame[:3] == b"\xaa\x05\x02":
                for item in self.custom_replies:
                    self.queue.put_nowait(item)
            if frame[:3] == b"\xaa\x1f\x01":
                for item in self.replies:
                    self.queue.put_nowait(item)

        async def receive(self, *, timeout):
            assert not self.receiving  # Exactly one encrypted receiver.
            self.receiving = True
            try:
                async with asyncio.timeout(timeout):
                    return await self.queue.get()
            finally:
                self.receiving = False

    monkeypatch.setattr(h7102, "GoveeAesRc4V1", Transport)
    return transports
