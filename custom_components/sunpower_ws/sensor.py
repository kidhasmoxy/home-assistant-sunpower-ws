from __future__ import annotations

import time
from typing import Optional, Set

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfPower,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from . import DOMAIN, get_hub, SunPowerWSHub

# Primary power sensors in kW
KW_SENSORS = {
    "pv_kw":   ("sunpower_pv_power_kw",   "PV Power",   UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
    "load_kw": ("sunpower_home_load_kw",  "Home Load",  UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
    "net_kw":  ("sunpower_grid_net_kw",   "Grid Net",   UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
}

# Legacy W sensors (optionally enabled via Options)
W_SENSORS = {
    "solar_w": ("sunpower_pv_production", "PV Production", UnitOfPower.WATT, SensorDeviceClass.POWER),
    "load_w":  ("sunpower_home_load",     "Home Load",     UnitOfPower.WATT, SensorDeviceClass.POWER),
    "net_w":   ("sunpower_grid_net",      "Grid Net",      UnitOfPower.WATT, SensorDeviceClass.POWER),
}

# Lifetime energy direct from WS (kWh)
WS_LIFETIME_SENSORS = [
    ("pv_en_kwh", "sunpower_pv_lifetime_kwh",         "PV Lifetime Energy",         SensorStateClass.TOTAL_INCREASING),
    ("site_load_en_kwh", "sunpower_home_load_lifetime_kwh", "Home Load Lifetime Energy", SensorStateClass.TOTAL_INCREASING),
    ("net_en_kwh", "sunpower_grid_net_lifetime_kwh",  "Grid Net Lifetime Energy",   SensorStateClass.TOTAL),  # informational only
]

DERIVED_ENERGY = [
    ("pv_kw",   "sunpower_pv_energy_kwh",        "PV Energy (derived)"),
    ("load_kw", "sunpower_home_load_energy_kwh", "Home Load Energy (derived)"),
]


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    hub: SunPowerWSHub = get_hub(hass)
    entities: list[SensorEntity] = []

    # kW sensors (always on)
    for key, (uid, name, uom, device_class) in KW_SENSORS.items():
        entities.append(GenericLiveSensor(hub, key, uid, name, uom, device_class, enabled_by_default=True))

    # Legacy W sensors (toggle via Options)
    cfg = {**entry.data, **entry.options}
    enable_w = bool(cfg.get("enable_w_sensors", False))
    if enable_w:
        for key, (uid, name, uom, device_class) in W_SENSORS.items():
            entities.append(GenericLiveSensor(hub, key, uid, name, uom, device_class, enabled_by_default=True))

    # Grid import/export (kW) split
    entities.append(GridSplitPowerSensor(hub, "import"))
    entities.append(GridSplitPowerSensor(hub, "export"))

    # WS lifetime kWh
    for src_key, uid, name, state_class in WS_LIFETIME_SENSORS:
        entities.append(LifetimeFromWSSensor(hub, src_key, uid, name, state_class))

    # Derived energy (kWh) by integrating kW
    for src_key, uid, name in DERIVED_ENERGY:
        entities.append(IntegratingEnergySensor(hub, src_key, uid, name))

    # Grid import/export energy (kWh)
    entities.append(GridSplitEnergySensor(hub, "import"))
    entities.append(GridSplitEnergySensor(hub, "export"))

    # Site lifetime from DeviceList (optional)
    if getattr(hub, "enable_devicelist_scan", True):
        entities.append(SiteLifetimeEnergySensor(hub))

    async_add_entities(entities)

    # Dynamic per-inverter from DeviceList (optional)
    if getattr(hub, "enable_devicelist_scan", True):
        manager = InverterEntityManager(hub, async_add_entities)
        manager.start()


class GenericLiveSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, hub: SunPowerWSHub, key: str, unique_id: str, name: str, uom, device_class: Optional[SensorDeviceClass], enabled_by_default: bool):
        self._hub = hub
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{unique_id}"
        self._attr_native_unit_of_measurement = uom
        self._attr_device_class = device_class
        self._attr_native_value = None
        self._attr_entity_registry_enabled_default = enabled_by_default
        self._last_publish_ts: float = 0.0

    async def async_added_to_hass(self):
        @callback
        def _listener(data: dict):
            if self._key in data and isinstance(data[self._key], (int, float)):
                self._attr_native_value = float(data[self._key])
                now = time.time()
                if (not getattr(self._hub, "enable_ws_throttle", True)) or (
                    (now - self._last_publish_ts) >= getattr(self._hub, "ws_update_interval", 1)
                ):
                    self._last_publish_ts = now
                    self.async_write_ha_state()
        self._hub.add_listener(_listener)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "sunpower_pvs_ws")},
            name="SunPower PVS (WebSocket)",
            manufacturer="SunPower",
            model="PVS WebSocket Gateway",
            model_id="pvs_ws",
        )


