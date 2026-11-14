## FC01 Read Coils (อ่าน coil)
<!-- id: fc01 | category: Function codes | keywords: fc01 01 read coils coil บิต 0x output ดิจิทัล relay -->
อ่านบิตเอาต์พุตที่เขียนได้ (coil, พื้นที่ 0xxxx) เช่น สถานะรีเลย์

- **Quantity:** 1–**2000** บิตต่อคำขอ
- ผลลัพธ์: ค่า 0/1 ของแต่ละบิต
- ถ้าอ่านเกินช่วงที่อุปกรณ์มี จะได้ exception 02 ([[exc02]])
- เขียน coil ได้ด้วย [[fc05]] และ [[fc15]]

## FC02 Read Discrete Inputs (อ่านอินพุตดิจิทัล)
<!-- id: fc02 | category: Function codes | keywords: fc02 02 discrete inputs อินพุต 1x สถานะ digital input -->
อ่านบิตอินพุตที่อ่านได้อย่างเดียว (พื้นที่ 1xxxx) เช่น สวิตช์ ขีดจำกัด

- **Quantity:** 1–**2000** บิตต่อคำขอ
- เขียนไม่ได้ (ไม่มี function code สำหรับเขียน discrete input)

## FC03 Read Holding Registers (อ่าน holding register)
<!-- id: fc03 | category: Function codes | keywords: fc03 03 holding registers 4x register ค่าตั้ง setpoint อ่าน -->
อ่าน register 16 บิตที่อ่านและเขียนได้ (พื้นที่ 4xxxx) เป็น function ที่ใช้บ่อยที่สุด ใช้เก็บค่าวัด ค่าตั้ง และพารามิเตอร์

- **Quantity:** 1–**125** register ต่อคำขอ (ค่า 32 บิต/float ใช้ 2 register, double ใช้ 4)
- บางอุปกรณ์จำกัดจำนวนต่อคำขอน้อยกว่านี้ (เช่น Eastron 40 ค่า, Keller 4 ค่า) preset ใช้ฟิลด์ `max_block` ([[preset-schema]])
- เขียนได้ด้วย [[fc06]], [[fc16]], [[fc22]], [[fc23]]

## FC04 Read Input Registers (อ่าน input register)
<!-- id: fc04 | category: Function codes | keywords: fc04 04 input registers 3x register วัด sensor อ่านอย่างเดียว -->
อ่าน register 16 บิตที่อ่านได้อย่างเดียว (พื้นที่ 3xxxx) ผู้ผลิตมักวางค่าวัดจริงไว้ที่นี่ เช่น แรงดัน กระแส อุณหภูมิ

- **Quantity:** 1–**125** register ต่อคำขอ
- ถ้าอุปกรณ์ไม่ตอบ FC03 ให้ลอง FC04 ผู้ผลิตบางรายใช้ต่างจากที่คาด เช็กคู่มือเสมอ
- เขียนไม่ได้

## FC05 Write Single Coil (เขียน coil เดียว)
<!-- id: fc05 | category: Function codes | keywords: fc05 05 write single coil เขียน on off เปิด ปิด relay ff00 -->
สั่ง coil เดียวให้ ON หรือ OFF

- ค่าที่ส่งมีสองแบบเท่านั้น: **0xFF00 = ON**, **0x0000 = OFF** ค่าอื่นอุปกรณ์ต้องตอบ exception 03
- คำตอบคือการสะท้อนคำขอกลับ (echo)
- **ระวัง:** ถ้า coil ควบคุมรีเลย์หรือเครื่องจักรจริง สภาพจะเปลี่ยนทันที

## FC06 Write Single Register (เขียน register เดียว)
<!-- id: fc06 | category: Function codes | keywords: fc06 06 write single register เขียน preset single setpoint -->
เขียนค่า 16 บิตลง holding register หนึ่งตัว

- คำตอบคือ echo ของคำขอ
- ค่า 32 บิต (Long, Float) ต้องเขียนหลาย register ใช้ [[fc16]] ไม่ใช่ FC06 สองครั้ง เพราะอุปกรณ์อาจอ่านได้ค่าครึ่ง ๆ กลาง ๆ ระหว่างเขียน

## FC15 Write Multiple Coils (เขียนหลาย coil)
<!-- id: fc15 | category: Function codes | keywords: fc15 0f 15 write multiple coils เขียนหลายบิต -->
เขียน coil ต่อเนื่องกันหลายตัวในคำขอเดียว

