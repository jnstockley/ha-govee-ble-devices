"""Custom Apply must serialize, preserve settings, and confirm actual replies."""

import asyncio
from time import monotonic

import pytest
from govee_ble_client import H7102WorkMode, H7126Mode, build_packet
from govee_ble_client.profiles import h7102
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.govee_ble_devices import coordinator as integration
from custom_components.govee_ble_devices.custom_schedule import CustomScheduleEditor

from .helpers import reply, use_replies

SETTINGS = bytes.fromhex("00 02 00140014 03 001e001e 01 ffffffff")


@pytest.fixture
async def purifier(coordinator, radio, monkeypatch):
    coordinator.model = "H7126"
    coordinator.custom_editor = CustomScheduleEditor(
        coordinator.hass, "purifier", "H7126"
    )
    coordinator.custom_editor.observe(SETTINGS)
    state = {
        "settings": SETTINGS,
        "power": True,
        "custom": False,
        "confirm": True,
        "fail_write": False,
        "schedule_reply": True,
        "power_reply": True,
        "writes": 0,
        "on_submit": None,
        "gear": 3,
        "schedule_prefix": 0xAA,
        "submitted": [],
    }

    def respond(client, data):
        if data[:3] == b"\xaa\x01\x00" and state["power_reply"]:
            reply(client, build_packet(0xAA, 0x01, bytes((state["power"],))))
        elif data[:3] == b"\xaa\x05\x02" and state["schedule_reply"]:
            reply(
                client,
                build_packet(
                    state["schedule_prefix"], 0x05, b"\x02" + state["settings"]
                ),
            )
        elif data[:3] == b"\xaa\x05\x00":
            reply(
                client,
                build_packet(0xAA, 0x05, bytes((0, 2 if state["custom"] else 1))),
            )
        elif data[:3] == b"\xaa\x05\x01":
            reply(client, build_packet(0xAA, 0x05, bytes((1, state["gear"]))))
        elif data[0:2] == b"\xaa\x16":
            reply(client, build_packet(0xAA, 0x16, b"\x00"))
        elif data[0:2] == b"\xaa\x19":
            reply(client, build_packet(0xAA, 0x19, b"\x00\x00\x00\x00\x00"))
        elif data[:3] == b"\x33\x01\x01":
            state["power"] = True
        elif data[:3] == b"\x3a\x05\x02":
            state["writes"] += 1
            state["submitted"].append(data[3:19])
            if state["fail_write"]:
                raise OSError("ambiguous control")
            if state["confirm"]:
                state["settings"] = data[3:19]
                state["custom"] = True
            if state["on_submit"]:
                state["on_submit"]()

    use_replies(monkeypatch, radio, respond, purifier=True)
    monkeypatch.setattr(integration, "COMMAND_VERIFY_DELAY", 100)
    monkeypatch.setattr(integration, "H7126_COMMAND_SETTLE_DELAY", 0)
    monkeypatch.setattr(integration, "RESPONSE_TIMEOUT", 0.9)
    return coordinator, state


@pytest.mark.parametrize("powered_on", [True, False])
@pytest.mark.parametrize("gear", [1, 2, 3])
async def test_purifier_apply_changes_mode_and_confirms_blocks(
    purifier, radio, powered_on, gear
):
    coordinator, state = purifier
    state["power"] = powered_on
    state["gear"] = gear
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")
    # A change in the app to an untouched block must survive Apply.
    state["settings"] = bytes.fromhex("00 02 00140014 02 00280028 01 ffffffff")
    await coordinator.async_apply_custom()
    assert state["settings"] == bytes.fromhex("00 01 00140014 02 00280028 01 ffffffff")
    assert state["writes"] == 1
    assert coordinator.data["mode"] is H7126Mode.CUSTOM
    assert coordinator.fresh("mode")
    assert not coordinator.custom_editor.snapshot().edits
    controls = [data for _, data, _ in radio.clients[0].writes if data[0] != 0xAA]
    assert len(controls) == (1 if powered_on else 2)
    assert not radio.clients[0].is_connected


@pytest.mark.parametrize("missing", ["schedule_reply", "power_reply"])
async def test_missing_preflight_never_sends_controls(purifier, radio, missing):
    coordinator, state = purifier
    state[missing] = False
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")
    with pytest.raises(UpdateFailed):
        await coordinator.async_apply_custom()
    assert all(data[0] == 0xAA for _, data, _ in radio.clients[0].writes)
    assert coordinator.custom_editor.snapshot().edits


@pytest.mark.parametrize("failure", ["confirm", "fail_write"])
async def test_uncertain_apply_retains_edits_without_replaying(purifier, failure):
    coordinator, state = purifier
    state[failure] = failure == "fail_write"
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")
    with pytest.raises(UpdateFailed):
        await coordinator.async_apply_custom()
    assert state["writes"] == 1
    assert coordinator.custom_editor.snapshot().edits


