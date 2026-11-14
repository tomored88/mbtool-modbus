## ไฟล์ Preset: ทุกฟิลด์อธิบาย
<!-- id: preset-schema | category: Preset และเครื่องมือ | keywords: preset schema json ฟิลด์ name address fc type word_order byte_swap scale offset unit access min max group enum sim length address_base max_block alarms notes source verified -->
Preset คือไฟล์ JSON แผนที่ register ของอุปกรณ์หนึ่งรุ่น อยู่ในโฟลเดอร์ `presets/` (ไลบรารี 30 ไฟล์ใน `presets/library/`)

**ระดับไฟล์**

| ฟิลด์ | ความหมาย |
|---|---|
| `name`, `manufacturer`, `model` | ชื่อที่แสดงและใช้จับคู่ตอนระบุอุปกรณ์ |
| `verified` | `true` เมื่อเทียบกับอุปกรณ์จริงแล้ว ([[verified-presets]]) |
| `connection` | ค่าเชื่อมต่อแนะนำ: `type` (tcp/rtu), `baud`, `parity`, `stop`, `data`, `unit`, `host`, `port`, `timeout` |
| `address_base` | `0` = address ในไฟล์เป็น protocol address, `1` = นับจาก 1 ([[address-bases]]) |
| `max_block` | จำนวน register สูงสุดต่อคำขอที่อุปกรณ์รับได้ (1–125) |
| `alarms` | (ไม่บังคับ) กฎแจ้งเตือน ([[alerts]]) |
| `source`, `doc_revision`, `notes` | แหล่งที่มา เวอร์ชันเอกสาร และข้อควรระวัง |

**ระดับจุดวัด (`points`)**

| ฟิลด์ | ความหมาย |
|---|---|
| `name` | ชื่อไม่ซ้ำ |
| `address` | address ตาม `address_base` |
| `fc` | 1, 2, 3 หรือ 4 (พื้นที่ที่อ่าน) |
| `type` | `uint16 int16 uint32 int32 uint64 int64 float32 float64 bool string` |
| `length` | (string) จำนวน register |
| `word_order` | `big` (word สูงก่อน) หรือ `little` ([[word-order]]) |
| `byte_swap` | `true` สลับ byte ภายในทุก register |
| `scale`, `offset` | ค่าจริง = ดิบ × scale + offset ([[scale-offset]]) |
| `unit` | หน่วยที่แสดง |
| `access` | `r` อ่านอย่างเดียว, `rw` อ่านเขียนได้, `w` เขียนอย่างเดียว (คำสั่ง ไม่ถูกอ่าน) |
| `min`, `max` | ช่วงค่าที่อนุญาตตอนเขียน |
| `group` | กลุ่ม เช่น measure, energy, config, control |
| `enum` | แปลงค่าเป็นข้อความ เช่น `{"0":"Stop","1":"Run"}` |
| `sim` | ค่าที่ simulator ใช้เมื่อเติมจาก preset |
| `sim_pattern` | วิธีที่ simulator ทำให้ค่าขยับ เช่น `{"kind":"sine","min":0,"max":10,"period":30}` ดู [[sim-signals]] |
| `device_type` (ระดับ preset) | ชนิดอุปกรณ์สำหรับโหมด Device behaviour: meter, drive, level, pressure, flow, temperature, io |

การอ่านรวมจุดที่อยู่ใกล้กันเป็นคำขอเดียวโดยอัตโนมัติ (ช่องว่างไม่เกิน 4 register และไม่เกิน `max_block`)

## ทำไม preset ถึงเป็น "ยังไม่ยืนยัน" (UNVERIFIED)
<!-- id: verified-presets | category: Preset และเครื่องมือ | keywords: verified unverified ยืนยัน ไลบรารี library เชื่อถือ ตรวจสอบ ความถูกต้อง -->
preset ในไลบรารีเขียนจากคู่มือที่เผยแพร่สาธารณะ **ยังไม่เคยทดสอบกับอุปกรณ์จริง** แผนที่ register ที่ผิดทำให้ค่าเพี้ยนหรือเขียนผิด โดยเฉพาะไดรฟ์มอเตอร์ เครื่องมือวัดการไหล/ระดับ ซึ่งใช้ตัดสินใจ

วิธียืนยัน: ต่ออุปกรณ์ เลือก preset เทียบแต่ละค่ากับหน้าจอเครื่อง (ใช้ "Try other base" ถ้าเหลื่อม 1) แก้จุดที่ผิดในตัวแก้ preset แล้วติ๊ก "Mark as verified" พร้อมหมายเหตุ ไฟล์ทุกไฟล์ระบุ URL แหล่งที่มาและข้อสงสัยใน `notes`

