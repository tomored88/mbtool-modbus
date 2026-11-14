## ช่องกรอกและค่าที่ใช้ได้ (ตารางอ้างอิง)
<!-- id: values-fields | category: ตารางค่า | keywords: ช่อง ค่าที่ใช้ได้ ช่วง ค่าเริ่มต้น default range allowed values ตารางอ้างอิง ฟิลด์ -->
| ช่อง | ค่าที่ใช้ได้ | ค่าเริ่มต้น | หมายเหตุ |
|---|---|---|---|
| Slave ID | serial: 1–247 (0 = broadcast สำหรับเขียน), TCP: 0–255 | 1 | [[slave-id]] |
| Function (อ่าน) | 01, 02, 03, 04 | 03 | [[data-areas]] |
| Address | 0–65535 (ตามฐานที่เลือก) | 0 | [[address-bases]] |
| Quantity | FC01/02: 1–2000, FC03/04: 1–125 | 10 | [[limits]] |
| Scan Rate | ตั้งแต่ 20 มิลลิวินาทีขึ้นไป | 1000 | ช่วงระหว่างรอบ poll |
| Timeout | 50–60000 มิลลิวินาที | 1000 | เวลารอคำตอบ |
| Port (TCP) | 1–65535 | 502 | [[connect-tcp]] |
| Baud rate | 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200 (พิมพ์ค่าอื่นได้) | 9600 | [[serial-settings]] |
| Data bits | 7 (ASCII), 8 | 8 | RTU ต้อง 8 |
| Parity | None (N), Even (E), Odd (O) | None | |
| Stop bits | 1, 2 | 1 | |
| Display | Unsigned, Signed, Hex, Binary, Long, Long Inverse, Float, Float Inverse, Double, Double Inverse | Unsigned | [[display-formats]] |
| Address base | 0, 1, PLC (4xxxx) | 0 | [[address-bases]] |
| Rows per column | Auto, 10, 20, 30, 40 | Auto | [[poll-panel]] |

## ค่า Function code และ Exception code (ตารางเลขฐาน)
<!-- id: values-codes | category: ตารางค่า | keywords: function code exception code ตาราง เลขฐาน hex decimal 0x83 0x80 รหัส เลข -->
| Function | ทศนิยม | Hex | ใน exception response |
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

เมื่ออุปกรณ์ปฏิเสธ จะตอบ function code บวก **0x80** ตามด้วยรหัส exception ([[exceptions]]) เช่น `83 02` = อ่าน holding register ผิด address

## ค่าที่ใช้เขียน coil และการแปลงค่า
<!-- id: values-coil | category: ตารางค่า | keywords: coil on off ff00 0000 เขียน coil ค่า เปิด ปิด -->
| ต้องการ | ค่าบนสาย (FC05) | ในช่อง Write |
|---|---|---|
| ON | 0xFF00 | 1 / true |
| OFF | 0x0000 | 0 / false |

ค่าอื่น (เช่น 0x0001) ผิดมาตรฐาน อุปกรณ์ต้องตอบ exception 03 ([[fc05]])

## เงื่อนไขของระบบแจ้งเตือนและรูปแบบเวลา log
<!-- id: values-alerts-log | category: ตารางค่า | keywords: alert condition above below outside equals changed no data stuck รูปแบบเวลา time format iso unix excel serial -->
**เงื่อนไขแจ้งเตือน** ([[alerts]])

| เงื่อนไข | เตือนเมื่อ |
|---|---|
| above | ค่า > เกณฑ์ |
| below | ค่า < เกณฑ์ |
| outside | ค่าอยู่นอกช่วง [ต่ำ, สูง] |
| equals | ค่า = เกณฑ์ |
| changed | ค่าต่างจากรอบก่อน |
| no data | ไม่มีข้อมูลที่ถูกต้องเกิน N วินาที (อุปกรณ์เงียบ) |
| stuck | ค่าไม่เปลี่ยนเกิน N วินาที |

**รูปแบบเวลาใน log** ([[logging]])

| รูปแบบ | ตัวอย่าง |
|---|---|
| iso | 2026-11-03 12:00:00 |
| iso_ms | 2026-11-03 12:00:00.456 |
| unix | 1793707200.456 (วินาทีนับจาก 1970) |
| excel | 46329.50000000 (เลขวันที่ของ Excel ตั้งรูปแบบเซลล์เป็นวันที่/เวลา) |
