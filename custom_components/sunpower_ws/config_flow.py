from __future__ import annotations
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from . import DOMAIN, DEFAULT_HOST, DEFAULT_PORT, DEFAULT_POLL_INTERVAL

class SunPowerWSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        if user_input is not None:
            # prevent duplicates on same host:port
            for entry in self._async_current_entries(include_ignore=False):
                if entry.data.get("host") == user_input.get("host") and entry.data.get("port") == user_input.get("port"):
                    return self.async_abort(reason="already_configured")
            return self.async_create_entry(title=f"SunPower PVS ({user_input.get('host')})", data=user_input)
        schema = vol.Schema({
            vol.Optional("host", default=DEFAULT_HOST): str,
            vol.Optional("port", default=DEFAULT_PORT): int,
            vol.Optional("poll_interval", default=DEFAULT_POLL_INTERVAL): int,
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_import(self, user_input=None) -> FlowResult:
        return await self.async_step_user(user_input)


class SunPowerWSOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            data = dict(self.config_entry.data)
            data["poll_interval"] = max(60, int(user_input.get("poll_interval", data.get("poll_interval", DEFAULT_POLL_INTERVAL))))
            data["enable_w_sensors"] = bool(user_input.get("enable_w_sensors", False))
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            return self.async_create_entry(title="", data={})

        data = self.config_entry.data
        schema = vol.Schema({
            vol.Optional("poll_interval", default=data.get("poll_interval", DEFAULT_POLL_INTERVAL)): int,
            vol.Optional("enable_w_sensors", default=data.get("enable_w_sensors", False)): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)


async def async_get_options_flow(config_entry):
    return SunPowerWSOptionsFlowHandler(config_entry)
