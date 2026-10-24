"""Connect briefly to each Govee device and retain fresh validated readings."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from time import monotonic
from typing import Any, Self

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from govee_ble_client import (
    GattConnection,
    H5179Profile,
    H7102Client,
    H7126Mode,
    H7126Profile,
)
from govee_ble_client.exceptions import GoveeBLEError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_MODEL

_LOGGER = logging.getLogger(__name__)
POLL_INTERVAL = timedelta(seconds=90)
MAX_AGE = 300.0


class RetryGattConnection(GattConnection):
    """Use Home Assistant's retry connector with the client's GATT API."""

    async def connect(self) -> Self:
        """Establish a retrying BLE connection before protocol operations."""
        self._client = await establish_connection(
            BleakClientWithServiceCache,
            self._device,
            self._device.name or self._device.address,
            timeout=self._timeout,
        )
        return self


class GoveeBLECoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """A single connection owner for one physical Govee device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=POLL_INTERVAL,
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self.model: str = entry.data[CONF_MODEL]
        self._lock = asyncio.Lock()
        self._seen: dict[str, float] = {}

    def fresh(self, key: str) -> bool:
        """Whether a field was decoded from a recent valid reply."""
        return (
            self.last_update_success
            and key in self._seen
            and monotonic() - self._seen[key] < MAX_AGE
        )

    def _device(self) -> BLEDevice:
        ble_device = bluetooth.async_ble_device_from_address(self.hass, self.address)
        if ble_device is None:
            raise UpdateFailed(
                "Device is not reachable through a connectable Bluetooth adapter"
            )
        return ble_device

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch status while serializing it with control operations."""
        async with self._lock:
            try:
                values = await self._read()
            except (
                BleakError,
                GoveeBLEError,
                OSError,
                TimeoutError,
                ValueError,
            ) as err:
                raise UpdateFailed("Could not read Govee BLE device") from err
        if not values:
            raise UpdateFailed("Device returned no valid status fields")
        now = monotonic()
        self._seen.update({key: now for key in values})
        return {**(self.data or {}), **values}

    async def _read(self) -> dict[str, Any]:
        device = self._device()
        if self.model == "H7102":
            return await self._read_h7102(device)
        profile = H5179Profile() if self.model == "H5179" else H7126Profile()
        values: dict[str, Any] = {}
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def on_notification(_sender: Any, data: bytearray) -> None:
            queue.put_nowait(bytes(data))

        async with RetryGattConnection(device, timeout=12) as connection:
            notify_uuid = (
                profile.io_uuid if self.model == "H5179" else profile.notify_uuid
            )
            await connection.start_notify(notify_uuid, on_notification)
            try:
                if self.model == "H5179":
                    await profile.request_measurement(connection)
                    await asyncio.sleep(0.12)
                    await profile.request_battery(connection)
                else:
                    for query in (
                        profile.request_power_state,
                        profile.request_mode_hint,
                        profile.request_gear_mode,
                        profile.request_panel_display_state,
                        profile.request_filter_life,
                    ):
                        await query(connection)
                        await asyncio.sleep(0.12)
                await self._collect(queue, profile.parse_notification, values)
            finally:
                await connection.stop_notify(notify_uuid)
        return values

    async def _read_h7102(self, device: BLEDevice) -> dict[str, Any]:
        values: dict[str, Any] = {}
        async with (
            RetryGattConnection(device, timeout=12) as connection,
            H7102Client(connection) as client,
        ):
            await client.request_state()
            deadline = monotonic() + 4
            while (remaining := deadline - monotonic()) > 0:
                try:
                    await client.receive(timeout=min(remaining, 0.8))
                except TimeoutError:
                    continue
                except ValueError:
                    _LOGGER.debug(
                        "Ignored invalid H7102 notification from %s", self.address
                    )
                    continue
            state = client.state
            for key in (
                "power_state",
                "work_mode",
                "fan_speed",
                "display_state",
                "oscillation_state",
            ):
                if (value := getattr(state, key)) is not None:
                    values[key] = value
        return values

    async def _collect(
        self,
        queue: asyncio.Queue[bytes],
        parser: Callable[[bytes], Any],
        values: dict[str, Any],
    ) -> None:
        deadline = monotonic() + 3
        while (remaining := deadline - monotonic()) > 0:
            try:
                raw = await asyncio.wait_for(queue.get(), min(remaining, 0.8))
            except TimeoutError:
                continue
            try:
                notification = parser(raw)
            except ValueError:
                _LOGGER.debug("Ignored invalid BLE notification from %s", self.address)
                continue
            keys = (
                ("temperature_c", "humidity_percent", "battery_percent")
                if self.model == "H5179"
                else ("power_state", "mode", "display_state", "filter_life_percent")
            )
            for key in keys:
                if (value := getattr(notification, key)) is not None:
                    if (
                        key == "mode"
                        and values.get("mode") == H7126Mode.CUSTOM
                        and value != H7126Mode.CUSTOM
                    ):
                        continue
                    values[key] = value

    async def async_command(self, command: str, *args: Any) -> None:
        """Issue a verified model command, then read its actual state."""
        await self.async_commands([(command, args)])

    async def async_commands(self, commands: list[tuple[str, tuple[Any, ...]]]) -> None:
        """Send related commands in one session and then refresh state."""
        async with self._lock:
            try:
                device = self._device()
                async with RetryGattConnection(device, timeout=12) as connection:
                    if self.model == "H7102":
                        async with H7102Client(connection) as client:
                            for index, (command, args) in enumerate(commands):
                                if index:
                                    await asyncio.sleep(0.1)
                                await getattr(client, command)(*args)
                    elif self.model == "H7126":
                        profile = H7126Profile()
                        for index, (command, args) in enumerate(commands):
                            if index:
                                await asyncio.sleep(0.1)
                            await getattr(profile, command)(connection, *args)
                    else:
                        raise ValueError("This device has no controls")
            except (
                BleakError,
                GoveeBLEError,
                OSError,
                TimeoutError,
                ValueError,
            ) as err:
                raise UpdateFailed("Could not send Govee BLE command") from err
        await self.async_request_refresh()
