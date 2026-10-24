"""Read-only measurements from Govee BLE devices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeBLECoordinator
from .entity import GoveeBLEEntity


@dataclass(frozen=True)
class SensorDescription:
    key: str
    translation_key: str
    device_class: SensorDeviceClass | None = None
    unit: str | None = None
    state_class: SensorStateClass | None = None


SENSORS = {
    "H5179": (
        SensorDescription(
            "temperature_c",
            "temperature",
            SensorDeviceClass.TEMPERATURE,
            UnitOfTemperature.CELSIUS,
            SensorStateClass.MEASUREMENT,
        ),
        SensorDescription(
            "humidity_percent",
            "humidity",
            SensorDeviceClass.HUMIDITY,
            PERCENTAGE,
            SensorStateClass.MEASUREMENT,
        ),
        SensorDescription(
            "battery_percent",
            "battery",
            SensorDeviceClass.BATTERY,
            PERCENTAGE,
            SensorStateClass.MEASUREMENT,
        ),
    ),
    "H7126": (
        SensorDescription(
            "filter_life_percent",
            "filter_life",
            None,
            PERCENTAGE,
            SensorStateClass.MEASUREMENT,
        ),
    ),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add only measurements supported by this model."""
    coordinator: GoveeBLECoordinator = entry.runtime_data
    async_add_entities(
        GoveeBLESensor(coordinator, description)
        for description in SENSORS.get(coordinator.model, ())
    )


class GoveeBLESensor(GoveeBLEEntity, SensorEntity):
    """One validated sensor field."""

    def __init__(
        self, coordinator: GoveeBLECoordinator, description: SensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self._attr_translation_key = description.translation_key
        self._attr_device_class = description.device_class
        self._attr_native_unit_of_measurement = description.unit
        self._attr_state_class = description.state_class

    @property
    def native_value(self) -> float | int | None:
        """Return the latest decoded value."""
        return (self.coordinator.data or {}).get(self.key)
