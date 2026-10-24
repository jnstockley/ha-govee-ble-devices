"""Verified fan and purifier controls."""

from __future__ import annotations

from govee_ble_client import H7102WorkMode
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import GoveeBLECoordinator
from .entity import GoveeBLEEntity

PRESETS = {
    "H7102": ("Normal", "Sleep", "Nature"),
    "H7126": ("Sleep", "Low", "High", "Custom"),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add a fan for a controllable model."""
    coordinator: GoveeBLECoordinator = entry.runtime_data
    if coordinator.model in PRESETS:
        async_add_entities([GoveeBLEFan(coordinator)])


class GoveeBLEFan(GoveeBLEEntity, FanEntity):
    """Power, mode and supported speed or oscillation controls."""

    _attr_translation_key = "fan"

    def __init__(self, coordinator: GoveeBLECoordinator) -> None:
        super().__init__(coordinator, "power_state")
        self._attr_preset_modes = PRESETS[coordinator.model]
        self._attr_supported_features = (
            FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
            | FanEntityFeature.PRESET_MODE
        )
        if coordinator.model == "H7102":
            self._attr_supported_features |= (
                FanEntityFeature.SET_SPEED | FanEntityFeature.OSCILLATE
            )
            self._attr_speed_count = 8

    @property
    def is_on(self) -> bool | None:
        return (self.coordinator.data or {}).get("power_state")

    @property
    def preset_mode(self) -> str | None:
        key = "work_mode" if self.coordinator.model == "H7102" else "mode"
        if not self.coordinator.fresh(key):
            return None
        mode = (self.coordinator.data or {}).get(key)
        return mode.value.title() if mode is not None else None

    @property
    def percentage(self) -> int | None:
        if self.coordinator.model != "H7102" or not self.coordinator.fresh("fan_speed"):
            return None
        speed = (self.coordinator.data or {}).get("fan_speed")
        return round(speed * 100 / 8) if speed is not None else None

    @property
    def oscillating(self) -> bool | None:
        if not self.coordinator.fresh("oscillation_state"):
            return None
        return (self.coordinator.data or {}).get("oscillation_state")

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: object,
    ) -> None:
        if percentage == 0:
            await self.async_turn_off()
            return
        if (
            self.is_on
            and self.coordinator.fresh("power_state")
            and percentage is None
            and preset_mode is None
        ):
            return
        commands: list[tuple[str, tuple[object, ...]]] = [("turn_on", ())]
        if preset_mode is not None:
            if preset_mode not in self.preset_modes:
                raise ValueError("Unsupported preset mode")
            if self.coordinator.model == "H7102":
                commands.append(
                    ("set_work_mode", (H7102WorkMode(preset_mode.lower()),))
                )
            else:
                commands.append((f"set_{preset_mode.lower()}_mode", ()))
        if percentage is not None and self.coordinator.model == "H7102":
            commands.append(
                ("set_fan_speed", (max(1, min(8, round(percentage * 8 / 100))),))
            )
        await self.coordinator.async_commands(commands)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self.coordinator.async_command("turn_off")

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in self.preset_modes:
            raise ValueError("Unsupported preset mode")
        if self.is_on is not True or not self.coordinator.fresh("power_state"):
            await self.async_turn_on(preset_mode=preset_mode)
            return
        if self.coordinator.model == "H7102":
            await self.coordinator.async_command(
                "set_work_mode", H7102WorkMode(preset_mode.lower())
            )
        else:
            await self.coordinator.async_command(f"set_{preset_mode.lower()}_mode")

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage == 0:
            await self.async_turn_off()
            return
        speed = round(percentage * 8 / 100)
        commands: list[tuple[str, tuple[object, ...]]] = []
        if self.is_on is not True or not self.coordinator.fresh("power_state"):
            commands.append(("turn_on", ()))
        commands.append(("set_fan_speed", (max(1, min(8, speed)),)))
        await self.coordinator.async_commands(commands)

    async def async_oscillate(self, oscillating: bool) -> None:
        await self.coordinator.async_command("set_oscillation", oscillating)