MCP ปฏิเสธการเขียนผ่าน preset ที่ยังไม่ยืนยัน ([[mcp-security]])

## Test Center
<!-- id: testcenter | category: Preset และเครื่องมือ | keywords: test center ทดสอบ slave มาตรฐาน conformance exception เวลาตอบ testcenter -->
ตรวจว่า slave ทำตามกฎของ Modbus: อ่านทั้ง 4 พื้นที่, จำนวนที่ส่งกลับตรง, quantity 0 หรือเกินขีดจำกัดต้องตอบ exception 03, address เกินช่วงต้องตอบ 02, function ที่ไม่รู้จักต้องตอบ 01, คำขอตัดทอนต้องไม่ทำให้อุปกรณ์ค้าง และวัดเวลาตอบ (ต่ำสุด/เฉลี่ย/สูงสุด)

ผลเป็น PASS / FAIL / WARN / SKIP **โหมดค่าตั้งต้นอ่านอย่างเดียว** ปลอดภัยกับอุปกรณ์จริง การทดสอบเขียน (FC05/06/15/16/22/23) ต้องเปิดเอง (`--write --scratch <address>`) และคืนค่าเดิมหลังทดสอบ

`python src/testcenter.py tcp://host:502 -u 1 --addr 0`

## สร้าง Preset จากคู่มือใหม่
<!-- id: preset-howto | category: Preset และเครื่องมือ | keywords: สร้าง preset ใหม่ csv excel นำเข้า register map คู่มือ วิธี -->
1. ถ้ามีตาราง register ใน Excel ให้ทำ CSV คอลัมน์ `name,address,fc,type,scale,offset,unit,access,min,max,group,word_order,byte_swap,length,enum` (ต้องมีแค่ `name` กับ `address`) enum เขียนเป็น `0=Stop;1=Run`
2. ใน Preset dialog เลือก Import CSV แล้วตรวจในตัวแก้ preset
3. ตั้ง `address_base` ให้ตรงกับวิธีนับของคู่มือ ([[address-bases]])
4. ทดลองอ่านกับอุปกรณ์หรือ simulator เทียบกับหน้าจอ แล้ว Mark as verified

## MCP: ให้ AI ใช้เครื่องมือ Modbus
<!-- id: mcp | category: Preset และเครื่องมือ | keywords: mcp server ai claude เครื่องมือ tools ลงทะเบียน register stdio http -->
`mbtool_mcp.py` คือ MCP server ให้ AI ที่รองรับ MCP เรียกใช้ได้: เชื่อมต่อ อ่านค่า สรุปค่าซ้ำ ดู traffic สแกน ระบุอุปกรณ์ ตีความ register รายงานวินิจฉัย Test Center อ่านตาม preset และค้นคู่มือนี้

- **stdio:** โปรแกรมไคลเอนต์เปิดเอง ลงทะเบียนด้วยไฟล์ `.mcp.json` (ตัวอย่างใน `.mcp.json.example`)
- **HTTP:** `python src/mbtool_mcp.py --http` แชร์ให้ AI ของคนอื่น ต้องมี token ดู [[mcp-security]]
- **การเขียนค่าปิดเป็นค่าเริ่มต้น** เปิดด้วย `MBTOOL_ALLOW_WRITE=1` และระบุช่วงใน `MBTOOL_WRITE_ALLOW` เช่น `1:3:100-110;1:0:0-7` (Slave:พื้นที่:ช่วง พื้นที่ 3 = holding, 0 = coil)

เครื่องมือหลัก: `status`, `connect`, `read`, `snapshot`, `get_traffic`, `sim_start`, `sim_load_preset` (ค่าจำลองขยับตาม preset ดู [[sim-signals]]), `scan_serial_settings`, `scan_unit_ids`, `scan_register_map`, `identify_device`, `interpret_registers`, `diagnostic_report`, `test_center`, `list_presets`, `preset_read`, `search_help`, `get_help`

## ตรวจตัวโปรแกรมเอง (Self test)
<!-- id: selftest | category: Preset และเครื่องมือ | keywords: selftest self test ตรวจ ทดสอบ โปรแกรม ติดตั้ง exe ใช้ได้ไหม pass fail -->
คำสั่ง `mbtool-cli.exe selftest` (ตัวติดตั้ง/portable) หรือ `python src/mbtool.py selftest` ตรวจว่าโปรแกรมบนเครื่องนี้ทำงานครบ โดยไม่ต้องมีอุปกรณ์จริง แล้วพิมพ์ PASS / FAIL ทีละข้อ:

