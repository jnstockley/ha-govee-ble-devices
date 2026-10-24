"""UI configuration and Bluetooth discovery for Govee BLE devices."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import CONF_MODEL, DOMAIN, SUPPORTED_MODELS


def model_from_name(name: str | None) -> str | None:
    """Only offer models with a verified client profile."""
    value = (name or "").upper()
    if "GV5179" in value:
        return "H5179"
    return next((model for model in SUPPORTED_MODELS if model in value), None)


class GoveeBLEConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a separate entry for each physical Bluetooth address."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: bluetooth.BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Offer a discovered, connectable supported model."""
        model = model_from_name(discovery_info.name)
        if model is None or not discovery_info.connectable:
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name or model}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Confirm a Bluetooth discovery."""
        assert self._discovery is not None
        if user_input is not None:
            model = model_from_name(self._discovery.name)
            assert model is not None
            return self.async_create_entry(
                title=self._discovery.name or f"Govee {model}",
                data={CONF_ADDRESS: self._discovery.address, CONF_MODEL: model},
            )
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._discovery.name or "Govee device"},
        )

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Pick a device from Home Assistant's shared Bluetooth scanner."""

        def discovered() -> dict[str, bluetooth.BluetoothServiceInfoBleak]:
            return {
                info.address: info
                for info in bluetooth.async_discovered_service_info(self.hass)
                if info.connectable and model_from_name(info.name)
            }

        devices = discovered()
        if user_input is None and not devices:
            await bluetooth.async_request_active_scan(self.hass, 5)
            devices = discovered()
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            info = devices.get(address)
            if info is None:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._schema(devices),
                    errors={"base": "device_missing"},
                )
            return await self.async_step_bluetooth(info)
        if not devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(step_id="user", data_schema=self._schema(devices))

    @staticmethod
    def _schema(devices: dict[str, bluetooth.BluetoothServiceInfoBleak]) -> vol.Schema:
        options = [
            SelectOptionDict(value=address, label=f"{info.name} ({address})")
            for address, info in sorted(devices.items())
        ]
        return vol.Schema(
            {
                vol.Required(CONF_ADDRESS): SelectSelector(
                    SelectSelectorConfig(options=options)
                )
            }
        )
