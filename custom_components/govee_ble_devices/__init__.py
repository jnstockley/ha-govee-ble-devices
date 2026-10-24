"""Govee BLE devices backed by govee-ble-client."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from .coordinator import GoveeBLECoordinator

PLATFORMS = ("sensor", "fan", "switch", "button")
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an individual device."""
    # Failed setup can leave a backend that still needs disconnecting. HA
    # drains on-unload callbacks on setup failure, so keep its owner in the
    # entry and clean it before replacing it on a subsequent setup attempt.
    if previous := getattr(entry, "runtime_data", None):
        try:
            await previous.async_shutdown()
        except UpdateFailed as err:
            raise ConfigEntryNotReady(
                "Previous BLE session still needs cleanup"
            ) from err
    coordinator = GoveeBLECoordinator(hass, entry)
    entry.runtime_data = coordinator
    try:
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        try:
            await coordinator.async_shutdown()
        except Exception as err:  # noqa: BLE001 - preserve the initiating setup error
            _LOGGER.debug("Setup cleanup retained error=%s", type(err).__name__)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an individual device."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Allow Home Assistant to offer the device again after removal."""
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS])
