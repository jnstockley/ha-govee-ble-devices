"""Shared entity identity and availability."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GoveeBLECoordinator
from .custom_schedule import FieldValue


class GoveeBLEEntity(CoordinatorEntity[GoveeBLECoordinator]):
    """Base for a field belonging to one BLE address."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GoveeBLECoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.address.upper()}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address.upper())},
            name=coordinator.config_entry.title,
            manufacturer="Govee",
            model=coordinator.model,
        )

    @property
    def available(self) -> bool:
        """Avoid presenting old or never received values as live."""
        return super().available and self.coordinator.fresh(self.key)


class GoveeBLECustomEntity(GoveeBLEEntity):
    """A local draft field, whose availability is independent of radio freshness."""

    def __init__(
        self, coordinator: GoveeBLECoordinator, block: int, field: str
    ) -> None:
        super().__init__(coordinator, f"custom_block_{block + 1}_{field}")
        self.block = block
        self.field = field
        self._attr_translation_key = f"custom_{field}"
        self._attr_translation_placeholders = {"block": str(block + 1)}

    @property
    def draft_value(self) -> FieldValue | None:
        editor = self.coordinator.custom_editor
        return editor.value(self.block, self.field) if editor is not None else None

    @property
    def available(self) -> bool:
        return not self.coordinator._closing and self.draft_value is not None

    async def async_edit(self, value: FieldValue) -> None:
        await self.coordinator.async_edit_custom(self.block, self.field, value)
