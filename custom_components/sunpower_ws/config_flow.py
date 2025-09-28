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
        
        # Show the reconfigure form
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Optional("host", default=current_data.get("host", DEFAULT_HOST)): str,
                vol.Optional("port", default=current_data.get("port", DEFAULT_PORT)): int,
                vol.Optional("enable_w_sensors", default=current_data.get("enable_w_sensors", False)): bool,
                vol.Optional("enable_devicelist_scan", default=current_data.get("enable_devicelist_scan", True)): bool,
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
                vol.Optional("enable_ws_throttle", default=current_data.get("enable_ws_throttle", True)): bool,
                vol.Optional("ws_update_interval", default=current_data.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)): int,
                vol.Optional("poll_interval", default=current_data.get("poll_interval", DEFAULT_POLL_INTERVAL)): int,
            }),
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
        
        # Add devicelist scan toggle with conditional fields
        schema_dict[vol.Optional("enable_devicelist_scan", default=current.get("enable_devicelist_scan", True))] = selector.BooleanSelector(
            selector.BooleanSelectorConfig(
                # This is the key - using a selector with show_toggle=True enables dynamic fields
                show_toggle=True,
            )
        )
        
        # Add poll_interval as a conditional field under enable_devicelist_scan
        schema_dict["poll_interval"] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=60,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="seconds",
                # This makes the field conditional on the toggle above
                required=current.get("enable_devicelist_scan", True),
            )
        )
        
        # Add throttle toggle with conditional fields
        schema_dict[vol.Optional("enable_ws_throttle", default=current.get("enable_ws_throttle", True))] = selector.BooleanSelector(
            selector.BooleanSelectorConfig(
                show_toggle=True,
            )
        )
        
        # Add ws_update_interval as a conditional field under enable_ws_throttle
        schema_dict["ws_update_interval"] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="seconds",
                # This makes the field conditional on the toggle above
                required=current.get("enable_ws_throttle", True),
            )
        )
        
        # Set default values for the conditional fields
        defaults = {
            "poll_interval": current.get("poll_interval", DEFAULT_POLL_INTERVAL),
            "ws_update_interval": current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL),
        }
        
        schema = vol.Schema(schema_dict)
        return self.async_show_form(
            step_id="init", 
            data_schema=schema, 
            errors=errors,
            description_placeholders=defaults,
            last_step=True
        )