async def test_invalid_merge_rejected_before_power_on(purifier, radio):
    coordinator, state = purifier
    state["power"] = False
    state["settings"] = bytes.fromhex("07 02 00140014 03 001e001e 01 ffffffff")
    with pytest.raises(UpdateFailed):
        await coordinator.async_apply_custom()
    assert all(data[0] == 0xAA for _, data, _ in radio.clients[0].writes)


async def test_optional_schedule_timeout_keeps_other_observations(purifier):
    coordinator, state = purifier
    state["schedule_reply"] = False
    values = await coordinator._async_update_data()
    assert values["power_state"] is True
    assert values["mode"] is H7126Mode.HIGH
    assert "custom_settings" not in values


@pytest.mark.parametrize("prefix", [0x3A, 0xEE])
@pytest.mark.parametrize("during_verification", [False, True])
async def test_schedule_echo_cannot_satisfy_preflight_or_confirmation(
    purifier, radio, prefix, during_verification
):
    coordinator, state = purifier
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")
    if during_verification:
        # Return the echo only when the postwrite queries are issued, after drain.
        state["on_submit"] = lambda: state.update(schedule_prefix=prefix)
    else:
        state["schedule_prefix"] = prefix
    with pytest.raises(UpdateFailed):
        await coordinator.async_apply_custom()
    assert state["writes"] == int(during_verification)
    assert coordinator.custom_editor.snapshot().edits
    assert not radio.clients[0].is_connected


@pytest.mark.parametrize("prefix", [0x00, 0x3A, 0xEE])
async def test_polling_ignores_malformed_or_echoed_schedule(purifier, prefix):
    coordinator, state = purifier
    state["schedule_prefix"] = prefix
    values = await coordinator._async_update_data()
    assert values["power_state"] is True
    assert "custom_settings" not in values


async def test_readback_timeout_after_write_retains_draft(purifier):
    coordinator, state = purifier
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")
    state["on_submit"] = lambda: state.update(schedule_reply=False)
    with pytest.raises(UpdateFailed):
        await coordinator.async_apply_custom()
    assert state["writes"] == 1
    assert coordinator.custom_editor.snapshot().edits


async def test_repeated_apply_uses_request_snapshots_and_retains_newer_edits(purifier):
    coordinator, state = purifier
    async with coordinator._transaction(control=True):
        await coordinator.custom_editor.async_edit(0, "mode", "sleep")
        first = asyncio.create_task(coordinator.async_apply_custom())
        await asyncio.sleep(0)
        await coordinator.custom_editor.async_edit(0, "mode", "low")
        second = asyncio.create_task(coordinator.async_apply_custom())
        await asyncio.sleep(0)
        await coordinator.custom_editor.async_edit(0, "mode", "high")
    await asyncio.gather(first, second)
    assert [settings[1] for settings in state["submitted"]] == [1, 2]
    assert coordinator.custom_editor.value(0, "mode") == "high"
    assert coordinator.custom_editor.snapshot().edits


async def test_apply_separates_queued_speed_batches(
    coordinator, radio, encrypted, monkeypatch
):
    coordinator.custom_editor = CustomScheduleEditor(coordinator.hass, "fan", "H7102")
    coordinator.custom_editor.observe(SETTINGS)
    original = h7102.GoveeAesRc4V1.write

    async def write(self, frame):
        self.replies[1] = build_packet(0xAA, 0x01, b"\x01")
        if frame[:3] == b"\x33\x05\x02":
            self.replies[2] = build_packet(0xAA, 0x05, b"\x00\x02")
        await original(self, frame)

    monkeypatch.setattr(h7102.GoveeAesRc4V1, "write", write)
    monkeypatch.setattr(
        h7102.GoveeAesRc4V1,
        "custom_replies",
        (build_packet(0xAA, 0x05, b"\x02" + SETTINGS),),
    )
    monkeypatch.setattr(integration, "COMMAND_VERIFY_DELAY", 100)
    async with coordinator._transaction(control=True):
        first = asyncio.create_task(coordinator.async_command("set_fan_speed", 4))
        await asyncio.sleep(0)
        apply = asyncio.create_task(coordinator.async_apply_custom())
        await asyncio.sleep(0)
        second = asyncio.create_task(coordinator.async_command("set_fan_speed", 6))
        await asyncio.sleep(0)
    await asyncio.gather(first, apply, second)
    controls = [
        (frame[2], frame[3])
        for transport in encrypted
        for frame in transport.writes
        if frame[:2] == b"\x33\x05"
    ]
    assert controls == [(1, 4), (2, 0), (1, 6)]


