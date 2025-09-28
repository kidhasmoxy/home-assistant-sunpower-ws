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
DEFAULT_POLL_INTERVAL = 300  # seconds
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
    """WebSocket client + optional low-rate DeviceList poller."""

    def __init__(self, hass: HomeAssistant, host: str, port: int, poll_interval: int, enable_devicelist_scan: bool, ws_update_interval: int, consumption_measure: str, enable_ws_throttle: bool):
        self.hass = hass
        self.host = host
        self.port = port
        self.poll_interval = max(60, int(poll_interval or DEFAULT_POLL_INTERVAL))
        self.enable_devicelist_scan = enable_devicelist_scan
        self.ws_update_interval = max(1, int(ws_update_interval or DEFAULT_WS_UPDATE_INTERVAL))
        self.consumption_measure = consumption_measure or "house_usage"
        self.enable_ws_throttle = bool(enable_ws_throttle)
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._listeners: list[Callable[[dict], None]] = []
        self._devicelist_listeners: list[Callable[[dict], None]] = []
        self._task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()
        self._connected = False
        self._last_connection_attempt = 0

    def add_listener(self, cb: Callable[[dict], None]) -> None:
        self._listeners.append(cb)

    def add_devicelist_listener(self, cb: Callable[[dict], None]) -> None:
        self._devicelist_listeners.append(cb)

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
            
        # Start the WebSocket runner task (non-blocking)
        self._task = self.hass.async_create_task(
            self._runner(),
            name="SunPowerWS WebSocket Runner"
        )
        
        # Start the DeviceList poller if enabled (non-blocking)
        if self.enable_devicelist_scan:
            _LOGGER.debug("Starting DeviceList poller with interval %s seconds", self.poll_interval)
            self._poll_task = self.hass.async_create_task(
                self._devicelist_poller(),
                name="SunPowerWS DeviceList Poller"
            )
            
        # Register stop handler
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_hass_stop)
        
        _LOGGER.debug("SunPowerWSHub startup initiated (tasks started)")

    async def async_stop(self) -> None:
        """Stop the hub and clean up resources."""
        _LOGGER.debug("Stopping SunPowerWSHub")
        self._stopped.set()
        
        # Close the WebSocket connection
        if self._ws:
            try:
                if not self._ws.closed:
                    await self._ws.close()
                self._ws = None
            except Exception as e:
                _LOGGER.warning("Error closing WebSocket connection: %s", e)
        
        # Cancel and wait for tasks to complete
        for task_name, task in [("WebSocket", self._task), ("DeviceList poller", self._poll_task)]:
            if task:
                try:
                    if not task.done():
                        task.cancel()
                        try:
                            await asyncio.wait_for(task, timeout=5)
                        except asyncio.TimeoutError:
                            _LOGGER.warning("%s task did not complete in time", task_name)
                        except asyncio.CancelledError:
                            pass
                except Exception as e:
                    _LOGGER.warning("Error cancelling %s task: %s", task_name, e)
        
        # Close the HTTP session
        if self._session:
            try:
                await self._session.close()
                self._session = None
            except Exception as e:
                _LOGGER.warning("Error closing HTTP session: %s", e)
                
        # Reset connection status
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

    async def _devicelist_poller(self) -> None:
        """Poll the DeviceList API periodically for inverter data."""
        url = f"http://{self.host}/cgi-bin/dl_cgi?Command=DeviceList"
        backoff = self.poll_interval
        poll_count = 0
        success_count = 0
        error_count = 0
        
        _LOGGER.debug("Starting DeviceList poller with interval %s seconds", self.poll_interval)
        
        while not self._stopped.is_set():
            poll_count += 1
            try:
                if self._session is None:
                    _LOGGER.warning("HTTP session is None, creating new session")
                    self._session = aiohttp.ClientSession()
                    
                try:
                    # Use asyncio.timeout for the entire operation
                    async with asyncio.timeout(20):
                        try:
                            async with self._session.get(url, timeout=15) as resp:
                                if resp.status == 200:
                                    data = await resp.json(content_type=None)
                                    parsed = self._parse_devicelist(data)
                                    
                                    # Notify listeners
                                    for cb in self._devicelist_listeners:
                                        try:
                                            cb(parsed)
                                        except Exception as cb_error:
                                            _LOGGER.error("Error in DeviceList callback: %s", cb_error)
                                    
                                    # Reset backoff on success
                                    backoff = self.poll_interval
                                    success_count += 1
                                    
                                    # Log success periodically
                                    if success_count % 10 == 0:
                                        _LOGGER.debug("DeviceList poll success count: %s", success_count)
                                else:
                                    _LOGGER.warning("DeviceList HTTP error: %s", resp.status)
                                    backoff = min(max(60, backoff * 2), 3600)
                                    error_count += 1
                        except aiohttp.ClientError as client_error:
                            _LOGGER.warning("DeviceList HTTP client error: %s", client_error)
                            backoff = min(max(60, backoff * 2), 3600)
                            error_count += 1
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout fetching DeviceList from %s", url)
                    backoff = min(max(60, backoff * 2), 3600)
                    error_count += 1
            except asyncio.CancelledError:
                _LOGGER.info("DeviceList poller cancelled")
                break
            except Exception as e:
                _LOGGER.error("DeviceList poll error: %s", e)
                backoff = min(max(60, backoff * 2), 3600)
                error_count += 1
                
            # Log error stats periodically
            if error_count > 0 and error_count % 5 == 0:
                _LOGGER.warning("DeviceList poll errors: %s/%s", error_count, poll_count)
                
            # Wait before next poll
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                _LOGGER.info("DeviceList poller sleep cancelled")
                break
                
        _LOGGER.info("DeviceList poller stopped after %s polls (%s successful, %s errors)",
                     poll_count, success_count, error_count)

    def _parse_devicelist(self, data: dict) -> dict:
        result: dict[str, Any] = {"site_lifetime_kwh": None, "inverters": []}
        devices = []
        if isinstance(data, dict):
            if "devices" in data and isinstance(data["devices"], list):
                devices = data["devices"]
            elif "DeviceList" in data and isinstance(data["DeviceList"], dict):
                if isinstance(data["DeviceList"].get("devices"), list):
                    devices = data["DeviceList"]["devices"]

        total_kwh = 0.0
        any_kwh = False
        for d in devices or []:
            inv = self._extract_inverter_metrics(d)
            if inv:
                result["inverters"].append(inv)
                if inv.get("lifetime_kwh") is not None:
                    total_kwh += inv["lifetime_kwh"]
                    any_kwh = True

        if any_kwh:
            result["site_lifetime_kwh"] = total_kwh
        return result

    def _extract_inverter_metrics(self, dev: dict):
        if not isinstance(dev, dict):
            return None
        name = str(dev.get("name") or dev.get("DeviceName") or dev.get("model") or dev.get("Model") or "device")
        dtype = str(dev.get("type") or dev.get("DeviceType") or "").lower()
        serial = str(dev.get("serial") or dev.get("SerialNumber") or dev.get("id") or dev.get("DeviceID") or name)
        looks_inverter = ("invert" in dtype) or ("invert" in name.lower()) or ("micro" in name.lower())

        lifetime_kwh = None
        def scan_for_lifetime(d: dict):
            nonlocal lifetime_kwh
            for k, v in d.items():
                if isinstance(v, dict):
                    scan_for_lifetime(v)
                else:
                    kl = str(k).lower()
                    if ("lifetime" in kl) and ("energy" in kl or "ac" in kl):
                        try:
                            val = float(v)
                        except Exception:
                            continue
                        if "wh" in kl and val > 1000:
                            lifetime_kwh = val / 1000.0
                        elif "kwh" in kl:
                            lifetime_kwh = val
                        else:
                            lifetime_kwh = val / 1000.0 if val > 5000 else val
        scan_for_lifetime(dev)

        def _first_num(dct, keys):
            for k in keys:
                v = dct.get(k)
                if isinstance(v, (int, float)):
                    return float(v)
            return None

        ac_w = _first_num(dev, ["ac_w", "ac_power", "acwatts", "ac_watts", "acp"])
        dc_v = _first_num(dev, ["dc_v", "dc_voltage", "dcv"])
        dc_a = _first_num(dev, ["dc_a", "dc_current", "dca"])
        temp_c = _first_num(dev, ["temp_c", "temperature_c", "temperature"])

        if lifetime_kwh is None and not looks_inverter:
            return None

        return {
            "id": serial or name,
            "name": name,
            "lifetime_kwh": lifetime_kwh,
            "ac_w": ac_w,
            "dc_v": dc_v,
            "dc_a": dc_a,
            "temp_c": temp_c,
        }

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
        self.hass.async_create_task(self.async_stop())


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
        poll_interval = cfg.get("poll_interval", DEFAULT_POLL_INTERVAL)
        enable_devicelist_scan = cfg.get("enable_devicelist_scan", True)
        ws_update_interval = cfg.get("ws_update_interval", DEFAULT_WS_UPDATE_INTERVAL)
        consumption_measure = cfg.get("consumption_measure", "house_usage")
        enable_ws_throttle = cfg.get("enable_ws_throttle", True)
        
        _LOGGER.debug(
            "Configuration: host=%s, port=%s, poll_interval=%s, enable_devicelist_scan=%s, "
            "ws_update_interval=%s, consumption_measure=%s, enable_ws_throttle=%s",
            host, port, poll_interval, enable_devicelist_scan, 
            ws_update_interval, consumption_measure, enable_ws_throttle
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
            hass, host, port, poll_interval, enable_devicelist_scan, 
            ws_update_interval, consumption_measure, enable_ws_throttle
        )
        hass.data.setdefault(DOMAIN, {})["hub"] = hub
        
        # Start the hub in background to ensure fast bootup
        try:
            # Create background task without awaiting to prevent bootstrap blocking
            hass.async_create_task(
                hub.async_start(),
                name="SunPowerWS Hub Background Startup"
            )
            _LOGGER.info("SunPower WebSocket hub background startup scheduled")
        except Exception as ex:
            _LOGGER.warning("Error scheduling SunPower WebSocket hub startup: %s", ex)
        
        # Set up the sensor platform
        _LOGGER.debug("Setting up sensor platform")
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        
        # Reload integration when options change
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
        
        _LOGGER.info("SunPower WebSocket integration setup complete for entry: %s", entry.entry_id)
        return True
        
    except Exception as ex:
        _LOGGER.exception("Error setting up SunPower WebSocket integration: %s", ex)
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hub: SunPowerWSHub = hass.data.get(DOMAIN, {}).get("hub")
    if hub:
        await hub.async_stop()
    return unload_ok


@callback
def get_hub(hass: HomeAssistant) -> SunPowerWSHub | None:
    return hass.data.get(DOMAIN, {}).get("hub")


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