class GridSplitPowerSensor(SensorEntity):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(self, hub: SunPowerWSHub, mode: str):
        assert mode in ("import", "export")
        self._hub = hub
        self._mode = mode
        self._attr_name = f"Grid {'Import' if mode=='import' else 'Export'} (kW)"
        self._attr_unique_id = f"{DOMAIN}_grid_{mode}_kw"
        self._attr_native_value = 0.0
        self._last_publish_ts: float = 0.0

    async def async_added_to_hass(self):
        @callback
        def _listener(data: dict):
            p = data.get("net_kw")
            if isinstance(p, (int, float)):
                if self._mode == "import":
                    self._attr_native_value = max(float(p), 0.0)
                else:
                    self._attr_native_value = max(-float(p), 0.0)
                now = time.time()
                if (not getattr(self._hub, "enable_ws_throttle", True)) or (
                    (now - self._last_publish_ts) >= getattr(self._hub, "ws_update_interval", 1)
                ):
                    self._last_publish_ts = now
                    self.async_write_ha_state()
        self._hub.add_listener(_listener)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "sunpower_pvs_ws")},
            name="SunPower PVS (WebSocket)",
            manufacturer="SunPower",
            model="PVS WebSocket Gateway",
            model_id="pvs_ws",
        )


class LifetimeFromWSSensor(SensorEntity):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, hub: SunPowerWSHub, key: str, unique_id: str, name: str, state_class: SensorStateClass):
        self._hub = hub
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{unique_id}"
        self._attr_state_class = state_class
        self._attr_native_value = None

    async def async_added_to_hass(self):
        @callback
        def _listener(data: dict):
            val = data.get(self._key)
            if isinstance(val, (int, float)):
                self._attr_native_value = float(val)
                self.async_write_ha_state()
        self._hub.add_listener(_listener)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "sunpower_pvs_ws")},
            name="SunPower PVS (WebSocket)",
            manufacturer="SunPower",
            model="PVS WebSocket Gateway",
            model_id="pvs_ws",
        )


class IntegratingEnergySensor(RestoreEntity, SensorEntity):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, hub: SunPowerWSHub, source_key: str, unique_id: str, name: str):
        self._hub = hub
        self._source_key = source_key
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{unique_id}"
        self._attr_native_value = None
        self._last_ts: Optional[float] = None
        self._last_kw: Optional[float] = None
        self._last_publish_ts: float = 0.0

    async def async_added_to_hass(self):
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                self._attr_native_value = float(last.state)
            except Exception:
                self._attr_native_value = 0.0
        else:
            self._attr_native_value = 0.0

        @callback
        def _listener(data: dict):
            now = time.time()
            p = data.get(self._source_key)
            if isinstance(p, (int, float)):
                if self._last_ts is not None and self._last_kw is not None:
                    dt = now - self._last_ts
                    if dt > 0:
                        kwh = (self._last_kw + float(p)) / 2.0 * (dt / 3600.0)
                        if kwh > 0:
                            self._attr_native_value = (self._attr_native_value or 0.0) + kwh
                            if (not getattr(self._hub, "enable_ws_throttle", True)) or (
                                (now - self._last_publish_ts) >= getattr(self._hub, "ws_update_interval", 1)
                            ):
                                self._last_publish_ts = now
                                self.async_write_ha_state()
                self._last_ts = now
                self._last_kw = float(p)
        self._hub.add_listener(_listener)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "sunpower_pvs_ws")},
            name="SunPower PVS (WebSocket)",
            manufacturer="SunPower",
            model="PVS WebSocket Gateway",
            model_id="pvs_ws",
        )


class GridSplitEnergySensor(IntegratingEnergySensor):
    def __init__(self, hub: SunPowerWSHub, mode: str):
        assert mode in ("import", "export")
        name = f"Grid {'Import' if mode=='import' else 'Export'} Energy"
        uid = f"grid_{mode}_energy_kwh"
        super().__init__(hub, "net_kw", uid, name)
        self._mode = mode

    async def async_added_to_hass(self):
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                self._attr_native_value = float(last.state)
            except Exception:
                self._attr_native_value = 0.0
        else:
            self._attr_native_value = 0.0

        self._last_ts = None
        self._last_kw = None

        @callback
        def _listener(data: dict):
            now = time.time()
            p = data.get("net_kw")
            if isinstance(p, (int, float)):
                val = float(p)
                if self._mode == "import":
                    val = max(val, 0.0)
                else:
                    val = max(-val, 0.0)
                if self._last_ts is not None and self._last_kw is not None:
                    dt = now - self._last_ts
                    if dt > 0:
                        kwh = (self._last_kw + val) / 2.0 * (dt / 3600.0)
                        if kwh > 0:
                            self._attr_native_value = (self._attr_native_value or 0.0) + kwh
                            if (not getattr(self._hub, "enable_ws_throttle", True)) or (
                                (now - self._last_publish_ts) >= getattr(self._hub, "ws_update_interval", 1)
                            ):
                                self._last_publish_ts = now
                                self.async_write_ha_state()
                self._last_ts = now
                self._last_kw = val
        self._hub.add_listener(_listener)


