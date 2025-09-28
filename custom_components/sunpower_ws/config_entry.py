"""Config entry button for SunPower WebSocket integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from . import DOMAIN

@callback
def async_reconfigure_button(hass: HomeAssistant, config_entry: ConfigEntry):
    """Return a reconfigure button for the integration."""
    return {
        "handler": f"{DOMAIN}.reconfigure",
        "name": "Reconfigure",
        "description": "Reconfigure this integration",
        "data": {"entry_id": config_entry.entry_id},
    }
