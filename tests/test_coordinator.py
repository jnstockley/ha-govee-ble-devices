"""Regression tests for integration-owned BLE transactions, without RF I/O."""

import asyncio
import logging
from time import monotonic
from types import SimpleNamespace

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


@pytest.fixture
def paused_encrypted_setup(encrypted, monkeypatch):
    """Allow new user targets to arrive while the first handshake is pending."""
    entered = asyncio.Event()
    release = asyncio.Event()
    original = h7102.GoveeAesRc4V1.connect

    async def connect(self, **kwargs):
        if not entered.is_set():
            entered.set()
            await release.wait()
        await original(self, **kwargs)

    monkeypatch.setattr(h7102.GoveeAesRc4V1, "connect", connect)
    monkeypatch.setattr(integration, "COMMAND_VERIFY_DELAY", 60)
    return SimpleNamespace(entered=entered, release=release, transports=encrypted)


async def test_speed_burst_during_setup_submits_only_latest_target(
    coordinator, paused_encrypted_setup
):
    first = asyncio.create_task(coordinator.async_command("set_fan_speed", 8))
    await asyncio.wait_for(paused_encrypted_setup.entered.wait(), 1)
    second = asyncio.create_task(coordinator.async_command("set_fan_speed", 4))
    duplicate = asyncio.create_task(coordinator.async_command("set_fan_speed", 4))
    await asyncio.sleep(0)
    paused_encrypted_setup.release.set()
    await asyncio.wait_for(asyncio.gather(first, second, duplicate), 2)
    assert len(paused_encrypted_setup.transports) == 1
    assert [frame[:4] for frame in paused_encrypted_setup.transports[0].writes] == [
        b"\x33\x05\x01\x04"
    ]
    assert coordinator.data["fan_speed"] == 4
    assert coordinator._verify_generation == 1
    assert not coordinator.fresh("fan_speed")


async def test_queued_speed_burst_uses_one_write_after_active_read(
    coordinator, paused_encrypted
):
    read = asyncio.create_task(coordinator._async_update_data())
    await asyncio.wait_for(paused_encrypted.entered.wait(), 1)
    speeds = [
        asyncio.create_task(coordinator.async_command("set_fan_speed", speed))
        for speed in (8, 5, 4)
    ]
    await asyncio.sleep(0)
    paused_encrypted.release.set()
    await asyncio.wait_for(asyncio.gather(read, *speeds), 2)
    assert len(paused_encrypted.transports) == 2
    assert [frame[:4] for frame in paused_encrypted.transports[1].writes] == [
        b"\x33\x05\x01\x04"
    ]


@pytest.mark.parametrize("cancel_index,expected_speed", [(0, 4), (2, 5)])
async def test_cancelled_queued_speed_leaves_other_batch_callers_live(
    coordinator, paused_encrypted, cancel_index, expected_speed
):
    read = asyncio.create_task(coordinator._async_update_data())
    await asyncio.wait_for(paused_encrypted.entered.wait(), 1)
    speeds = [
        asyncio.create_task(coordinator.async_command("set_fan_speed", speed))
        for speed in (8, 5, 4)
    ]
    await asyncio.sleep(0)
    speeds[cancel_index].cancel()
    with pytest.raises(asyncio.CancelledError):
        await speeds[cancel_index]
    paused_encrypted.release.set()
    await asyncio.wait_for(
        asyncio.gather(read, *(task for task in speeds if not task.cancelled())), 2
    )
    assert len(paused_encrypted.transports) == 2
    assert paused_encrypted.transports[1].writes[0][:4] == bytes(
        (0x33, 0x05, 0x01, expected_speed)
    )
    assert not coordinator._operations


@pytest.mark.parametrize(
    "command,args,frame",
    [
        ("turn_off", (), b"\x33\x01\x00"),
        ("set_work_mode", (H7102WorkMode.SLEEP,), b"\x33\x05\x05"),
        ("set_oscillation", (True,), b"\x33\x1f\x01"),
    ],
)
async def test_speed_batch_does_not_cross_other_control_requests(
    coordinator, paused_encrypted_setup, command, args, frame
):
    first = asyncio.create_task(coordinator.async_command("set_fan_speed", 8))
    await asyncio.wait_for(paused_encrypted_setup.entered.wait(), 1)
    barrier = asyncio.create_task(coordinator.async_command(command, *args))
    await asyncio.sleep(0)
    last = asyncio.create_task(coordinator.async_command("set_fan_speed", 4))
    await asyncio.sleep(0)
    paused_encrypted_setup.release.set()
    await asyncio.wait_for(asyncio.gather(first, barrier, last), 2)
    transports = paused_encrypted_setup.transports
    assert len(transports) == 3
    assert transports[0].writes[0][:4] == b"\x33\x05\x01\x08"
    assert transports[1].writes[0][:3] == frame
    assert transports[2].writes[0][:4] == b"\x33\x05\x01\x04"


