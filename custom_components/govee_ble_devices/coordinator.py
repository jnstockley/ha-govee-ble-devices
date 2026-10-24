"""Connect briefly to each Govee device and retain fresh validated readings."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from govee_ble_client import (
    GattConnection,
    H5179Profile,
    H7102Client,
    H7102Profile,
    H7126Mode,
    H7126Profile,
)
from govee_ble_client.exceptions import GoveeBLEError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connection import ProfileGattConnection
from .const import CONF_MODEL
from .notifications import NotificationBuffer, Readings

_LOGGER = logging.getLogger(__name__)
POLL_INTERVAL = timedelta(seconds=90)
MAX_AGE = 300.0
COMMAND_VERIFY_DELAY = 1.0
H7126_COMMAND_SETTLE_DELAY = 0.3
RESPONSE_TIMEOUT = 4.0


@dataclass
class _Session:
    """Retain every resource until its teardown has succeeded."""

    connection: GattConnection | None = None
    encrypted: H7102Client | None = None
    partial_client: Any = None


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
        self._sessions: list[_Session] = []
        self._closing = False
        self._operations: set[asyncio.Task] = set()
        self._verify_task: asyncio.Task | None = None
        self._shutdown_lock = asyncio.Lock()

    def fresh(self, key: str) -> bool:
        """Whether a field was decoded from a recent valid reply."""
        return (
            self.last_update_success
            and key in self._seen
            and monotonic() - self._seen[key] < MAX_AGE
        )

    def _device(self) -> BLEDevice:
        ble_device = self._resolve_device()
        if ble_device is None:
            raise UpdateFailed(
                "Device is not reachable through a connectable Bluetooth adapter"
            )
        return ble_device

    def _resolve_device(self) -> BLEDevice | None:
        return bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )

    async def _cleanup(self, session: _Session) -> None:
        """Close encryption before GATT; retain failed handles for another try."""
        error: Exception | None = None
        if session.encrypted is not None:
            try:
                await session.encrypted.close()
                session.encrypted = None
            except Exception as err:  # noqa: BLE001 - retain all backend cleanup failures
                error = err
        if session.connection is not None:
            try:
                await session.connection.disconnect()
                session.connection = None
            except Exception as err:  # noqa: BLE001 - retain all backend cleanup failures
                error = err
        if session.partial_client is not None:
            try:
                async with asyncio.timeout(10):
                    await session.partial_client.disconnect()
                session.partial_client = None
            except Exception as err:  # noqa: BLE001 - retain all backend cleanup failures
                error = err
        if error is not None:
            raise error
        self._sessions.remove(session)

    @asynccontextmanager
    async def _session(
        self,
    ) -> AsyncIterator[tuple[GattConnection, H7102Client | None]]:
        """Use public client hooks without bypassing transport lifecycle tracking."""
        for pending in list(self._sessions):
            await self._cleanup(pending)
        session = _Session()
        self._sessions.append(session)

        async def connector(device, *, timeout, disconnected_callback, services=None):
            # Retry connector constructs its client internally. Capture it before
            # awaiting setup so cancellation cannot orphan a partially opened link.
            class ManagedClient(BleakClientWithServiceCache):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    session.partial_client = self

            handed_off = False

            def disconnected(client):
                # Retry connector reuses its client across setup attempts. A
                # failed intermediate attempt is not loss of the usable GATT
                # session. GattConnection checks is_connected on handoff and
                # notifies observers itself if setup ultimately fails.
                if handed_off:
                    disconnected_callback(client)

            options = {} if services is None else {"services": services}
            client = await establish_connection(
                ManagedClient,
                device,
                "Govee device",
                disconnected_callback=disconnected,
                max_attempts=3,
                timeout=timeout,
                **options,
            )
            handed_off = True
            session.partial_client = None  # GattConnection now owns the client.
            return client

        profile = {
            "H5179": H5179Profile,
            "H7102": H7102Profile,
            "H7126": H7126Profile,
        }[self.model]
        try:
            connection = session.connection = ProfileGattConnection(
                self._device(),
                timeout=45,
                operation_timeout=10,
                services=(profile.service_uuid,),
                connector=connector,
                device_resolver=self._resolve_device,
            )
            started = monotonic()
            await connection.connect()
            _LOGGER.debug(
                "GATT setup model=%s elapsed=%.3fs", self.model, monotonic() - started
            )
            if self.model == "H7102":
                session.encrypted = H7102Client(connection)
                await session.encrypted.connect()
                _LOGGER.debug(
                    "Encrypted setup model=%s elapsed=%.3fs",
                    self.model,
                    monotonic() - started,
                )
            yield connection, session.encrypted
        except BaseException:
            try:
                await self._cleanup(session)
            except Exception as err:  # noqa: BLE001 - retain all backend cleanup failures
                _LOGGER.debug(
                    "Retained BLE cleanup handle error=%s", type(err).__name__
                )
            raise
        else:
            await self._cleanup(session)

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        """Track even queued operations so unload can cancel and await them."""
        if self._closing:
            raise UpdateFailed("Govee BLE device is shutting down")
        task = asyncio.current_task()
        self._operations.add(task)
        try:
            async with self._lock:
                if self._closing:
                    raise UpdateFailed("Govee BLE device is shutting down")
                yield
        finally:
            self._operations.discard(task)

    async def async_shutdown(self) -> None:
        """Stop admission, await owned work, and retry retained BLE teardown."""
        async with self._shutdown_lock:
            self._closing = True
            await super().async_shutdown()
            tasks = set(self._operations)
            if self._verify_task is not None:
                tasks.add(self._verify_task)
            tasks.discard(asyncio.current_task())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            async with self._lock:
                for attempt in range(2):
                    for session in list(self._sessions):
                        try:
                            await self._cleanup(session)
                        except Exception as err:  # noqa: BLE001 - retain all backend cleanup failures
                            _LOGGER.debug(
                                "Shutdown cleanup attempt=%d error=%s",
                                attempt + 1,
                                type(err).__name__,
                            )
                    if not self._sessions:
                        return
                raise UpdateFailed("BLE teardown failed; cleanup handles retained")

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and publish observations while holding the device transaction lock."""
        async with self._transaction():
            try:
                values = await self._read()
            except (
                BleakError,
                GoveeBLEError,
                OSError,
                TimeoutError,
                ValueError,
                RuntimeError,
            ) as err:
                raise UpdateFailed("Could not read Govee BLE device") from err
            if not values:
                raise UpdateFailed("Device returned no valid status fields")
            self._seen.update(values.seen)
            return {**(self.data or {}), **values}

    @asynccontextmanager
    async def _notifications(self, connection, profile):
        buffer = NotificationBuffer(connection)
        unsubscribe = connection.add_disconnect_callback(buffer.disconnected)
        try:
            uuid = profile.io_uuid if self.model == "H5179" else profile.notify_uuid
            await connection.start_notify(uuid, buffer.notification)
            yield buffer
        finally:
            unsubscribe()
            # The session owner disconnects GATT, which removes subscriptions.
            # Avoid stop_notify on a lost link masking the original failure.

    async def _read(self) -> Readings:
        values = Readings()
        async with self._session() as (connection, client):
            started = monotonic()
            deadline = started + RESPONSE_TIMEOUT
            if client is not None:
                await self._read_h7102(client, values, deadline)
            else:
                profile = H5179Profile() if self.model == "H5179" else H7126Profile()
                async with self._notifications(connection, profile) as buffer:
                    queries = (
                        (profile.request_measurement, profile.request_battery)
                        if self.model == "H5179"
                        else (
                            profile.request_power_state,
                            profile.request_mode_hint,
                            profile.request_gear_mode,
                            profile.request_panel_display_state,
                            profile.request_filter_life,
                        )
                    )
                    # Setup has its own budget; query writes and response waiting
                    # share this deadline. Keep the captured conservative pacing.
                    async with asyncio.timeout_at(deadline):
                        for index, query in enumerate(queries):
                            if index:
                                await asyncio.sleep(0.12)
                            await query(connection)
                    await self._collect(buffer, profile, values, deadline)
            _LOGGER.debug(
                "Response collection model=%s elapsed=%.3fs fields=%d",
                self.model,
                monotonic() - started,
                len(values),
            )
        return values

    async def _read_h7102(self, client, values: Readings, deadline: float) -> None:
        async with asyncio.timeout_at(deadline):
            await client.request_state()
        keys = (
            "power_state",
            "work_mode",
            "fan_speed",
            "display_state",
            "oscillation_state",
        )
        while (remaining := deadline - monotonic()) > 0:
            try:
                notification = await client.receive(timeout=remaining)
            except TimeoutError:
                break
            except ValueError:
                continue
            received = monotonic()
            for key in keys:
                values.observe(key, getattr(notification, key), received)
            if all(key in values for key in keys):
                break

    async def _collect(
        self, buffer, profile, values: Readings, deadline: float
    ) -> None:
        keys = (
            ("temperature_c", "humidity_percent", "battery_percent")
            if self.model == "H5179"
            else ("power_state", "mode", "display_state", "filter_life_percent")
        )
        mode_hint = None
        gear = None
        while True:
            try:
                notification, received = await buffer.next(
                    profile.parse_notification, deadline
                )
            except TimeoutError:
                return
            if self.model == "H7126":
                raw = notification.raw
                if raw[:3] == b"\xaa\x05\x00" and raw[3] in (1, 2):
                    mode_hint = raw[3]
                    if mode_hint == 2:
                        values.observe("mode", H7126Mode.CUSTOM, received)
                    elif gear is not None:
                        values.observe("mode", *gear)
                elif raw[:3] == b"\xaa\x05\x01" and notification.mode is not None:
                    gear = (notification.mode, received)
                    if mode_hint == 1:
                        values.observe("mode", *gear)
            for key in keys:
                if key == "mode":
                    continue  # Saved gear is active only when the category says so.
                values.observe(key, getattr(notification, key), received)
            if all(key in values for key in keys):
                return

    async def async_command(self, command: str, *args: Any) -> None:
        """Issue a verified model command, then read its actual state."""
        await self.async_commands([(command, args)])

    async def async_commands(self, commands: list[tuple[str, tuple[Any, ...]]]) -> None:
        """Submit controls once; transport completion is provisional until readback."""
        if not commands:
            return
        async with self._transaction():
            try:
                async with self._session() as (connection, client):
                    if client is not None:
                        for index, (command, args) in enumerate(commands):
                            if index:
                                await asyncio.sleep(0.1)
                            await getattr(client, command)(*args)
                    elif self.model == "H7126":
                        profile = H7126Profile()
                        custom = None
                        if any(command == "set_custom_mode" for command, _ in commands):
                            custom = await self._saved_custom(connection, profile)
                        for index, (command, args) in enumerate(commands):
                            if index:
                                await asyncio.sleep(0.1)
                            if command == "set_custom_mode":
                                await profile.set_custom_schedule(connection, custom)
                            else:
                                await getattr(profile, command)(connection, *args)
                        # Write-without-response completion can mean only queued
                        # locally. Immediate Core Bluetooth teardown drops the
                        # final control; allow it to transmit before disconnect.
                        # This is not confirmation: readback still owns truth.
                        await asyncio.sleep(H7126_COMMAND_SETTLE_DELAY)
                    else:
                        raise ValueError("This device has no controls")
            except (
                BleakError,
                GoveeBLEError,
                OSError,
                TimeoutError,
                ValueError,
                RuntimeError,
            ) as err:
                # A failed transport write may already have reached the device.
                # Request observations; never replay the control automatically.
                self._schedule_verification()
                raise UpdateFailed("Could not send Govee BLE command") from err
            updates = self._command_updates(commands)
            if updates:
                # Preserve observed ages and availability; a write cannot revive
                # an unavailable device or establish a fresh observation.
                self.data = {**(self.data or {}), **updates}
                self.async_update_listeners()
            self._schedule_verification()

    async def _saved_custom(self, connection, profile):
        """Read before selecting Custom; refuse settings the writer cannot preserve."""
        async with self._notifications(connection, profile) as buffer:
            deadline = monotonic() + RESPONSE_TIMEOUT
            async with asyncio.timeout_at(deadline):
                await profile.request_custom_mode_settings(connection)
                while True:
                    notification, _received = await buffer.next(
                        profile.parse_notification, deadline
                    )
                    if notification.raw[:3] != b"\xaa\x05\x02":
                        continue
                    schedule = notification.custom_schedule
                    # The released H7126 writer always writes marker zero.
                    if schedule is None or schedule.marker != 0:
                        raise ValueError("Saved Custom schedule cannot be preserved")
                    segments = []
                    for index, segment in enumerate(schedule.segments):
                        duration = segment.duration_minutes
                        if (
                            segment.mode is None
                            or (segment.is_continuous and index != 2)
                            or (
                                not segment.is_continuous
                                and (duration is None or not 1 <= duration <= 65534)
                            )
                        ):
                            raise ValueError("Saved Custom segment cannot be preserved")
                        segments.append((segment.mode, duration))
                    return tuple(segments)

    def _schedule_verification(self) -> None:
        if self._closing:
            return
        if self._verify_task is not None and not self._verify_task.done():
            self._verify_task.cancel()
        self._verify_generation += 1
        self._verify_task = self.config_entry.async_create_background_task(
            self.hass,
            self._verify_command(self._verify_generation),
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
        if not self._closing and generation == self._verify_generation:
            await self.async_refresh()
