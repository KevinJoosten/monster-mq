# PLC4X Complex Type Support

## Overview

This document describes the current state of complex data type support in MonsterMQ's PLC4X integration, particularly for protocols like Allen-Bradley EtherNet/IP.

## Current Implementation

MonsterMQ's PLC4X integration currently handles **simple data types only**:

- **Numbers** (Int, Float, Double, Long, etc.)
- **Booleans** (converted to 0/1)
- **Strings** (basic string types)

### Code Reference

The implementation in `Plc4xConnector.kt` uses:

```kotlin
val value = response.getObject(address.name)

val numericValue = when (rawValue) {
    is Number -> rawValue
    is Boolean -> if (rawValue) 1 else 0
    is String -> rawValue.toDoubleOrNull()
    else -> null
}
```

Values are extracted, optionally transformed (scaling/offset), and published to MQTT as JSON:

```json
{
  "value": 23.5,
  "timestamp": "2025-11-27T10:30:00Z",
  "device": "PLC1",
  "address": "Temperature"
}
```

## PLC4X Library Capabilities

The underlying **Apache PLC4X library DOES support complex types**:

- ✅ **Structs/UDTs** (User-Defined Types)
- ✅ **Arrays** (single and multi-dimensional)
- ✅ **Nested structures** (structs within structs)
- ✅ **Complex protocol-specific types**

PLC4X can read these types, but **MonsterMQ doesn't currently parse or publish them in a structured way**.

## Protocol-Specific Support

### Allen-Bradley EtherNet/IP

EtherNet/IP (Rockwell/Allen-Bradley) supports:

| Type | PLC4X Support | MonsterMQ Support | Notes |
|------|---------------|-------------------|-------|
| BOOL | ✅ | ✅ | Converted to 0/1 |
| SINT, INT, DINT, LINT | ✅ | ✅ | All numeric types work |
| REAL, LREAL | ✅ | ✅ | Floating-point types work |
| STRING | ✅ | ✅ | Basic string support |
| **UDTs/Structs** | ✅ | ❌ | Requires enhancement |
| **Arrays** | ✅ | ❌ | Requires enhancement |
| **Nested Structs** | ✅ | ❌ | Requires enhancement |

#### Known Issues: Byte Order

**Problem:** PLC4X 0.13.1 EtherNet/IP driver expects BIG_ENDIAN byte order by default. Some Allen-Bradley PLCs (CompactLogix/ControlLogix) may use LITTLE_ENDIAN, causing:

```
org.apache.plc4x.java.api.exceptions.PlcRuntimeException: 
The remote device doesn't seem to use BIG_ENDIAN byte order.
```

**Workarounds:**

1. **Try AB_ETHERNET protocol instead of ETHERNET_IP**
   - Connection string format: `ab-eth://host:port/backplane/slot`
   - Example: `ab-eth://10.1.25.87:44818/0/0` (backplane 0, slot 0)
   - Uses a different driver but requires backplane/slot parameters
   - Change protocol dropdown to "AB Ethernet (Allen-Bradley)"
   - **Note:** May still fail due to driver limitations

2. **Bridge via Node-RED (Recommended)**
   - If Node-RED can connect successfully, use it as MQTT bridge
   - Node-RED → MQTT → MonsterMQ
   - Most reliable solution for Allen-Bradley PLCs
   - See: `doc/plc4x-ethernet-ip-troubleshooting.md`

3. **Use connection string parameters** (if available in your PLC4X version)
   - Try: `eip://10.1.25.87:44818?byteOrder=LITTLE_ENDIAN`
   - Note: Parameter support varies by PLC4X version

3. **Bridge via Node-RED**
   - If Node-RED can connect successfully, use it as a bridge
   - Node-RED → MQTT → MonsterMQ
   - Temporary solution until PLC4X library is upgraded

4. **Upgrade PLC4X library**
   - Edit `broker/pom.xml` and change version:
   ```xml
   <plc4x.version>0.13.1</plc4x.version>  <!-- Current -->
   <!-- Try newer versions like 0.14.0, 0.15.0 if available -->
   ```
   - Rebuild with `mvn clean package`
   - Test compatibility with newer drivers

