from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr

DOMAIN = "sunpower_ws"
_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "172.27.153.1"
DEFAULT_PORT = 9002
DEFAULT_WS_UPDATE_INTERVAL = 5  # seconds for throttling WS-driven sensor writes
WS_PATH = "/"

PLATFORMS = ["sensor"]

# Normalized map: keys we emit to listeners
FIELD_MAP = {
    # Primary normalized power in kW
    "pv_kw": ["pv_p", "solar_p", "pv_kw"],
    "load_kw": ["site_load_p", "load_p", "load_kw"],
    "net_kw": ["net_p", "grid_p", "net_kw"],

    # Legacy power in W (populate if present or derived)
    "solar_w": ["solar_w", "pv_w"],
    "load_w":  ["load_w", "house_w"],
    "net_w":   ["net_w"],
    "grid_w":  ["grid_w"],

    # Battery state
    "battery_soc": ["soc", "battery_soc", "ess_soc"],

    # Lifetime energy in kWh from WS
    "pv_en_kwh": ["pv_en"],
    "site_load_en_kwh": ["site_load_en"],
    "net_en_kwh": ["net_en"],
}


class SunPowerWSHub:
    """WebSocket client for SunPower PVS."""

    def __init__(self, hass: HomeAssistant, host: str, port: int, ws_update_interval: int, consumption_measure: str, enable_ws_throttle: bool):
        self.hass = hass
        self.host = host
        self.port = port
        self.ws_update_interval = max(1, int(ws_update_interval or DEFAULT_WS_UPDATE_INTERVAL))
        self.consumption_measure = consumption_measure or "house_usage"
        self.enable_ws_throttle = bool(enable_ws_throttle)
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._listeners: list[Callable[[dict], None]] = []
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()
        self._connected = False
        self._last_connection_attempt = 0

    def add_listener(self, cb: Callable[[dict], None]) -> None:
        self._listeners.append(cb)

    async def async_start(self) -> None:
        """Start the hub and connect to the WebSocket."""
        # Don't start if already running
        if self._task:
            _LOGGER.debug("Hub already started, not starting again")
            return
            
        _LOGGER.debug("Starting SunPowerWSHub for %s:%s", self.host, self.port)
        
        # Reset the stopped flag
        self._stopped.clear()
        
        # Create a new session if needed
        if not self._session:
            self._session = aiohttp.ClientSession()
            
        # Use background tasks (HA 2023.9+) to avoid bootstrap tracking
        self._task = self.hass.async_create_background_task(
            self._runner(),
            name="SunPowerWS WebSocket Runner"
        )
            
        # Register stop handler
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_hass_stop)
        
        _LOGGER.debug("SunPowerWSHub startup initiated (WebSocket task started)")

    async def async_stop(self) -> None:
        """Stop the hub and clean up resources."""
        _LOGGER.debug("Stopping SunPowerWSHub")
        
        # Signal stop to all running tasks
        self._stopped.set()
        
        # Cancel and wait for WebSocket task to complete
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                _LOGGER.warning("WebSocket task did not complete in time")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                _LOGGER.warning("Error waiting for WebSocket task: %s", e)
        
        # Close the WebSocket connection
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception as e:
                _LOGGER.warning("Error closing WebSocket connection: %s", e)
        
        # Close the HTTP session
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception as e:
                _LOGGER.warning("Error closing HTTP session: %s", e)
        
        # Clear references
        self._ws = None
        self._session = None
        self._task = None
        self._connected = False
        
        _LOGGER.debug("SunPowerWSHub stopped successfully")

    async def _runner(self) -> None:
        url = f"ws://{self.host}:{self.port}{WS_PATH}"
        backoff = 1
        while not self._stopped.is_set():
            try:
                # Only log connection attempts if we're not connected or it's been a while
                import time
                now = time.time()
                if not self._connected or (now - self._last_connection_attempt) > 60:
                    _LOGGER.info("Connecting to SunPower PVS WS at %s", url)
                else:
                    _LOGGER.debug("Reconnecting to SunPower PVS WS at %s", url)
                
                self._last_connection_attempt = now
                assert self._session is not None
                
                # Try to establish WebSocket connection with timeout
                try:
                    # Use shorter timeout during startup to prevent bootstrap delays
                    timeout_duration = 5 if not self._connected else 10
                    async with asyncio.timeout(timeout_duration):
                        self._ws = await self._session.ws_connect(url, heartbeat=30)
                    
                    if not self._connected:
                        _LOGGER.info("Successfully connected to WebSocket at %s", url)
                    else:
                        _LOGGER.debug("Successfully reconnected to WebSocket at %s", url)
                    
                    self._connected = True
                    backoff = 1  # Reset backoff on successful connection
                    
                    # Handle WebSocket messages using async for loop (Home Assistant friendly)
                    try:
                        async for msg in self._ws:
                            if self._stopped.is_set():
                                break
                                
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                except json.JSONDecodeError:
                                    _LOGGER.debug("Non-JSON message: %s", msg.data)
                                    continue
                                normalized = self._normalize_payload(data)
                                if normalized:
                                    for cb in self._listeners:
                                        try:
                                            cb(normalized)
                                        except Exception as cb_error:
                                            _LOGGER.error("Error in WebSocket callback: %s", cb_error)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                _LOGGER.warning("WS error: %s", self._ws.exception())
                                break
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                _LOGGER.info("WebSocket connection closed")
                                break
                            elif msg.type == aiohttp.WSMsgType.CLOSING:
                                _LOGGER.info("WebSocket connection closing")
                                break
                    except Exception as e:
                        _LOGGER.warning("Error processing WebSocket messages: %s", e)
                    finally:
                        # Clean up the WebSocket connection
                        if self._ws and not self._ws.closed:
                            try:
                                await self._ws.close()
                            except Exception:
                                pass
                        self._ws = None
                        if self._connected:
                            _LOGGER.info("WebSocket connection to %s lost", url)
                            self._connected = False
                        
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout connecting to SunPower PVS at %s (connection took longer than 10 seconds)", url)
                except aiohttp.ClientError as e:
                    _LOGGER.warning("Failed to connect to WebSocket at %s: %s", url, e)
                except Exception as e:
                    _LOGGER.warning("Unexpected error connecting to WebSocket at %s: %s", url, e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.warning("WS connect/loop error: %s", e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


    def _normalize_payload(self, data: dict) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        payload = data
        if "params" in data and isinstance(data["params"], dict):
            payload = data["params"]
        elif "power" in data and isinstance(data["power"], dict):
            payload = data["power"]

        result: Dict[str, Any] = {}
        # Pull known keys
        for norm_key, candidates in FIELD_MAP.items():
            for k in candidates:
                if k in payload:
                    result[norm_key] = payload[k]
                    break

        # Derive legacy W from kW if only kW present
        def kw_to_w(v):
            try:
                return float(v) * 1000.0
            except Exception:
                return None

        if "pv_kw" in result and "solar_w" not in result:
            vw = kw_to_w(result["pv_kw"])
            result["solar_w"] = vw if vw is not None else result.get("solar_w")
        if "load_kw" in result and "load_w" not in result:
            vw = kw_to_w(result["load_kw"])
            result["load_w"] = vw if vw is not None else result.get("load_w")
        if "net_kw" in result and "net_w" not in result:
            vw = kw_to_w(result["net_kw"])
            result["net_w"] = vw if vw is not None else result.get("net_w")

        # Compute/override net based on configured consumption measure
        try:
            cmode = getattr(self, "consumption_measure", "house_usage")
            if cmode == "house_usage":
                # Always derive net from house load and PV if both are available.
                # Convention: net_kw = load_kw - pv_kw  (positive = grid import, negative = grid export)
                if "pv_kw" in result and "load_kw" in result:
                    result["net_kw"] = float(result["load_kw"]) - float(result["pv_kw"])  # override any provided net
            elif cmode == "grid_import":
                # If the device did not provide a signed net, fallback to using load as import magnitude.
                if "net_kw" not in result and "load_kw" in result:
                    result["net_kw"] = float(result["load_kw"])  # non-negative import magnitude
        except Exception:
            pass

        # Normalize numeric types
        for k in list(result.keys()):
            v = result[k]
            if isinstance(v, (int, float)):
                continue
            try:
                result[k] = float(v)
            except Exception:
                pass

        return result

    @callback
    def _on_hass_stop(self, _event):
        # Use background task to avoid blocking shutdown
        self.hass.async_create_background_task(
            self.async_stop(),
            name="SunPowerWS Hub Shutdown"
        )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SunPower WebSocket from a config entry."""
    _LOGGER.info("Setting up SunPower WebSocket integration for entry: %s", entry.entry_id)
    
    try:
        # Get configuration from entry data and options
        cfg = {**entry.data, **entry.options}
        host = cfg.get("host", DEFAULT_HOST)
        port = cfg.get("port", DEFAULT_PORT)
        ws_update_interval = cfg.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)
        consumption_measure = cfg.get("consumption_measure", "house_usage")
        enable_ws_throttle = cfg.get("enable_ws_throttle", True)
        
        _LOGGER.debug(
            "Configuration: host=%s, port=%s, ws_update_interval=%s, consumption_measure=%s, enable_ws_throttle=%s",
            host, port, ws_update_interval, consumption_measure, enable_ws_throttle
        )
        
        # Register the device in the device registry
        device_registry = dr.async_get(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, "sunpower_pvs_ws")},
            name="SunPower PVS (WebSocket)",
            manufacturer="SunPower",
            model="PVS WebSocket Gateway",
            model_id="pvs_ws",
            configuration_url=f"http://{host}",
        )
        
        # Create and store the hub instance
        hub = SunPowerWSHub(
            hass, host, port, ws_update_interval, consumption_measure, enable_ws_throttle
        )
        hass.data[DOMAIN] = hub
        
        # Start hub with proper error handling and lifecycle management
        try:
            await hub.async_start()
            _LOGGER.info("SunPower WebSocket hub started successfully")
        except Exception as ex:
            _LOGGER.warning("Error starting SunPower WebSocket hub: %s. Integration will continue with limited functionality.", ex)
            # Don't raise - allow integration to continue without WebSocket
        
        # Set up the sensor platform (delay to avoid bootstrap blocking)
        def setup_platforms():
            """Set up platforms after a brief delay."""
            hass.async_create_background_task(
                hass.config_entries.async_forward_entry_setups(entry, PLATFORMS),
                name="SunPowerWS Platform Setup"
            )
            _LOGGER.debug("Sensor platform setup initiated")
        
        # Delay platform setup slightly to avoid bootstrap blocking
        hass.loop.call_later(0.1, setup_platforms)
        
        # Reload integration when options change
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
        
        _LOGGER.info("SunPower WebSocket integration setup complete for entry: %s", entry.entry_id)
        return True
        
    except Exception as ex:
        _LOGGER.exception("Error setting up SunPower WebSocket integration: %s", ex)
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Stop the hub first before unloading platforms
    hub: SunPowerWSHub = hass.data.get(DOMAIN)
    if hub:
        try:
            await hub.async_stop()
        except Exception as ex:
            _LOGGER.warning("Error stopping hub during unload: %s", ex)
    
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    # Clean up data storage only if unload was successful
    if unload_ok and DOMAIN in hass.data:
        del hass.data[DOMAIN]
    
    return unload_ok


@callback
def get_hub(hass: HomeAssistant) -> SunPowerWSHub | None:
    return hass.data.get(DOMAIN)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
