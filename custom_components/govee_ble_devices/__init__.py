"""Govee BLE devices backed by govee-ble-client."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import CONF_MODEL, DOMAIN
from .coordinator import GoveeBLECoordinator
from .custom_schedule import CustomScheduleEditor

PLATFORMS = ("sensor", "fan", "switch", "button", "number", "select")
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
        if coordinator.custom_editor is not None:
            await coordinator.custom_editor.async_load()
        await coordinator.async_config_entry_first_refresh()
        if coordinator.custom_editor is not None:
            _remove_obsolete_custom_entities(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        try:
            await coordinator.async_shutdown()
        except Exception as err:  # noqa: BLE001 - preserve the initiating setup error
            _LOGGER.debug("Setup cleanup retained error=%s", type(err).__name__)
        raise
    return True


def _remove_obsolete_custom_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the draft editor's superseded final-block duration controls."""
    registry = er.async_get(hass)
    address = entry.data[CONF_ADDRESS].upper()
    for platform, field in (("number", "duration"), ("switch", "continuous")):
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, f"{address}_custom_block_3_{field}"
        )
        if entity_id is not None:
            registered = registry.async_get(entity_id)
            if registered is not None and registered.config_entry_id == entry.entry_id:
                registry.async_remove(entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an individual device."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Allow Home Assistant to offer the device again after removal."""
    if entry.data[CONF_MODEL] in ("H7102", "H7126"):
        await CustomScheduleEditor(
            hass, entry.entry_id, entry.data[CONF_MODEL]
        ).async_remove()
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS])
