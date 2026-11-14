## FC01 Read Coils
<!-- id: fc01 | category: Function codes | keywords: fc01 01 read coils coil bit 0x output digital relay -->
Reads writable output bits (coils, 0xxxx area), for example relay states.

- **Quantity:** 1–**2000** bits per request
- Result: the 0/1 value of each bit
- Reading beyond the range the device has returns exception 02 ([[exc02]])
- Coils can be written with [[fc05]] and [[fc15]]

## FC02 Read Discrete Inputs
<!-- id: fc02 | category: Function codes | keywords: fc02 02 discrete inputs input 1x status digital -->
Reads read-only input bits (1xxxx area), for example switches and limit switches.

- **Quantity:** 1–**2000** bits per request
- Cannot be written (there is no write function code for discrete inputs)

## FC03 Read Holding Registers
<!-- id: fc03 | category: Function codes | keywords: fc03 03 holding registers 4x register setpoint read -->
Reads 16-bit read/write registers (4xxxx area). This is the most commonly used function. It holds measurements, setpoints and parameters.

- **Quantity:** 1–**125** registers per request (a 32-bit/float value uses 2 registers, a double uses 4)
- Some devices limit the count per request to less than this (for example Eastron 40 values, Keller 4 values). Presets use the `max_block` field ([[preset-schema]])
- Can be written with [[fc06]], [[fc16]], [[fc22]], [[fc23]]

## FC04 Read Input Registers
<!-- id: fc04 | category: Function codes | keywords: fc04 04 input registers 3x register measurement sensor read-only -->
Reads 16-bit read-only registers (3xxxx area). Manufacturers often put the actual measurements here, for example voltage, current and temperature.

- **Quantity:** 1–**125** registers per request
- If the device does not respond to FC03, try FC04. Some manufacturers use them differently than expected, so always check the manual
- Cannot be written

## FC05 Write Single Coil
<!-- id: fc05 | category: Function codes | keywords: fc05 05 write single coil on off relay ff00 -->
Sets a single coil to ON or OFF.

- Only two values are valid: **0xFF00 = ON**, **0x0000 = OFF**. For any other value the device must respond with exception 03
- The response is an echo of the request
- **Caution:** if the coil controls a real relay or machine, its state changes immediately

## FC06 Write Single Register
<!-- id: fc06 | category: Function codes | keywords: fc06 06 write single register preset setpoint -->
Writes a 16-bit value to one holding register.

- The response is an echo of the request
- 32-bit values (Long, Float) must be written to multiple registers. Use [[fc16]] rather than FC06 twice, because the device may read a half-updated value in the middle of the write

## FC15 Write Multiple Coils
<!-- id: fc15 | category: Function codes | keywords: fc15 0f 15 write multiple coils bits -->
Writes several consecutive coils in a single request.

- **Quantity:** 1–**1968** bits
- Bits are packed into bytes, with the first bit in bit 0 of the first byte
- Response: the starting address and the quantity written

## FC16 Write Multiple Registers
<!-- id: fc16 | category: Function codes | keywords: fc16 10 16 write multiple registers float long -->
Writes several consecutive holding registers in a single request. Used for writing 32/64-bit values.

- **Quantity:** 1–**123** registers
- Words must be ordered as the device specifies. See [[word-order]]
- Response: the starting address and the quantity written

## FC22 Mask Write Register
<!-- id: fc22 | category: Function codes | keywords: fc22 16 22 mask write register and or bit modify bits -->
Changes only some bits of a holding register without touching the other bits.

Result = **(current value AND and_mask) OR (or_mask AND (NOT and_mask))**

Example: original value 0x12, and_mask 0xF2, or_mask 0x25 gives 0x17. Many models do not support it and will respond with exception 01

## FC23 Read/Write Multiple Registers
<!-- id: fc23 | category: Function codes | keywords: fc23 17 23 read write multiple registers read/write simultaneous -->
Writes registers first, then reads back in a single request (the write is always performed before the read).

- Reads up to **125** registers and writes up to **121** registers
- Use it when you want to read back a value you just commanded, with no gap between the two actions

## FC43/14 Read Device Identification (ask which model the device is)
<!-- id: fc43 | category: Function codes | keywords: fc43 2b 43 14 mei device identification vendor product identify model manufacturer -->
A supporting device responds with the vendor name (VendorName), product code (ProductCode), version (MajorMinorRevision), and possibly the URL, product name and model name.

The program uses it in the [[scanner]] to suggest a preset that matches the device. Many devices do not support it and respond with exception 01, which is not a problem.

## FC17 Report Slave ID
<!-- id: fc17 | category: Function codes | keywords: fc17 11 17 report slave id device run indicator -->
An older function that makes the device report information about itself (the data format differs by manufacturer). The program shows it as hex and ASCII text for you to interpret.

## Summary table of limits for each function
<!-- id: limits | category: Function codes | keywords: limit maximum quantity max 125 2000 123 1968 121 253 -->
| Function | What it does | Maximum per request |
|---|---|---|
| FC01 | Read coils | 2000 bits |
| FC02 | Read discrete inputs | 2000 bits |
| FC03 | Read holding registers | 125 |
| FC04 | Read input registers | 125 |
| FC05 | Write single coil | 1 |
| FC06 | Write single register | 1 |
| FC15 | Write multiple coils | 1968 bits |
| FC16 | Write multiple registers | 123 |
| FC22 | Mask write | 1 |
| FC23 | Read/write | read 125, write 121 |

The maximum PDU size is 253 bytes per the standard (the numbers above derive from this). Requesting more returns exception 03.
