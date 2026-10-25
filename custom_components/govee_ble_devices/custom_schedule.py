"""Local custom schedule drafts and lossless, validated settings merges."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from govee_ble_client import H7126Mode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)
MODE_CODES = {"sleep": 1, "low": 2, "high": 3}
CONTINUOUS = b"\xff\xff\xff\xff"
type FieldValue = int | str | bool


@dataclass(frozen=True)
class DraftEdit:
    """One value and the revision that submitted it."""

    block: int
    field: str
    value: FieldValue
    revision: int


@dataclass(frozen=True)
class DraftSnapshot:
    """An immutable Apply request, independent of subsequent edits."""

    edits: tuple[DraftEdit, ...]


class CustomScheduleEditor:
    """Persist local edits without treating them as device observations."""

    def __init__(self, hass: HomeAssistant, entry_id: str, model: str) -> None:
        self.hass = hass
        self.model = model
        self.store: Store[dict[str, Any]] = Store(
            hass, 1, f"govee_ble_devices.custom_schedule.{entry_id}"
        )
        self.settings: bytes | None = None
        self._edits: dict[tuple[int, str], DraftEdit] = {}
        self._revision = 0
        self._save_lock = asyncio.Lock()

    def _validate(self, block: int, field: str, value: object) -> None:
        if type(block) is not int or not 0 <= block < 3:
            raise ValueError("Custom block must be 1, 2, or 3")
        valid = False
        if field == "duration":
            valid = block < 2 and type(value) is int and 1 <= value <= 65534
        elif field == "speed" and self.model == "H7102":
            valid = type(value) is int and 1 <= value <= 8
        elif field == "oscillation" and self.model == "H7102":
            valid = type(value) is bool
        elif field == "mode" and self.model == "H7126":
            valid = isinstance(value, str) and value in MODE_CODES
        if not valid:
            raise ValueError(f"Invalid custom block {field}")

    async def async_load(self) -> None:
        """Load only fields valid for this device model."""
        try:
            data = await self.store.async_load()
        except (KeyError, TypeError):
            # Store validates its envelope before returning the payload. A
            # structurally invalid local draft must not disable BLE setup.
            _LOGGER.warning("Ignoring malformed custom schedule draft storage")
            return
        if not isinstance(data, dict) or data.get("model") != self.model:
            return
        edits = data.get("edits")
        if not isinstance(edits, list):
            return
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            block, field, value = (
                edit.get("block"),
                edit.get("field"),
                edit.get("value"),
            )
            if not isinstance(field, str):
                continue
            try:
                self._validate(block, field, value)
            except ValueError:
                continue
            self._set(block, field, value)

    async def _save(self) -> None:
        async with self._save_lock:
            await self.store.async_save(
                {
                    "model": self.model,
                    "edits": [
                        {"block": edit.block, "field": edit.field, "value": edit.value}
                        for edit in self._edits.values()
                    ],
                }
            )

    async def async_remove(self) -> None:
        """Remove drafts when the owning config entry is deleted."""
        async with self._save_lock:
            await self.store.async_remove()

    def observe(self, settings: bytes) -> None:
        """Accept a saved settings block without replacing pending edits."""
        if not isinstance(settings, bytes) or len(settings) != 16:
            raise ValueError("Custom settings must contain 16 bytes")
        self.settings = settings

    @staticmethod
    def _finite_duration(raw: bytes) -> int | None:
        if raw[:2] != raw[2:]:
            return None
        value = int.from_bytes(raw[:2], "big")
        return value if 1 <= value <= 65534 else None

    def value(self, block: int, field: str) -> FieldValue | None:
        """Read local edits first, then values actually observed on the device."""
        if field == "continuous" or (block == 2 and field == "duration"):
            return None
        if edit := self._edits.get((block, field)):
            return edit.value
        if self.settings is None:
            return None
        offset = 1 + block * 5
        raw_value = self.settings[offset]
        duration = self.settings[offset + 1 : offset + 5]
        if field == "speed":
            speed = raw_value & 0x0F
            return speed if 1 <= speed <= 8 else None
        if field == "oscillation":
            return bool(raw_value & 0x10)
        if field == "mode":
            return next(
                (key for key, code in MODE_CODES.items() if code == raw_value), None
            )
        if field == "duration":
            return self._finite_duration(duration)
        return None

    def _set(self, block: int, field: str, value: FieldValue) -> None:
        self._revision += 1
        self._edits[block, field] = DraftEdit(block, field, value, self._revision)

    async def async_edit(self, block: int, field: str, value: FieldValue) -> None:
        """Stage and persist an edit; this method never performs radio I/O."""
        self._validate(block, field, value)
        self._set(block, field, value)
        await self._save()

    def snapshot(self) -> DraftSnapshot:
        """Freeze the exact revisions requested by an Apply press."""
        return DraftSnapshot(tuple(self._edits.values()))

    def merge(self, settings: bytes, snapshot: DraftSnapshot) -> bytes:
        """Overlay edited fields on a fresh device read, preserving unknown bits."""
        if len(settings) != 16:
            raise ValueError("Custom settings must contain 16 bytes")
        result = bytearray(settings)
        for edit in snapshot.edits:
            self._validate(edit.block, edit.field, edit.value)
            offset = 1 + edit.block * 5
            if edit.field == "speed":
                result[offset] = (result[offset] & 0xF0) | edit.value
            elif edit.field == "oscillation":
                result[offset] = (result[offset] & ~0x10) | (0x10 if edit.value else 0)
            elif edit.field == "mode":
                result[offset] = MODE_CODES[edit.value]
            elif edit.field == "duration":
                result[offset + 1 : offset + 5] = edit.value.to_bytes(2, "big") * 2
        result[12:16] = CONTINUOUS
        for block in range(3):
            offset = 1 + block * 5
            if block < 2 and result[offset + 1 : offset + 5] == CONTINUOUS:
                raise ValueError("Only custom block 3 can be continuous")
            if self.model == "H7102" and not 1 <= result[offset] & 0x0F <= 8:
                raise ValueError("Saved custom speed cannot be preserved")
        merged = bytes(result)
        if self.model == "H7126":
            self.h7126_segments(merged)
        return merged

    @staticmethod
    def h7126_segments(settings: bytes) -> tuple[tuple[H7126Mode, int | None], ...]:
        """Refuse settings the released H7126 writer cannot reproduce."""
        if len(settings) != 16 or settings[0] != 0:
            raise ValueError("Saved Custom schedule marker cannot be preserved")
        segments = []
        for block in range(3):
            offset = 1 + block * 5
            mode = next(
                (mode for mode, code in MODE_CODES.items() if code == settings[offset]),
                None,
            )
            raw = settings[offset + 1 : offset + 5]
            duration = CustomScheduleEditor._finite_duration(raw)
            if mode is None or (
                duration is None and not (block == 2 and raw == CONTINUOUS)
            ):
                raise ValueError("Saved Custom segment cannot be preserved")
            segments.append((H7126Mode(mode), duration))
        return tuple(segments)

    async def async_confirm(self, snapshot: DraftSnapshot, settings: bytes) -> None:
        """Clear only confirmed revisions, retaining edits made during Apply."""
        self.observe(settings)
        for edit in snapshot.edits:
            if self._edits.get((edit.block, edit.field)) == edit:
                del self._edits[edit.block, edit.field]
        await self._save()
