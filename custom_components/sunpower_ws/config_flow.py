from __future__ import annotations
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from . import DOMAIN, DEFAULT_HOST, DEFAULT_PORT, DEFAULT_POLL_INTERVAL, DEFAULT_WS_UPDATE_INTERVAL

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
            vol.Optional("enable_devicelist_scan", default=True): bool,
            vol.Optional("consumption_measure", default="house_usage"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": "House usage (load)", "value": "house_usage"},
                        {"label": "Grid import (from utility)", "value": "grid_import"},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
            vol.Optional("ws_update_interval", default=DEFAULT_WS_UPDATE_INTERVAL): int,
            vol.Optional("enable_ws_throttle", default=True): bool,
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
            current = {**self.config_entry.data, **self.config_entry.options}
            options = {
                "host": user_input.get("host", current.get("host")),
                "port": int(user_input.get("port", current.get("port", DEFAULT_PORT))),
                "poll_interval": max(60, int(user_input.get("poll_interval", current.get("poll_interval", DEFAULT_POLL_INTERVAL)))),
                "enable_w_sensors": bool(user_input.get("enable_w_sensors", current.get("enable_w_sensors", False))),
                "enable_devicelist_scan": bool(user_input.get("enable_devicelist_scan", current.get("enable_devicelist_scan", True))),
                "consumption_measure": user_input.get("consumption_measure", current.get("consumption_measure", "house_usage")),
                "ws_update_interval": max(1, int(user_input.get("ws_update_interval", current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)))),
                "enable_ws_throttle": bool(user_input.get("enable_ws_throttle", current.get("enable_ws_throttle", True))),
            }
            self.hass.config_entries.async_update_entry(self.config_entry, options=options)
            return self.async_create_entry(title="", data={})

        data = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema({
            vol.Optional("host", default=data.get("host", DEFAULT_HOST)): str,
            vol.Optional("port", default=data.get("port", DEFAULT_PORT)): int,
            vol.Optional("poll_interval", default=data.get("poll_interval", DEFAULT_POLL_INTERVAL)): int,
            vol.Optional("enable_w_sensors", default=data.get("enable_w_sensors", False)): bool,
            vol.Optional("enable_devicelist_scan", default=data.get("enable_devicelist_scan", True)): bool,
            vol.Optional("consumption_measure", default=data.get("consumption_measure", "house_usage")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": "House usage (load)", "value": "house_usage"},
                        {"label": "Grid import (from utility)", "value": "grid_import"},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
            vol.Optional("ws_update_interval", default=data.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)): int,
            vol.Optional("enable_ws_throttle", default=data.get("enable_ws_throttle", True)): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)


async def async_get_options_flow(config_entry):
    return SunPowerWSOptionsFlowHandler(config_entry)
