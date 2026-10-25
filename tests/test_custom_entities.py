"""Standard entities edit local drafts without issuing BLE controls."""

import pytest

from custom_components.govee_ble_devices import button, number, select, switch
from custom_components.govee_ble_devices.custom_schedule import CustomScheduleEditor

SETTINGS = bytes.fromhex("00 02 00140014 03 001e001e 01 ffffffff")


async def entities_for(coordinator, model, platform):
    coordinator.model = model
    coordinator.custom_editor = (
        CustomScheduleEditor(coordinator.hass, "entity-test", model)
        if model != "H5179"
        else None
    )
    if coordinator.custom_editor:
        coordinator.custom_editor.observe(SETTINGS)
    coordinator.config_entry.runtime_data = coordinator
    result = []
    await platform.async_setup_entry(
        coordinator.hass, coordinator.config_entry, result.extend
    )
    return result


@pytest.mark.parametrize(
    ("model", "numbers", "selects", "switches", "buttons"),
    [
        ("H7102", 5, 0, 4, 1),
        ("H7126", 2, 3, 1, 2),
        ("H5179", 0, 0, 0, 0),
    ],
)
async def test_model_controls_and_unique_identities(
    coordinator, model, numbers, selects, switches, buttons
):
    groups = []
    for platform, count in (
        (number, numbers),
        (select, selects),
        (switch, switches),
        (button, buttons),
    ):
        entities = await entities_for(coordinator, model, platform)
        assert len(entities) == count
        groups.extend(entities)
    assert len({entity.unique_id for entity in groups}) == len(groups)


async def test_speed_and_oscillation_edits_are_local(coordinator, radio):
    numbers = await entities_for(coordinator, "H7102", number)
    speed = next(
        entity for entity in numbers if entity.block == 0 and entity.field == "speed"
    )
    assert speed.available
    assert speed.native_min_value == 1
    assert speed.native_max_value == 8
    assert speed.native_step == 1
    await speed.async_set_native_value(8)
    with pytest.raises(ValueError):
        await speed.async_set_native_value(2.5)
    assert speed.native_value == 8
    switches = []
    await switch.async_setup_entry(
        coordinator.hass, coordinator.config_entry, switches.extend
    )
    oscillation = next(
        entity
        for entity in switches
        if getattr(entity, "field", None) == "oscillation" and entity.block == 0
    )
    await oscillation.async_turn_on()
    assert oscillation.is_on is True
    assert not radio.clients
    assert coordinator.data is None  # No projected device mode from a local edit.


@pytest.mark.parametrize("model", ["H7102", "H7126"])
async def test_final_block_has_no_duration_or_continuous_control(coordinator, model):
    numbers = await entities_for(coordinator, model, number)
    assert not any(
        entity.block == 2 and entity.field == "duration" for entity in numbers
    )
    durations = [entity for entity in numbers if entity.field == "duration"]
    assert {entity.block for entity in durations} == {0, 1}
    await durations[0].async_set_native_value(65534)
    assert durations[0].native_value == 65534
    switches = []
    await switch.async_setup_entry(
        coordinator.hass, coordinator.config_entry, switches.extend
    )
    assert not any(
        getattr(entity, "field", None) == "continuous" for entity in switches
    )


async def test_mode_select_local_availability_and_apply_reachability(
    coordinator, monkeypatch
):
    modes = await entities_for(coordinator, "H7126", select)
    assert modes[0].options == ["sleep", "low", "high"]
    await modes[0].async_select_option("sleep")
    assert modes[0].current_option == "sleep"
    assert modes[0].available  # Local edits do not require a fresh power reading.
    with pytest.raises(ValueError):
        await modes[0].async_select_option("custom")
    buttons = []
    await button.async_setup_entry(
        coordinator.hass, coordinator.config_entry, buttons.extend
    )
    apply = next(
        entity for entity in buttons if entity.translation_key == "apply_custom"
    )
    monkeypatch.setattr(coordinator, "_resolve_device", lambda: None)
    assert not apply.available
    monkeypatch.setattr(coordinator, "_resolve_device", lambda: object())
    assert apply.available
    coordinator.custom_editor.settings = None
    assert not apply.available
    assert modes[0].available  # Persisted edits still have a known local value.
    assert not modes[1].available
