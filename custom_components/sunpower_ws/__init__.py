from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.config_entries import ConfigEntry

DOMAIN = "sunpower_ws"
_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "172.27.153.1"
DEFAULT_PORT = 9002
DEFAULT_POLL_INTERVAL = 300  # seconds
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

    def __init__(self, hass: HomeAssistant, host: str, port: int, poll_interval: int, enable_devicelist_scan: bool):
        self.hass = hass
        self.host = host
        self.port = port
        self.poll_interval = max(60, int(poll_interval or DEFAULT_POLL_INTERVAL))
        self.enable_devicelist_scan = enable_devicelist_scan
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._listeners: list[Callable[[dict], None]] = []
        self._devicelist_listeners: list[Callable[[dict], None]] = []
        self._task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()

    def add_listener(self, cb: Callable[[dict], None]) -> None:
        self._listeners.append(cb)

    def add_devicelist_listener(self, cb: Callable[[dict], None]) -> None:
        self._devicelist_listeners.append(cb)

    async def async_start(self) -> None:
        if self._task:
            return
        self._session = aiohttp.ClientSession()
        self._task = self.hass.async_create_task(self._runner())
        # Start lightweight DeviceList poller for lifetime/panels
        if self.enable_devicelist_scan:
            self._poll_task = self.hass.async_create_task(self._devicelist_poller())
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_hass_stop)

    async def async_stop(self) -> None:
        self._stopped.set()
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        if self._task:
            await self._task
        if self._poll_task:
            await self._poll_task

    async def _runner(self) -> None:
        url = f"ws://{self.host}:{self.port}{WS_PATH}"
        backoff = 1
        while not self._stopped.is_set():
            try:
                _LOGGER.info("Connecting to SunPower PVS WS at %s", url)
                assert self._session is not None
                async with self._session.ws_connect(url, heartbeat=30) as ws:
                    self._ws = ws
                    backoff = 1
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except Exception:
                                _LOGGER.debug("Non-JSON message: %s", msg.data)
                                continue
                            normalized = self._normalize_payload(data)
                            if normalized:
                                for cb in self._listeners:
                                    cb(normalized)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            _LOGGER.warning("WS error: %s", ws.exception())
                            break
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.warning("WS connect/loop error: %s", e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _devicelist_poller(self) -> None:
        url = f"http://{self.host}/cgi-bin/dl_cgi?Command=DeviceList"
        backoff = self.poll_interval
        while not self._stopped.is_set():
            try:
                assert self._session is not None
                async with self._session.get(url, timeout=20) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        parsed = self._parse_devicelist(data)
                        for cb in self._devicelist_listeners:
                            cb(parsed)
                        backoff = self.poll_interval
                    else:
                        _LOGGER.debug("DeviceList HTTP %s", resp.status)
                        backoff = min(max(60, backoff * 2), 3600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                _LOGGER.debug("DeviceList poll error: %s", e)
                backoff = min(max(60, backoff * 2), 3600)
            await asyncio.sleep(backoff)

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
            vw = kw_to_w(result["pv_kw"]);  result["solar_w"] = vw if vw is not None else result.get("solar_w")
        if "load_kw" in result and "load_w" not in result:
            vw = kw_to_w(result["load_kw"]); result["load_w"] = vw if vw is not None else result.get("load_w")
        if "net_kw" in result and "net_w" not in result:
            vw = kw_to_w(result["net_kw"]);  result["net_w"] = vw if vw is not None else result.get("net_w")

        # Compute net if missing
        if "net_kw" not in result:
            if "pv_kw" in result and "load_kw" in result:
                try:
                    result["net_kw"] = float(result["load_kw"]) - float(result["pv_kw"])
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
    host = entry.data.get("host", DEFAULT_HOST)
    port = entry.data.get("port", DEFAULT_PORT)
    poll_interval = entry.data.get("poll_interval", DEFAULT_POLL_INTERVAL)
    enable_devicelist_scan = entry.data.get("enable_devicelist_scan", True)
    hub = SunPowerWSHub(hass, host, port, poll_interval, enable_devicelist_scan)
    hass.data.setdefault(DOMAIN, {})["hub"] = hub
    await hub.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hub: SunPowerWSHub = hass.data.get(DOMAIN, {}).get("hub")
    if hub:
        await hub.async_stop()
    return unload_ok


@callback
def get_hub(hass: HomeAssistant) -> SunPowerWSHub | None:
    return hass.data.get(DOMAIN, {}).get("hub")
