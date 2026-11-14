# Contributing to mbtool

Thanks for helping. Bug reports, presets checked on real devices, translations, fixes and new features are all
welcome. mbtool is MIT licensed; by sending a pull request you agree that your contribution is released under the
same [MIT license](LICENSE).

(ภาษาไทยอยู่ท้ายไฟล์)

## Ways to help

- **Report a bug** - open an [issue](../../issues/new/choose) with the version (`mbtool-cli.exe --version`), what you
  did, what you expected and what happened. A copy of the Traffic window (F8) or `crash.log` (Help > Open data
  folder > logs) helps a lot.
- **Verify a preset on a real device** - every preset in `presets/library` is marked `"verified": false` because it
  was written from a manual. If you have the device, compare the values with its display and send the corrections
  (and the firmware version) - this is the most valuable contribution for users.
- **Add a preset** - copy `presets/example_generic_meter.json`, fill it from the manufacturer's public manual and
  keep `"verified": false` until checked on a device. Use the Preset editor (F7 > Edit...) or see the manual topic
  "Preset files: every field explained".
- **Improve the manual** - topics are Markdown in `help/` (Thai) and `help/en/` (English). Both languages must keep
  the same topic ids, links, tables and code blocks (a test checks this).
- **Code** - see below.

## Development setup

Windows 10 / 11, Python 3.11 or newer (CI tests 3.11 and 3.13). 3.10 is not enough: its `time.sleep` on
Windows is only accurate to about 15 ms, which breaks the RTU frame gaps and the PLC cycle timing.

```
git clone https://github.com/tomored88/mbtool-modbus.git
cd mbtool-modbus
install.bat                         # creates .venv and installs requirements.txt
.venv\Scripts\python -m pip install pytest
run.bat                             # starts the GUI from source
```

Without the batch files: `pip install -r requirements.txt pytest`, then `python src/mbtool.py`.

No hardware is needed for development: the GUI has **Demo devices** (Devices window, F11), `python src/mbtool.py sim`
starts a slave simulator, and `python src/mbtool.py vcom` gives an emulated RS-485 line for RTU tests.

## Tests

```
python -m pytest tests                  # everything, about 3-4 minutes
python -m pytest tests/test_core.py     # one file
python src/mbtool.py selftest           # the self test that also ships in the exe
```

- Every pull request runs the full suite on GitHub Actions (Windows). Please run it locally first.
- GUI tests drive the real Tk windows in the same process (no mouse or keyboard automation); they open and close
  windows quickly on your screen, that is normal.
- New features need tests. Pure logic goes into a module without Tk (see `monitor.py`, `bushealth.py`,
  `export.py`) and gets plain unit tests; the GUI part gets an in-process test like `tests/test_healthui.py`.
- `tests/soak.py` (long run) and `tests/load_bus.py` (many devices) are manual tools, not part of the suite.
- Keep `--capture=sys` in `pytest.ini`: pytest's default fd capture makes Tk's start-up fail intermittently on
  Windows. Time anything at millisecond scale with `time.perf_counter()` - `time.monotonic()` ticks only every
  ~15.6 ms on Windows before Python 3.13.

## Building the Windows app

```
pip install pyinstaller pillow
packaging\build_exe.bat
```

Output in `dist\`: `mbtool\` (the program folder), `mbtool-setup.exe` (installer) and `mbtool-portable.zip`.
Check the result with `dist\mbtool\mbtool-cli.exe selftest`. The version number is `VERSION` in `src/appinfo.py`.

## Code guidelines

- Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first: where things live and how the threads talk to the GUI.
- GUI code lives in `src/mbui/`, one module per window. A module that needs a class from a sibling module imports it
  at the bottom of the file (see the existing modules) and only uses it inside functions.
- Python standard library + pyserial for the program; `mcp` only for the MCP server. Ask in an issue before adding a
  new dependency (it also has to work in the PyInstaller build).
- Never touch Tk from a worker thread: post a message to the app queue and handle it on the GUI thread.
- Device I/O must never block the GUI thread.
- Keep the MCP server read-only by default. Anything that writes to a device must stay behind
  `MBTOOL_ALLOW_WRITE=1` plus the address allowlist.
- User-visible text in the program is English; the manual exists in Thai and English - update both (or open the
  pull request and say which language still needs translating).
- Write the protocol from the public Modbus specification. Do not copy code, text, icons or screenshots from other
  Modbus tools, and do not use their product names in the program, manual or README.
- Keep pull requests focused: one fix or feature per pull request, with a short description of what and why.

## Pull request checklist

- [ ] `python -m pytest tests` passes
- [ ] new behaviour has a test
- [ ] manual updated (Thai and English) if the user sees the change
- [ ] no other product's name, code or text copied in

---

## ภาษาไทย (สรุป)

ยินดีรับทุกการช่วยเหลือ: แจ้งบั๊ก, ตรวจ preset กับอุปกรณ์จริง, แปลคู่มือ, แก้โค้ด และเพิ่มฟีเจอร์
โค้ดที่ส่งเข้ามาจะอยู่ภายใต้ license MIT เหมือนโปรเจกต์

- **แจ้งบั๊ก:** เปิด [issue](../../issues/new/choose) บอกเวอร์ชัน สิ่งที่ทำ ผลที่ควรได้ และผลที่ได้จริง แนบ Traffic (F8) หรือ `crash.log` ถ้ามี
- **ตรวจ preset กับเครื่องจริง:** preset ทุกไฟล์ยังเป็น `"verified": false` ถ้ามีอุปกรณ์ เทียบค่ากับหน้าจอเครื่องแล้วส่งที่แก้มา ช่วยผู้ใช้ได้มากที่สุด
- **ติดตั้งเพื่อพัฒนา:** `install.bat` แล้ว `run.bat` (ต้องมี Python 3.11 ขึ้นไป) ไม่ต้องมีฮาร์ดแวร์ ใช้ Demo devices หรือ `python src/mbtool.py sim` ได้
- **ทดสอบ:** `python -m pytest tests` ต้องผ่านก่อนส่ง Pull Request และ GitHub จะรันเทสต์ทั้งชุดให้อัตโนมัติทุกครั้ง
- **build ตัวติดตั้ง:** `packaging\build_exe.bat`
- **กติกา:** ห้ามใช้ Tk จาก thread อื่น, MCP ต้องอ่านอย่างเดียวเป็นค่าเริ่มต้น, ข้อความในโปรแกรมเป็นภาษาอังกฤษ คู่มือแก้ทั้งไทยและอังกฤษ,
  เขียนตามมาตรฐาน Modbus ห้ามคัดลอกโค้ด ข้อความ หรือใช้ชื่อสินค้าของโปรแกรมอื่น
- โครงสร้างโปรแกรมอ่านได้ที่ [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
