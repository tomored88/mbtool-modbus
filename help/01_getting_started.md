## เริ่มต้นใช้งานอย่างเร็ว
<!-- id: quickstart | category: เริ่มต้นใช้งาน | keywords: เริ่มต้น quick start วิธีใช้ ครั้งแรก connect poll -->
ขั้นตอนอ่านค่าจากอุปกรณ์ครั้งแรก:

1. กด **F3** (Connection Setup) เลือกโหมด **TCP**, **RTU** หรือ **ASCII** แล้วกรอกค่าการเชื่อมต่อ (ดู [[connect-tcp]] และ [[connect-serial]])
2. กด **F5** หรือปุ่ม Connect โปรแกรมจะเริ่ม poll ตามค่าในแผง Poll
3. ในแผง Poll ตั้ง **Slave ID**, **Function**, **Address**, **Quantity**, **Scan Rate** และ **Display** (ดู [[poll-panel]])
4. ดูผลในตาราง ค่าที่เปลี่ยนจะกะพริบสีเหลือง แถบสถานะด้านล่างแสดงจำนวนครั้งที่สำเร็จ/ผิดพลาด
5. ถ้าไม่มีอุปกรณ์ ให้ลองกับ simulator: กด **F9** (Slave Sim) หรือรัน `python src/mbtool.py sim --port 5020`

ถ้าค่าที่ได้ไม่ตรงกับหน้าจอเครื่อง ดู [[trouble-values-wrong]]

มีเวอร์ชันใหม่หรือยัง ดู [[updates]]

## เชื่อมต่อแบบ TCP (Modbus/TCP)
<!-- id: connect-tcp | category: เริ่มต้นใช้งาน | keywords: tcp ip host port 502 ethernet เครือข่าย gateway -->
ใช้กับอุปกรณ์ที่มีพอร์ตเครือข่าย หรือ gateway ที่แปลง RS-485 เป็น Ethernet

- **Host:** ที่อยู่ IP หรือชื่อเครื่อง เช่น `192.168.1.50`
- **Port:** ค่ามาตรฐานคือ **502** (บางรุ่นใช้พอร์ตอื่น เช่น Huawei SUN2000 ใช้ 6607)
- **Slave ID (Unit ID):** อุปกรณ์ TCP ส่วนมากไม่สนใจค่านี้ (มักใช้ 1 หรือ 255) แต่ถ้าเป็น gateway ต่อ RS-485 ต่อ ค่านี้คือ Slave ID ของอุปกรณ์ที่อยู่หลัง gateway
- **Timeout:** เวลารอคำตอบ ค่าตั้งต้นประมาณ 1 วินาที ถ้าเครือข่ายช้าให้เพิ่ม

TCP ไม่มี CRC เพราะ TCP ตรวจความถูกต้องเอง แต่ละคำขอมี Transaction ID ใช้จับคู่กับคำตอบ ดู [[frame-tcp]]

## เชื่อมต่อแบบ Serial (RTU / ASCII)
<!-- id: connect-serial | category: เริ่มต้นใช้งาน | keywords: serial rtu ascii com rs485 rs232 baud parity stop bits พอร์ต -->
ใช้กับ RS-485/RS-232 ผ่านตัวแปลง USB

- **COM port:** เลือกจากรายการ (กด Refresh ถ้าเพิ่งเสียบอุปกรณ์) ช่องนี้พิมพ์เองได้ เช่น `socket://127.0.0.1:7000` สำหรับสายจำลอง ([[vcom]])
- **Baud rate, Data bits, Parity, Stop bits:** ต้องตรงกับอุปกรณ์ทุกค่า ดู [[serial-settings]]
- **โหมด RTU หรือ ASCII:** อุปกรณ์เกือบทั้งหมดใช้ RTU ดู [[rtu-vs-ascii]]
- ถ้าไม่ทราบค่า ใช้ตัวสแกนอัตโนมัติ: [[scanner]]
- พอร์ต COM เปิดได้ทีละโปรแกรม ถ้าขึ้น "busy" ให้ปิดโปรแกรมอื่นที่ใช้พอร์ตนั้น ดู [[trouble-port-busy]]

## ทดสอบโดยไม่มีอุปกรณ์ (Simulator และสายจำลอง)
<!-- id: vcom | category: เริ่มต้นใช้งาน | keywords: simulator จำลอง vcom null modem ทดสอบ socket slave sim -->
- **Slave Sim (F9):** เปิด slave จำลองใน GUI แก้ค่าในตารางได้ เลือก TCP, RTU หรือ ASCII
- **ใน CLI:** `python src/mbtool.py sim --port 5020 --preset presets/example_generic_meter.json` ให้ simulator ตอบตามค่า `sim` ของ preset เพิ่ม `--signals profile` ให้ค่าขยับเหมือนอุปกรณ์จริง ([[sim-signals]])
- **สายจำลอง (vcom):** `python src/mbtool.py vcom --port 7000` สร้าง null-modem ผ่าน TCP โดยไม่ต้องติดตั้งไดรเวอร์ ใส่ `socket://127.0.0.1:7000` ในช่อง COM ทั้งฝั่ง master และ slave
- **ข้อจำกัด:** สายจำลองไม่บังคับค่า baud/parity จึงทดสอบการเฟรม RTU ได้ แต่ทดสอบการตั้งค่าผิดไม่ได้

