"""Regression tests for integration-owned BLE transactions, without RF I/O."""

import asyncio
from time import monotonic

import bleak_retry_connector
import pytest
from bleak.exc import BleakError
from govee_ble_client import H5179Profile, H7102WorkMode, H7126Mode, build_packet
from govee_ble_client.exceptions import DeviceNotConnectedError
from govee_ble_client.profiles import h7102

from custom_components.govee_ble_devices import coordinator as integration
from custom_components.govee_ble_devices.fan import GoveeBLEFan

from .helpers import RadioClient, reply, use_replies


async def test_complete_sensor_read_finishes_early_with_zero_values(
    coordinator, radio, monkeypatch
):
    def respond(client, data):
        reply(client, b"bad")
        if data[1] == 0x0A:
            reply(client, build_packet(0xAA, 0x0A, b"\x00\x00\x00\x00"))
        else:
            reply(client, build_packet(0xAA, 0x08, b"\x00"))

    use_replies(monkeypatch, radio, respond)
    values = await asyncio.wait_for(coordinator._async_update_data(), 0.8)
    assert values == {"temperature_c": 0, "humidity_percent": 0, "battery_percent": 0}
    assert coordinator.fresh("battery_percent")
    assert not radio.clients[0].is_connected


async def test_partial_sensor_read_keeps_valid_fields_and_previous_data(
    coordinator, radio, monkeypatch
):
    coordinator.data = {"battery_percent": 80}
    monkeypatch.setattr(integration, "RESPONSE_TIMEOUT", 0.25, raising=False)

    def respond(client, data):
        reply(client, build_packet(0xAA, 0x0A, b"\x10\x27\x88\x13"))

    use_replies(monkeypatch, radio, respond)
    values = await asyncio.wait_for(coordinator._async_update_data(), 0.6)
    assert values == {
        "temperature_c": 100,
        "humidity_percent": 50,
        "battery_percent": 80,
    }
    assert not coordinator.fresh("battery_percent")


@pytest.mark.parametrize("custom_first", [True, False])
async def test_purifier_custom_hint_wins_over_saved_gear_reply(
    coordinator, radio, monkeypatch, custom_first
):
    coordinator.model = "H7126"

    def respond(client, data):
        if data[1] == 0x01:
            reply(client, build_packet(0xAA, 0x01, b"\x00"))
        elif data[1] == 0x05 and data[2] == 0:
            frames = [
                build_packet(0xAA, 0x05, b"\x00\x02"),
                build_packet(0xAA, 0x05, b"\x01\x03"),
            ]
            for frame in frames if custom_first else reversed(frames):
                reply(client, frame)
        elif data[1] == 0x16:
            reply(client, build_packet(0xAA, 0x16, b"\x00"))
        elif data[1] == 0x19:
            reply(client, build_packet(0xAA, 0x19, b"\x00\x00\x00\x00\x00"))

    use_replies(monkeypatch, radio, respond, purifier=True)
    values = await asyncio.wait_for(coordinator._async_update_data(), 0.9)
    assert values == {
        "power_state": False,
        "mode": H7126Mode.CUSTOM,
        "display_state": False,
        "filter_life_percent": 0,
    }


async def test_notification_flood_fails_without_unbounded_buffering(
    coordinator, radio, monkeypatch
):
    def respond(client, data):
        for _ in range(100):
            reply(client, b"bad")

    use_replies(monkeypatch, radio, respond)
    with pytest.raises(integration.UpdateFailed):
        await asyncio.wait_for(coordinator._async_update_data(), 0.7)
    assert not radio.clients[0].is_connected


async def test_disconnect_wakes_plain_reader(coordinator, radio, monkeypatch):
    def respond(client, data):
        asyncio.get_running_loop().call_later(0.15, client.lose_link)

    use_replies(monkeypatch, radio, respond)
    with pytest.raises(integration.UpdateFailed):
        await asyncio.wait_for(coordinator._async_update_data(), 0.7)


