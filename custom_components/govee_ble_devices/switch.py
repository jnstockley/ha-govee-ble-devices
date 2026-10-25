"""Panel display controls."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeBLECoordinator
from .entity import GoveeBLECustomEntity, GoveeBLEEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add a panel display switch for controllable models."""
    coordinator: GoveeBLECoordinator = entry.runtime_data
    if coordinator.model in ("H7102", "H7126"):
        entities: list[SwitchEntity] = [GoveeBLEDisplaySwitch(coordinator)]
        if coordinator.custom_editor is not None and coordinator.model == "H7102":
            entities.extend(
                GoveeBLECustomSwitch(coordinator, block, "oscillation")
                for block in range(3)
            )
        async_add_entities(entities)


class GoveeBLEDisplaySwitch(GoveeBLEEntity, SwitchEntity):
    """Device panel lights."""

    _attr_translation_key = "display"

    def __init__(self, coordinator: GoveeBLECoordinator) -> None:
        super().__init__(coordinator, "display_state")

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get(self.key)

    async def async_turn_on(self, **kwargs: object) -> None:
        await self.coordinator.async_command("set_panel_display", True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self.coordinator.async_command("set_panel_display", False)


class GoveeBLECustomSwitch(GoveeBLECustomEntity, SwitchEntity):
    """Local per-block oscillation flag."""

    @property
    def is_on(self) -> bool | None:
        value = self.draft_value
        return value if type(value) is bool else None

    async def async_turn_on(self, **kwargs: object) -> None:
        await self.async_edit(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self.async_edit(False)
