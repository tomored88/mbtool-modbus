## รหัส Exception ทั้งหมด (สรุป)
<!-- id: exceptions | category: Exception และ error | keywords: exception รหัส error ข้อผิดพลาด illegal 01 02 03 04 05 06 08 0a 0b ตาราง -->
Exception คือคำตอบที่อุปกรณ์ส่งกลับเมื่อรับคำขอได้แต่ทำตามไม่ได้ (function code ในคำตอบถูกเพิ่ม 0x80) ถือว่า **อุปกรณ์ยังมีชีวิตอยู่และการตั้งค่าสายถูกต้อง** แล้ว ปัญหาอยู่ที่ตัวคำขอ

| รหัส | ชื่อ | ความหมายสั้น ๆ |
|---|---|---|
| 01 | Illegal Function | ไม่รองรับ function นี้ [[exc01]] |
| 02 | Illegal Data Address | address ผิดหรือเกินช่วง [[exc02]] |
| 03 | Illegal Data Value | จำนวนหรือค่าผิด [[exc03]] |
| 04 | Slave Device Failure | อุปกรณ์เกิดข้อผิดพลาดภายใน [[exc04]] |
| 05 | Acknowledge | รับงานแล้ว กำลังทำ (งานนาน) |
| 06 | Slave Device Busy | กำลังยุ่ง ลองใหม่ภายหลัง [[exc04]] |
| 08 | Memory Parity Error | ความจำของอุปกรณ์ผิดพลาด |
| 0A (10) | Gateway Path Unavailable | gateway ไม่มีเส้นทางไปยังอุปกรณ์ปลายทาง |
| 0B (11) | Gateway Target Device Failed to Respond | gateway ถามอุปกรณ์ปลายทางแล้วไม่ตอบ [[trouble-no-response]] |

## Exception 01: Illegal Function
<!-- id: exc01 | category: Exception และ error | keywords: exception 01 illegal function ไม่รองรับ function unsupported -->
อุปกรณ์ไม่รองรับ function code ที่ขอ

- ลองสลับ FC03 กับ FC04 (อ่าน holding เทียบกับ input register)
- FC22, FC23, FC43 อุปกรณ์จำนวนมากไม่รองรับ ถือเป็นเรื่องปกติ
- ถ้าเขียนไม่ได้ อาจเป็นพื้นที่อ่านอย่างเดียว

## Exception 02: Illegal Data Address
<!-- id: exc02 | category: Exception และ error | keywords: exception 02 illegal data address address ผิด เหลื่อม off by one เกินช่วง -->
address ที่ขอ (หรือ address ใดในช่วงที่ขอ) ไม่มีในอุปกรณ์ เป็น error ที่พบบ่อยที่สุดตอนทำ register map

สาเหตุที่พบบ่อย:
1. **address เหลื่อมไป 1** นับ address ฐาน 0 เทียบกับฐาน 1 ผิด ดู [[address-bases]] ลอง "Try other base" ใน preset
2. **ช่วงที่ขอคร่อมขอบตาราง** เช่น ขอ 10 ตัวแต่ตารางเหลือ 6 ตัว ให้ลดจำนวนหรือใช้ [[scanner]] หาขอบเขตแน่นอน
3. ใช้ FC ผิดพื้นที่ (ค่าอยู่ใน input register แต่ขอ holding register)
4. ค่า 32 บิตอยู่ที่ address สุดท้ายและอีกครึ่งอยู่เลยตาราง

## Exception 03: Illegal Data Value
<!-- id: exc03 | category: Exception และ error | keywords: exception 03 illegal data value quantity จำนวน ค่าผิด 0xff00 -->
ค่าในคำขอไม่ยอมรับ

- จำนวน (Quantity) เป็น 0 หรือเกินขีดจำกัดของ function ([[limits]]) หรือเกินที่อุปกรณ์รุ่นนั้นรับได้ ลด Quantity หรือกำหนด `max_block` ใน preset
- FC05 ต้องใช้ 0xFF00 หรือ 0x0000 เท่านั้น
- ค่าที่เขียนนอกช่วงที่อุปกรณ์ยอมรับ
- Byte count ในคำขอหลายค่าไม่ตรงกับจำนวน (โปรแกรมนี้สร้างให้ถูกอยู่แล้ว)

## Exception 04 และ 06: อุปกรณ์ล้มเหลวหรือยุ่ง
<!-- id: exc04 | category: Exception และ error | keywords: exception 04 06 busy failure slave device ยุ่ง ล้มเหลว ภายใน -->
- **04:** เกิดข้อผิดพลาดภายในที่ทำคำขอไม่สำเร็จ ลองใหม่ ถ้ายังเป็นอยู่ให้ตรวจสถานะ/รหัสความผิดพลาดของอุปกรณ์เอง
- **06:** กำลังทำงานอื่นอยู่ (เช่น เขียน EEPROM) ลดความถี่ในการ poll แล้วลองใหม่

## Response timeout (ไม่มีคำตอบ)
<!-- id: err-timeout | category: Exception และ error | keywords: timeout ไม่ตอบ no response เงียบ response timeout -->
ส่งคำขอแล้วไม่ได้อะไรกลับมาภายในเวลา Timeout ไม่ใช่ exception จากอุปกรณ์ ดู [[trouble-no-response]] สาเหตุหลักคือ Slave ID ผิด baud/parity ไม่ตรง สายต่อผิด หรืออุปกรณ์ไม่ได้รับไฟ

## CRC error / LRC error
<!-- id: err-crc | category: Exception และ error | keywords: crc lrc error ข้อมูลเสีย checksum noise รบกวน -->
ได้รับข้อมูลแต่ตรวจ checksum ไม่ผ่าน (RTU ใช้ CRC-16, ASCII ใช้ LRC) แปลว่ามีสัญญาณ แต่ข้อมูลเสีย

สาเหตุ: baud หรือ parity ไม่ตรง (เจอบ่อยสุด), สาย A/B สลับ, ไม่มี termination 120 Ω, สายยาวหรือมีสัญญาณรบกวน, มี master สองตัวพูดพร้อมกัน ดู [[trouble-crc]]

## ข้อความ error อื่นที่โปรแกรมแสดง
<!-- id: err-other | category: Exception และ error | keywords: error ข้อความ unexpected unit echo malformed mbap busy does not exist -->
| ข้อความ | ความหมาย |
|---|---|
| unexpected slave id / unit id in response | คำตอบมาจาก Slave ID ต่างจากที่ถาม: มีอุปกรณ์สองตัวใช้ ID เดียวกัน หรือ gateway เปลี่ยน ID |
| response does not echo request | คำตอบของ FC05/06/15/16/22 ไม่ตรงกับที่ส่ง |
| malformed response (byte count) | จำนวน byte ในคำตอบไม่สอดคล้องกับจำนวนที่ขอ |
| MBAP header invalid; connection closed | หัวข้อมูล TCP ผิด โปรแกรมปิดการเชื่อมต่อเพื่อไม่ให้ข้อมูลเหลื่อม |
| COMx is busy | พอร์ตถูกโปรแกรมอื่นเปิดอยู่ [[trouble-port-busy]] |
| COMx does not exist | ไม่มีพอร์ตนี้ ตรวจอะแดปเตอร์แล้วกด Refresh |