## ปุ่มลัดทั้งหมด
<!-- id: shortcuts | category: เริ่มต้นใช้งาน | keywords: shortcut ปุ่มลัด คีย์ลัด f1 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12 ctrl keyboard ctrl+t ctrl+w tab แท็บ -->
| ปุ่ม | ทำอะไร |
|---|---|
| F1 | เปิดคู่มือนี้ |
| F3 | ตั้งค่าการเชื่อมต่อ (Setup) |
| F4 | ตัวตรวจค่า (Value Inspector) [[inspector]] |
| F5 | เชื่อมต่อ / ตัดการเชื่อมต่อ |
| F6 | เขียนค่า (Write dialog) [[write-dialog]] |
| F7 | Preset [[preset-view]] |
| F8 | Traffic monitor [[traffic-monitor]] |
| F9 | Slave Sim [[slave-sim]] (ทดสอบโดยไม่มีอุปกรณ์: [[vcom]]) |
| F10 | กราฟ Trend [[trend]] |
| F11 | หลายอุปกรณ์ (Devices) [[devices]] |
| F12 | AI Assist [[ai-assist]] |
| Ctrl+L | เริ่ม/หยุดบันทึก log [[logging]] |
| Ctrl+S / Ctrl+O | บันทึก / เปิด workspace [[workspace]] |
| Ctrl+M | แผนผังบัส RS-485 (Bus map) [[bus-map]] |
| Ctrl+T / Ctrl+W | เปิดแท็บ poll ใหม่ / ปิดแท็บ poll ที่เปิดอยู่ [[poll-tabs]] |
| Ctrl+Tab, Ctrl+PgUp / Ctrl+PgDn | สลับแท็บ poll (ดับเบิลคลิกชื่อแท็บ = เปลี่ยนชื่อ, คลิกปุ่มกลาง = ปิด) |
| Esc | ปิดหน้าต่างย่อยส่วนใหญ่ / ยกเลิกการแก้ไข / ล้างช่องค้นหา |

## อัปเดตโปรแกรม (Check for updates)
<!-- id: updates | category: เริ่มต้นใช้งาน | keywords: update อัปเดต เวอร์ชันใหม่ new version check for updates release ดาวน์โหลด download ติดตั้งทับ portable github -->
เมนู **Help > Check for updates...** ตรวจกับหน้า Releases บน GitHub ว่ามี mbtool เวอร์ชันใหม่กว่าที่ใช้อยู่ไหม

- **มีเวอร์ชันใหม่:** จะมีหน้าต่างบอกเลขเวอร์ชัน วันที่ออก และรายการสิ่งที่เปลี่ยน ปุ่ม **Download installer** (ถ้าใช้ตัว portable ปุ่มจะเป็น **Download portable zip**) เปิดลิงก์ดาวน์โหลดในเบราว์เซอร์ **Release page** เปิดหน้า release **Skip this version** ไม่ต้องเตือนเวอร์ชันนี้ตอนเปิดโปรแกรมอีก **Later** ปิดไว้ก่อน
- **เป็นเวอร์ชันล่าสุดแล้ว:** บอกว่า up to date
- **ตรวจตอนเปิดโปรแกรม:** ติ๊ก **Help > Check for updates when mbtool starts** (เปิดไว้เป็นค่าเริ่มต้น) โปรแกรมจะตรวจเองไม่เกินวันละครั้ง ถ้าเครื่องไม่มีอินเทอร์เน็ตจะไม่แจ้งอะไร และไม่ทำให้โปรแกรมช้า

**ติดตั้งเวอร์ชันใหม่:**
- ตัวติดตั้ง: รัน `mbtool-setup.exe` ที่ดาวน์โหลดมา แล้วติดตั้งลงโฟลเดอร์เดิมได้เลย ไม่ต้องถอนก่อน
- Portable: แตกไฟล์ zip ใหม่ แล้วย้ายโฟลเดอร์ `data` จากที่เดิมไปไว้ข้าง `mbtool.exe` ตัวใหม่

ค่าที่ตั้งไว้ อุปกรณ์ workspace และ preset ที่แก้เองยังอยู่ครบ ส่วน preset ในคลังสำเร็จรูปจะอัปเดตเป็นชุดใหม่ให้เอง

โปรแกรมไม่ดาวน์โหลดหรือติดตั้งอะไรเอง และส่งแค่คำขออ่านข้อมูล release สาธารณะของ GitHub (ระบุแค่ชื่อและเวอร์ชันของโปรแกรม) ไม่ส่งข้อมูลอื่นของคุณ CLI: `mbtool-cli.exe update-check` (or `python src/mbtool.py update-check`)
