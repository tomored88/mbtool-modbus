## อภิธานศัพท์ A–M
<!-- id: glossary-am | category: อภิธานศัพท์ | keywords: glossary ศัพท์ ความหมาย คำศัพท์ master slave client server coil register baud bcd broadcast -->
- **ADU (Application Data Unit):** เฟรมเต็มที่ส่งบนสาย = PDU + ส่วนหัว/ท้ายของโปรโตคอลชั้นล่าง (MBAP, หรือ Slave ID + CRC)
- **Baud rate:** จำนวนสัญลักษณ์ต่อวินาทีบนสาย serial ([[serial-settings]])
- **BCD:** เก็บเลขทศนิยมทีละหลักใน nibble ([[strings-bcd]])
- **Broadcast:** ส่งถึง Slave ID 0 ทุกตัวรับแต่ไม่ตอบ ([[slave-id]])
- **Coil:** บิตเอาต์พุตอ่านเขียนได้ ([[data-areas]])
- **CRC:** รหัสตรวจความถูกต้องของเฟรม RTU ([[frame-rtu]])
- **Discrete input:** บิตอินพุตอ่านอย่างเดียว
- **Exception:** คำตอบแจ้งว่าทำตามคำขอไม่ได้ ([[exceptions]])
- **Function code (FC):** รหัสบอกว่าจะทำอะไร เช่น 03 = อ่าน holding register ([[limits]])
- **Gateway:** ตัวแปลงระหว่างเครือข่าย เช่น Ethernet กับ RS-485
- **Holding register:** register 16 บิตอ่านเขียนได้ ([[fc03]])
- **Input register:** register 16 บิตอ่านอย่างเดียว ([[fc04]])
- **LRC:** รหัสตรวจของเฟรม ASCII ([[frame-ascii]])
- **Master / Client:** ฝั่งที่ส่งคำขอ (โปรแกรมนี้เป็น master) ส่วน **Slave / Server** คือฝั่งที่ตอบ ปัจจุบันมาตรฐานเรียก client/server
- **MBAP:** หัว 7 byte ของ Modbus/TCP ([[frame-tcp]])

## อภิธานศัพท์ N–Z
<!-- id: glossary-nz | category: อภิธานศัพท์ | keywords: glossary ศัพท์ pdu preset rtu scale slave unit word endian plc polling scan rate transaction -->
- **PDU (Protocol Data Unit):** Function code + ข้อมูล ส่วนที่เหมือนกันทุกโหมดการส่ง
- **PLC addressing:** วิธีเขียน address ด้วยเลขนำหน้าพื้นที่ เช่น 40001 ([[address-bases]])
- **Polling:** การถามค่าซ้ำเป็นรอบ ๆ ตาม Scan Rate ([[poll-panel]])
- **Preset:** ไฟล์ JSON แผนที่ register ของอุปกรณ์หนึ่งรุ่น ([[preset-schema]])
- **Protocol address:** address ที่ส่งจริงบนสาย เริ่มจาก 0
- **RTU:** โหมดไบนารีบน serial ([[rtu-vs-ascii]])
- **Scale:** ตัวคูณแปลงค่าดิบเป็นค่าจริง ([[scale-offset]])
- **Slave ID / Unit ID:** หมายเลขอุปกรณ์ 1–247 ([[slave-id]])
- **Termination:** ตัวต้านทาน 120 Ω ปลายสาย RS-485 ([[rs485-wiring]])
- **Transaction ID:** เลขจับคู่คำขอกับคำตอบใน Modbus/TCP
- **Word:** หนึ่ง register = 16 บิต **Word order:** ลำดับ word ของค่า 32/64 บิต ([[word-order]])
