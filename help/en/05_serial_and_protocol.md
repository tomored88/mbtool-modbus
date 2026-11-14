## Serial settings: baud, data bits, parity, stop bits
<!-- id: serial-settings | category: Serial and protocol | keywords: baud parity stop bits data 8n1 8e1 8n2 serial settings rate -->
Both sides (master and slave) must be set identically on every parameter. Getting even one wrong gives garbled data or no response.

- **Baud rate:** 1200, 2400, 4800, **9600**, **19200**, 38400, 57600, 115200. The most common defaults are 9600 or 19200
- **Data bits:** RTU must always be **8** (ASCII uses 7 or 8)
- **Parity:** None (N), Even (E), Odd (O). The Modbus standard specifies **Even** as the default, but most devices on the market use **None**
- **Stop bits:** the standard specifies 1 stop bit with parity (E/O) and 2 stop bits if parity is None (8N2), but in practice 8N1 is also very common

Common combinations: **9600 8N1**, **9600 8E1**, **19200 8E1**, **19200 8N1**, 9600 8N2. If you do not know the settings, use the [[scanner]]

A higher baud rate requires a shorter cable: enter the real cable length in the [[bus-map]] (Ctrl+M) to see the maximum recommended baud rate for that length, along with warnings about terminators / stubs.

## RTU vs ASCII
<!-- id: rtu-vs-ascii | category: Serial and protocol | keywords: rtu ascii difference frame lrc crc mode -->
- **RTU:** sent as 8-bit binary data. Compact and fast. Checked with CRC-16. Used by almost all devices
- **ASCII:** sent as hexadecimal characters, starting with `:` and ending with CR LF. Checked with LRC. About twice as slow, but tolerant of gaps between bytes. Used by some older devices

Both sides must use the same mode. See [[frame-rtu]] and [[frame-ascii]]

## Modbus/TCP frame structure
<!-- id: frame-tcp | category: Serial and protocol | keywords: mbap tcp frame transaction id protocol length unit header 7 byte -->
It begins with a 7-byte **MBAP** header, followed by the PDU.

| Field | Size | Meaning |
|---|---|---|
| Transaction ID | 2 | Number matching a request to its response |
| Protocol ID | 2 | Must be 0 |
| Length | 2 | Number of bytes that follow (Unit ID + PDU) |
| Unit ID | 1 | Slave ID (actually used when there is a gateway) |
| PDU | up to 253 | Function code + data |

There is no CRC because TCP checks integrity itself. For a real example see [[traffic-monitor]]

## RTU frame structure and the 3.5-character silent interval
<!-- id: frame-rtu | category: Serial and protocol | keywords: rtu frame crc16 3.5 character silence t35 gap silent interval header -->
`[Slave ID 1 byte] [PDU] [CRC 2 byte]`

- **CRC-16:** polynomial 0xA001, initial value 0xFFFF, sent **low byte first**
- There must be a **silent interval of at least 3.5 character times** before and after the frame, which marks the frame boundaries (at 19200 baud ≈ 2 ms; above 19200 a fixed 1.75 ms is used). The program waits for this before sending every request
- This program's slave uses a silent interval of at least 20 ms to end a frame, to allow for USB-serial converters that deliver data in bursts

## ASCII frame structure
<!-- id: frame-ascii | category: Serial and protocol | keywords: ascii frame lrc colon crlf -->
`: [Slave ID][PDU][LRC] CR LF` where every byte is shown as 2 hex characters (for example `:010300000001FB`)

**LRC** = the two's complement of the sum of all bytes (Slave ID + PDU)

## Slave ID and broadcast
<!-- id: slave-id | category: Serial and protocol | keywords: slave id unit address broadcast 0 247 device number -->
- Valid Slave IDs are **1–247** (each must be unique on the same line)
- **0 = broadcast:** sent to all devices at once and **nobody replies**. Usable only for write commands. The program sends it and does not wait for a response
- 248–255 are reserved
- On TCP it is normally ignored, except when going through a gateway

## RS-485 wiring
<!-- id: rs485-wiring | category: Serial and protocol | keywords: rs485 wire a b termination 120 ohm bias ground twisted pair wiring 2 -->
- Use twisted-pair cable wired as a **daisy chain** (not a star)
- The A/B terminals (or D-/D+) may be labeled in opposite ways by different manufacturers. If there is no response at all, try swapping the two wires (this does no damage)
- Fit a **120 Ω** termination resistor at both ends of the line
- Use bias resistors (pull-up/pull-down) at one point at the master so the line does not float when nobody is transmitting
- Connect the devices' reference GND when the distance is long or they use different power sources
- There may be only **one** master on a line

## Automatic connection-settings scan
<!-- id: scanner | category: Serial and protocol | keywords: scan auto search baud parity slave id find device identify register range -->
`scanner.py` (and the matching MCP tool) tries connection settings for you. Everything is read-only.

- `python src/scanner.py serial COM3 --units 1-10,247` tries baud × parity × stop bits × Slave ID, ordering the most common values first. It reports the correct settings and "noise" (settings that returned data that could not be decoded, meaning you are close)
- `python src/scanner.py ids URL` which Slave IDs respond
- `python src/scanner.py regs URL -u 1 --start 0 --end 999` the readable register ranges, using bisection to find the exact boundaries
- `python src/scanner.py identify URL -u 1` asks for the device model (FC43/14, FC17) and suggests a preset

Limitations: tested against the simulator only. The behavior of real devices with a wrong baud rate may differ. The scan takes longer when many Slave IDs are tried.
