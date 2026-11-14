## ค่า Serial: baud, data bits, parity, stop bits
<!-- id: serial-settings | category: Serial และโปรโตคอล | keywords: baud parity stop bits data bits 8n1 8e1 8n2 serial การตั้งค่า อัตราบอด -->
ทั้งสองฝั่ง (master และ slave) ต้องตั้งค่าเหมือนกันทุกตัว ผิดแม้ค่าเดียวจะได้ข้อมูลเพี้ยนหรือไม่ตอบ

- **Baud rate:** 1200, 2400, 4800, **9600**, **19200**, 38400, 57600, 115200 ค่าตั้งต้นที่พบบ่อยคือ 9600 หรือ 19200
- **Data bits:** RTU ต้องเป็น **8** เสมอ (ASCII ใช้ 7 หรือ 8)
- **Parity:** None (N), Even (E), Odd (O) มาตรฐานของ Modbus กำหนดค่าตั้งต้นเป็น **Even** แต่อุปกรณ์ส่วนมากในตลาดใช้ **None**
- **Stop bits:** มาตรฐานกำหนด 1 stop bit กับ parity (E/O) และ 2 stop bits ถ้า parity เป็น None (8N2) แต่ในทางปฏิบัติ 8N1 ก็พบมาก

รูปแบบที่พบบ่อย: **9600 8N1**, **9600 8E1**, **19200 8E1**, **19200 8N1**, 9600 8N2 ถ้าไม่ทราบค่า ใช้ [[scanner]]

baud สูงต้องใช้สายสั้นลง: ใส่ความยาวสายจริงใน [[bus-map]] (Ctrl+M) จะเห็น baud สูงสุดที่แนะนำสำหรับความยาวนั้นและคำเตือนเรื่อง terminator / stub

## RTU กับ ASCII
<!-- id: rtu-vs-ascii | category: Serial และโปรโตคอล | keywords: rtu ascii ต่างกัน frame lrc crc โหมด -->
- **RTU:** ส่งเป็นข้อมูลไบนารี 8 บิต ประหยัดและเร็ว ตรวจด้วย CRC-16 ใช้กับอุปกรณ์เกือบทั้งหมด
- **ASCII:** ส่งเป็นตัวอักษรฐาน 16 ขึ้นต้นด้วย `:` ลงท้าย CR LF ตรวจด้วย LRC ช้ากว่าราวสองเท่า แต่ทนต่อช่องว่างระหว่าง byte ใช้กับอุปกรณ์เก่าบางรุ่น

ทั้งสองโหมดต้องตรงกันทั้งสองฝั่ง ดู [[frame-rtu]] และ [[frame-ascii]]

## โครงสร้างเฟรม Modbus/TCP
<!-- id: frame-tcp | category: Serial และโปรโตคอล | keywords: mbap tcp frame transaction id protocol id length unit id header 7 byte -->
ขึ้นต้นด้วยหัว **MBAP** 7 byte แล้วตามด้วย PDU

| ฟิลด์ | ขนาด | ความหมาย |
|---|---|---|
| Transaction ID | 2 | เลขจับคู่คำขอกับคำตอบ |
| Protocol ID | 2 | ต้องเป็น 0 |
| Length | 2 | จำนวน byte ที่ตามมา (Unit ID + PDU) |
| Unit ID | 1 | Slave ID (ใช้จริงเมื่อมี gateway) |
| PDU | ไม่เกิน 253 | Function code + ข้อมูล |

ไม่มี CRC เพราะ TCP ตรวจเอง ดูตัวอย่างจริงใน [[traffic-monitor]]

## โครงสร้างเฟรม RTU และช่วงเงียบ 3.5 ตัวอักษร
<!-- id: frame-rtu | category: Serial และโปรโตคอล | keywords: rtu frame crc16 3.5 character silence t35 gap ช่วงเงียบ หัว เฟรม -->
`[Slave ID 1 byte] [PDU] [CRC 2 byte]`

