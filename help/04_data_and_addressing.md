## พื้นที่ข้อมูลทั้ง 4 ของ Modbus
<!-- id: data-areas | category: ข้อมูลและ address | keywords: coil discrete input holding input register 0x 1x 3x 4x พื้นที่ ตาราง ชนิดข้อมูล -->
| พื้นที่ | ขนาด | อ่าน/เขียน | อ่านด้วย | เขียนด้วย | เลขแบบ PLC |
|---|---|---|---|---|---|
| Coils | 1 บิต | อ่าน+เขียน | FC01 | FC05, FC15 | 0xxxx |
| Discrete Inputs | 1 บิต | อ่านอย่างเดียว | FC02 | - | 1xxxx |
| Input Registers | 16 บิต | อ่านอย่างเดียว | FC04 | - | 3xxxx |
| Holding Registers | 16 บิต | อ่าน+เขียน | FC03 | FC06, FC16, FC22, FC23 | 4xxxx |

พื้นที่ทั้งสี่เป็นตารางแยกกัน address 0 ของ coil ไม่ใช่ตัวเดียวกับ address 0 ของ holding register

## Address base: 0, 1 และแบบ PLC (4xxxx)
<!-- id: address-bases | category: ข้อมูลและ address | keywords: address base 0 1 plc 40001 protocol off by one เหลื่อม นับ เริ่ม ฐาน -->
สาเหตุอันดับหนึ่งที่ค่าอ่านผิดไปหนึ่งช่อง คือสับสนวิธีนับ address

- **Protocol address (ฐาน 0):** ค่าที่ส่งจริงบนสาย register แรกคือ **0** โปรแกรมนี้ใช้เป็นค่าเริ่มต้น
- **ฐาน 1:** คู่มือบางเล่มนับ register แรกเป็น **1** (แต่บนสายยังเป็น 0 ถ้าอ่านคู่มือแล้วใช้ฐาน 1 ต้องลบ 1)
- **แบบ PLC / 4xxxx:** เติมเลขนำหน้าตามพื้นที่ แล้วนับจาก 1 เช่น holding register แรก = **40001**, input register แรก = **30001** ลบเลขนำหน้าแล้วลบ 1 ได้ protocol address

ตัวอย่าง: คู่มือบอก "Voltage = 40011"
- ฐาน PLC: 40011 → protocol address **10**
- ถ้าคู่มือบอก "Voltage = register 11 (1-based)" → protocol address **10**
- ถ้าคู่มือบอก "Voltage = address 0x000A" → protocol address **10**

ตัวเลือก "Address base" อยู่ในแผง Poll ใต้ช่อง Address จะแสดง `= protocol N` ให้ดู ส่วน preset มีฟิลด์ `address_base` ([[preset-schema]]) และปุ่ม "Try other base" ลองฐานอื่นชั่วคราวเพื่อเทียบกับหน้าจอเครื่อง

## ชนิดข้อมูลและรูปแบบการแสดงผล (Display)
<!-- id: display-formats | category: ข้อมูลและ address | keywords: display format unsigned signed hex binary long float double inverse รูปแบบ แสดง 16 32 64 บิต -->
register หนึ่งตัวมี 16 บิต ค่าที่ใหญ่กว่าใช้หลาย register ต่อกัน

| รูปแบบ | ใช้ register | ช่วงค่า / คำอธิบาย |
|---|---|---|
| Unsigned | 1 | 0 ถึง 65535 |
| Signed | 1 | -32768 ถึง 32767 (two's complement) ใช้กับอุณหภูมิติดลบ |
| Hex | 1 | แสดงเป็นเลขฐาน 16 เช่น 0x4348 |
| Binary | 1 | แสดงทีละบิต ดูสถานะ/แฟล็ก |
| Long | 2 | จำนวนเต็มมีเครื่องหมาย 32 บิต |
| Float | 2 | จุดทศนิยม IEEE 754 32 บิต |
| Double | 4 | จุดทศนิยม IEEE 754 64 บิต |
| Long/Float/Double Inverse | 2 / 2 / 4 | เหมือนเดิมแต่สลับลำดับ word ดู [[word-order]] |

ถ้าไม่แน่ใจว่าเป็นชนิดไหน ใช้ตัวตรวจค่า **F4** ([[inspector]]) ซึ่งแสดงทุกแบบพร้อมกัน หรือเปิด Multi-type view ([[multi-view]]) เพื่อดูทุก register ในหลายรูปแบบเป็นคอลัมน์เคียงกัน

## ลำดับ byte และ word (word order, byte swap)
<!-- id: word-order | category: ข้อมูลและ address | keywords: word order byte swap abcd cdab badc dcba endian big little inverse float สลับ ลำดับ -->
Modbus กำหนดแค่ว่า register หนึ่งตัวส่ง byte สูงก่อน ส่วนค่า 32 บิตที่แยกเป็น 2 register ผู้ผลิตแต่ละรายเรียงต่างกัน ตั้งชื่อ byte เป็น A B C D (A = byte สูงสุด)

| ชื่อ | ลำดับที่เก็บใน register 1, register 2 | หมายเหตุ |
|---|---|---|
| **ABCD** (big-endian) | AB, CD | มาตรฐานของ Modbus เรียก "Float" |
| **CDAB** (word-swapped) | CD, AB | พบบ่อยมาก เรียก "Float Inverse" ใน GUI |
| **BADC** (byte-swapped) | BA, DC | พบน้อย preset ใช้ `byte_swap: true` |
| **DCBA** (little-endian) | DC, BA | preset ใช้ `word_order: little` + `byte_swap: true` |

**วิธีหาลำดับที่ถูก:** อ่าน register คู่นั้น แล้วเปิด **F4** ลำดับที่ให้ค่าสมเหตุสมผล (เช่น 230.4 V ไม่ใช่ 3.2e-38) คือคำตอบ ตัวอย่าง `0x4348 0xCCCD` เป็น 200.8 ในลำดับ ABCD

## Scale, offset และหน่วย
<!-- id: scale-offset | category: ข้อมูลและ address | keywords: scale offset หน่วย unit ทศนิยม หาร คูณ gain engineering value -->
อุปกรณ์จำนวนมากเก็บทศนิยมเป็นจำนวนเต็ม เช่น อุณหภูมิ 25.3 °C เก็บเป็น 253

**ค่าจริง = ค่าดิบ × scale + offset** ใน preset ตั้ง `scale: 0.1` ตอนเขียนกลับ โปรแกรมคำนวณย้อน ถ้าค่าที่ป้อนไม่หารลงตัวกับ scale จะปฏิเสธ

บางรุ่นมี register แยกกำหนดตำแหน่งทศนิยมหรือรหัสหน่วย (เช่น Omron, Autonics, flowmeter) จึงแสดงเป็นค่าดิบพร้อมจุดรหัสหน่วยแยกต่างหากใน preset

## ข้อความ (string), BCD และเวลา
<!-- id: strings-bcd | category: ข้อมูลและ address | keywords: string ascii bcd เวลา unix timestamp ข้อความ ชื่อ serial number -->
- **String:** 1 register เก็บ 2 ตัวอักษร ASCII (byte สูงก่อน) preset ใช้ `type: string` และ `length` เป็นจำนวน register
- **BCD:** แต่ละ nibble (4 บิต) เป็นหนึ่งหลักทศนิยม เช่น 0x1234 = 1234
- **Unix time:** ค่า 32 บิตนับวินาทีตั้งแต่ 1970-01-01 ตัวตรวจค่าแสดงให้เมื่อค่าอยู่ช่วงปี 2000–2100
