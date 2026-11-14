## All exception codes (summary)
<!-- id: exceptions | category: Exceptions and errors | keywords: exception code error illegal 01 02 03 04 05 06 08 0a 0b table -->
An exception is the response a device returns when it received the request but cannot carry it out (the function code in the response is increased by 0x80). It means **the device is alive and the line settings are correct**. The problem is in the request itself.

| Code | Name | Short meaning |
|---|---|---|
| 01 | Illegal Function | This function is not supported [[exc01]] |
| 02 | Illegal Data Address | Address is wrong or out of range [[exc02]] |
| 03 | Illegal Data Value | Quantity or value is wrong [[exc03]] |
| 04 | Slave Device Failure | Internal error in the device [[exc04]] |
| 05 | Acknowledge | Request accepted, still processing (long operation) |
| 06 | Slave Device Busy | Busy, try again later [[exc04]] |
| 08 | Memory Parity Error | Device memory error |
| 0A (10) | Gateway Path Unavailable | The gateway has no path to the target device |
| 0B (11) | Gateway Target Device Failed to Respond | The gateway queried the target device and got no response [[trouble-no-response]] |

## Exception 01: Illegal Function
<!-- id: exc01 | category: Exceptions and errors | keywords: exception 01 illegal function unsupported -->
The device does not support the requested function code.

- Try swapping FC03 and FC04 (reading holding versus input registers)
- Many devices do not support FC22, FC23 and FC43. This is normal
- If writing fails, the area may be read-only

## Exception 02: Illegal Data Address
<!-- id: exc02 | category: Exceptions and errors | keywords: exception 02 illegal data address wrong off by one out of range -->
The requested address (or any address within the requested range) does not exist in the device. This is the most common error when building a register map.

Common causes:
1. **Address off by 1.** Mixing up 0-based and 1-based address counting. See [[address-bases]]. Try "Try other base" in the preset
2. **The requested range straddles the edge of the table.** For example, requesting 10 values when only 6 remain in the table. Reduce the quantity or use the [[scanner]] to find the exact boundaries
3. Using the wrong FC for the area (the value is in the input registers but you requested holding registers)
4. A 32-bit value is at the last address and its other half lies beyond the table

## Exception 03: Illegal Data Value
<!-- id: exc03 | category: Exceptions and errors | keywords: exception 03 illegal data value quantity count wrong 0xff00 -->
A value in the request is not accepted.

- Quantity is 0, exceeds the function's limit ([[limits]]), or exceeds what that device model accepts. Reduce Quantity or set `max_block` in the preset
- FC05 must use only 0xFF00 or 0x0000
- The value written is outside the range the device accepts
- The byte count in a multi-value request does not match the quantity (this program already builds it correctly)

## Exceptions 04 and 06: device failure or busy
<!-- id: exc04 | category: Exceptions and errors | keywords: exception 04 06 busy failure slave device internal -->
- **04:** an internal error prevented the request from completing. Try again. If it persists, check the device's own status/error code
- **06:** the device is busy with something else (for example writing to EEPROM). Reduce the polling rate and try again

## Response timeout (no response)
<!-- id: err-timeout | category: Exceptions and errors | keywords: timeout no response silent -->
A request was sent and nothing came back within the Timeout. This is not an exception from the device. See [[trouble-no-response]]. The main causes are a wrong Slave ID, mismatched baud/parity, wrong wiring, or the device not being powered.

## CRC error / LRC error
<!-- id: err-crc | category: Exceptions and errors | keywords: crc lrc error corrupted data checksum noise interference -->
Data was received but failed the checksum (RTU uses CRC-16, ASCII uses LRC). This means there is a signal, but the data is corrupted.

Causes: mismatched baud or parity (most common), A/B wires swapped, no 120 Ω termination, a long cable or electrical noise, two masters talking at the same time. See [[trouble-crc]]

## Other error messages shown by the program
<!-- id: err-other | category: Exceptions and errors | keywords: error message unexpected unit echo malformed mbap busy does not exist -->
| Message | Meaning |
|---|---|
| unexpected slave id / unit id in response | The response came from a different Slave ID than the one queried: two devices share the same ID, or a gateway changed the ID |
| response does not echo request | The FC05/06/15/16/22 response does not match what was sent |
| malformed response (byte count) | The byte count in the response is inconsistent with the quantity requested |
| MBAP header invalid; connection closed | The TCP header is invalid. The program closes the connection so that data does not become misaligned |
| COMx is busy | The port is open in another program [[trouble-port-busy]] |
| COMx does not exist | This port does not exist. Check the adapter and press Refresh |