- **CRC-16:** polynomial 0xA001, ค่าเริ่ม 0xFFFF ส่ง **byte ต่ำก่อน**
- ต้องมี **ช่วงเงียบอย่างน้อย 3.5 เท่าของเวลาส่งหนึ่งตัวอักษร** ก่อนและหลังเฟรม เป็นตัวบอกขอบเขต (ที่ 19200 baud ≈ 2 ms; เหนือ 19200 ใช้ 1.75 ms คงที่) โปรแกรมรอให้ครบก่อนส่งทุกคำขอ
- ตัว slave ของโปรแกรมนี้ใช้ช่วงเงียบอย่างน้อย 20 ms จบเฟรม เผื่อตัวแปลง USB-serial ที่ส่งข้อมูลเป็นช่วง ๆ

## โครงสร้างเฟรม ASCII
<!-- id: frame-ascii | category: Serial และโปรโตคอล | keywords: ascii frame lrc colon crlf เฟรม -->
`: [Slave ID][PDU][LRC] CR LF` โดยทุก byte แสดงเป็นอักษร hex 2 ตัว (เช่น `:010300000001FB`)

**LRC** = ส่วนเติมเต็มสองของผลรวม byte (Slave ID + PDU) ทั้งหมด

## Slave ID และ broadcast
<!-- id: slave-id | category: Serial และโปรโตคอล | keywords: slave id unit id address broadcast 0 247 หมายเลขอุปกรณ์ -->
- Slave ID ที่ใช้ได้คือ **1–247** (บนสายเดียวกันแต่ละตัวต้องไม่ซ้ำ)
- **0 = broadcast:** ส่งถึงทุกตัวพร้อมกัน **ไม่มีใครตอบ** ใช้ได้เฉพาะคำสั่งเขียน โปรแกรมส่งแล้วไม่รอคำตอบ
- 248–255 สงวนไว้
- บน TCP ปกติไม่สนใจ ยกเว้นเมื่อผ่าน gateway

## การเดินสาย RS-485
<!-- id: rs485-wiring | category: Serial และโปรโตคอล | keywords: rs485 สาย a b termination 120 ohm bias ground สายคู่ตีเกลียว ต่อสาย 2 wire -->
- ใช้สายคู่ตีเกลียว ต่อแบบ **daisy chain** (ไม่แยกเป็นดาว)
- ขั้ว A/B (หรือ D-/D+) ผู้ผลิตแต่ละรายตั้งชื่อสลับกันได้ ถ้าไม่ตอบเลยให้ลองสลับสอง เส้น (ไม่ทำให้เสียหาย)
- ใส่ตัวต้านทาน termination **120 Ω** ที่ปลายสายทั้งสองข้าง
- ใช้ bias resistor (ดึงขึ้น/ลง) ที่ master หนึ่งจุดเพื่อให้สายไม่ลอยตอนไม่มีใครส่ง
- ต่อ GND อ้างอิงของอุปกรณ์เมื่อระยะไกลหรือใช้คนละแหล่งไฟ
- มี master ได้ **ตัวเดียว** บนสายเดียว

## สแกนค่าการเชื่อมต่ออัตโนมัติ
<!-- id: scanner | category: Serial และโปรโตคอล | keywords: scan สแกน auto ค้นหา baud parity slave id ค้นอุปกรณ์ identify register range -->
`scanner.py` (และเครื่องมือ MCP ที่ตรงกัน) ลองค่าเชื่อมต่อให้ ทุกอย่างเป็นการอ่านอย่างเดียว

- `python src/scanner.py serial COM3 --units 1-10,247` ลอง baud × parity × stop bits × Slave ID โดยเรียงค่าที่พบบ่อยก่อน แจ้งค่าที่ถูก และ "noise" (ค่าที่ได้ข้อมูลแต่ถอดรหัสไม่ได้ แปลว่าใกล้ถูกแล้ว)
- `python src/scanner.py ids URL` Slave ID ใดตอบบ้าง
- `python src/scanner.py regs URL -u 1 --start 0 --end 999` ช่วง register ที่อ่านได้ แบ่งครึ่งหาขอบเขตแน่นอน
- `python src/scanner.py identify URL -u 1` ถามรุ่นอุปกรณ์ (FC43/14, FC17) และแนะนำ preset

ข้อจำกัด: ทดสอบกับตัวจำลองเท่านั้น พฤติกรรมของอุปกรณ์จริงเมื่อตั้ง baud ผิดอาจต่างกัน การสแกนนานขึ้นเมื่อลอง Slave ID เยอะ