- **Quantity:** 1–**1968** บิต
- บิตถูกอัดเป็น byte โดยบิตแรกอยู่ที่ bit 0 ของ byte แรก
- คำตอบ: address เริ่มและจำนวนที่เขียน

## FC16 Write Multiple Registers (เขียนหลาย register)
<!-- id: fc16 | category: Function codes | keywords: fc16 10 16 write multiple registers เขียนหลาย float long -->
เขียน holding register ต่อเนื่องกันหลายตัวในคำขอเดียว ใช้เขียนค่า 32/64 บิต

- **Quantity:** 1–**123** register
- ต้องเรียงลำดับ word ตามที่อุปกรณ์กำหนด ดู [[word-order]]
- คำตอบ: address เริ่มและจำนวนที่เขียน

## FC22 Mask Write Register (เขียนแบบมาสก์)
<!-- id: fc22 | category: Function codes | keywords: fc22 16 22 mask write register and or bit บิต แก้บางบิต -->
เปลี่ยนเฉพาะบางบิตของ holding register โดยไม่แตะบิตอื่น

ผลลัพธ์ = **(ค่าปัจจุบัน AND and_mask) OR (or_mask AND (NOT and_mask))**

ตัวอย่าง: ค่าเดิม 0x12, and_mask 0xF2, or_mask 0x25 ได้ 0x17 อุปกรณ์หลายรุ่นไม่รองรับ จะตอบ exception 01

## FC23 Read/Write Multiple Registers (อ่านและเขียนในคำขอเดียว)
<!-- id: fc23 | category: Function codes | keywords: fc23 17 23 read write multiple registers อ่านเขียน พร้อมกัน -->
เขียน register ก่อน แล้วอ่านกลับในคำขอเดียว (การเขียนทำก่อนการอ่านเสมอ)

- อ่านได้สูงสุด **125** register เขียนได้สูงสุด **121** register
- ใช้เมื่อต้องการอ่านค่าที่เพิ่งสั่งโดยไม่ให้มีช่องว่างระหว่างสองการกระทำ

## FC43/14 Read Device Identification (ถามว่าอุปกรณ์คือรุ่นอะไร)
<!-- id: fc43 | category: Function codes | keywords: fc43 2b 43 14 mei device identification vendor product ระบุรุ่น ผู้ผลิต -->
อุปกรณ์ที่รองรับจะตอบชื่อผู้ผลิต (VendorName), รหัสสินค้า (ProductCode), เวอร์ชัน (MajorMinorRevision) และอาจมี URL, ชื่อสินค้า, ชื่อรุ่น

โปรแกรมใช้ใน [[scanner]] เพื่อแนะนำ preset ที่ตรงกับอุปกรณ์ อุปกรณ์จำนวนมากไม่รองรับ จะตอบ exception 01 ซึ่งไม่ใช่ปัญหา

## FC17 Report Slave ID
<!-- id: fc17 | category: Function codes | keywords: fc17 11 17 report slave id รหัสอุปกรณ์ run indicator -->
function เก่าที่ให้อุปกรณ์รายงานข้อมูลของตัวเอง (รูปแบบข้อมูลต่างกันตามผู้ผลิต) โปรแกรมแสดงเป็น hex และข้อความ ASCII ให้ตีความเอง

## ตารางสรุปขีดจำกัดของแต่ละ function
<!-- id: limits | category: Function codes | keywords: ขีดจำกัด limit สูงสุด quantity max 125 2000 123 1968 121 253 -->
| Function | ทำอะไร | จำนวนสูงสุดต่อคำขอ |
|---|---|---|
| FC01 | อ่าน coil | 2000 บิต |
| FC02 | อ่าน discrete input | 2000 บิต |
| FC03 | อ่าน holding register | 125 |
| FC04 | อ่าน input register | 125 |
| FC05 | เขียน coil เดียว | 1 |
| FC06 | เขียน register เดียว | 1 |
| FC15 | เขียนหลาย coil | 1968 บิต |
| FC16 | เขียนหลาย register | 123 |
| FC22 | เขียนแบบมาสก์ | 1 |
| FC23 | อ่าน/เขียน | อ่าน 125, เขียน 121 |

ขนาด PDU สูงสุด 253 byte ตามมาตรฐาน (ตัวเลขข้างบนมาจากข้อนี้) ขอเกินจะได้ exception 03
