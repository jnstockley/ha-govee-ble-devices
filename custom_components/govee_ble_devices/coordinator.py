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
COMMAND_VERIFY_DELAY = 1.0


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
        self._verify_generation = 0

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
            state_keys = (
                "power_state",
                "work_mode",
                "fan_speed",
                "display_state",
                "oscillation_state",
            )
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
                if all(getattr(client.state, key) is not None for key in state_keys):
                    break
            state = client.state
            for key in state_keys:
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
        """Send related commands and verify their state after a short delay."""
        async with self._lock:
            try:
                started = monotonic()
                device = self._device()
                async with RetryGattConnection(device, timeout=12) as connection:
                    _LOGGER.debug(
                        "BLE connection to %s established in %.2f seconds",
                        self.address,
                        monotonic() - started,
                    )
                    if self.model == "H7102":
                        async with H7102Client(connection) as client:
                            _LOGGER.debug(
                                "H7102 session with %s established in %.2f seconds",
                                self.address,
                                monotonic() - started,
                            )
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
                _LOGGER.debug(
                    "BLE commands for %s completed in %.2f seconds",
                    self.address,
                    monotonic() - started,
                )
            except (
                BleakError,
                GoveeBLEError,
                OSError,
                TimeoutError,
                ValueError,
            ) as err:
                raise UpdateFailed("Could not send Govee BLE command") from err
            updates = self._command_updates(commands)
            if updates:
                self._seen.update({key: monotonic() for key in updates})
                self.async_set_updated_data({**(self.data or {}), **updates})
            self._verify_generation += 1
            generation = self._verify_generation
            self.config_entry.async_create_background_task(
                self.hass,
                self._verify_command(generation),
                name=f"{self.name} verify command",
            )

    def _command_updates(
        self, commands: list[tuple[str, tuple[Any, ...]]]
    ) -> dict[str, Any]:
        """Project values accepted by the device while readback is pending."""
        updates: dict[str, Any] = {}
        for command, args in commands:
            if command == "turn_on":
                updates["power_state"] = True
            elif command == "turn_off":
                updates["power_state"] = False
            elif command == "set_fan_speed":
                updates["fan_speed"] = args[0]
            elif command == "set_work_mode":
                updates["work_mode"] = args[0]
            elif command == "set_oscillation":
                updates["oscillation_state"] = args[0]
            elif command == "set_panel_display":
                updates["display_state"] = args[0]
            elif command.startswith("set_") and command.endswith("_mode"):
                updates["mode"] = H7126Mode(
                    command.removeprefix("set_").removesuffix("_mode")
                )
        return updates

    async def _verify_command(self, generation: int) -> None:
        """Coalesce nearby writes into one device readback."""
        await asyncio.sleep(COMMAND_VERIFY_DELAY)
        if generation == self._verify_generation:
            await self.async_refresh()