**Root Cause:** This is a PLC4X driver limitation, not a MonsterMQ issue. Node-RED likely uses a different Allen-Bradley library (node-red-contrib-cip-ethernet-ip) that handles endianness automatically.

### Other Protocols

- **Siemens S7**: Supports DBs, structs, arrays (same limitations apply)
- **Modbus**: Simple registers only (no complex types in protocol)
- **Beckhoff ADS**: Supports complex types (same limitations apply)

## Current Workaround

To work with complex data structures, **reference individual members explicitly**:

### Example: Reading a Motor Structure

Instead of reading the entire structure:
```
❌ Address: MyMotor
```

Reference individual members:
```
✅ Address: MyMotor.Speed
✅ Address: MyMotor.Current
✅ Address: MyMotor.Temperature
✅ Address: MyMotor.Status
```

### EtherNet/IP Address Syntax

For Allen-Bradley CLX/CompactLogix:
```
Tag: Program:MainProgram.MyMotor.Speed
Tag: Program:MainProgram.MyMotor.Current
```

For ControlLogix:
```
Tag: MyMotor.Speed
Tag: MyMotor[0].Speed  (for arrays)
```

## Future Enhancement Path

To support complex types, the following changes would be needed:

### 1. Detect Complex Types

```kotlin
when (val value = response.getObject(address.name)) {
    is PlcStruct -> handleStructValue(address, value)
    is PlcList -> handleArrayValue(address, value)
    is Number, String, Boolean -> handleSimpleValue(address, value)
    else -> logger.warning("Unsupported type: ${value.javaClass}")
}
```

### 2. Serialize Structs to JSON

```kotlin
private fun handleStructValue(address: Plc4xAddress, struct: PlcStruct) {
    val jsonObject = JsonObject()
    struct.keys.forEach { key ->
        val fieldValue = struct.getValue(key)
        jsonObject.put(key, convertToJson(fieldValue))
    }
    publishToMqtt(address, jsonObject)
}
```

### 3. Handle Arrays

```kotlin
private fun handleArrayValue(address: Plc4xAddress, array: PlcList) {
    val jsonArray = JsonArray()
    array.forEach { element ->
        jsonArray.add(convertToJson(element))
    }
    publishToMqtt(address, jsonArray)
}
```

### 4. Update GraphQL Schema

Add support for complex value types in the address configuration:
- Option to read entire structs
- Option to flatten structs to individual topics
- Option to publish as nested JSON

### 5. Frontend Support

- UI for browsing available tags/structures
- Tag browser for EtherNet/IP controllers
- Preview of struct members before adding

## Design Considerations

When implementing complex type support:

1. **Topic Structure**: How to map struct members to MQTT topics?
   - Option A: Single topic with JSON payload
   - Option B: Individual topics per member (current workaround)
   - Option C: Configurable (both options available)

2. **Performance**: Reading entire structs vs. individual members
   - Structs are more efficient (single read)
   - But may transfer unnecessary data

3. **Backwards Compatibility**: Ensure existing simple tag configs continue working

4. **Protocol Variations**: Different protocols have different struct formats
   - Need generic handling that works across protocols

## Related Files

- `broker/src/main/kotlin/devices/plc4x/Plc4xConnector.kt` - Main connector logic
- `broker/src/main/kotlin/stores/devices/Plc4xConfig.kt` - Configuration data classes
- `broker/src/main/kotlin/graphql/Plc4xClientConfigMutations.kt` - GraphQL mutations
- `broker/src/main/resources/schema-queries.graphqls` - GraphQL schema (Plc4xProtocol enum)

## References

- [Apache PLC4X Documentation](https://plc4x.apache.org/)
- [PLC4X EtherNet/IP Driver](https://plc4x.apache.org/users/protocols/eip.html)
- [Allen-Bradley Tag Addressing](https://literature.rockwellautomation.com/)

## Status

**Current Status**: Simple types only  
**Priority**: Low (workaround available)  
**Complexity**: Medium (requires careful design for topic mapping)

---

*Last Updated: November 27, 2025*
