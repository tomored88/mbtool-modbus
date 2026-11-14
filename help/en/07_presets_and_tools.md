## Preset files: every field explained
<!-- id: preset-schema | category: Presets and tools | keywords: preset, schema, json, field, name, address, fc, type, word_order, byte_swap, scale, offset, unit, access, min, max, group, enum, sim, length, address_base, max_block, alarms, notes, source, verified -->
A preset is a JSON file holding the register map of one device model. Presets live in the `presets/` folder (the library of 30 files is in `presets/library/`).

**File level**

| Field | Meaning |
|---|---|
| `name`, `manufacturer`, `model` | Display name, also used for matching when identifying a device |
| `verified` | `true` once checked against a real device ([[verified-presets]]) |
| `connection` | Recommended connection settings: `type` (tcp/rtu), `baud`, `parity`, `stop`, `data`, `unit`, `host`, `port`, `timeout` |
| `address_base` | `0` = addresses in the file are protocol addresses, `1` = counted from 1 ([[address-bases]]) |
| `max_block` | Maximum number of registers per request that the device accepts (1–125) |
| `alarms` | (optional) Alert rules ([[alerts]]) |
| `source`, `doc_revision`, `notes` | Source, document revision, and cautions |

**Point level (`points`)**

| Field | Meaning |
|---|---|
| `name` | Unique name |
| `address` | Address according to `address_base` |
| `fc` | 1, 2, 3 or 4 (the area that is read) |
| `type` | `uint16 int16 uint32 int32 uint64 int64 float32 float64 bool string` |
| `length` | (string) number of registers |
| `word_order` | `big` (high word first) or `little` ([[word-order]]) |
| `byte_swap` | `true` swaps the bytes within every register |
| `scale`, `offset` | Real value = raw × scale + offset ([[scale-offset]]) |
| `unit` | Displayed unit |
| `access` | `r` read-only, `rw` read/write, `w` write-only (a command, never read) |
| `min`, `max` | Allowed value range when writing |
| `group` | Group, e.g. measure, energy, config, control |
| `enum` | Maps values to text, e.g. `{"0":"Stop","1":"Run"}` |
| `sim` | Value the simulator uses when filled from the preset |
| `sim_pattern` | How the simulator makes the value move, e.g. `{"kind":"sine","min":0,"max":10,"period":30}`; see [[sim-signals]] |
| `device_type` (preset level) | Device type for Device behaviour mode: meter, drive, level, pressure, flow, temperature, io |

Reads automatically merge nearby points into a single request (gap of at most 4 registers and no more than `max_block`).

## Why a preset is "UNVERIFIED"
<!-- id: verified-presets | category: Presets and tools | keywords: verified, unverified, library, trust, check, accuracy -->
Presets in the library are written from publicly available manuals and **have not been tested against real devices**. A wrong register map gives wrong values or wrong writes, especially on motor drives and flow/level instruments, which are used for decisions.

How to verify: connect the device, select the preset, and compare each value with the device display (use "Try other base" if it is off by 1). Fix wrong points in the preset editor, then tick "Mark as verified" and add a note. Every file lists its source URL and any doubts in `notes`.

MCP refuses writes through presets that are not verified ([[mcp-security]]).

## Test Center
<!-- id: testcenter | category: Presets and tools | keywords: test center, test, slave, standard, conformance, exception, response time, testcenter -->
Checks that a slave follows the Modbus rules: reads all 4 areas, returns the correct count, answers exception 03 for quantity 0 or over the limit, exception 02 for an out-of-range address, exception 01 for an unknown function, does not hang on a truncated request, and measures response time (min/average/max).

Results are PASS / FAIL / WARN / SKIP. **The default mode is read-only**, safe for real devices. Write tests (FC05/06/15/16/22/23) must be enabled explicitly (`--write --scratch <address>`) and restore the original value afterwards.

`python src/testcenter.py tcp://host:502 -u 1 --addr 0`

## Creating a preset from a new manual
<!-- id: preset-howto | category: Presets and tools | keywords: create, new preset, csv, excel, import, register map, manual, how to -->
1. If the register table is in Excel, make a CSV with columns `name,address,fc,type,scale,offset,unit,access,min,max,group,word_order,byte_swap,length,enum` (only `name` and `address` are required). Write enum as `0=Stop;1=Run`.
2. In the Preset dialog choose Import CSV, then check the result in the preset editor.
3. Set `address_base` to match how the manual counts ([[address-bases]]).
4. Try reading from the device or the simulator and compare with the display, then Mark as verified.