async def test_public_session_tracks_disconnect_and_filters_services(
    coordinator, radio
):
    async with coordinator._session() as (connection, client):
        assert client is None
        await H5179Profile().request_battery(connection)
        backend = radio.clients[0]
        assert backend.options["services"] == (H5179Profile.service_uuid,)
        assert backend.options["max_attempts"] == 3
        assert radio.resolved  # Fresh HA lookup, rather than address-only scanning.
        assert backend.writes[0][1][:2] == b"\xaa\x08"
        backend.lose_link()
        assert not connection.is_connected
        with pytest.raises(DeviceNotConnectedError):
            await H5179Profile().request_battery(connection)
    assert not backend.is_connected


async def test_session_retries_retained_failed_cleanup(coordinator, radio):
    with pytest.raises(OSError):
        async with coordinator._session():
            radio.clients[0].disconnect_failures = 1
    assert radio.clients[0].is_connected
    async with coordinator._session():
        assert not radio.clients[0].is_connected
    assert not radio.clients[1].is_connected


async def test_cancelled_setup_cleans_partial_retry_client(
    coordinator, radio, monkeypatch, device
):
    entered = asyncio.Event()
    clients = []

    async def establish(client_class, native_device, name, **kwargs):
        client = client_class(native_device, **kwargs)
        clients.append(client)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(integration, "establish_connection", establish)

    async def connect():
        async with coordinator._session():
            pytest.fail("Cancelled setup must not enter the transaction")

    task = asyncio.create_task(connect())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not clients[0].is_connected


