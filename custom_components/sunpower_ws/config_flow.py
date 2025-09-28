from __future__ import annotations
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from . import DOMAIN, DEFAULT_HOST, DEFAULT_PORT, DEFAULT_POLL_INTERVAL, DEFAULT_WS_UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

class SunPowerWSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._user_input = {}

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            # Store current input
            self._user_input.update(user_input)
            
            # Check if we need to show conditional fields
            if ("enable_devicelist_scan" in user_input or "enable_ws_throttle" in user_input) and \
               ("poll_interval" not in user_input or "ws_update_interval" not in user_input):
                # Show step 2 with conditional fields
                return await self.async_step_user_2()
            
            # prevent duplicates on same host:port
            for entry in self._async_current_entries(include_ignore=False):
                if entry.data.get("host") == user_input.get("host") and entry.data.get("port") == user_input.get("port"):
                    return self.async_abort(reason="already_configured")
            
            # Create entry with all collected data
            return self.async_create_entry(title=f"SunPower PVS ({user_input.get('host')})", data=self._user_input)
        
        # Build initial schema
        schema = vol.Schema({
            vol.Optional("host", default=DEFAULT_HOST): str,
            vol.Optional("port", default=DEFAULT_PORT): int,
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
            vol.Optional("enable_devicelist_scan", default=True): bool,
            vol.Optional("enable_ws_throttle", default=True): bool,
        })
        
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_user_2(self, user_input=None) -> FlowResult:
        """Handle the conditional fields step."""
        errors = {}
        
        if user_input is not None:
            # Merge with previous input
            self._user_input.update(user_input)
            
            # prevent duplicates on same host:port
            for entry in self._async_current_entries(include_ignore=False):
                if entry.data.get("host") == self._user_input.get("host") and entry.data.get("port") == self._user_input.get("port"):
                    return self.async_abort(reason="already_configured")
            
            # Create entry with all collected data
            return self.async_create_entry(title=f"SunPower PVS ({self._user_input.get('host')})", data=self._user_input)
        
        # Build conditional schema based on previous selections
        schema_dict = {}
        
        # Show poll_interval only if devicelist scan is enabled
        if self._user_input.get("enable_devicelist_scan", True):
            schema_dict[vol.Optional("poll_interval", default=DEFAULT_POLL_INTERVAL)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=60,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            )
        
        # Show ws_update_interval only if WS throttle is enabled
        if self._user_input.get("enable_ws_throttle", True):
            schema_dict[vol.Optional("ws_update_interval", default=DEFAULT_WS_UPDATE_INTERVAL)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            )
        
        # If no conditional fields needed, proceed directly
        if not schema_dict:
            return self.async_create_entry(title=f"SunPower PVS ({self._user_input.get('host')})", data=self._user_input)
        
        schema = vol.Schema(schema_dict)
        return self.async_show_form(step_id="user_2", data_schema=schema, errors=errors)

    async def async_step_import(self, user_input=None) -> FlowResult:
        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SunPowerWSOptionsFlowHandler(config_entry)
        
    async def async_step_reconfigure(self, user_input=None) -> FlowResult:
        """Handle reconfigure step to allow changing config entry data."""
        reconfigure_entry = self._get_reconfigure_entry()
        if reconfigure_entry is None:
            return self.async_abort(reason="unknown")
            
        current_data = {**reconfigure_entry.data, **reconfigure_entry.options}
        
        if user_input is not None:
            # Process the user input and update the config entry
            new_data = {
                "host": user_input.get("host", current_data.get("host", DEFAULT_HOST)),
                "port": user_input.get("port", current_data.get("port", DEFAULT_PORT)),
                "enable_devicelist_scan": user_input.get("enable_devicelist_scan", current_data.get("enable_devicelist_scan", True)),
                "consumption_measure": user_input.get("consumption_measure", current_data.get("consumption_measure", "house_usage")),
                "ws_update_interval": user_input.get("ws_update_interval", current_data.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)),
                "enable_ws_throttle": user_input.get("enable_ws_throttle", current_data.get("enable_ws_throttle", True)),
                "poll_interval": user_input.get("poll_interval", current_data.get("poll_interval", DEFAULT_POLL_INTERVAL)),
                "enable_w_sensors": user_input.get("enable_w_sensors", current_data.get("enable_w_sensors", False)),
            }
            
            # Update the config entry and reload
            return self.async_update_reload_and_abort(
                reconfigure_entry,
                data_updates=new_data,
                reload_even_if_entry_is_unchanged=False,
            )
        
        # Build schema - show all fields but organize logically
        schema_dict = {
            vol.Optional("host", default=current_data.get("host", DEFAULT_HOST)): str,
            vol.Optional("port", default=current_data.get("port", DEFAULT_PORT)): int,
            vol.Optional("enable_w_sensors", default=current_data.get("enable_w_sensors", False)): bool,
            vol.Optional("consumption_measure", default=current_data.get("consumption_measure", "house_usage")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": "House usage (load)", "value": "house_usage"},
                        {"label": "Grid import (from utility)", "value": "grid_import"},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
            vol.Optional("enable_devicelist_scan", default=current_data.get("enable_devicelist_scan", True)): bool,
        }
        
        # Add poll_interval right after devicelist scan toggle
        if current_data.get("enable_devicelist_scan", True):
            schema_dict[vol.Optional("poll_interval", default=current_data.get("poll_interval", DEFAULT_POLL_INTERVAL))] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=60,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            )
        
        # Add WS throttle toggle
        schema_dict[vol.Optional("enable_ws_throttle", default=current_data.get("enable_ws_throttle", True))] = bool
        
        # Add ws_update_interval right after WS throttle toggle
        if current_data.get("enable_ws_throttle", True):
            schema_dict[vol.Optional("ws_update_interval", default=current_data.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL))] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            )
        
        # Show the reconfigure form
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(schema_dict),
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
            vol.Optional("enable_devicelist_scan", default=current.get("enable_devicelist_scan", True)): bool,
        }
        
        # Add poll_interval right after devicelist scan toggle if enabled
        if current.get("enable_devicelist_scan", True):
            schema_dict[vol.Optional("poll_interval", default=current.get("poll_interval", DEFAULT_POLL_INTERVAL))] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=60,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            )
        
        # Add WS throttle toggle
        schema_dict[vol.Optional("enable_ws_throttle", default=current.get("enable_ws_throttle", True))] = bool
        
        # Add ws_update_interval right after WS throttle toggle if enabled
        if current.get("enable_ws_throttle", True):
            schema_dict[vol.Optional("ws_update_interval", default=current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL))] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            )
        
        schema = vol.Schema(schema_dict)
        return self.async_show_form(
            step_id="init", 
            data_schema=schema, 
            errors=errors,
            last_step=True
        )


