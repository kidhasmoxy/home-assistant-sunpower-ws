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

    async def async_step_reconfigure(self, user_input=None) -> FlowResult:
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry is None:
            return self.async_abort(reason="unknown")

        current = {**entry.data, **entry.options}
        if user_input is not None:
            # Save base settings and decide if we need advanced step
            self._reconfig_pending = {
                "host": user_input.get("host", current.get("host")),
                "port": int(user_input.get("port", current.get("port", DEFAULT_PORT))),
                "enable_w_sensors": bool(user_input.get("enable_w_sensors", current.get("enable_w_sensors", False))),
                "enable_devicelist_scan": bool(user_input.get("enable_devicelist_scan", current.get("enable_devicelist_scan", True))),
                "consumption_measure": user_input.get("consumption_measure", current.get("consumption_measure", "house_usage")),
                "enable_ws_throttle": bool(user_input.get("enable_ws_throttle", current.get("enable_ws_throttle", True))),
            }
            need_ws = self._reconfig_pending["enable_ws_throttle"]
            need_poll = self._reconfig_pending["enable_devicelist_scan"]
            if need_ws or need_poll:
                return await self.async_step_reconfigure_advanced()
            # No advanced fields needed; persist with existing intervals
            options = {**current, **self._reconfig_pending}
            self.hass.config_entries.async_update_entry(entry, options=options)
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="reconfigured")

        schema = vol.Schema({
            vol.Optional("host", default=current.get("host", DEFAULT_HOST)): str,
            vol.Optional("port", default=current.get("port", DEFAULT_PORT)): int,
            vol.Optional("enable_w_sensors", default=current.get("enable_w_sensors", False)): bool,
            vol.Optional("enable_devicelist_scan", default=current.get("enable_devicelist_scan", True)): bool,
            vol.Optional("consumption_measure", default=current.get("consumption_measure", "house_usage")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": "House usage (load)", "value": "house_usage"},
                        {"label": "Grid import (from utility)", "value": "grid_import"},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
            vol.Optional("enable_ws_throttle", default=current.get("enable_ws_throttle", True)): bool,
        })
        return self.async_show_form(step_id="reconfigure", data_schema=schema)

    async def async_step_reconfigure_advanced(self, user_input=None) -> FlowResult:
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry is None:
            return self.async_abort(reason="unknown")
        current = {**entry.data, **entry.options}
        pending = getattr(self, "_reconfig_pending", {})
        fields = {}
        if pending.get("enable_ws_throttle", current.get("enable_ws_throttle", True)):
            fields[vol.Optional("ws_update_interval", default=current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL))] = int
        if pending.get("enable_devicelist_scan", current.get("enable_devicelist_scan", True)):
            fields[vol.Optional("poll_interval", default=current.get("poll_interval", DEFAULT_POLL_INTERVAL))] = int
        schema = vol.Schema(fields)
        if user_input is not None:
            options = {**current, **pending}
            if "ws_update_interval" in user_input:
                options["ws_update_interval"] = max(1, int(user_input["ws_update_interval"]))
            if "poll_interval" in user_input:
                options["poll_interval"] = max(60, int(user_input["poll_interval"]))
            self.hass.config_entries.async_update_entry(entry, options=options)
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="reconfigured")
        return self.async_show_form(step_id="reconfigure_advanced", data_schema=schema)


class SunPowerWSOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry
        self._pending: dict | None = None

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            current = {**self.config_entry.data, **self.config_entry.options}
            self._pending = {
                "host": user_input.get("host", current.get("host")),
                "port": int(user_input.get("port", current.get("port", DEFAULT_PORT))),
                "enable_w_sensors": bool(user_input.get("enable_w_sensors", current.get("enable_w_sensors", False))),
                "enable_devicelist_scan": bool(user_input.get("enable_devicelist_scan", current.get("enable_devicelist_scan", True))),
                "consumption_measure": user_input.get("consumption_measure", current.get("consumption_measure", "house_usage")),
                "enable_ws_throttle": bool(user_input.get("enable_ws_throttle", current.get("enable_ws_throttle", True))),
            }
            need_ws = self._pending["enable_ws_throttle"]
            need_poll = self._pending["enable_devicelist_scan"]
            if need_ws or need_poll:
                return await self.async_step_advanced()
            # nothing more to ask
            options = {**current, **self._pending}
            self.hass.config_entries.async_update_entry(self.config_entry, options=options)
            return self.async_create_entry(title="", data={})

        data = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema({
            vol.Optional("host", default=data.get("host", DEFAULT_HOST)): str,
            vol.Optional("port", default=data.get("port", DEFAULT_PORT)): int,
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
            vol.Optional("enable_ws_throttle", default=data.get("enable_ws_throttle", True)): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_advanced(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}
        pending = self._pending or {}
        fields = {}
        if pending.get("enable_ws_throttle", current.get("enable_ws_throttle", True)):
            fields[vol.Optional("ws_update_interval", default=current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL))] = int
        if pending.get("enable_devicelist_scan", current.get("enable_devicelist_scan", True)):
            fields[vol.Optional("poll_interval", default=current.get("poll_interval", DEFAULT_POLL_INTERVAL))] = int
        schema = vol.Schema(fields)
        if user_input is not None:
            options = {**current, **pending}
            if "ws_update_interval" in user_input:
                options["ws_update_interval"] = max(1, int(user_input["ws_update_interval"]))
            if "poll_interval" in user_input:
                options["poll_interval"] = max(60, int(user_input["poll_interval"]))
            self.hass.config_entries.async_update_entry(self.config_entry, options=options)
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="advanced", data_schema=schema)




async def async_get_options_flow(config_entry):
    return SunPowerWSOptionsFlowHandler(config_entry)