## MCP: letting an AI use the Modbus tools
<!-- id: mcp | category: Presets and tools | keywords: mcp, server, ai, claude, tools, register, stdio, http -->
`mbtool_mcp.py` is an MCP server that any MCP-capable AI can call: connect, read values, summarize repeated values, view traffic, scan, identify a device, interpret registers, diagnostic report, Test Center, read by preset, and search this manual.

- **stdio:** the client program launches it itself. Register it with a `.mcp.json` file (example in `.mcp.json.example`).
- **HTTP:** `python src/mbtool_mcp.py --http` shares it with someone else's AI. A token is required; see [[mcp-security]].
- **Writing is off by default.** Enable it with `MBTOOL_ALLOW_WRITE=1` and specify ranges in `MBTOOL_WRITE_ALLOW`, e.g. `1:3:100-110;1:0:0-7` (Slave:area:range; area 3 = holding, 0 = coil).

Main tools: `status`, `connect`, `read`, `snapshot`, `get_traffic`, `sim_start`, `sim_load_preset` (simulated values move according to the preset, see [[sim-signals]]), `scan_serial_settings`, `scan_unit_ids`, `scan_register_map`, `identify_device`, `interpret_registers`, `diagnostic_report`, `test_center`, `list_presets`, `preset_read`, `search_help`, `get_help`

## Self test
<!-- id: selftest | category: Presets and tools | keywords: selftest, self test, check, test, program, install, exe, works, pass, fail -->
The command `mbtool-cli.exe selftest` (installer/portable) or `python src/mbtool.py selftest` checks that the program works completely on this machine, with no real device needed, and prints PASS / FAIL for each item:

- every preset in the library opens
- TCP read/write against the simulator, RTU over a simulated line with 3 slaves, and reconnecting after a TCP drop
- MCP server, scanners, cycle calculator, Bus Map, and moving simulated values
- GUI: main poll, Multi-type view, poll tabs, Demo devices, and every main window

It does not open a real COM port and does not touch your settings (it uses a temporary folder). It takes about 15 seconds and is suitable as a check after installing on a new machine. If anything FAILs, include that message when reporting the problem.

## Security of MCP over HTTP
<!-- id: mcp-security | category: Presets and tools | keywords: security, token, http, lan, audit log, write, allowlist, vpn, tailscale, lockout, rate limit -->
- Every request needs a **bearer token** (compared in constant time). More than 10 wrong attempts in 1 minute lock out for 1 minute, and the number of requests per second is limited.
- Binds to 127.0.0.1 (this machine only) unless you pass `--host` yourself. Across a network it is **plain HTTP**; use a VPN such as Tailscale, or add `--certfile/--keyfile`.
- A connected AI **cannot choose targets itself** (connect, disconnect, scan COM ports) unless `--allow-connect` is given.
- **No writes at all** in HTTP mode unless `--allow-write` is given together with a list of allowed ranges.
- Every tool call and every failed login is recorded in `~/.mbtool/mcp_audit.log` (tokens are not logged).
- Values read from a device are treated as data only, never as user instructions.

## CLI command reference
<!-- id: cli | category: Presets and tools | keywords: cli, command line, command, terminal, python, mbtool, read, write, sim, vcom, preset, url -->
URL format: `tcp://host:502` or `serial:COM3?baud=19200&parity=E&stop=1&mode=rtu` (`mode` is `rtu` or `ascii`)

| Command | What it does |
|---|---|
| `python src/mbtool.py` | Open the GUI |
| `python src/mbtool.py read URL -u 1 -f 3 -a 0 -n 10 [--fmt Float] [--scan 1000] [--log out.csv]` | Read values |
| `python src/mbtool.py write URL -u 1 -f 16 -a 50 --fmt Float 3.14` | Write a value |
| `python src/mbtool.py sim --port 5020 [--preset f.json] [--signals profile --device-type drive --speed 2] [--serial COM4 --mode rtu]` | Simulated slave, values move according to the mode ([[sim-signals]]) |
| `python src/mbtool.py vcom --port 7000` | Virtual null-modem line |
| `python src/mbtool.py preset-list / preset-read / preset-write` | Work with presets |
| `python src/scanner.py serial COM3` | Scan connection settings ([[scanner]]) |
| `python src/testcenter.py URL` | Check a slave ([[testcenter]]) |
| `python src/interpret.py --words 0x4348 0xCCCD` | Interpret registers ([[inspector]]) |
| `python src/helpdocs.py search word` | Search this manual |
| `mbtool-cli.exe selftest` (or `python src/mbtool.py selftest`) | Check the program on this machine without any device ([[selftest]]) |
