"""Feature sheet, 3 A4 pages (the last one: screenshots from docs/screenshots), in Thai and English from one layout.

  python docs/make_feature_sheet.py          -> docs/feature-sheet-th.html/.pdf, docs/feature-sheet-en.html/.pdf
PDF is printed with headless Microsoft Edge (commission.to_pdf). Edit the texts below, then run again.
"""
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import appinfo  # noqa: E402

VERSION = appinfo.VERSION

T = {
    "en": {
        "lang": "en", "title": "mbtool - Feature Sheet",
        "tag": "Read, write, simulate and analyse Modbus - and design the RS-485 bus - in one program",
        "ver": f"Version {VERSION}", "os": "Windows 10 / 11",
        "s1": "Master and data", "s2": "Many devices and bus design", "s3": "Data out, reports and AI",
        "cards1": [
            ("Every connection", ["Modbus TCP, RTU and ASCII", "COM port list, baud / parity / stop bits",
                                  "FC 01-06, 15, 16, 22, 23, 43/14, 17", "Several programs share one COM port in turn"]),
            ("Live register table", ["Unsigned / Signed / Hex / Binary / Long / Float / Double",
                                     "Address base 0 / 1 / PLC (4xxxx)", "Multi-type view: several data types side by side",
                                     "Poll tabs: several reads in the main window"]),
            ("Value inspector (F4)", ["One value in every meaning: int, float, text, time, BCD",
                                      "Tries word / byte orders ABCD CDAB BADC DCBA", "Finds the cause of odd values fast"]),
            ("Device presets (F7)", ["Register maps in JSON: name, unit, scale, data type",
                                     "Library of 30 models: meters, drives, level / pressure / flow",
                                     "Range check before writing, editor, import from CSV"]),
            ("Slave simulator (F9)", ["TCP / RTU / ASCII without real hardware",
                                      "Moving values: random, loop, sine, by device type or preset",
                                      "Fault injection: no reply, slow, CRC, exception, wrong ID ..."]),
            ("Automatic scan", ["Finds baud / parity, Slave IDs and readable addresses",
                                "Reads the model (FC43 / FC17) and suggests a preset",
                                "Test Center checks the device against the Modbus rules"]),
        ],
        "cards2": [
            ("Devices window (F11)", ["Many devices at once, one tab each",
                                      "Scan & add: tick the devices found, tabs are created",
                                      "Demo devices: 5 simulated devices to try everything"]),
            ("PLC cycle and calculator", ["Reads every device in turn like a PLC task",
                                          "Measured cycle time, jitter, overruns, line busy %",
                                          "Calculator with baud what-if before you build"]),
            ("Bus health test", ["Real response times per device (avg / p95 / max)",
                                 "Gap sweep: how tightly the line can be polled",
                                 "Recommended timeout and gap - Apply with one click"]),
            ("Error types per device", ["Timeout, CRC, exception, bad frame, wrong ID, no connection",
                                        "Response time and line quality for every device",
                                        "Shown in the device list, device tab and bus map"]),
            ("RS-485 bus map (Ctrl+M)", ["Draw the line: order, cable and stub lengths, terminators",
                                         "Max baud for the length, unit loads, voltage drop",
                                         "Animation of a request and the answer on the wire"]),
            ("Trend, alerts, log", ["Trend graph (F10) of any point", "Alerts: high / low limits with sound",
                                    "CSV log per device, daily or by size, Excel-ready"]),
        ],
        "cards3": [
            ("MQTT / database export", ["Every reading to an MQTT broker (JSON or one topic per point)",
                                        "TLS, retain, minimum interval, keeps data while the broker is away",
                                        "SQLite database file - Grafana, Python, Excel"]),
            ("Commissioning report", ["One click: HTML + PDF, English or Thai",
                                      "Devices, error types, response times, values, cycle time",
                                      "Health test, bus map and sign-off boxes"]),
            ("Manual (F1)", ["85 topics in Thai and English - switch at any time",
                             "F1 on any field opens its topic", "Error messages link to 'what does it mean'"]),
        ],
        "ai_h": "Let an AI analyse the device (F12 AI Assist)",
        "ai_rows": [("Share the live connection", "MCP server in the program: the AI reads the connected device; token, read-only"),
                    ("Copy a report", "Diagnostic report to paste into any AI chat"),
                    ("MCP server", "mbtool-cli.exe mcp - 34 tools for Claude and other MCP clients "
                                   "(scan, read, health test, bus calculator, manual ...)")],
        "ai_th": ("Option", "What it does"),
        "ai_note": "Security: this PC only by default, every write refused unless you allow it, lock-out after wrong tokens, every call logged.",
        "keys": [("F1", "Manual"), ("F3", "Connection"), ("F4", "Inspector"), ("F6", "Write"), ("F9", "Simulator"),
                 ("F11", "Devices"), ("Ctrl+M", "Bus map"), ("F12", "AI Assist")],
        "inst_h": "Install and use",
        "inst": ["<b>mbtool-setup.exe</b> - installer, choose the folder, no admin rights",
                 "<b>mbtool-portable.zip</b> - unzip and run, data in one folder (USB stick)",
                 "No Python needed &nbsp;·&nbsp; <b>mbtool-cli.exe selftest</b> checks the PC in 15 s"],
        "status_h": "Verification status",
        "status": "{tests} automated tests ({suites} test files) against simulators and emulated RS-485 lines, plus fault, load and "
                  "15-minute soak tests. Not yet tested with real devices: every preset is UNVERIFIED - compare with "
                  "the device display before writing. The .exe is not code-signed (SmartScreen: More info > Run anyway).",
        "flow_h": "Typical workflow on site",
        "flow": [("1", "Connect", "TCP or the COM port of the USB-RS485 adapter"),
                 ("2", "Scan & add", "find the Slave IDs, tick them, presets are suggested"),
                 ("3", "Health test", "real response times, set timeout and gap"),
                 ("4", "PLC cycle", "measure the full cycle time, compare with the estimate"),
                 ("5", "Report", "commissioning report HTML / PDF with sign-off"),
                 ("6", "Export", "keep sending data to MQTT / SQLite")],
        "foot_l": "mbtool · built from the public Modbus specification", "foot_r": "mbtool-cli.exe --help",
        "page": "Page",
        "s4": "Screenshots",
        "shots": [("main", "Main window", "Live register table: every register as several data types side by side"),
                  ("devices", "Devices", "Five devices on one RS-485 line, one tab each, error types and response times"),
                  ("health", "Bus health test", "Real response times per device, recommended timeout and gap, expected cycle"),
                  ("busmap", "RS-485 bus map", "Cable lengths, terminators and a request travelling to a device and back")],
        "shots_note": "Captured from mbtool with the built-in demo devices (simulated, no hardware).",
    },
    "th": {
        "lang": "th", "title": "mbtool - Feature Sheet",
        "tag": "อ่าน เขียน จำลอง วิเคราะห์ Modbus และออกแบบบัส RS-485 ครบในโปรแกรมเดียว",
        "ver": f"เวอร์ชัน {VERSION}", "os": "Windows 10 / 11",
        "s1": "Master และข้อมูล", "s2": "หลายอุปกรณ์ และการออกแบบบัส", "s3": "ส่งข้อมูลออก รายงาน และ AI",
        "cards1": [
            ("เชื่อมต่อได้ทุกแบบ", ["Modbus TCP, RTU และ ASCII", "เลือกพอร์ต COM ตั้ง baud / parity / stop bits",
                                   "FC 01-06, 15, 16, 22, 23, 43/14, 17", "หลายส่วนในโปรแกรมใช้พอร์ต COM เดียวกันได้ เรียงคิวกัน"]),
            ("ตารางค่าแบบเรียลไทม์", ["Unsigned / Signed / Hex / Binary / Long / Float / Double",
                                     "ฐาน address 0 / 1 / PLC (4xxxx)", "Multi-type view: ดูหลายชนิดข้อมูลพร้อมกัน",
                                     "แท็บ Poll: อ่านหลายชุดในหน้าต่างหลัก"]),
            ("ตัวตรวจค่า (F4)", ["แปลงค่าเดียวเป็นทุกความหมาย: int, float, ข้อความ, เวลา, BCD",
                                "ลองลำดับ word / byte ABCD CDAB BADC DCBA", "หาสาเหตุค่าเพี้ยนได้เร็ว"]),
            ("Preset อุปกรณ์ (F7)", ["แผนที่รีจิสเตอร์ใน JSON: ชื่อ หน่วย scale ชนิดข้อมูล",
                                    "คลังสำเร็จรูป 30 รุ่น: มิเตอร์ ไดรฟ์ level / pressure / flow",
                                    "ตรวจช่วงค่าก่อนเขียน แก้ preset ได้ นำเข้าจาก CSV"]),
            ("Slave จำลอง (F9)", ["TCP / RTU / ASCII ไม่ต้องมีอุปกรณ์จริง",
                                 "ค่าขยับได้: สุ่ม วนลูป sine ตามชนิดอุปกรณ์ หรือตาม preset",
                                 "จำลองความผิดพลาด: ไม่ตอบ ตอบช้า CRC เสีย exception ID ผิด ..."]),
            ("สแกนอัตโนมัติ", ["หา baud / parity, Slave ID และช่วง address ที่อ่านได้",
                              "อ่านชื่อรุ่น (FC43 / FC17) แล้วแนะนำ preset", "Test Center ตรวจอุปกรณ์ตามกฎ Modbus"]),
        ],
        "cards2": [
            ("หน้าต่าง Devices (F11)", ["อ่านหลายอุปกรณ์พร้อมกัน แยกแท็บละตัว",
                                       "Scan & add: ติ๊กอุปกรณ์ที่เจอ แล้วสร้างแท็บให้",
                                       "Demo devices: อุปกรณ์จำลอง 5 ตัว ลองได้ทุกฟีเจอร์"]),
            ("PLC cycle และเครื่องคำนวณ", ["อ่านทุกอุปกรณ์เรียงกันเหมือน task ของ PLC",
                                          "วัดเวลารอบจริง jitter overrun และ % สายไม่ว่าง",
                                          "คำนวณล่วงหน้า พร้อมตาราง what-if ของ baud"]),
            ("ทดสอบสุขภาพบัส", ["เวลาตอบจริงของแต่ละอุปกรณ์ (เฉลี่ย / p95 / สูงสุด)",
                               "ไล่ลด gap หาความถี่ที่สายรับได้", "แนะนำ timeout และ gap กด Apply ได้ทันที"]),
            ("ชนิด error รายอุปกรณ์", ["Timeout, CRC, exception, เฟรมเสีย, ID ผิด, เชื่อมต่อไม่ได้",
                                      "เวลาตอบและคุณภาพสายของทุกอุปกรณ์", "แสดงในตาราง แท็บอุปกรณ์ และแผนผังบัส"]),
            ("แผนผังบัส RS-485 (Ctrl+M)", ["วาดสาย: ลำดับ ความยาวสาย สายแยก terminator",
                                          "baud สูงสุดตามความยาว unit load แรงดันตก",
                                          "ภาพเคลื่อนไหวคำขอและคำตอบบนสาย"]),
            ("Trend แจ้งเตือน และ log", ["กราฟ Trend (F10) ของจุดไหนก็ได้", "Alerts: เกณฑ์สูง / ต่ำ พร้อมเสียง",
                                        "Log CSV แยกอุปกรณ์ รายวันหรือตามขนาด เปิดใน Excel ได้"]),
        ],
        "cards3": [
            ("ส่งข้อมูลออก MQTT / ฐานข้อมูล", ["ส่งทุกค่าไป MQTT broker (JSON หรือแยก topic ต่อจุด)",
                                               "TLS, retain, กำหนดความถี่ เก็บข้อมูลรอเมื่อ broker หลุด",
                                               "ไฟล์ฐานข้อมูล SQLite ใช้กับ Grafana, Python, Excel"]),
            ("รายงานส่งมอบงาน", ["กดครั้งเดียวได้ HTML + PDF ภาษาไทยหรืออังกฤษ",
                                "อุปกรณ์ ชนิด error เวลาตอบ ค่าที่อ่านได้ เวลารอบ",
                                "ผลทดสอบสุขภาพบัส แผนผังบัส และช่องลงนาม"]),
            ("คู่มือในโปรแกรม (F1)", ["85 หัวข้อ ภาษาไทยและอังกฤษ สลับได้ตลอด",
                                     "กด F1 ที่ช่องไหนก็เปิดหัวข้อของช่องนั้น", "ข้อความ error มีลิงก์ \"ความหมายคืออะไร\""]),
        ],
        "ai_h": "ให้ AI ช่วยวิเคราะห์ (F12 AI Assist)",
        "ai_rows": [("แชร์การเชื่อมต่อสด", "เซิร์ฟเวอร์ MCP ในโปรแกรม ให้ AI อ่านอุปกรณ์ที่ต่ออยู่ มี token อ่านอย่างเดียว"),
                    ("คัดลอกรายงาน", "รายงานวินิจฉัยไปวางใน AI ตัวไหนก็ได้"),
                    ("MCP server", "mbtool-cli.exe mcp - 34 เครื่องมือ สำหรับ Claude และ AI อื่นที่รองรับ MCP "
                                   "(สแกน อ่าน ทดสอบบัส คำนวณรอบ คู่มือ ...)")],
        "ai_th": ("ทางเลือก", "ทำอะไร"),
        "ai_note": "ความปลอดภัย: ค่าเริ่มต้นรับเฉพาะเครื่องนี้ ปฏิเสธการเขียนจนกว่าคุณอนุญาต ล็อกเมื่อ token ผิดซ้ำ บันทึกทุกการเรียกใช้",
        "keys": [("F1", "คู่มือ"), ("F3", "ตั้งค่าเชื่อมต่อ"), ("F4", "ตัวตรวจค่า"), ("F6", "เขียนค่า"),
                 ("F9", "Slave จำลอง"), ("F11", "Devices"), ("Ctrl+M", "แผนผังบัส"), ("F12", "AI Assist")],
        "inst_h": "ติดตั้งและใช้งาน",
        "inst": ["<b>mbtool-setup.exe</b> ตัวติดตั้ง เลือกโฟลเดอร์เองได้ ไม่ต้องสิทธิ์ admin",
                 "<b>mbtool-portable.zip</b> แตกไฟล์แล้วรันได้เลย ข้อมูลอยู่ในโฟลเดอร์เดียว (ใส่ USB ได้)",
                 "ไม่ต้องมี Python &nbsp;·&nbsp; <b>mbtool-cli.exe selftest</b> ตรวจเครื่องใน 15 วินาที"],
        "status_h": "สถานะการตรวจสอบ",
        "status": "ทดสอบอัตโนมัติ {tests} ข้อ ({suites} ไฟล์) กับ simulator และสาย RS-485 จำลอง รวมทดสอบความผิดพลาด โหลด และรันยาว 15 นาที "
                  "ยังไม่ได้ทดสอบกับอุปกรณ์จริง preset ทุกไฟล์ยังเป็น UNVERIFIED ให้เทียบกับหน้าจอเครื่องก่อนเขียนค่า "
                  "และ .exe ยังไม่ได้ลงนามดิจิทัล (SmartScreen: More info > Run anyway)",
        "flow_h": "ขั้นตอนใช้งานหน้างานโดยทั่วไป",
        "flow": [("1", "เชื่อมต่อ", "TCP หรือพอร์ต COM ของ USB-RS485"),
                 ("2", "Scan & add", "หา Slave ID ติ๊กเลือก โปรแกรมแนะนำ preset"),
                 ("3", "Health test", "วัดเวลาตอบจริง ตั้ง timeout และ gap"),
                 ("4", "PLC cycle", "วัดเวลารอบทั้งบัส เทียบกับค่าคำนวณ"),
                 ("5", "รายงาน", "ออกรายงานส่งมอบงาน HTML / PDF พร้อมลงนาม"),
                 ("6", "ส่งข้อมูลออก", "ส่งค่าต่อเนื่องไป MQTT / SQLite")],
        "foot_l": "mbtool · พัฒนาจากข้อกำหนดสาธารณะของ Modbus", "foot_r": "mbtool-cli.exe --help",
        "page": "หน้า",
        "s4": "ภาพหน้าจอโปรแกรม",
        "shots": [("main", "หน้าต่างหลัก", "ตารางค่าแบบเรียลไทม์ แสดงทุก register เป็นหลายชนิดข้อมูลพร้อมกัน"),
                  ("devices", "หน้าต่าง Devices", "5 อุปกรณ์บนสาย RS-485 เส้นเดียว แยกแท็บละตัว พร้อมชนิด error และเวลาตอบ"),
                  ("health", "ทดสอบสุขภาพบัส", "เวลาตอบจริงของแต่ละอุปกรณ์ ค่า timeout / gap ที่แนะนำ และเวลารอบที่คาด"),
                  ("busmap", "แผนผังบัส RS-485", "ความยาวสาย terminator และภาพคำขอวิ่งไปหาอุปกรณ์แล้วตอบกลับ")],
        "shots_note": "ถ่ายจาก mbtool โดยใช้ Demo devices ที่มีในโปรแกรม (อุปกรณ์จำลอง ไม่ต้องมีฮาร์ดแวร์)",
    },
}

