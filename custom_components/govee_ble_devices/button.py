"""H7126 maintenance action."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeBLECoordinator
from .entity import GoveeBLEEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add filter reset only for H7126."""
    coordinator: GoveeBLECoordinator = entry.runtime_data
    if coordinator.model == "H7126":
        async_add_entities([GoveeBLEFilterReset(coordinator)])


class GoveeBLEFilterReset(GoveeBLEEntity, ButtonEntity):
    """Reset filter life after replacement."""

    _attr_translation_key = "reset_filter"

    def __init__(self, coordinator: GoveeBLECoordinator) -> None:
        super().__init__(coordinator, "filter_life_percent")

    async def async_press(self) -> None:
        await self.coordinator.async_command("reset_filter_life")
