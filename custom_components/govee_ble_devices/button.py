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
    """Add model maintenance and custom schedule actions."""
    coordinator: GoveeBLECoordinator = entry.runtime_data
    entities: list[ButtonEntity] = []
    if coordinator.model == "H7126":
        entities.append(GoveeBLEFilterReset(coordinator))
    if coordinator.custom_editor is not None:
        entities.append(GoveeBLEApplyCustom(coordinator))
    async_add_entities(entities)


class GoveeBLEFilterReset(GoveeBLEEntity, ButtonEntity):
    """Reset filter life after replacement."""

    _attr_translation_key = "reset_filter"

    def __init__(self, coordinator: GoveeBLECoordinator) -> None:
        super().__init__(coordinator, "filter_life_percent")

    async def async_press(self) -> None:
        await self.coordinator.async_command("reset_filter_life")


class GoveeBLEApplyCustom(GoveeBLEEntity, ButtonEntity):
    """Save staged blocks to the device and select Custom mode."""

    _attr_translation_key = "apply_custom"

    def __init__(self, coordinator: GoveeBLECoordinator) -> None:
        super().__init__(coordinator, "apply_custom")

    @property
    def available(self) -> bool:
        editor = self.coordinator.custom_editor
        return (
            not self.coordinator._closing
            and editor is not None
            and editor.settings is not None
            and self.coordinator._resolve_device() is not None
        )

    async def async_press(self) -> None:
        await self.coordinator.async_apply_custom()
