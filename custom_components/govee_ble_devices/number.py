"""Draft custom block speeds and durations."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeBLECoordinator
from .entity import GoveeBLECustomEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add editable numbers only for models with custom schedules."""
    coordinator: GoveeBLECoordinator = entry.runtime_data
    if coordinator.custom_editor is None:
        return
    fields = ("speed", "duration") if coordinator.model == "H7102" else ("duration",)
    async_add_entities(
        [
            GoveeBLECustomNumber(coordinator, block, field)
            for block in range(3)
            for field in fields
            if field != "duration" or block < 2
        ]
    )


class GoveeBLECustomNumber(GoveeBLECustomEntity, NumberEntity):
    """A numeric field staged until Apply is pressed."""

    _attr_native_min_value = 1
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: GoveeBLECoordinator, block: int, field: str
    ) -> None:
        super().__init__(coordinator, block, field)
        self._attr_native_max_value = 8 if field == "speed" else 65534
        if field == "duration":
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> int | None:
        value = self.draft_value
        return value if type(value) is int else None

    async def async_set_native_value(self, value: float) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not float(value).is_integer()
        ):
            raise ValueError("Custom block values must be whole numbers")
        await self.async_edit(int(value))
