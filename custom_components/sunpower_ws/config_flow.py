from __future__ import annotations
import asyncio
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
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH
    
    # Define allowed sources
    SUPPORTED_SOURCES = ["user", "import", "reconfigure"]
    
    # This enables the reconfigure button in the UI
    @property
    def _supports_reconfigure(self) -> bool:
        return True

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
        """Handle the reconfigure step."""
        # Get the entry_id from context
        if "entry_id" not in self.context:
            _LOGGER.error("Reconfigure called without entry_id in context")
            return self.async_abort(reason="unknown")
            
        entry_id = self.context["entry_id"]
        entry = self.hass.config_entries.async_get_entry(entry_id)
        
        if entry is None:
            _LOGGER.error(f"Could not find entry with ID {entry_id}")
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
                
                try:    
                    # Update the entry with new options
                    self.hass.config_entries.async_update_entry(entry, data={}, options=options)
                    # Try to reload with a timeout to prevent hanging
                    await asyncio.wait_for(self.hass.config_entries.async_reload(entry.entry_id), timeout=30)
                    return self.async_abort(reason="reconfigured")
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout during reconfiguration reload, but options were saved")
                    errors["base"] = "timeout"
                    # Even though we had a timeout, the options were saved, so we can still return success
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
            step_id="reconfigure", 
            data_schema=schema, 
            errors=errors,
            description_placeholders=defaults,
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


