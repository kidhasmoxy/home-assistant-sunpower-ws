# Comprehensive Home Assistant Integration Validation

## Full Codebase Review Against HA Documentation

---

## Issues Found and Fixes Required

### ❌ **CRITICAL ISSUE 1: Entity Naming Convention**

**Documentation**: [Adopting a new way to name entities](https://developers.home-assistant.io/blog/2022/07/10/entity_naming/)

**Problem**: Entities are using old naming convention with device name included in entity name.

**Current Implementation**:
```python
self._attr_name = "PV Power"  # Wrong: Should not include device name
self._attr_name = "WebSocket Connection"  # Wrong
```

**Required Fix**: Use `has_entity_name = True` and set name to describe the entity, not the device.

**Correct Pattern**:
```python
_attr_has_entity_name = True
_attr_name = "Power"  # Device name will be prepended automatically
```

---

### ❌ **CRITICAL ISSUE 2: Deprecated asyncio.timeout**

**Documentation**: [Working with Async](https://developers.home-assistant.io/docs/asyncio_working_with_async/)

**Problem**: Using `asyncio.timeout()` which may not be available in all Python versions.

**Current Code** (line 172, 257):
```python
async with asyncio.timeout(timeout_duration):
    self._ws = await self._session.ws_connect(url, heartbeat=30)
```

**Required Fix**: Use `async_timeout` from Home Assistant or handle TimeoutError properly.

---

### ❌ **ISSUE 3: Missing State Class on Power Sensors**

**Documentation**: [Sensor entity](https://developers.home-assistant.io/docs/core/entity/sensor/)

**Problem**: Power sensors should have `state_class = MEASUREMENT` for statistics.

**Current**: No state_class defined on GenericLiveSensor

**Required**: Add `_attr_state_class = SensorStateClass.MEASUREMENT` for power sensors

---

### ⚠️ **ISSUE 4: Connection Status Sensor Missing Device Class**

**Problem**: Connection status sensor has no device_class

**Current**:
```python
class ConnectionStatusSensor(SensorEntity):
    _attr_icon = "mdi:wifi"
```

**Recommendation**: Consider using `SensorDeviceClass.ENUM` with options or binary_sensor instead

---

### ⚠️ **ISSUE 5: Inline time.time() Import**

**Problem**: Importing `time` inside methods instead of at module level

**Current** (line 142, 158):
```python
import time
now = time.time()
```

**Fix**: Move import to top of file

---

### ⚠️ **ISSUE 6: Extra State Attributes on Connection Sensor**

**Documentation**: [Sensor entity](https://developers.home-assistant.io/docs/core/entity/sensor/)

> "Instead of adding extra_state_attributes for a sensor entity, create an additional sensor entity."

**Current**: ConnectionStatusSensor uses `extra_state_attributes`

**Recommendation**: Consider creating separate diagnostic sensors for connection_count, last_data_received, etc.

---

### ✅ **CORRECT: State Classes on Energy Sensors**

**Good**: Properly using `SensorStateClass.TOTAL_INCREASING` for energy sensors

---

### ✅ **CORRECT: Device Classes**

**Good**: Properly using `SensorDeviceClass.POWER`, `SensorDeviceClass.ENERGY`, etc.

---

### ✅ **CORRECT: Unique IDs**

**Good**: All entities have unique_id set

---

### ✅ **CORRECT: RestoreEntity**

**Good**: IntegratingEnergySensor properly extends RestoreEntity

---

## Priority Fixes

### **HIGH PRIORITY**

1. ✅ **Fix Entity Naming** - Add `has_entity_name = True` to all entities
2. ✅ **Fix asyncio.timeout** - Use proper timeout handling
3. ✅ **Add State Class to Power Sensors** - Enable statistics

### **MEDIUM PRIORITY**

4. ✅ **Move time import** - Clean up imports
5. ⚠️ **Review extra_state_attributes** - Consider separate sensors

### **LOW PRIORITY**

6. ⚠️ **Connection sensor device class** - Improve typing

---

## Detailed Fix Plan

### Fix 1: Entity Naming (All Sensors)

**Pattern to apply**:
```python
class GenericLiveSensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True  # ADD THIS
    
    def __init__(self, hub, key, unique_id, name, uom, device_class, enabled_by_default):
        # name should be just the entity name, not "SunPower PV Power"
        self._attr_name = name  # e.g., "Power" not "PV Power"
```

### Fix 2: asyncio.timeout

**Replace**:
```python
async with asyncio.timeout(timeout_duration):
    self._ws = await self._session.ws_connect(url, heartbeat=30)
```

**With**:
```python
try:
    self._ws = await asyncio.wait_for(
        self._session.ws_connect(url, heartbeat=30),
        timeout=timeout_duration
    )
except asyncio.TimeoutError:
    _LOGGER.warning("Timeout connecting to WebSocket")
    raise
```

### Fix 3: State Class for Power Sensors

**Add to GenericLiveSensor**:
```python
def __init__(self, hub, key, unique_id, name, uom, device_class, enabled_by_default, state_class=None):
    self._attr_state_class = state_class
```

**Update sensor creation**:
```python
for key, (uid, name, uom, device_class) in KW_SENSORS.items():
    entities.append(GenericLiveSensor(
        hub, key, uid, name, uom, device_class, 
        enabled_by_default=True,
        state_class=SensorStateClass.MEASUREMENT  # ADD THIS
    ))
```

### Fix 4: Move time import

**At top of __init__.py**:
```python
import time
```

**Remove inline imports**

---

## Summary

| Category | Status | Count |
|----------|--------|-------|
| Critical Issues | ❌ | 2 |
| Important Issues | ⚠️ | 3 |
| Already Correct | ✅ | 5 |

**Next Steps**: Apply all HIGH PRIORITY fixes immediately.
