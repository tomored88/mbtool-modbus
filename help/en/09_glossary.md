## Glossary A–M
<!-- id: glossary-am | category: Glossary | keywords: glossary, terms, meaning, vocabulary, master, slave, client, server, coil, register, baud, bcd, broadcast -->
- **ADU (Application Data Unit):** The full frame sent on the wire = PDU + header/trailer of the lower-layer protocol (MBAP, or Slave ID + CRC)
- **Baud rate:** Number of symbols per second on a serial line ([[serial-settings]])
- **BCD:** Stores a decimal number one digit per nibble ([[strings-bcd]])
- **Broadcast:** Sent to Slave ID 0; every slave receives it but none replies ([[slave-id]])
- **Coil:** A read/write output bit ([[data-areas]])
- **CRC:** Error-check code of an RTU frame ([[frame-rtu]])
- **Discrete input:** A read-only input bit
- **Exception:** A reply saying the request could not be carried out ([[exceptions]])
- **Function code (FC):** A code saying what to do, e.g. 03 = read holding registers ([[limits]])
- **Gateway:** A converter between networks, e.g. Ethernet and RS-485
- **Holding register:** A read/write 16-bit register ([[fc03]])
- **Input register:** A read-only 16-bit register ([[fc04]])
- **LRC:** Error-check code of an ASCII frame ([[frame-ascii]])
- **Master / Client:** The side that sends requests (this program is a master), while **Slave / Server** is the side that answers. The standard now uses client/server
- **MBAP:** The 7-byte header of Modbus/TCP ([[frame-tcp]])

## Glossary N–Z
<!-- id: glossary-nz | category: Glossary | keywords: glossary, terms, pdu, preset, rtu, scale, slave, unit, word, endian, plc, polling, scan rate, transaction -->
- **PDU (Protocol Data Unit):** Function code + data; the part that is the same in every transmission mode
- **PLC addressing:** Writing an address with an area-prefix digit, e.g. 40001 ([[address-bases]])
- **Polling:** Repeatedly asking for values in cycles at the Scan Rate ([[poll-panel]])
- **Preset:** A JSON file holding the register map of one device model ([[preset-schema]])
- **Protocol address:** The address actually sent on the wire, starting from 0
- **RTU:** Binary mode on serial ([[rtu-vs-ascii]])
- **Scale:** A multiplier that converts a raw value to the real value ([[scale-offset]])
- **Slave ID / Unit ID:** Device number 1–247 ([[slave-id]])
- **Termination:** A 120 Ω resistor at the ends of an RS-485 line ([[rs485-wiring]])
- **Transaction ID:** A number that pairs requests with responses in Modbus/TCP
- **Word:** One register = 16 bits. **Word order:** The order of words in a 32/64-bit value ([[word-order]])
