from __future__ import annotations
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from . import DOMAIN, DEFAULT_HOST, DEFAULT_PORT, DEFAULT_WS_UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

class SunPowerWSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            # prevent duplicates on same host:port
            for entry in self._async_current_entries(include_ignore=False):
                if entry.data.get("host") == user_input.get("host") and entry.data.get("port") == user_input.get("port"):
                    return self.async_abort(reason="already_configured")
            
            # Create entry with all data
            return self.async_create_entry(title=f"SunPower PVS ({user_input.get('host')})", data=user_input)
        
        # Build schema with all fields organized logically
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
            vol.Optional("enable_ws_throttle", default=True): bool,
            vol.Optional("ws_update_interval", default=DEFAULT_WS_UPDATE_INTERVAL): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            ),
        })
        
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

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
            # Note: Unchecked checkboxes don't appear in user_input, so we need to explicitly set them
            data_updates = {
                "host": user_input["host"],
                "port": user_input["port"],
                "consumption_measure": user_input["consumption_measure"],
                "ws_update_interval": user_input["ws_update_interval"],
                "enable_ws_throttle": user_input.get("enable_ws_throttle", False),  # False if unchecked
                "enable_w_sensors": user_input.get("enable_w_sensors", False),  # False if unchecked
            }
            
            _LOGGER.debug("Reconfigure: Updating config entry with data: %s", data_updates)
            
            # Update the config entry and reload
            return self.async_update_reload_and_abort(
                reconfigure_entry,
                data_updates=data_updates,
                reload_even_if_entry_is_unchanged=True,
            )
        
        # Build schema with all fields organized logically
        schema = vol.Schema({
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
            vol.Optional("enable_ws_throttle", default=current_data.get("enable_ws_throttle", True)): bool,
            vol.Optional("ws_update_interval", default=current_data.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            ),
        })
        
        # Show the reconfigure form
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
        )


class SunPowerWSOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        # Don't explicitly set config_entry - it's deprecated
        # The parent class handles this automatically
        self._pending: dict | None = None

    async def async_step_init(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}
        errors = {}
        
        if user_input is not None:
            try:
                options = {}
                # Copy existing options first
                for key, value in current.items():
                    if key not in ["host", "port", "enable_w_sensors", 
                                "consumption_measure", "enable_ws_throttle", "ws_update_interval"]:
                        options[key] = value
                        
                # Update with new values
                # Note: Unchecked checkboxes don't appear in user_input, so we check with "in" operator
                options.update({
                    "host": user_input.get("host", current.get("host")),
                    "port": int(user_input.get("port", current.get("port", DEFAULT_PORT))),
                    "enable_w_sensors": user_input.get("enable_w_sensors", False),  # False if not in user_input
                    "consumption_measure": user_input.get("consumption_measure", current.get("consumption_measure", "house_usage")),
                    "enable_ws_throttle": user_input.get("enable_ws_throttle", False),  # False if not in user_input
                    "ws_update_interval": max(1, int(user_input.get("ws_update_interval", current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)))),
                })
                    
                # Update the entry with new options
                self.hass.config_entries.async_update_entry(self.config_entry, data={}, options=options)
                return self.async_create_entry(title="", data={})
            except Exception as ex:
                errors["base"] = "unknown"
                _LOGGER.exception("Unexpected exception during options update: %s", ex)

        # Build schema with all fields organized logically
        schema = vol.Schema({
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
            vol.Optional("enable_ws_throttle", default=current.get("enable_ws_throttle", True)): bool,
            vol.Optional("ws_update_interval", default=current.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="seconds",
                )
            ),
        })
        
        return self.async_show_form(
            step_id="init", 
            data_schema=schema, 
            errors=errors,
            last_step=True
        )


