# SunPower WebSocket (PVS) — Home Assistant Custom Integration

Version: v0.5.1

- Power in **kW** (PV, Load, Net, Grid Import, Grid Export)
- Energy in **kWh** (PV Lifetime, Home Load Lifetime, derived PV/Load, Grid Import/Export)
- Site/per-inverter lifetime via low-rate DeviceList polling
- UI configuration with options (poll interval, enable legacy W sensors)

## Install
1. Copy `custom_components/sunpower_ws/` into your HA config.
2. Restart HA.
3. Settings → Devices & Services → Add Integration → *SunPower WebSocket (PVS)*

## Energy Mapping
- Solar production → `sensor.sunpower_pv_lifetime_kwh`
- Home consumption → `sensor.sunpower_home_load_lifetime_kwh`
- Grid consumption → `sensor.sunpower_grid_import_energy_kwh`
- Return to grid → `sensor.sunpower_grid_export_energy_kwh`