async def test_cancelled_newest_queued_speed_cannot_replace_live_target(
    coordinator, paused_encrypted_setup
):
    first = asyncio.create_task(coordinator.async_command("set_fan_speed", 8))
    await asyncio.wait_for(paused_encrypted_setup.entered.wait(), 1)
    cancelled = asyncio.create_task(coordinator.async_command("set_fan_speed", 4))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    paused_encrypted_setup.release.set()
    await asyncio.wait_for(first, 1)
    assert paused_encrypted_setup.transports[0].writes[0][:4] == b"\x33\x05\x01\x08"
    assert len(paused_encrypted_setup.transports) == 1
    assert not coordinator._operations


async def test_cancelled_speed_writer_does_not_retry_for_queued_followers(
    coordinator, paused_encrypted_setup
):
    first = asyncio.create_task(coordinator.async_command("set_fan_speed", 8))
    await asyncio.wait_for(paused_encrypted_setup.entered.wait(), 1)
    follower = asyncio.create_task(coordinator.async_command("set_fan_speed", 4))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    paused_encrypted_setup.release.set()
    with pytest.raises(integration.UpdateFailed):
        await asyncio.wait_for(follower, 1)
    assert len(paused_encrypted_setup.transports) == 1
    assert not paused_encrypted_setup.transports[0].writes
    assert not coordinator._sessions


async def test_ambiguous_speed_batch_write_fails_all_followers_without_replay(
    coordinator, paused_encrypted_setup, monkeypatch
):
    original = h7102.GoveeAesRc4V1.write

    async def fail(self, frame):
        await original(self, frame)
        raise OSError("May have reached the fan")

    monkeypatch.setattr(h7102.GoveeAesRc4V1, "write", fail)
    first = asyncio.create_task(coordinator.async_command("set_fan_speed", 8))
    await asyncio.wait_for(paused_encrypted_setup.entered.wait(), 1)
    follower = asyncio.create_task(coordinator.async_command("set_fan_speed", 4))
    await asyncio.sleep(0)
    paused_encrypted_setup.release.set()
    results = await asyncio.wait_for(
        asyncio.gather(first, follower, return_exceptions=True), 1
    )
    assert all(isinstance(result, integration.UpdateFailed) for result in results)
    assert len(paused_encrypted_setup.transports) == 1
    assert len(paused_encrypted_setup.transports[0].writes) == 1
    assert coordinator._verify_task is not None


async def test_speed_request_after_submission_starts_a_new_write(
    coordinator, encrypted, monkeypatch
):
    entered = asyncio.Event()
    release = asyncio.Event()
    original = h7102.GoveeAesRc4V1.close

    async def close(self):
        if not entered.is_set():
            entered.set()
            await release.wait()
        await original(self)

    monkeypatch.setattr(h7102.GoveeAesRc4V1, "close", close)
    first = asyncio.create_task(coordinator.async_command("set_fan_speed", 8))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(coordinator.async_command("set_fan_speed", 4))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 1)
    assert [transport.writes[0][:4] for transport in encrypted] == [
        b"\x33\x05\x01\x08",
        b"\x33\x05\x01\x04",
    ]


async def test_shutdown_cancels_all_speed_batch_callers(
    coordinator, paused_encrypted_setup
):
    first = asyncio.create_task(coordinator.async_command("set_fan_speed", 8))
    await asyncio.wait_for(paused_encrypted_setup.entered.wait(), 1)
    follower = asyncio.create_task(coordinator.async_command("set_fan_speed", 4))
    await asyncio.sleep(0)
    await asyncio.wait_for(coordinator.async_shutdown(), 1)
    assert first.cancelled() and follower.cancelled()
    assert not coordinator._sessions
    assert not coordinator._operations


@pytest.fixture
def paused_encrypted(encrypted, monkeypatch):
    """Hold the first real-client query at the simulated radio boundary."""
    entered = asyncio.Event()
    release = asyncio.Event()
    original = h7102.GoveeAesRc4V1.write

    async def write(self, frame):
        if not entered.is_set():
            entered.set()
            await release.wait()
        await original(self, frame)

    monkeypatch.setattr(h7102.GoveeAesRc4V1, "write", write)
    monkeypatch.setattr(integration, "COMMAND_VERIFY_DELAY", 60)
    return SimpleNamespace(entered=entered, release=release, transports=encrypted)


