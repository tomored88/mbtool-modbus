## Input fields and allowed values (reference table)
<!-- id: values-fields | category: Values reference | keywords: field, allowed values, range, default, reference table -->
| Field | Allowed values | Default | Notes |
|---|---|---|---|
| Slave ID | serial: 1–247 (0 = broadcast for writes), TCP: 0–255 | 1 | [[slave-id]] |
| Function (read) | 01, 02, 03, 04 | 03 | [[data-areas]] |
| Address | 0–65535 (depending on the selected base) | 0 | [[address-bases]] |
| Quantity | FC01/02: 1–2000, FC03/04: 1–125 | 10 | [[limits]] |
| Scan Rate | 20 milliseconds or more | 1000 | Interval between poll cycles |
| Timeout | 50–60000 milliseconds | 1000 | Time to wait for a response |
| Port (TCP) | 1–65535 | 502 | [[connect-tcp]] |
| Baud rate | 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200 (other values can be typed) | 9600 | [[serial-settings]] |
| Data bits | 7 (ASCII), 8 | 8 | RTU requires 8 |
| Parity | None (N), Even (E), Odd (O) | None | |
| Stop bits | 1, 2 | 1 | |
| Display | Unsigned, Signed, Hex, Binary, Long, Long Inverse, Float, Float Inverse, Double, Double Inverse | Unsigned | [[display-formats]] |
| Address base | 0, 1, PLC (4xxxx) | 0 | [[address-bases]] |
| Rows per column | Auto, 10, 20, 30, 40 | Auto | [[poll-panel]] |

## Function code and exception code values (numeric table)
<!-- id: values-codes | category: Values reference | keywords: function code, exception code, table, number base, hex, decimal, 0x83, 0x80, code, number -->
| Function | Decimal | Hex | In an exception response |
|---|---|---|---|
| Read Coils | 1 | 0x01 | 0x81 |
| Read Discrete Inputs | 2 | 0x02 | 0x82 |
| Read Holding Registers | 3 | 0x03 | 0x83 |
| Read Input Registers | 4 | 0x04 | 0x84 |
| Write Single Coil | 5 | 0x05 | 0x85 |
| Write Single Register | 6 | 0x06 | 0x86 |
| Write Multiple Coils | 15 | 0x0F | 0x8F |
| Write Multiple Registers | 16 | 0x10 | 0x90 |
| Report Slave ID | 17 | 0x11 | 0x91 |
| Mask Write Register | 22 | 0x16 | 0x96 |
| Read/Write Multiple Registers | 23 | 0x17 | 0x97 |
| Read Device Identification (MEI) | 43 | 0x2B | 0xAB |

When a device rejects a request, it replies with the function code plus **0x80**, followed by the exception code ([[exceptions]]). For example `83 02` = read holding registers at an invalid address.

## Values for writing a coil and value conversion
<!-- id: values-coil | category: Values reference | keywords: coil, on, off, ff00, 0000, write coil, value -->
| Desired | Value on the wire (FC05) | In the Write field |
|---|---|---|
| ON | 0xFF00 | 1 / true |
| OFF | 0x0000 | 0 / false |

Any other value (e.g. 0x0001) violates the standard, and the device must reply with exception 03 ([[fc05]]).

## Alert conditions and log time formats
<!-- id: values-alerts-log | category: Values reference | keywords: alert, condition, above, below, outside, equals, changed, no data, stuck, time format, iso, unix, excel serial -->
**Alert conditions** ([[alerts]])

| Condition | Alert when |
|---|---|
| above | value > threshold |
| below | value < threshold |
| outside | value is outside the range [low, high] |
| equals | value = threshold |
| changed | value differs from the previous cycle |
| no data | no valid data for more than N seconds (device silent) |
| stuck | value has not changed for more than N seconds |

**Time formats in the log** ([[logging]])

| Format | Example |
|---|---|
| iso | 2026-11-03 12:00:00 |
| iso_ms | 2026-11-03 12:00:00.456 |
| unix | 1793707200.456 (seconds since 1970) |
| excel | 46329.50000000 (Excel date number; set the cell format to date/time) |