- preset ในไลบรารีเปิดได้ทุกไฟล์
- อ่าน/เขียน TCP กับ simulator, RTU บนสายจำลอง 3 slave และการต่อใหม่หลัง TCP หลุด
- MCP server, ตัวสแกน, ตัวคำนวณรอบ, Bus Map และค่าจำลองที่ขยับ
- GUI: poll หลัก, Multi-type view, แท็บ poll, Demo devices และหน้าต่างหลักทุกบาน

ไม่เปิดพอร์ต COM จริง ไม่แตะการตั้งค่าของคุณ (ใช้โฟลเดอร์ชั่วคราว) ใช้เวลาประมาณ 15 วินาที เหมาะสำหรับเช็กหลังติดตั้งบนเครื่องใหม่ ถ้ามี FAIL ให้ส่งข้อความนั้นมาด้วยเมื่อแจ้งปัญหา

## ความปลอดภัยของ MCP แบบ HTTP
<!-- id: mcp-security | category: Preset และเครื่องมือ | keywords: security ความปลอดภัย token http lan audit log เขียน allowlist vpn tailscale ล็อก rate limit -->
- ทุกคำขอต้องมี **bearer token** (เปรียบเทียบแบบเวลาคงที่) ใส่ผิดเกิน 10 ครั้งใน 1 นาทีจะถูกล็อก 1 นาที และจำกัดจำนวนคำขอต่อวินาที
- ผูกกับ 127.0.0.1 (เครื่องนี้เท่านั้น) เว้นแต่คุณสั่ง `--host` เอง ข้ามเครือข่ายเป็น **HTTP ธรรมดา** ใช้ VPN เช่น Tailscale หรือใส่ `--certfile/--keyfile`
- AI ที่เชื่อมมา **เลือกเป้าหมายเองไม่ได้** (ต่อ ตัด สแกนพอร์ต COM) เว้นแต่ `--allow-connect`
- **เขียนค่าไม่ได้เลย** ในโหมด HTTP เว้นแต่ `--allow-write` พร้อมรายการช่วงที่อนุญาต
- ทุกการเรียกเครื่องมือและการล็อกอินผิดถูกบันทึกใน `~/.mbtool/mcp_audit.log` (ไม่บันทึก token)
- ค่าที่อ่านจากอุปกรณ์ถือเป็นข้อมูลเท่านั้น ไม่ใช่คำสั่งของผู้ใช้

## อ้างอิงคำสั่ง CLI
<!-- id: cli | category: Preset และเครื่องมือ | keywords: cli command line คำสั่ง terminal python mbtool read write sim vcom preset url -->
รูปแบบ URL: `tcp://host:502` หรือ `serial:COM3?baud=19200&parity=E&stop=1&mode=rtu` (`mode` เป็น `rtu` หรือ `ascii`)

| คำสั่ง | ทำอะไร |
|---|---|
| `python src/mbtool.py` | เปิด GUI |
| `python src/mbtool.py read URL -u 1 -f 3 -a 0 -n 10 [--fmt Float] [--scan 1000] [--log out.csv]` | อ่านค่า |
| `python src/mbtool.py write URL -u 1 -f 16 -a 50 --fmt Float 3.14` | เขียนค่า |
| `python src/mbtool.py sim --port 5020 [--preset f.json] [--signals profile --device-type drive --speed 2] [--serial COM4 --mode rtu]` | slave จำลอง ค่าขยับตามโหมด ([[sim-signals]]) |
| `python src/mbtool.py vcom --port 7000` | สายจำลอง null-modem |
| `python src/mbtool.py preset-list / preset-read / preset-write` | ทำงานกับ preset |
| `python src/scanner.py serial COM3` | สแกนค่าเชื่อมต่อ ([[scanner]]) |
| `python src/testcenter.py URL` | ตรวจ slave ([[testcenter]]) |
| `python src/interpret.py --words 0x4348 0xCCCD` | ตีความ register ([[inspector]]) |
| `python src/helpdocs.py search คำ` | ค้นคู่มือนี้ |
| `mbtool-cli.exe selftest` (หรือ `python src/mbtool.py selftest`) | ตรวจตัวโปรแกรมบนเครื่องนี้โดยไม่ต้องมีอุปกรณ์ ([[selftest]]) |