async def test_controls_overtake_queued_read_and_preserve_control_order(
    coordinator, paused_encrypted
):
    active = asyncio.create_task(coordinator._async_update_data())
    await asyncio.wait_for(paused_encrypted.entered.wait(), 1)
    queued_read = asyncio.create_task(coordinator._async_update_data())
    await asyncio.sleep(0)
    first_control = asyncio.create_task(coordinator.async_command("turn_off"))
    await asyncio.sleep(0)
    second_control = asyncio.create_task(coordinator.async_command("turn_on"))
    await asyncio.sleep(0)
    assert len(paused_encrypted.transports) == 1
    assert not active.done()
    paused_encrypted.release.set()
    await asyncio.wait_for(
        asyncio.gather(active, queued_read, first_control, second_control), 3
    )
    assert [transport.writes[0][:3] for transport in paused_encrypted.transports] == [
        b"\xaa\x01\x00",  # Active poll completes before any controls.
        b"\x33\x01\x00",  # Earlier control stays first.
        b"\x33\x01\x01",
        b"\xaa\x01\x00",  # Queued poll runs after both controls.
    ]
    assert all(transport.closed for transport in paused_encrypted.transports)


@pytest.mark.parametrize("cancel_active", [False, True])
async def test_cancelled_transaction_does_not_strand_queued_read(
    coordinator, paused_encrypted, cancel_active
):
    active = asyncio.create_task(coordinator._async_update_data())
    await asyncio.wait_for(paused_encrypted.entered.wait(), 1)
    control = asyncio.create_task(coordinator.async_command("turn_off"))
    queued_read = asyncio.create_task(coordinator._async_update_data())
    await asyncio.sleep(0)
    cancelled = active if cancel_active else control
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    paused_encrypted.release.set()
    await asyncio.wait_for(
        asyncio.gather(
            *(task for task in (active, control, queued_read) if task is not cancelled)
        ),
        3,
    )
    assert queued_read.result()["fan_speed"] == 5
    assert not coordinator._operations
    assert all(transport.closed for transport in paused_encrypted.transports)


async def test_shutdown_cancels_active_and_both_queued_transaction_classes(
    coordinator, paused_encrypted
):
    active = asyncio.create_task(coordinator._async_update_data())
    await asyncio.wait_for(paused_encrypted.entered.wait(), 1)
    control = asyncio.create_task(coordinator.async_command("turn_off"))
    read = asyncio.create_task(coordinator._async_update_data())
    await asyncio.sleep(0)
    await asyncio.wait_for(coordinator.async_shutdown(), 1)
    assert all(task.cancelled() for task in (active, control, read))
    assert not coordinator._operations
    assert not coordinator._sessions


async def test_busy_device_does_not_block_another_device(coordinator, paused_encrypted):
    active = asyncio.create_task(coordinator._async_update_data())
    await asyncio.wait_for(paused_encrypted.entered.wait(), 1)
    other = integration.GoveeBLECoordinator(coordinator.hass, coordinator.config_entry)
    other.model = "H7102"
    try:
        await asyncio.wait_for(other.async_command("turn_off"), 0.5)
        assert not active.done()
        assert paused_encrypted.transports[1].writes[0][:3] == b"\x33\x01\x00"
    finally:
        await other.async_shutdown()
        paused_encrypted.release.set()
        await active


async def test_latency_timings_separate_setup_control_and_cleanup(
    coordinator, encrypted, monkeypatch, caplog
):
    clock = [0.0]
    monkeypatch.setattr(integration, "monotonic", lambda: clock[0])
    establish = integration.establish_connection
    connect = h7102.GoveeAesRc4V1.connect
    close = h7102.GoveeAesRc4V1.close

    async def timed_establish(*args, **kwargs):
        result = await establish(*args, **kwargs)
        clock[0] += 2.0
        return result

    async def timed_connect(self, **kwargs):
        await connect(self, **kwargs)
        clock[0] += 3.0

    async def timed_close(self):
        await close(self)
        clock[0] += 1.0

    monkeypatch.setattr(integration, "establish_connection", timed_establish)
    monkeypatch.setattr(h7102.GoveeAesRc4V1, "connect", timed_connect)
    monkeypatch.setattr(h7102.GoveeAesRc4V1, "close", timed_close)
    with caplog.at_level(logging.DEBUG, logger=integration.__name__):
        await coordinator.async_command("turn_off")
    timings = {
        record.govee_phase: (record.govee_elapsed, record.govee_outcome)
        for record in caplog.records
        if hasattr(record, "govee_phase")
    }
    assert timings == {
        "queue": (0.0, "ok"),
        "gatt_setup": (2.0, "ok"),
        "encrypted_setup": (3.0, "ok"),
        "controls": (0.0, "ok"),
        "cleanup": (1.0, "ok"),
        "transaction": (6.0, "ok"),
    }
    assert coordinator.address not in caplog.text


