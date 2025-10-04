# SunPower WebSocket (PVS) — Home Assistant Custom Integration

Version: v0.6.0

- **WebSocket-only** integration (DeviceList API removed due to 403 errors)
- Power in **kW** (PV, Load, Net, Grid Import, Grid Export)
- Energy in **kWh** (PV Lifetime, Home Load Lifetime, derived PV/Load, Grid Import/Export)
- Real-time updates via WebSocket connection
- UI configuration with options (WebSocket throttling, consumption measure)

## Install
1. Copy `custom_components/sunpower_ws/` into your HA config.
2. Restart HA.
3. Settings → Devices & Services → Add Integration → *SunPower WebSocket (PVS)*

## Energy Mapping
- Solar production → `sensor.sunpower_pv_lifetime_kwh`
- Home consumption → `sensor.sunpower_home_load_lifetime_kwh`
- Grid consumption → `sensor.sunpower_grid_import_energy_kwh`
- Return to grid → `sensor.sunpower_grid_export_energy_kwh`

## Changes in v0.6.0
- **Removed DeviceList API functionality** (was causing 403 errors)
- Removed per-inverter sensors
- Removed site lifetime energy sensor from DeviceList
- Simplified configuration (removed poll interval and DeviceList scan options)
- WebSocket-only operation for reliable real-time data
