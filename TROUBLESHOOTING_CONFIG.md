# Troubleshooting Configuration Not Saving

## Issue
Settings changes in the reconfigure flow are not being saved properly.

## What Was Fixed

### 1. **Checkbox Handling**
**Problem**: Unchecked checkboxes don't appear in `user_input` at all.

**Fix**: Changed from:
```python
"enable_ws_throttle": user_input.get("enable_ws_throttle", True)  # WRONG - defaults to True when unchecked!
```

To:
```python
"enable_ws_throttle": user_input.get("enable_ws_throttle", False)  # CORRECT - False when unchecked
```

### 2. **Data Updates**
**Fix**: Using direct field access for required fields, `.get()` only for checkboxes:
```python
data_updates = {
    "host": user_input["host"],  # Required field
    "port": user_input["port"],  # Required field
    "consumption_measure": user_input["consumption_measure"],  # Required field
    "ws_update_interval": user_input["ws_update_interval"],  # Required field
    "enable_ws_throttle": user_input.get("enable_ws_throttle", False),  # Checkbox
    "enable_w_sensors": user_input.get("enable_w_sensors", False),  # Checkbox
}
```

### 3. **Added Debug Logging**
Added logging to see what's being saved and loaded:
```python
_LOGGER.debug("Reconfigure: Updating config entry with data: %s", data_updates)
_LOGGER.debug("Entry data: %s", entry.data)
_LOGGER.debug("Entry options: %s", entry.options)
_LOGGER.info("Configuration loaded: host=%s, port=%s, ws_update_interval=%s, consumption_measure=%s, enable_ws_throttle=%s", ...)
```

## How to Test

1. **Enable Home Assistant Debug Logging** for this integration:
   ```yaml
   # configuration.yaml
   logger:
     default: info
     logs:
       custom_components.sunpower_ws: debug
   ```

2. **Restart Home Assistant** to apply logging changes

3. **Go to Settings → Devices & Services → SunPower WebSocket → Reconfigure**

4. **Make your changes**:
   - Uncheck "Enable WS throttle"
   - Change "WS update interval" to a different value (e.g., 10)
   - Click Submit

5. **Check the Home Assistant logs** for these messages:
   ```
   Reconfigure: Updating config entry with data: {'host': '...', 'enable_ws_throttle': False, 'ws_update_interval': 10, ...}
   Entry data: {'host': '...', 'enable_ws_throttle': False, 'ws_update_interval': 10, ...}
   Configuration loaded: host=..., enable_ws_throttle=False, ws_update_interval=10
   ```

## Expected Behavior

After reconfiguring:
- ✅ The integration should reload
- ✅ New settings should appear in the logs
- ✅ `enable_ws_throttle=False` should show in logs
- ✅ Your custom `ws_update_interval` should show in logs
- ✅ The hub should be created with the new settings

## If Settings Still Don't Save

Check the logs for:

1. **Is the reconfigure being called?**
   Look for: `"Reconfigure: Updating config entry with data:"`

2. **What data is being sent?**
   The log will show the exact dictionary being saved

3. **What data is being loaded?**
   Look for: `"Entry data:"` and `"Entry options:"`

4. **Is there a mismatch?**
   Compare what was saved vs what was loaded

## Common Issues

### Issue: Checkbox always shows as True
**Cause**: Using `.get("enable_ws_throttle", True)` instead of `.get("enable_ws_throttle", False)`

**Fix**: ✅ Already fixed in the code

### Issue: Settings revert after reload
**Cause**: Options flow might be overwriting data

**Solution**: Use reconfigure flow (not options flow) for changing settings

### Issue: Some fields save, others don't
**Cause**: Mixing `entry.data` and `entry.options`

**Solution**: Reconfigure updates `entry.data`, which takes precedence over `entry.options`

## Data vs Options

In Home Assistant:
- **`entry.data`**: Set during initial setup and reconfigure (immutable by user normally)
- **`entry.options`**: Set during options flow (user-configurable settings)

Our integration reads: `cfg = {**entry.data, **entry.options}`
- This means `entry.options` **overwrites** `entry.data` if both have the same key
- Reconfigure updates `entry.data`
- Options flow updates `entry.options`

**Recommendation**: Use **reconfigure flow** for all settings changes to ensure they're saved in `entry.data`.
