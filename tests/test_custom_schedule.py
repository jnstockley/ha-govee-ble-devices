"""Draft editing must preserve saved fields and concurrent user changes."""

import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant

from custom_components.govee_ble_devices.custom_schedule import CustomScheduleEditor

FAN_SETTINGS = bytes.fromhex("07 95 00140015 16 001e001e 08 ffffffff")
PURIFIER_SETTINGS = bytes.fromhex("00 02 00140014 03 001e001e 01 ffffffff")


@pytest.fixture
async def editor(tmp_path):
    return CustomScheduleEditor(HomeAssistant(str(tmp_path)), "test-entry", "H7102")


async def test_fan_merge_preserves_unknown_bytes_and_external_changes(editor):
    editor.observe(FAN_SETTINGS)
    await editor.async_edit(0, "speed", 8)
    await editor.async_edit(1, "oscillation", False)
    # External app changed the third block's speed after our observation.
    fresh = bytes.fromhex("07 95 00140015 16 001e001e 03 ffffffff")
    assert editor.merge(fresh, editor.snapshot()) == bytes.fromhex(
        "07 98 00140015 06 001e001e 03 ffffffff"
    )
    assert editor.value(0, "duration") is None  # Unequal duration words.


@pytest.mark.parametrize(
    ("block", "field", "value"),
    [
        (0, "speed", True),
        (0, "speed", 0),
        (0, "speed", 9),
        (0, "duration", 0),
        (0, "duration", 65535),
        (0, "duration", 1.5),
        (0, "duration", False),
        (0, "continuous", True),
        (2, "continuous", False),
        (2, "continuous", True),
        (2, "duration", 10),
        (1, "oscillation", 1),
        (3, "speed", 1),
        (0, "mode", "high"),
    ],
)
async def test_invalid_edits_are_rejected(editor, block, field, value):
    with pytest.raises(ValueError):
        await editor.async_edit(block, field, value)


@pytest.mark.parametrize("model", ["H7102", "H7126"])
async def test_apply_always_encodes_final_block_as_continuous(editor, model):
    editor.model = model
    saved = bytes.fromhex("00 02 00140014 03 001e001e 01 00280028")
    editor.observe(saved)
    assert editor.value(2, "duration") is None
    merged = editor.merge(saved, editor.snapshot())
    assert merged == bytes.fromhex("00 02 00140014 03 001e001e 01 ffffffff")


async def test_drafts_survive_restart_and_do_not_leak_between_entries(editor):
    editor.observe(FAN_SETTINGS)
    await editor.async_edit(0, "speed", 8)
    restored = CustomScheduleEditor(editor.hass, "test-entry", "H7102")
    await restored.async_load()
    assert restored.value(0, "speed") == 8
    assert restored.value(1, "speed") is None
    other = CustomScheduleEditor(editor.hass, "other-entry", "H7102")
    await other.async_load()
    assert other.value(0, "speed") is None
    await restored.async_remove()
    empty = CustomScheduleEditor(editor.hass, "test-entry", "H7102")
    await empty.async_load()
    assert empty.value(0, "speed") is None


async def test_removed_final_duration_draft_fields_are_ignored_on_load(editor):
    await editor.store.async_save(
        {
            "model": "H7102",
            "last_duration": 40,
            "edits": [
                {"block": 2, "field": "duration", "value": 40},
                {"block": 2, "field": "continuous", "value": False},
                {"block": 2, "field": "speed", "value": 8},
                {"block": 0, "field": "duration", "value": 65534},
            ],
        }
    )
    restored = CustomScheduleEditor(editor.hass, "test-entry", "H7102")
    await restored.async_load()
    assert {(edit.block, edit.field) for edit in restored.snapshot().edits} == {
        (2, "speed"),
        (0, "duration"),
    }
    merged = restored.merge(
        bytes.fromhex("00 02 00140014 03 001e001e 01 00280028"), restored.snapshot()
    )
    assert merged == bytes.fromhex("00 02 fffefffe 03 001e001e 08 ffffffff")


async def test_confirm_keeps_edits_made_after_apply_snapshot(editor):
    editor.observe(FAN_SETTINGS)
    await editor.async_edit(0, "speed", 8)
    snapshot = editor.snapshot()
    settings = editor.merge(FAN_SETTINGS, snapshot)
    await editor.async_edit(0, "speed", 7)
    await editor.async_confirm(snapshot, settings)
    assert editor.value(0, "speed") == 7
    assert editor.snapshot().edits
    await editor.async_confirm(
        editor.snapshot(), editor.merge(settings, editor.snapshot())
    )
    assert not editor.snapshot().edits


async def test_invalid_persisted_values_and_wrong_model_are_ignored(editor):
    await editor.store.async_save(
        {
            "model": "H7102",
            "edits": [
                {"block": 0, "field": "speed", "value": 99},
                {"block": 1, "field": "speed", "value": 8},
                {"block": True, "field": "speed", "value": 2},
                "invalid",
            ],
            "last_duration": "bad",
        }
    )
    await editor.async_load()
    assert editor.value(0, "speed") is None
    assert editor.value(1, "speed") == 8
    other_model = CustomScheduleEditor(editor.hass, "test-entry", "H7126")
    await other_model.async_load()
    assert not other_model.snapshot().edits


@pytest.mark.parametrize("envelope", [{"version": 1}, {"data": {}}, [1], True])
async def test_malformed_storage_envelope_does_not_block_initialization(
    editor, envelope
):
    path = Path(editor.store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope))
    await editor.async_load()
    assert not editor.snapshot().edits
    # A normal edit can create a valid draft after the invalid data was ignored.
    await editor.async_edit(0, "speed", 8)
    restored = CustomScheduleEditor(editor.hass, "test-entry", "H7102")
    await restored.async_load()
    assert restored.value(0, "speed") == 8


async def test_purifier_rejects_unpreservable_fields_and_supports_replacement(tmp_path):
    editor = CustomScheduleEditor(HomeAssistant(str(tmp_path)), "purifier", "H7126")
    editor.observe(PURIFIER_SETTINGS)
    await editor.async_edit(0, "mode", "sleep")
    await editor.async_edit(0, "duration", 1)
    assert editor.h7126_segments(
        editor.merge(PURIFIER_SETTINGS, editor.snapshot())
    ) == (("sleep", 1), ("high", 30), ("sleep", None))
    for settings in (
        bytes.fromhex("07 02 00140014 03 001e001e 01 ffffffff"),
        bytes.fromhex("00 02 00140014 09 001e001e 01 ffffffff"),
        bytes.fromhex("00 02 00140014 03 001e001f 01 ffffffff"),
    ):
        with pytest.raises(ValueError):
            editor.merge(settings, editor.snapshot())
    with pytest.raises(ValueError):
        await editor.async_edit(0, "mode", "custom")
