## No response from the device (timeout)
<!-- id: trouble-no-response | category: Troubleshooting | keywords: no response, timeout, silent, cannot connect, troubleshooting, checklist -->
Check in this order (easy and common causes first)

1. **The device is powered** and in Modbus mode (some models require enabling the Modbus protocol in a menu, e.g. ADAM, DTSU666)
2. **The Slave ID is correct.** Check the device display; if unknown, use [[scanner]]
3. **baud / parity / stop bits match** ([[serial-settings]]). A single wrong value means no response
4. **The A/B wires are not swapped.** Try swapping the two wires ([[rs485-wiring]])
5. **The port is not held by another program** ([[trouble-port-busy]])
6. **The mode matches:** RTU or ASCII ([[rtu-vs-ascii]])
7. TCP: can you ping it? Does a firewall block port 502? Are the IP/port correct?
8. Check [[traffic-monitor]] for any RX. If there is RX but it is errors, there is a signal ([[trouble-crc]])
9. **Timeout sometimes, not every time:** the device may respond more slowly than the configured timeout. Increase Timeout in Setup to more than the slowest response time. On RTU, if the response arrives after the timeout, the program keeps listening on the line for a short time (0.15 seconds) and then discards the late response. But if it arrives much later, the old response may be taken as the answer to the next request, because RTU frames carry no sequence number (TCP does not have this problem because of the Transaction ID)
10. **TCP dropped:** if the device closes the connection or sends corrupt data, the program discards that connection and reconnects by itself on the next request

## Frequent CRC / LRC errors
<!-- id: trouble-crc | category: Troubleshooting | keywords: crc, frequent errors, corrupt data, noise, interference, termination, long cable, intermittent -->
- **Always wrong:** baud or parity does not match. Try [[scanner]]
- **Sometimes wrong:** noise, missing 120 Ω termination at the cable ends, cable too long or not twisted pair, no bias, cable shared with high-voltage power wiring
- **Wrong when there are multiple devices:** two masters talking at once, or duplicate Slave IDs
- Some cheap USB-serial adapters break up frames. Try a lower baud or a different adapter
- In Devices, try increasing the gap between devices

## COM port in use (busy)
<!-- id: trouble-port-busy | category: Troubleshooting | keywords: port, busy, com, in use, access denied, permission, close, other program -->
A port can be opened by only one program at a time (within this program, Poll 1, tabs, and devices can share the same port, see [[poll-tabs]]). Close any other program using that port (vendor configuration tool, another Modbus master program, another instance of this program). If the MCP server (stdio) has the port open, the GUI cannot open it, and vice versa; use AI Assist, which shares the GUI's connection, instead ([[ai-assist]]). If you unplug and replug the USB, press Refresh.

## Values do not match the device display
<!-- id: trouble-values-wrong | category: Troubleshooting | keywords: wrong values, mismatch, strange, float, swapped, off by one, scale, odd values -->
Work through in order

1. **Off by 1 register?** Try another address base (Address base or "Try other base") ([[address-bases]])
2. **Wrong data type?** A Float that shows as odd numbers (e.g. 1e-38 or 1e+30) is almost certainly a word order problem. Use the F4 value checker ([[inspector]], [[word-order]])
3. **Forgot the scale?** A value of 253 that should be 25.3 means scale 0.1 ([[scale-offset]])
4. **Signed or Unsigned?** A negative temperature shown as 65xxx means it needs Signed
5. **Wrong FC?** Try FC03 versus FC04 ([[fc03]], [[fc04]])
6. **Stale/cached values from the device:** some models update values slowly. Compare over several cycles (use snapshot or the chart)
7. **The preset is unverified** ([[verified-presets]]). Compare with the latest manual for that model

## Exception 02 when reading multiple values
<!-- id: trouble-exc2-block | category: Troubleshooting | keywords: exception 02, multiple read, straddle, table boundary, block, gap -->
Reading one value at a time works, but reading several at once gives exception 02. This means the requested range straddles a table boundary, or there is a gap of registers the device does not allow reading. Use `scanner.py regs` to find the readable ranges, then set the preset to split the requests (reduce `max_block` or define groups). See [[exc02]].

## Multiple masters on one line
<!-- id: trouble-multimaster | category: Troubleshooting | keywords: multiple masters, simultaneous, collision, two programs -->
Modbus RTU/ASCII allows only one master per line. If a PLC is already polling and you connect this program to the same line, requests collide and you get CRC errors/timeouts alternately. Stop one of the masters, or connect through a gateway that merges requests, or use Modbus/TCP instead (each connection is separate).

## A write does not change the value
<!-- id: trouble-write-fail | category: Troubleshooting | keywords: write fails, write, no change, read only, no effect, rejected, exception, write exception -->
- Getting exception 01/02/03? Check that the register is writable and the value is in range ([[exceptions]])
- No error but the value does not change: the device may need unlocking/a password first (e.g. SDM630 System type), may be in a mode that forbids edits, or the control system may immediately overwrite the value
- 32-bit values must be written with [[fc16]] in a single request, and the word order must match ([[word-order]])
- Some values must be written and then followed by a separate "save/apply" command, as per the manual