async def test_controls_do_not_refresh_observation_age_or_restore_availability(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"
    coordinator.last_update_success = False
    coordinator._seen["power_state"] = monotonic() - 301
    observed = coordinator._seen["power_state"]
    use_replies(monkeypatch, radio, lambda *_: None, purifier=True)
    await coordinator.async_command("turn_on")
    assert coordinator._seen["power_state"] == observed
    assert coordinator.last_update_success is False
    assert not coordinator.fresh("power_state")


async def test_custom_selection_reads_saved_segments_instead_of_fixed_defaults(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"
    settings = bytes.fromhex("00 02 00140014 03 001e001e 01 ffffffff")

    def respond(client, data):
        if data[:3] == b"\xaa\x05\x02":
            reply(client, build_packet(0xAA, 0x05, b"\x02" + settings))

    use_replies(monkeypatch, radio, respond, purifier=True)
    await coordinator.async_commands([("turn_on", ()), ("set_custom_mode", ())])
    frames = [item[1] for item in radio.clients[0].writes]
    assert frames[0][:3] == b"\xaa\x05\x02"  # Preflight before any control.
    assert frames[-1][0:3] == b"\x3a\x05\x02"
    assert frames[-1][3:19] == settings


@pytest.mark.parametrize(
    "settings",
    [
        "07 02 00140014 03 001e001e 01 ffffffff",  # Writer cannot preserve marker.
        "00 02 00140015 03 001e001e 01 ffffffff",  # Mismatched duration words.
        "00 09 00140014 03 001e001e 01 ffffffff",  # Unknown mode.
        "00 02 ffffffff 03 001e001e 01 ffffffff",  # Continuous before final segment.
    ],
)
async def test_unrepresentable_custom_schedule_never_sends_controls(
    coordinator, radio, monkeypatch, settings
):
    coordinator.model = "H7126"

    def respond(client, data):
        if data[:3] == b"\xaa\x05\x02":
            reply(client, build_packet(0xAA, 0x05, b"\x02" + bytes.fromhex(settings)))

    use_replies(monkeypatch, radio, respond, purifier=True)
    with pytest.raises(integration.UpdateFailed):
        await coordinator.async_commands([("turn_on", ()), ("set_custom_mode", ())])
    assert all(item[1][0] == 0xAA for item in radio.clients[0].writes)


async def test_ambiguous_control_is_not_replayed_and_schedules_readback(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"

    def fail(client, data):
        raise OSError("May have reached device")

    use_replies(monkeypatch, radio, fail, purifier=True)
    with pytest.raises(integration.UpdateFailed):
        await coordinator.async_command("turn_on")
    assert len(radio.clients) == 1
    assert len(radio.clients[0].writes) == 1
    assert coordinator._verify_task is not None
    assert not radio.clients[0].is_connected


async def test_verification_is_coalesced_and_cancelled_on_shutdown(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"
    use_replies(monkeypatch, radio, lambda *_: None, purifier=True)
    await coordinator.async_command("turn_on")
    previous = coordinator._verify_task
    await coordinator.async_command("turn_off")
    await asyncio.sleep(0)
    assert previous.cancelled()
    current = coordinator._verify_task
    await coordinator.async_shutdown()
    assert current.done()
    with pytest.raises(integration.UpdateFailed):
        await coordinator.async_command("turn_on")
    assert len(radio.clients) == 2


async def test_shutdown_cancels_active_setup_and_queued_control(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"
    entered = asyncio.Event()
    original = integration.establish_connection

    async def establish(*args, **kwargs):
        client = await original(*args, **kwargs)
        entered.set()
        await asyncio.Event().wait()
        return client

    monkeypatch.setattr(integration, "establish_connection", establish)
    active = asyncio.create_task(coordinator.async_command("turn_on"))
    await asyncio.wait_for(entered.wait(), 1)
    queued = asyncio.create_task(coordinator.async_command("turn_off"))
    await asyncio.sleep(0)
    await asyncio.wait_for(coordinator.async_shutdown(), 0.8)
    assert active.cancelled()
    assert queued.cancelled()
    assert len(radio.clients) == 1
    assert not radio.clients[0].is_connected


async def test_shutdown_retries_failed_cleanup_before_releasing_owner(
    coordinator, radio
):
    with pytest.raises(OSError):
        async with coordinator._session():
            radio.clients[0].disconnect_failures = 2
    await coordinator.async_shutdown()
    assert not radio.clients[0].is_connected
    assert not coordinator._sessions


async def test_gear_reply_without_mode_hint_does_not_claim_active_mode(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"
    monkeypatch.setattr(integration, "RESPONSE_TIMEOUT", 0.65)

    def respond(client, data):
        if data[:3] == b"\xaa\x05\x01":
            reply(client, build_packet(0xAA, 0x05, b"\x01\x03"))
        elif data[1] == 0x01:
            reply(client, build_packet(0xAA, 0x01, b"\x01"))

    use_replies(monkeypatch, radio, respond, purifier=True)
    values = await coordinator._async_update_data()
    assert values == {"power_state": True}
    assert not coordinator.fresh("mode")


async def test_disconnect_resolver_refreshes_native_device(
    coordinator, radio, monkeypatch
):
    async with coordinator._session():
        pass
    monkeypatch.setattr(
        integration.bluetooth,
        "async_ble_device_from_address",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(integration.UpdateFailed):
        async with coordinator._session():
            pytest.fail("Unavailable device must not connect")
    assert len(radio.clients) == 1


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


@pytest.mark.parametrize("powered_on", [True, False])
async def test_h7102_custom_preset_preserves_saved_settings(
    coordinator, radio, encrypted, monkeypatch, powered_on
):
    # Preserve marker, reserved bits, and duration words without reconstructing.
    settings = bytes.fromhex("07 95 00140015 16 001e001e 08 ffffffff")
    monkeypatch.setattr(
        h7102.GoveeAesRc4V1,
        "custom_replies",
        (
            b"bad",
            build_packet(0xAA, 0x05, b"\x00\x01"),
            build_packet(0xAA, 0x05, b"\x02" + settings),
        ),
    )
    coordinator.data = {"power_state": powered_on}
    coordinator._seen["power_state"] = monotonic()
    fan = GoveeBLEFan(coordinator)

    assert "Custom" in fan.preset_modes
    await fan.async_set_preset_mode("Custom")

    frames = encrypted[0].writes
    assert frames[0][:3] == b"\xaa\x05\x02"
    assert [frame[:3] for frame in frames[1:]] == (
        [b"\x33\x05\x02"] if powered_on else [b"\x33\x01\x01", b"\x33\x05\x02"]
    )
    assert frames[-1][3:19] == settings
    assert coordinator.data["work_mode"] is H7102WorkMode.CUSTOM
    assert not coordinator.fresh("work_mode")
    assert encrypted[0].closed
    assert not radio.clients[0].is_connected


async def test_h7102_missing_custom_reply_sends_no_controls(
    coordinator, radio, encrypted, monkeypatch
):
    monkeypatch.setattr(integration, "RESPONSE_TIMEOUT", 0.02)
    with pytest.raises(integration.UpdateFailed):
        await coordinator.async_commands(
            [("turn_on", ()), ("set_work_mode", (H7102WorkMode.CUSTOM,))]
        )
    assert [frame[:3] for frame in encrypted[0].writes] == [b"\xaa\x05\x02"]
    assert encrypted[0].closed
    assert not radio.clients[0].is_connected


async def test_encrypted_read_uses_one_receiver_and_keeps_false_fields(
    coordinator, radio, encrypted
):
    values = await asyncio.wait_for(coordinator._async_update_data(), 0.9)
    assert values["power_state"] is False
    assert values["display_state"] is False
    assert values["oscillation_state"] is False
    assert values["fan_speed"] == 5
    assert values["work_mode"].value == "normal"
    assert encrypted[0].closed
    assert not radio.clients[0].is_connected


async def test_encrypted_read_retains_partial_state_on_missing_optional_reply(
    coordinator, radio, encrypted, monkeypatch
):
    monkeypatch.setattr(integration, "RESPONSE_TIMEOUT", 0.65)
    original = h7102.GoveeAesRc4V1

    def transport(*args, **kwargs):
        result = original(*args, **kwargs)
        result.replies = result.replies[:-1]
        return result

    monkeypatch.setattr(h7102, "GoveeAesRc4V1", transport)
    values = await asyncio.wait_for(coordinator._async_update_data(), 0.9)
    assert values["fan_speed"] == 5
    assert "oscillation_state" not in values
    assert not coordinator.fresh("oscillation_state")


async def test_real_retry_connector_accepts_managed_client_class(
    coordinator, radio, monkeypatch
):
    # This characterization exercises the actual pinned retry connector's
    # construction API; only the physical client and BlueZ cache lookup are fake.
    monkeypatch.setattr(
        integration, "establish_connection", bleak_retry_connector.establish_connection
    )
    monkeypatch.setattr(bleak_retry_connector, "IS_LINUX", False)
    async with coordinator._session() as (connection, _client):
        await H5179Profile().request_battery(connection)
        assert connection.is_connected


async def test_readback_replaces_provisional_control_with_observed_state(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"
    monkeypatch.setattr(integration, "COMMAND_VERIFY_DELAY", 0.01)

    def respond(client, data):
        if data[0] != 0xAA:
            return
        payload = {
            0x01: b"\x00",  # Device reports off despite the completed on write.
            0x16: b"\x01",
            0x19: b"\x00\x00\x00\x00\x64",
        }.get(data[1])
        if data[1] == 0x05:
            payload = b"\x00\x01" if data[2] == 0 else b"\x01\x02"
        if payload is not None:
            reply(client, build_packet(0xAA, data[1], payload))

    use_replies(monkeypatch, radio, respond, purifier=True)
    await coordinator.async_command("turn_on")
    assert coordinator.data["power_state"] is True
    assert not coordinator.fresh("power_state")
    await asyncio.wait_for(coordinator._verify_task, 1)
    assert coordinator.data["power_state"] is False
    assert coordinator.fresh("power_state")
    assert len(radio.clients) == 2  # One write session, one observed readback.


@pytest.mark.parametrize(
    "write_property, expected", [("write", True), ("write-without-response", False)]
)
async def test_sensor_write_selects_explicit_supported_att_mode(
    coordinator, radio, write_property, expected
):
    async with coordinator._session() as (connection, _client):
        radio.clients[0].services[0].characteristics[0].properties = [
            "notify",
            write_property,
        ]
        await H5179Profile().request_battery(connection)
        assert radio.clients[0].writes[0][2] is expected


async def test_purifier_uses_observed_write_without_response(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"
    use_replies(monkeypatch, radio, lambda *_: None, purifier=True)
    await coordinator.async_command("turn_on")
    assert radio.clients[0].writes[0][2] is False


async def test_unload_during_dispatched_write_cleans_up_without_replay(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"
    use_replies(monkeypatch, radio, lambda *_: None, purifier=True)
    entered = asyncio.Event()

    async def write(self, characteristic, data, *, response):
        self.writes.append((characteristic, data, response))
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(RadioClient, "write_gatt_char", write)
    task = asyncio.create_task(coordinator.async_command("turn_on"))
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(coordinator.async_shutdown(), 1)
    assert task.cancelled()
    assert len(radio.clients) == 1
    assert len(radio.clients[0].writes) == 1
    assert not radio.clients[0].is_connected


async def test_query_timeout_retires_link_instead_of_hanging(
    coordinator, radio, monkeypatch
):
    monkeypatch.setattr(integration, "RESPONSE_TIMEOUT", 0.05)

    async def write(self, *args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(RadioClient, "write_gatt_char", write)
    with pytest.raises(integration.UpdateFailed):
        await asyncio.wait_for(coordinator._async_update_data(), 0.5)
    assert not radio.clients[0].is_connected


async def test_successful_retry_survives_disconnect_from_failed_attempt(
    coordinator, radio, monkeypatch
):
    attempts = []

    async def connect(self, **kwargs):
        attempts.append(self)
        if len(attempts) == 1:
            self.lose_link()
            raise BleakError("connection failed")
        self.is_connected = True

    async def backoff(*args):
        pass

    monkeypatch.setattr(RadioClient, "connect", connect)
    monkeypatch.setattr(
        integration, "establish_connection", bleak_retry_connector.establish_connection
    )
    monkeypatch.setattr(bleak_retry_connector, "IS_LINUX", False)
    monkeypatch.setattr(bleak_retry_connector, "wait_for_disconnect", backoff)
    async with coordinator._session() as (connection, _client):
        assert len(attempts) == 2
        assert connection.is_connected
        await H5179Profile().request_battery(connection)
        attempts[-1].lose_link()
        assert not connection.is_connected  # Post-handoff disconnect still propagates.


async def test_purifier_control_reaches_radio_before_disconnect(
    coordinator, radio, monkeypatch
):
    """A no-response backend can return before the final frame goes on air."""
    coordinator.model = "H7126"
    applied = []

    def submit(client, data):
        def transmit():
            if client.is_connected:
                applied.append(data)

        asyncio.get_running_loop().call_later(0.05, transmit)

    use_replies(monkeypatch, radio, submit, purifier=True)
    await coordinator.async_commands([("turn_on", ()), ("set_low_mode", ())])
    await asyncio.sleep(0.06)
    assert [frame[:4] for frame in applied] == [
        b"\x33\x01\x01\x00",
        b"\x3a\x05\x01\x02",
    ]
    assert not radio.clients[0].is_connected


async def test_shutdown_cancels_purifier_control_while_radio_is_settling(
    coordinator, radio, monkeypatch
):
    coordinator.model = "H7126"
    submitted = asyncio.Event()
    use_replies(monkeypatch, radio, lambda *_: submitted.set(), purifier=True)
    command = asyncio.create_task(coordinator.async_command("turn_off"))
    await submitted.wait()
    await coordinator.async_shutdown()
    assert command.cancelled()
    assert not radio.clients[0].is_connected
    assert coordinator._verify_task is None