class SiteLifetimeEnergySensor(SensorEntity):
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_name = "Site Lifetime Energy"
    _attr_unique_id = f"{DOMAIN}_site_lifetime_energy_kwh"

    def __init__(self, hub: SunPowerWSHub):
        self._hub = hub
        self._attr_native_value = None

    async def async_added_to_hass(self):
        @callback
        def _dl_listener(data: dict):
            kwh = data.get("site_lifetime_kwh")
            if isinstance(kwh, (int, float)):
                self._attr_native_value = float(kwh)
                self.async_write_ha_state()
        self._hub.add_devicelist_listener(_dl_listener)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "sunpower_pvs_ws")},
            name="SunPower PVS (WebSocket)",
            manufacturer="SunPower",
            model="PVS WebSocket Gateway",
            model_id="pvs_ws",
        )


class InverterEntityManager:
    def __init__(self, hub: SunPowerWSHub, async_add_entities):
        self._hub = hub
        self._async_add = async_add_entities
        self._seen_ids: Set[str] = set()

    def start(self):
        @callback
        def _dl_listener(data: dict):
            inverters = data.get("inverters") or []
            new_entities = []
            for inv in inverters:
                iid = str(inv.get("id") or inv.get("name"))
                if not iid or iid in self._seen_ids:
                    continue
                self._seen_ids.add(iid)
                new_entities.extend(self._make_entities(inv))
            if new_entities:
                self._async_add(new_entities)
        self._hub.add_devicelist_listener(_dl_listener)

    def _make_entities(self, inv: dict) -> list[SensorEntity]:
        iid = str(inv.get("id") or inv.get("name"))
        name = str(inv.get("name") or f"Inverter {iid}")
        dev = DeviceInfo(
            identifiers={(DOMAIN, f"inverter_{iid}")},
            name=f"SunPower Inverter {iid}",
            manufacturer="SunPower",
            model="Microinverter",
            via_device=(DOMAIN, "sunpower_pvs_ws"),
        )
        ents: list[SensorEntity] = []

        ent = GenericNumberSensor(
            unique_id=f"{DOMAIN}_inv_{iid}_lifetime_kwh",
            name=f"{name} Lifetime Energy",
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            device_class=SensorDeviceClass.ENERGY,
            enabled_by_default=False,
        )
        ent.set_device_info(dev)
        def upd_life(data: dict, _iid=iid, e=ent):
            for i in data.get("inverters") or []:
                if str(i.get("id") or i.get("name")) == _iid:
                    val = i.get("lifetime_kwh")
                    if isinstance(val, (int, float)):
                        e.set_value(float(val))
        self._hub.add_devicelist_listener(upd_life)
        ents.append(ent)

        for key, unit, dc, name_suffix in [
            ("ac_w", UnitOfPower.WATT, SensorDeviceClass.POWER, " AC Power"),
            ("dc_v", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, " DC Voltage"),
            ("dc_a", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, " DC Current"),
            ("temp_c", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, " Temperature"),
        ]:
            ent2 = GenericNumberSensor(
                unique_id=f"{DOMAIN}_inv_{iid}_{key}",
                name=f"{name}{name_suffix}",
                unit=unit,
                device_class=dc,
                enabled_by_default=False,
            )
            ent2.set_device_info(dev)
            def make_updater(k=key, e=ent2, _iid=iid):
                def _upd(data: dict):
                    for i in data.get("inverters") or []:
                        if str(i.get("id") or i.get("name")) == _iid:
                            val = i.get(k)
                            if isinstance(val, (int, float)):
                                e.set_value(float(val))
                return _upd
            self._hub.add_devicelist_listener(make_updater())
            ents.append(ent2)

        return ents


class GenericNumberSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, unique_id: str, name: str, unit, device_class: Optional[SensorDeviceClass], enabled_by_default: bool = False):
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_native_value = None
        self._attr_entity_registry_enabled_default = enabled_by_default

    def set_device_info(self, dev: DeviceInfo):
        self._device_info = dev

    def set_value(self, v: float):
        self._attr_native_value = v
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return self._device_info