async def test_new_edit_during_apply_survives_confirmation(purifier):
    coordinator, state = purifier
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")
    changed = None

    def submit():
        nonlocal changed
        changed = asyncio.create_task(
            coordinator.custom_editor.async_edit(0, "mode", "low")
        )

    state["on_submit"] = submit
    await coordinator.async_apply_custom()
    await changed
    assert coordinator.custom_editor.value(0, "mode") == "low"
    assert coordinator.custom_editor.snapshot().edits


async def test_apply_without_edits_selects_saved_schedule(purifier):
    coordinator, state = purifier
    await coordinator.async_apply_custom()
    assert state["writes"] == 1
    assert state["settings"] == SETTINGS
    assert state["custom"] is True


async def test_existing_custom_preset_does_not_submit_pending_edits(purifier):
    coordinator, state = purifier
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")
    await coordinator.async_command("set_custom_mode")
    assert state["settings"] == SETTINGS
    assert coordinator.custom_editor.value(0, "mode") == "sleep"
    assert coordinator.custom_editor.snapshot().edits


@pytest.mark.parametrize("previous_mode", [1, 5, 6])
async def test_fan_apply_preserves_unknown_bits_and_confirms_custom(
    coordinator, radio, encrypted, monkeypatch, previous_mode
):
    coordinator.custom_editor = CustomScheduleEditor(coordinator.hass, "fan", "H7102")
    saved = bytes.fromhex("07 95 00140015 16 001e001e 08 ffffffff")
    coordinator.custom_editor.observe(saved)
    await coordinator.custom_editor.async_edit(0, "speed", 8)
    original = h7102.GoveeAesRc4V1.write

    async def write(self, frame):
        if not any(item[:3] == b"\x33\x05\x02" for item in self.writes):
            self.replies[2] = build_packet(0xAA, 0x05, bytes((0, previous_mode)))
        if frame[:3] == b"\x33\x05\x02":
            self.custom_replies = (build_packet(0xAA, 0x05, b"\x02" + frame[3:19]),)
            self.replies[2] = build_packet(0xAA, 0x05, b"\x00\x02")
            self.replies[1] = build_packet(0xAA, 0x01, b"\x01")
        await original(self, frame)

    monkeypatch.setattr(h7102.GoveeAesRc4V1, "write", write)
    monkeypatch.setattr(
        h7102.GoveeAesRc4V1,
        "custom_replies",
        (build_packet(0xAA, 0x05, b"\x02" + saved),),
    )
    await coordinator.async_apply_custom()
    controls = [frame for frame in encrypted[0].writes if frame[0] == 0x33]
    assert controls[-1][3:19] == bytes.fromhex("07 98 00140015 16 001e001e 08 ffffffff")
    assert coordinator.data["work_mode"] is H7102WorkMode.CUSTOM
    assert coordinator.fresh("custom_settings")
    assert not coordinator.custom_editor.snapshot().edits
    assert encrypted[0].closed


async def test_apply_cancellation_disconnects_and_retains_draft(purifier, radio):
    coordinator, state = purifier
    state["schedule_reply"] = False
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")
    task = asyncio.create_task(coordinator.async_apply_custom())
    deadline = monotonic() + 1
    while not radio.clients or not radio.clients[0].writes:
        assert monotonic() < deadline
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not radio.clients[0].is_connected
    assert coordinator.custom_editor.snapshot().edits


@pytest.mark.parametrize("failure", ["disconnect", "overflow"])
async def test_apply_link_failure_retains_draft_and_never_replays(
    purifier, radio, failure
):
    coordinator, state = purifier
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")

    def submit():
        client = radio.clients[0]
        if failure == "disconnect":
            client.lose_link()
        else:
            for _ in range(65):
                reply(client, build_packet(0xAA, 0x01, b"\x01"))

    state["on_submit"] = submit
    with pytest.raises(UpdateFailed):
        await coordinator.async_apply_custom()
    assert state["writes"] == 1
    assert coordinator.custom_editor.snapshot().edits
    assert not radio.clients[0].is_connected


async def test_shutdown_cancels_apply_and_cleans_session(purifier, radio):
    coordinator, state = purifier
    state["schedule_reply"] = False
    task = asyncio.create_task(coordinator.async_apply_custom())
    deadline = monotonic() + 1
    while not radio.clients or not radio.clients[0].writes:
        assert monotonic() < deadline
        await asyncio.sleep(0)
    await coordinator.async_shutdown()
    assert task.cancelled()
    assert not radio.clients[0].is_connected


async def test_refresh_observes_schedule_without_overwriting_pending_edits(purifier):
    coordinator, state = purifier
    await coordinator.custom_editor.async_edit(0, "mode", "sleep")
    state["settings"] = bytes.fromhex("00 02 00140014 01 001e001e 01 ffffffff")
    values = await coordinator._async_update_data()
    assert values["custom_settings"] == state["settings"]
    assert coordinator.fresh("custom_settings")
    assert coordinator.custom_editor.value(0, "mode") == "sleep"
    assert coordinator.custom_editor.value(1, "mode") == "sleep"