CSS = """
@page { size: A4; margin: 0; }
:root { --blue:#1a64d6; --ink:#1b1f27; --mute:#59606b; --line:#d7dbe1; --soft:#f4f6f9; }
* { box-sizing: border-box; }
body { margin:0; font-family:"Segoe UI","Leelawadee UI","Tahoma",sans-serif; color:var(--ink); background:#fff; }
.page { width:210mm; height:297mm; padding:11mm 12mm; display:flex; flex-direction:column; gap:4.2mm;
        page-break-after:always; overflow:hidden; }
.page:last-child { page-break-after:auto; }
header { display:flex; align-items:center; gap:5mm; background:var(--blue); color:#fff; border-radius:3mm; padding:5mm 6mm; }
.logo { width:15mm; height:15mm; border-radius:3mm; background:#fff; display:grid; place-items:center; flex:none; }
.logo svg { width:11mm; height:11mm; }
h1 { margin:0; font-size:21pt; line-height:1.1; }
header p { margin:1mm 0 0; font-size:10pt; opacity:.92; }
.sec { display:flex; align-items:center; gap:3mm; font-size:11.5pt; font-weight:700; color:var(--ink); margin-top:1mm; }
.sec:after { content:""; flex:1; border-top:1.5px solid var(--line); }
.grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:3.2mm; }
.card { border:1px solid var(--line); border-radius:2.5mm; padding:3mm 3.5mm; background:#fff; }
.card h2 { margin:0 0 1.4mm; font-size:10.8pt; color:var(--blue); }
.card ul { margin:0; padding-left:4mm; font-size:8.9pt; line-height:1.45; }
.card li { margin:0 0 .6mm; }
.keys { display:grid; grid-template-columns:repeat(8,1fr); gap:2mm; }
.key { background:var(--soft); border-radius:2mm; padding:1.8mm 1.5mm; font-size:7.8pt; text-align:center; }
.key b { display:block; color:var(--blue); font-size:9.5pt; }
h3 { margin:0 0 1.6mm; font-size:10.5pt; }
.two { display:grid; grid-template-columns:1fr 1fr; gap:3.2mm; }
table { width:100%; border-collapse:collapse; font-size:8.3pt; }
td, th { text-align:left; padding:1.1mm 1.6mm; border-bottom:1px solid var(--line); vertical-align:top; }
th { background:var(--soft); }
td:first-child { white-space:nowrap; font-weight:600; }
code { font-family:Consolas,monospace; font-size:8pt; }
.note { font-size:8.2pt; color:var(--mute); line-height:1.4; margin:1.6mm 0 0; }
.warn { background:#fff6e0; border:1px solid #f0d28a; border-radius:2.5mm; padding:3mm 3.5mm; font-size:8.3pt; line-height:1.45; }
.plain { margin:0; padding-left:4mm; font-size:8.4pt; line-height:1.5; }
.flow { display:grid; grid-template-columns:repeat(6,1fr); gap:2mm; }
.step { border:1px solid var(--line); border-top:3px solid var(--blue); border-radius:2mm; padding:2.4mm 2.4mm; font-size:8.3pt; line-height:1.4; }
.step b { display:block; font-size:9.6pt; margin-bottom:.8mm; }
.step i { font-style:normal; color:var(--blue); font-weight:700; margin-right:1mm; }
.shots { display:flex; flex-direction:column; gap:3mm; }
.shot { display:grid; grid-template-columns:112mm 1fr; gap:4mm; align-items:center; border:1px solid var(--line);
        border-radius:2.5mm; padding:2.5mm 3.5mm 2.5mm 2.5mm; background:#fff; }
.shot img { width:112mm; max-height:55mm; object-fit:contain; object-position:left center; display:block;
            border:1px solid var(--line); border-radius:1.5mm; }
.shot h2 { margin:0 0 1.4mm; font-size:11pt; color:var(--blue); }
.shot p { margin:0; font-size:9pt; line-height:1.5; color:var(--ink); }
footer { margin-top:auto; display:flex; justify-content:space-between; font-size:8pt; color:var(--mute);
         border-top:1px solid var(--line); padding-top:2mm; }
@media screen { body { background:#e9ecf1; } .page { margin:8mm auto; background:#fff; box-shadow:0 2px 14px #0003; } }
"""

