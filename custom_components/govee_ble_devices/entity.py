"""Shared entity identity and availability."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GoveeBLECoordinator


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
