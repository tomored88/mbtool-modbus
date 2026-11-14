## The 4 Modbus data areas
<!-- id: data-areas | category: Data and addressing | keywords: coil discrete input holding register 0x 1x 3x 4x area table data type -->
| Area | Size | Read/Write | Read with | Write with | PLC numbering |
|---|---|---|---|---|---|
| Coils | 1 bit | Read+Write | FC01 | FC05, FC15 | 0xxxx |
| Discrete Inputs | 1 bit | Read-only | FC02 | - | 1xxxx |
| Input Registers | 16 bit | Read-only | FC04 | - | 3xxxx |
| Holding Registers | 16 bit | Read+Write | FC03 | FC06, FC16, FC22, FC23 | 4xxxx |

The four areas are separate tables. Address 0 of the coils is not the same as address 0 of the holding registers.

## Address base: 0, 1 and PLC style (4xxxx)
<!-- id: address-bases | category: Data and addressing | keywords: address base 0 1 plc 40001 protocol off by one offset counting start -->
The number one cause of values being read one slot off is confusion about how addresses are counted.

- **Protocol address (0-based):** the value actually sent on the wire. The first register is **0**. This program uses it by default
- **1-based:** some manuals count the first register as **1** (but on the wire it is still 0. If you read the manual and use 1-based, you must subtract 1)
- **PLC style / 4xxxx:** a prefix is added according to the area, and counting starts from 1. For example the first holding register = **40001**, the first input register = **30001**. Remove the prefix and subtract 1 to get the protocol address

Example: the manual says "Voltage = 40011"
- PLC base: 40011 → protocol address **10**
- If the manual says "Voltage = register 11 (1-based)" → protocol address **10**
- If the manual says "Voltage = address 0x000A" → protocol address **10**

The "Address base" option is in the Poll panel below the Address field and shows `= protocol N`. In presets there is an `address_base` field ([[preset-schema]]) and a "Try other base" button that temporarily tries another base so you can compare with the device's display.

## Data types and display formats (Display)
<!-- id: display-formats | category: Data and addressing | keywords: display format unsigned signed hex binary long float double inverse 16 32 64 bit -->
One register is 16 bits. Larger values use several registers together.

| Format | Registers used | Range / description |
|---|---|---|
| Unsigned | 1 | 0 to 65535 |
| Signed | 1 | -32768 to 32767 (two's complement). Use for negative temperatures |
| Hex | 1 | Shown in base 16, for example 0x4348 |
| Binary | 1 | Shown bit by bit, to see status/flags |
| Long | 2 | 32-bit signed integer |
| Float | 2 | IEEE 754 32-bit floating point |
| Double | 4 | IEEE 754 64-bit floating point |
| Long/Float/Double Inverse | 2 / 2 / 4 | Same as above but with the word order swapped. See [[word-order]] |

If you are not sure which type it is, use the Value Inspector **F4** ([[inspector]]), which shows all formats at once, or open the Multi-type view ([[multi-view]]) to see every register in several formats as side-by-side columns.

## Byte and word order (word order, byte swap)
<!-- id: word-order | category: Data and addressing | keywords: word order byte swap abcd cdab badc dcba endian big little inverse float -->
Modbus only specifies that one register is sent high byte first. For a 32-bit value split across 2 registers, each manufacturer orders them differently. The bytes are named A B C D (A = most significant byte).

| Name | Order stored in register 1, register 2 | Notes |
|---|---|---|
| **ABCD** (big-endian) | AB, CD | The Modbus standard. Called "Float" |
| **CDAB** (word-swapped) | CD, AB | Very common. Called "Float Inverse" in the GUI |
| **BADC** (byte-swapped) | BA, DC | Rare. Presets use `byte_swap: true` |
| **DCBA** (little-endian) | DC, BA | Presets use `word_order: little` + `byte_swap: true` |

**How to find the right order:** read that pair of registers, then open **F4**. The order that gives a plausible value (for example 230.4 V, not 3.2e-38) is the answer. For example `0x4348 0xCCCD` is 200.8 in ABCD order.

## Scale, offset and units
<!-- id: scale-offset | category: Data and addressing | keywords: scale offset unit decimal divide multiply gain engineering value -->
Many devices store decimals as integers. For example a temperature of 25.3 °C is stored as 253.

**Actual value = raw value × scale + offset.** In a preset set `scale: 0.1`. When writing back, the program calculates in reverse. If the entered value is not evenly divisible by the scale, it is rejected.

Some models have a separate register that defines the decimal point position or a unit code (for example Omron, Autonics, flowmeters), so these are shown as raw values with the unit-code point separately in the preset.

## Strings, BCD and time
<!-- id: strings-bcd | category: Data and addressing | keywords: string ascii bcd time unix timestamp text name serial number -->
- **String:** 1 register holds 2 ASCII characters (high byte first). Presets use `type: string` and `length` as the number of registers
- **BCD:** each nibble (4 bits) is one decimal digit, for example 0x1234 = 1234
- **Unix time:** a 32-bit value counting seconds since 1970-01-01. The Value Inspector shows it when the value falls in the years 2000–2100
