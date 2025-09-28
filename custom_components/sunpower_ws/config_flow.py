from __future__ import annotations
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from . import DOMAIN, DEFAULT_HOST, DEFAULT_PORT, DEFAULT_POLL_INTERVAL, DEFAULT_WS_UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

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
        errors = {}
        
        if user_input is not None:
            try:
                options = {}
                # Copy existing options first
                for key, value in current.items():
                    if key not in ["host", "port", "enable_w_sensors", "enable_devicelist_scan", 
                                "consumption_measure", "enable_ws_throttle", "ws_update_interval", "poll_interval"]:
                        options[key] = value
                        
                # Update with new values
                options.update({
                    "host": user_input.get("host", current.get("host")),
                    "port": int(user_input.get("port", current.get("port", DEFAULT_PORT))),
                    "enable_w_sensors": bool(user_input.get("enable_w_sensors", current.get("enable_w_sensors", False))),
                    "enable_devicelist_scan": bool(user_input.get("enable_devicelist_scan", current.get("enable_devicelist_scan", True))),
                    "consumption_measure": user_input.get("consumption_measure", current.get("consumption_measure", "house_usage")),
                    "enable_ws_throttle": bool(user_input.get("enable_ws_throttle", current.get("enable_ws_throttle", True))),
                })
                
                # Add interval settings if provided
                if "ws_update_interval" in user_input and options["enable_ws_throttle"]:
                    options["ws_update_interval"] = max(1, int(user_input["ws_update_interval"]))
                elif options["enable_ws_throttle"]:
                    options["ws_update_interval"] = current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)
                    
                if "poll_interval" in user_input and options["enable_devicelist_scan"]:
                    options["poll_interval"] = max(60, int(user_input["poll_interval"]))
                elif options["enable_devicelist_scan"]:
                    options["poll_interval"] = current.get("poll_interval", DEFAULT_POLL_INTERVAL)
                    
                # Update the entry with new options
                self.hass.config_entries.async_update_entry(entry, data={}, options=options)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfigured")
            except Exception as ex:
                errors["base"] = "unknown"
                _LOGGER.exception("Unexpected exception during reconfiguration: %s", ex)

        # Build schema with conditional fields
        schema_dict = {
            vol.Optional("host", default=current.get("host", DEFAULT_HOST)): str,
            vol.Optional("port", default=current.get("port", DEFAULT_PORT)): int,
            vol.Optional("enable_w_sensors", default=current.get("enable_w_sensors", False)): bool,
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
        }
        
        # Add devicelist scan toggle and its dependent field
        schema_dict[vol.Optional("enable_devicelist_scan", default=current.get("enable_devicelist_scan", True))] = bool
        
        # Only show poll_interval if devicelist scan is enabled
        if user_input is not None:
            show_poll = user_input.get("enable_devicelist_scan", current.get("enable_devicelist_scan", True))
        else:
            show_poll = current.get("enable_devicelist_scan", True)
            
        if show_poll:
            schema_dict[vol.Optional("poll_interval", default=current.get("poll_interval", DEFAULT_POLL_INTERVAL))] = int
        
        # Add throttle toggle and its dependent field
        schema_dict[vol.Optional("enable_ws_throttle", default=current.get("enable_ws_throttle", True))] = bool
        
        # Only show ws_update_interval if throttle is enabled
        if user_input is not None:
            show_ws = user_input.get("enable_ws_throttle", current.get("enable_ws_throttle", True))
        else:
            show_ws = current.get("enable_ws_throttle", True)
            
        if show_ws:
            schema_dict[vol.Optional("ws_update_interval", default=current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL))] = int
        
        schema = vol.Schema(schema_dict)
        return self.async_show_form(
            step_id="reconfigure", 
            data_schema=schema, 
            errors=errors,
            last_step=True
        )


class SunPowerWSOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry
        self._pending: dict | None = None

    async def async_step_init(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}
        errors = {}
        
        if user_input is not None:
            try:
                options = {}
                # Copy existing options first
                for key, value in current.items():
                    if key not in ["host", "port", "enable_w_sensors", "enable_devicelist_scan", 
                                "consumption_measure", "enable_ws_throttle", "ws_update_interval", "poll_interval"]:
                        options[key] = value
                        
                # Update with new values
                options.update({
                    "host": user_input.get("host", current.get("host")),
                    "port": int(user_input.get("port", current.get("port", DEFAULT_PORT))),
                    "enable_w_sensors": bool(user_input.get("enable_w_sensors", current.get("enable_w_sensors", False))),
                    "enable_devicelist_scan": bool(user_input.get("enable_devicelist_scan", current.get("enable_devicelist_scan", True))),
                    "consumption_measure": user_input.get("consumption_measure", current.get("consumption_measure", "house_usage")),
                    "enable_ws_throttle": bool(user_input.get("enable_ws_throttle", current.get("enable_ws_throttle", True))),
                })
                
                # Add interval settings if provided
                if "ws_update_interval" in user_input and options["enable_ws_throttle"]:
                    options["ws_update_interval"] = max(1, int(user_input["ws_update_interval"]))
                elif options["enable_ws_throttle"]:
                    options["ws_update_interval"] = current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)
                    
                if "poll_interval" in user_input and options["enable_devicelist_scan"]:
                    options["poll_interval"] = max(60, int(user_input["poll_interval"]))
                elif options["enable_devicelist_scan"]:
                    options["poll_interval"] = current.get("poll_interval", DEFAULT_POLL_INTERVAL)
                    
                # Update the entry with new options
                self.hass.config_entries.async_update_entry(self.config_entry, data={}, options=options)
                return self.async_create_entry(title="", data={})
            except Exception as ex:
                errors["base"] = "unknown"
                _LOGGER.exception("Unexpected exception during options update: %s", ex)

        # Build schema with conditional fields
        schema_dict = {
            vol.Optional("host", default=current.get("host", DEFAULT_HOST)): str,
            vol.Optional("port", default=current.get("port", DEFAULT_PORT)): int,
            vol.Optional("enable_w_sensors", default=current.get("enable_w_sensors", False)): bool,
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
        }
        
        # Add devicelist scan toggle and its dependent field
        schema_dict[vol.Optional("enable_devicelist_scan", default=current.get("enable_devicelist_scan", True))] = bool
        
        # Only show poll_interval if devicelist scan is enabled
        if user_input is not None:
            show_poll = user_input.get("enable_devicelist_scan", current.get("enable_devicelist_scan", True))
        else:
            show_poll = current.get("enable_devicelist_scan", True)
            
        if show_poll:
            schema_dict[vol.Optional("poll_interval", default=current.get("poll_interval", DEFAULT_POLL_INTERVAL))] = int
        
        # Add throttle toggle and its dependent field
        schema_dict[vol.Optional("enable_ws_throttle", default=current.get("enable_ws_throttle", True))] = bool
        
        # Only show ws_update_interval if throttle is enabled
        if user_input is not None:
            show_ws = user_input.get("enable_ws_throttle", current.get("enable_ws_throttle", True))
        else:
            show_ws = current.get("enable_ws_throttle", True)
            
        if show_ws:
            schema_dict[vol.Optional("ws_update_interval", default=current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL))] = int
        
        schema = vol.Schema(schema_dict)
        return self.async_show_form(
            step_id="init", 
            data_schema=schema, 
            errors=errors,
            last_step=True
        )




async def async_get_options_flow(config_entry):
    return SunPowerWSOptionsFlowHandler(config_entry)