LOGO = ('<div class="logo"><svg viewBox="0 0 48 48"><path d="M5 34 L5 14 L15 14 L15 34 L25 34 L25 14 L35 14 L35 34 L43 34" '
        'fill="none" stroke="#1a64d6" stroke-width="5" stroke-linejoin="round"/></svg></div>')


def e(s):
    return html.escape(s)


def cards(items):
    return '<div class="grid">' + "".join(
        f'<div class="card"><h2>{e(h)}</h2><ul>' + "".join(f"<li>{e(x)}</li>" for x in lis) + "</ul></div>"
        for h, lis in items) + "</div>"


def render(t):
    head = (f'<header>{LOGO}<div><h1>mbtool &nbsp;·&nbsp; Modbus master &amp; simulator</h1>'
            f'<p>{e(t["tag"])} &nbsp;|&nbsp; {e(t["ver"])} &nbsp;|&nbsp; {e(t["os"])}</p></div></header>')

    def foot(n):
        return f'<footer><span>{e(t["foot_l"])}</span><span>{e(t["foot_r"])} &nbsp;·&nbsp; {e(t["page"])} {n}/3</span></footer>'
    ai = (f'<div class="card"><h3>{e(t["ai_h"])}</h3><table><tr><th>{e(t["ai_th"][0])}</th><th>{e(t["ai_th"][1])}</th></tr>'
          + "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td></tr>" for a, b in t["ai_rows"])
          + f'</table><p class="note">{e(t["ai_note"])}</p></div>')
    keys = '<div class="keys">' + "".join(f'<div class="key"><b>{e(k)}</b>{e(v)}</div>' for k, v in t["keys"]) + "</div>"
    inst = (f'<div class="two"><div class="card"><h3>{e(t["inst_h"])}</h3><ul class="plain">'
            + "".join(f"<li>{x}</li>" for x in t["inst"]) + "</ul></div>"
            f'<div class="warn"><b>{e(t["status_h"])}</b><br>{e(t["status"])}</div></div>')
    p1 = (f'<div class="page">{head}<div class="sec">{e(t["s1"])}</div>{cards(t["cards1"])}'
          f'<div class="sec">{e(t["s2"])}</div>{cards(t["cards2"])}{foot(1)}</div>')
    flow = (f'<div class="sec">{e(t["flow_h"])}</div><div class="flow">' + "".join(
        f'<div class="step"><b><i>{e(n)}</i>{e(h)}</b>{e(d)}</div>' for n, h, d in t["flow"]) + "</div>")
    p2 = (f'<div class="page"><div class="sec">{e(t["s3"])}</div>{cards(t["cards3"])}'
          f'{ai}{flow}{keys}{inst}{foot(2)}</div>')
    shots = '<div class="shots">' + "".join(
        f'<div class="shot"><img src="{img_uri(f)}" alt="{e(h)}"><div><h2>{e(h)}</h2><p>{e(d)}</p></div></div>'
        for f, h, d in t["shots"]) + "</div>"
    p3 = (f'<div class="page"><div class="sec">{e(t["s4"])}</div>{shots}'
          f'<p class="note">{e(t["shots_note"])}</p>{foot(3)}</div>')
    return (f'<!doctype html><html lang="{t["lang"]}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(t["title"])}</title>'
            f'<style>{CSS}</style></head><body>{p1}{p2}{p3}</body></html>')


def img_uri(name):
    """docs/screenshots/<name>.png as a data: URI, so the HTML sheet is one self-contained file."""
    import base64
    with open(os.path.join(HERE, "screenshots", name + ".png"), "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")


def test_counts():
    """(number of tests, number of test files) of tests/, counted by pytest itself - never typed by hand."""
    import re
    import subprocess
    root = os.path.dirname(HERE)
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"], cwd=root,
                         capture_output=True, text=True, timeout=300).stdout
    m = re.search(r"(\d+) tests? collected", out)
    if not m:
        raise SystemExit("could not count the tests (is pytest installed?): " + out[-500:])
    files = sorted({line.split("::")[0] for line in out.splitlines() if "::" in line})
    return int(m.group(1)), len(files)


def main():
    import commission
    tests, suites = test_counts()
    print(f"{tests} tests in {suites} test files")
    for lang, t in T.items():
        t = dict(t, status=t["status"].format(tests=tests, suites=suites))
        path = os.path.join(HERE, f"feature-sheet-{lang}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render(t))
        pdf = commission.to_pdf(path, path[:-5] + ".pdf")
        print(path, pdf)


if __name__ == "__main__":
    main()
