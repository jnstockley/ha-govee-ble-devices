"""Connect briefly to each Govee device and retain fresh validated readings."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
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
    H7102WorkMode,
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
from .custom_schedule import CustomScheduleEditor, FieldValue
from .notifications import NotificationBuffer, Readings
from .polling import poll_interval_seconds

_LOGGER = logging.getLogger(__name__)
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


@dataclass
class _SpeedBatch:
    """Live speed targets sharing one unsent write and its eventual outcome."""

    targets: dict[object, int] = field(default_factory=dict)
    sealed: bool = False
    done: bool = False
    error: BaseException | None = None


class GoveeBLECoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """A single connection owner for one physical Govee device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval = poll_interval_seconds(entry.options)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=timedelta(seconds=interval),
        )
        self._max_age = max(MAX_AGE, interval * 2)
        self.address: str = entry.data[CONF_ADDRESS]
        self.model: str = entry.data[CONF_MODEL]
        self._lock = asyncio.Lock()
        self._admission = asyncio.Condition(self._lock)
        self._active_transaction = False
        self._control_waiters: deque[object] = deque()
        self._read_waiters: deque[object] = deque()
        self._seen: dict[str, float] = {}
        self._verify_generation = 0
        self._sessions: list[_Session] = []
        self._closing = False
        self._operations: set[asyncio.Task] = set()
        self._verify_task: asyncio.Task | None = None
        self._shutdown_lock = asyncio.Lock()
        self._pending_speed: _SpeedBatch | None = None
        self.custom_editor = (
            CustomScheduleEditor(hass, entry.entry_id, self.model)
            if self.model in ("H7102", "H7126")
            else None
        )

    def fresh(self, key: str) -> bool:
        """Whether a field was decoded from a recent valid reply."""
        return (
            self.last_update_success
            and key in self._seen
            and monotonic() - self._seen[key] < self._max_age
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

    @contextmanager
    def _timing(self, phase: str, operation: str = "session") -> Iterator[None]:
        """Measure elapsed phases without logging commands or backend error text."""
        started = monotonic()
        outcome = "ok"
        try:
            yield
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except BaseException:
            outcome = "failed"
            raise
        finally:
            elapsed = monotonic() - started
            _LOGGER.debug(
                "BLE timing model=%s operation=%s phase=%s elapsed=%.3fs outcome=%s",
                self.model,
                operation,
                phase,
                elapsed,
                outcome,
                extra={
                    "govee_phase": phase,
                    "govee_operation": operation,
                    "govee_elapsed": elapsed,
                    "govee_outcome": outcome,
                },
            )

    async def _cleanup(self, session: _Session) -> None:
        """Close encryption before GATT; retain failed handles for another try."""
        with self._timing("cleanup"):
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
            with self._timing("gatt_setup"):
                await connection.connect()
            if self.model == "H7102":
                session.encrypted = H7102Client(connection)
                with self._timing("encrypted_setup"):
                    await session.encrypted.connect()
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
    async def _transaction(self, *, control: bool = False) -> AsyncIterator[None]:
        """Serialize complete transactions, admitting queued controls before reads."""
        operation = "control" if control else "read"
        with self._timing("transaction", operation):
            if self._closing:
                raise UpdateFailed("Govee BLE device is shutting down")
            task = asyncio.current_task()
            self._operations.add(task)
            admitted = False
            try:
                with self._timing("queue", operation):
                    async with self._admission:
                        queue = self._control_waiters if control else self._read_waiters
                        token = object()
                        queue.append(token)
                        try:
                            await self._admission.wait_for(
                                lambda: (
                                    self._closing
                                    or (
                                        not self._active_transaction
                                        and queue[0] is token
                                        and (control or not self._control_waiters)
                                    )
                                )
                            )
                        finally:
                            queue.remove(token)
                            self._admission.notify_all()
                        if self._closing:
                            raise UpdateFailed("Govee BLE device is shutting down")
                        self._active_transaction = admitted = True
                yield
            finally:
                if admitted:
                    async with self._admission:
                        self._active_transaction = False
                        self._admission.notify_all()
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
            if self.custom_editor is not None and "custom_settings" in values:
                self.custom_editor.observe(values["custom_settings"])
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
                    if self.model == "H7126" and self.custom_editor is not None:
                        queries += (profile.request_custom_mode_settings,)
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
        if self.custom_editor is not None:
            keys += ("custom_settings",)
        while (remaining := deadline - monotonic()) > 0:
            try:
                notification = await client.receive(timeout=remaining)
            except TimeoutError:
                break
            except ValueError:
                continue
            received = monotonic()
            if notification.fan_speed is not None:
                _LOGGER.debug(
                    "Fan speed observed model=%s speed=%s",
                    self.model,
                    notification.fan_speed,
                )
            for key in keys:
                value = (
                    notification.mode_selector_data
                    if key == "custom_settings" and notification.mode_selector == 2
                    else getattr(notification, key, None)
                )
                values.observe(key, value, received)
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
        if self.model == "H7126" and self.custom_editor is not None:
            keys += ("custom_settings",)
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
                if key == "custom_settings" and notification.raw[:3] != b"\xaa\x05\x02":
                    continue  # Command echoes are not saved-schedule readback.
                values.observe(key, getattr(notification, key), received)
            if all(key in values for key in keys):
                return

    async def async_edit_custom(
        self, block: int, field: str, value: FieldValue
    ) -> None:
        """Persist a local setting without changing reported device state."""
        if self.custom_editor is None:
            raise ValueError("This device has no custom schedule")
        try:
            await self.custom_editor.async_edit(block, field, value)
        finally:
            self.async_update_listeners()

    async def _custom_state(self, connection, client, buffer=None) -> Readings:
        """Read power, active mode, and saved settings through one receiver."""
        values = Readings()
        deadline = monotonic() + RESPONSE_TIMEOUT
        async with asyncio.timeout_at(deadline):
            if client is not None:
                await client.request_state()
            else:
                profile = H7126Profile()
                for index, query in enumerate(
                    (
                        profile.request_power_state,
                        profile.request_mode_hint,
                        profile.request_gear_mode,
                        profile.request_custom_mode_settings,
                    )
                ):
                    if index:
                        await asyncio.sleep(0.12)
                    await query(connection)
            mode_key = "work_mode" if client is not None else "mode"
            mode_hint = None
            gear = None
            while True:
                if client is not None:
                    try:
                        notification = await client.receive(
                            timeout=deadline - monotonic()
                        )
                    except ValueError:
                        continue
                    received = monotonic()
                    for key in (
                        "power_state",
                        "work_mode",
                        "fan_speed",
                        "oscillation_state",
                        "display_state",
                    ):
                        values.observe(key, getattr(notification, key), received)
                    if notification.mode_selector == 2:
                        values.observe(
                            "custom_settings", notification.mode_selector_data, received
                        )
                else:
                    notification, received = await buffer.next(
                        profile.parse_notification, deadline
                    )
                    values.observe("power_state", notification.power_state, received)
                    raw = notification.raw
                    if raw[:3] == b"\xaa\x05\x02":
                        values.observe(
                            "custom_settings", notification.custom_settings, received
                        )
                    if raw[:3] == b"\xaa\x05\x00" and raw[3] in (1, 2):
                        mode_hint = raw[3]
                    elif raw[:3] == b"\xaa\x05\x01" and notification.mode is not None:
                        gear = (notification.mode, received)
                    if mode_hint == 2:
                        values.observe("mode", H7126Mode.CUSTOM, received)
                    elif mode_hint == 1 and gear is not None:
                        values.observe("mode", *gear)
                if all(
                    key in values
                    for key in ("power_state", mode_key, "custom_settings")
                ):
                    return values

    async def _drain_custom_replies(self, client, buffer) -> None:
        """Discard queued prewrite observations before asking for verification."""
        if client is None:
            if buffer.overflow or not buffer.connection.is_connected:
                # Let next() report overflow/disconnect with its normal exception.
                await buffer.next(H7126Profile.parse_notification, monotonic())
            while not buffer.queue.empty():
                item = buffer.queue.get_nowait()
                if item is None:
                    raise GoveeBLEError("Disconnected before custom verification")
            return
        for _ in range(64):
            try:
                await client.receive(timeout=0.01)
            except TimeoutError:
                return
            except ValueError:
                continue
        raise GoveeBLEError("Too many pending custom observations")

    async def _apply_custom_session(self, connection, client, buffer, snapshot):
        """Preflight, submit exactly once, and verify within one owned session."""
        editor = self.custom_editor
        before = await self._custom_state(connection, client, buffer)
        settings = editor.merge(before["custom_settings"], snapshot)
        if before["power_state"] is False:
            if client is not None:
                await client.turn_on()
            else:
                await H7126Profile().turn_on(connection)
            await asyncio.sleep(0.1)
        if client is not None:
            await client.set_custom_settings(settings)
        else:
            await H7126Profile().set_custom_schedule(
                connection, editor.h7126_segments(settings)
            )
            await asyncio.sleep(H7126_COMMAND_SETTLE_DELAY)
        await self._drain_custom_replies(client, buffer)
        after = await self._custom_state(connection, client, buffer)
        mode_key = "work_mode" if client is not None else "mode"
        custom = H7102WorkMode.CUSTOM if client is not None else H7126Mode.CUSTOM
        if (
            after["custom_settings"] != settings
            or after[mode_key] != custom
            or not after["power_state"]
        ):
            raise ValueError("Device did not confirm custom settings and mode")
        return after

    async def async_apply_custom(self) -> None:
        """Apply a local snapshot and select Custom without replaying writes."""
        if self.custom_editor is None:
            raise ValueError("This device has no custom schedule")
        snapshot = self.custom_editor.snapshot()
        self._pending_speed = None
        async with self._transaction(control=True):
            try:
                async with self._session() as (connection, client):
                    with self._timing("controls", "custom"):
                        if client is not None:
                            values = await self._apply_custom_session(
                                connection, client, None, snapshot
                            )
                        else:
                            async with self._notifications(
                                connection, H7126Profile()
                            ) as buffer:
                                values = await self._apply_custom_session(
                                    connection, None, buffer, snapshot
                                )
            except asyncio.CancelledError:
                self._schedule_verification()
                raise
            except (
                BleakError,
                GoveeBLEError,
                OSError,
                TimeoutError,
                ValueError,
                RuntimeError,
            ) as err:
                self._schedule_verification()
                raise UpdateFailed(
                    "Could not confirm custom mode. Pending edits were retained; "
                    "check the device before applying again."
                ) from err
            self._seen.update(values.seen)
            self.async_set_updated_data({**(self.data or {}), **values})
            try:
                await self.custom_editor.async_confirm(
                    snapshot, values["custom_settings"]
                )
            except OSError as err:
                raise UpdateFailed(
                    "Custom mode was confirmed, but saving the local draft failed"
                ) from err
            finally:
                self.async_update_listeners()

    async def async_command(self, command: str, *args: Any) -> None:
        """Issue a verified model command, then read its actual state."""
        await self.async_commands([(command, args)])

    async def async_commands(self, commands: list[tuple[str, tuple[Any, ...]]]) -> None:
        """Coalesce unsent speed targets; serialize all controls without replay."""
        if not commands:
            return
        batch = None
        token = object()
        if (
            self.model == "H7102"
            and len(commands) == 1
            and commands[0][0] == "set_fan_speed"
            and len(commands[0][1]) == 1
            and type(speed := commands[0][1][0]) is int
            and 1 <= speed <= 8
        ):
            batch = self._pending_speed
            if batch is None or batch.sealed or batch.done:
                batch = self._pending_speed = _SpeedBatch()
            batch.targets[token] = speed
        else:
            # Never replace a speed target across a power, mode, or other control.
            self._pending_speed = None
        try:
            async with self._transaction(control=True):
                if batch is not None and batch.done:
                    if batch.error is not None:
                        raise UpdateFailed(
                            "Shared fan speed write failed"
                        ) from batch.error
                    return
                try:
                    await self._execute_commands(commands, batch)
                except BaseException as err:
                    if batch is not None:
                        batch.error = err
                    raise
                finally:
                    if batch is not None:
                        batch.done = True
        finally:
            if batch is not None:
                batch.targets.pop(token)
                if self._pending_speed is batch and (batch.done or not batch.targets):
                    self._pending_speed = None

    async def _execute_commands(
        self,
        commands: list[tuple[str, tuple[Any, ...]]],
        batch: _SpeedBatch | None,
    ) -> None:
        """Execute under transaction ownership; select the latest target after setup."""
        try:
            async with self._session() as (connection, client):
                if batch is not None:
                    # Freeze immediately before submission. Later requests must
                    # get a new batch, even while this session is cleaning up.
                    batch.sealed = True
                    if self._pending_speed is batch:
                        self._pending_speed = None
                    target = batch.targets[next(reversed(batch.targets))]
                    commands = [("set_fan_speed", (target,))]
                with self._timing("controls", "control"):
                    if client is not None:
                        custom_command = ("set_work_mode", (H7102WorkMode.CUSTOM,))
                        custom = None
                        if custom_command in commands:
                            custom = await self._saved_h7102_custom(client)
                        for index, (command, args) in enumerate(commands):
                            if index:
                                await asyncio.sleep(0.1)
                            if (command, args) == custom_command:
                                await client.set_custom_settings(custom)
                            else:
                                await getattr(client, command)(*args)
                            if command == "set_fan_speed":
                                _LOGGER.debug(
                                    "Fan speed submitted model=%s speed=%s",
                                    self.model,
                                    args[0],
                                )
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

    async def _saved_h7102_custom(self, client: H7102Client) -> bytes:
        """Read the saved block before any controls, preserving unknown fields."""
        deadline = monotonic() + RESPONSE_TIMEOUT
        async with asyncio.timeout_at(deadline):
            await client.request_custom_schedule()
            while (remaining := deadline - monotonic()) > 0:
                try:
                    notification = await client.receive(timeout=remaining)
                except ValueError:
                    continue
                if notification.mode_selector != 2:
                    continue
                settings = notification.mode_selector_data
                if settings is None or len(settings) != 16:
                    raise ValueError("Saved Custom settings must contain 16 bytes")
                return settings
            raise TimeoutError("Timed out reading saved Custom settings")

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
