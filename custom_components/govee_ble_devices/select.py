"""Draft H7126 custom block modes."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeBLECoordinator
from .entity import GoveeBLECustomEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add a mode selector for each purifier block."""
    coordinator: GoveeBLECoordinator = entry.runtime_data
    if coordinator.model == "H7126" and coordinator.custom_editor is not None:
        async_add_entities(
            [GoveeBLECustomMode(coordinator, block) for block in range(3)]
        )


class GoveeBLECustomMode(GoveeBLECustomEntity, SelectEntity):
    """A local block mode, not the current device mode."""

    def __init__(self, coordinator: GoveeBLECoordinator, block: int) -> None:
        super().__init__(coordinator, block, "mode")
        self._attr_options = ["sleep", "low", "high"]

    @property
    def current_option(self) -> str | None:
        value = self.draft_value
        return value if isinstance(value, str) else None

    async def async_select_option(self, option: str) -> None:
        await self.async_edit(option)