async def test_latency_timing_includes_command_queue_wait(
    coordinator, paused_encrypted, monkeypatch, caplog
):
    clock = [0.0]
    monkeypatch.setattr(integration, "monotonic", lambda: clock[0])
    with caplog.at_level(logging.DEBUG, logger=integration.__name__):
        active = asyncio.create_task(coordinator.async_command("turn_on"))
        await asyncio.wait_for(paused_encrypted.entered.wait(), 1)
        control = asyncio.create_task(coordinator.async_command("turn_off"))
        await asyncio.sleep(0)
        clock[0] = 5.0
        paused_encrypted.release.set()
        await asyncio.wait_for(asyncio.gather(active, control), 2)
    queue_records = [
        record
        for record in caplog.records
        if getattr(record, "govee_phase", None) == "queue"
        and record.govee_operation == "control"
    ]
    assert [record.govee_elapsed for record in queue_records] == [0.0, 5.0]


@pytest.mark.parametrize(
    "stage", ["gatt_setup", "encrypted_setup", "controls", "cleanup"]
)
async def test_failed_latency_phase_omits_private_exception_text(
    coordinator, encrypted, monkeypatch, caplog, stage
):
    async def fail(*args, **kwargs):
        raise OSError("PRIVATE_PACKET_AND_ADDRESS")

    target, name = {
        "gatt_setup": (integration, "establish_connection"),
        "encrypted_setup": (h7102.GoveeAesRc4V1, "connect"),
        "controls": (h7102.GoveeAesRc4V1, "write"),
        "cleanup": (RadioClient, "disconnect"),
    }[stage]
    with (
        caplog.at_level(logging.DEBUG, logger=integration.__name__),
        monkeypatch.context() as patch,
    ):
        patch.setattr(target, name, fail)
        with pytest.raises(integration.UpdateFailed):
            await coordinator.async_command("turn_off")
    failures = {
        record.govee_phase
        for record in caplog.records
        if getattr(record, "govee_outcome", None) == "failed"
    }
    assert {stage, "transaction"} <= failures
    assert "PRIVATE_PACKET_AND_ADDRESS" not in caplog.text
    assert coordinator.address not in caplog.text


async def test_cancelled_latency_phase_is_recorded(
    coordinator, paused_encrypted, caplog
):
    with caplog.at_level(logging.DEBUG, logger=integration.__name__):
        command = asyncio.create_task(coordinator.async_command("turn_off"))
        await asyncio.wait_for(paused_encrypted.entered.wait(), 1)
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await command
    cancelled = {
        record.govee_phase
        for record in caplog.records
        if getattr(record, "govee_outcome", None) == "cancelled"
    }
    assert {"controls", "transaction"} <= cancelled
    assert paused_encrypted.transports[0].closed


@pytest.mark.parametrize("percentage, speed", [(50, 4), (62, 5), (63, 5), (75, 6)])
async def test_speed_request_submission_and_observation_can_be_distinguished(
    coordinator, encrypted, caplog, percentage, speed
):
    coordinator.data = {"power_state": True, "fan_speed": 5}
    coordinator._seen.update(power_state=monotonic(), fan_speed=monotonic())
    fan = GoveeBLEFan(coordinator)
    with caplog.at_level(logging.DEBUG, logger="custom_components.govee_ble_devices"):
        await fan.async_set_percentage(percentage)
        # Write completion is provisional. The simulated fan still reports 5.
        assert coordinator.data["fan_speed"] == speed
        values = await coordinator._async_update_data()
    assert encrypted[0].writes[-1][0:4] == bytes((0x33, 0x05, 0x01, speed))
    assert values["fan_speed"] == 5
    assert f"Fan percentage request model=H7102 percentage={percentage}" in caplog.text
    assert f"Fan speed submitted model=H7102 speed={speed}" in caplog.text
    assert "Fan speed observed model=H7102 speed=5" in caplog.text
    assert coordinator.address not in caplog.text
    # A differing readback must not automatically replay an uncertain write.
    assert len(encrypted) == 2
    assert all(frame[0] == 0xAA for frame in encrypted[1].writes)


async def test_failed_speed_submission_is_not_logged_as_submitted(
    coordinator, encrypted, monkeypatch, caplog
):
    coordinator.data = {"power_state": True, "fan_speed": 5}
    coordinator._seen.update(power_state=monotonic(), fan_speed=monotonic())

    async def fail(*args, **kwargs):
        raise OSError("PRIVATE_BLE_PAYLOAD")

    monkeypatch.setattr(h7102.GoveeAesRc4V1, "write", fail)
    fan = GoveeBLEFan(coordinator)
    with (
        caplog.at_level(logging.DEBUG, logger="custom_components.govee_ble_devices"),
        pytest.raises(integration.UpdateFailed),
    ):
        await fan.async_set_percentage(50)
    assert "Fan percentage request model=H7102 percentage=50" in caplog.text
    assert "Fan speed submitted" not in caplog.text
    assert "PRIVATE_BLE_PAYLOAD" not in caplog.text
    assert coordinator.data["fan_speed"] == 5


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
