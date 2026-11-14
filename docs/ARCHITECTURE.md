# mbtool architecture

A map for contributors: what each file does, how data flows from a Modbus device to the screen, and where to put a
change. Plain Python 3.10+ with tkinter; pyserial for serial ports; the `mcp` package only for the MCP server.

## Layers

```
            GUI (tkinter)                      CLI                     MCP server (for AI)
          src/mbtool.py               src/mbtool.py cli_*()            src/mbtool_mcp.py
               │                               │                              │
               └──────────────┬────────────────┴──────────────┬───────────────┘
                              │                               │
        logic without Tk:  monitor.py  preset.py  signals.py  buscalc.py  busmap.py  bushealth.py
                           scanner.py  testcenter.py  interpret.py  datalog.py  export.py  commission.py
                           ai_report.py  helpdocs.py  demo.py
                              │
        protocol:          modbus_core.py   (masters, slaves, framing, CRC, PortHub, fault injection)
                              │
        files / paths:     appinfo.py       (version, data folders, crash log, portable mode)
```

Rule of thumb: **anything that can be tested without a window lives outside `mbtool.py`**. `mbtool.py` only builds
windows, keeps their state and calls the logic modules.

## Files in `src/`

| File | Role |
|---|---|
| `mbtool.py` | Entry point. `main()` parses the command line: no sub-command starts the GUI (`gui()`), sub-commands `read`, `write`, `sim`, `vcom`, `preset-*`, `busmap`, `health`, `mcp`, `selftest` run the CLI. Contains every window class (see below). |
| `modbus_core.py` | The protocol, written from the public Modbus specification: PDU build / parse, `TcpMaster`, `SerialMaster` (RTU / ASCII, t3.5 gap, late-answer guard), `SimSlave` (TCP slave + the shared data store), `SerialSlave`, `VirtualCable` (emulated RS-485 line over `socket://`), `PortHub` / `HubMaster` (one shared master per COM port), `Faults` (fault injection), display formats. |
| `monitor.py` | All non-GUI logic of the monitoring features: settings normalisation, device lists, address bases, multi-type rows, `MultiPoller` / `BusWorker` (polling threads for the Devices window, independent and PLC cycle mode), `DeviceStatus` and `LineQuality` (per-device counters, error types), cycle statistics, trend buffer, alarms, workspace files. |
| `preset.py` | Device presets: JSON register maps (`Preset`, `Point`), validation, read blocks, value decoding / encoding, CSV import. |
| `signals.py` | Moving values for the simulator (random walk, loop, sine, device-type profiles). |
| `scanner.py` | Serial-settings scan, Slave ID scan, register-range scan, FC43 / FC17 identification. |
| `testcenter.py` | Checks a slave against the Modbus rules (exception codes, limits, timing). |
| `interpret.py` | One value in every interpretation (int / float / text / BCD / time, all word orders). |
| `buscalc.py` | Cycle-time estimate for a bus (frame bytes, baud, gaps, device delays). |
| `busmap.py` | RS-485 wiring map: distances, recommended baud, unit loads, voltage drop, warnings, exchange timeline. |
| `bushealth.py` | Bus health test: response times, error types, gap sweep, recommended timeout / gap. |
| `datalog.py` | CSV logging with rotation, per-device files. |
| `export.py` | MQTT 3.1.1 publish client (own implementation) and SQLite export, in a background thread. |
| `commission.py` | Commissioning report: HTML (English / Thai), PDF via headless Microsoft Edge. |
| `ai_report.py` | Diagnostic report for AI chats. |
| `helpdocs.py` | The built-in manual: parses `help/*.md` (Thai) and `help/en/*.md` (English), search, context topics for F1. |
| `demo.py` | Demo devices: simulated slaves on an emulated RS-485 line or TCP, with moving values. |
| `mbtool_mcp.py` | MCP server (stdio or HTTP with token) exposing read, scan, health test, bus tools, presets, simulator and the manual. Writes are refused unless `MBTOOL_ALLOW_WRITE=1` and the address is in `MBTOOL_WRITE_ALLOW`. |
| `selftest.py` | `mbtool selftest`: protocol, simulator and GUI checks that also run inside the built exe. |
| `setup_app.py` | The Windows installer / uninstaller (packed into `mbtool-setup.exe`). |
| `appinfo.py` | Version, file locations (`%APPDATA%\mbtool` installed, `.\data` portable, repo root from source, `MBTOOL_HOME` override), preset seeding, crash handler. |

## Windows in `mbtool.py`

| Class | Window / part |
|---|---|
| `MasterApp` | Main window: toolbar, poll tabs, Poll 1 connection and polling, menus, settings, workspace, the message queue |
| `RegGrid`, `MultiGrid` | The register table (one format) and the multi-type table |
| `TabStrip`, `PollTab` | Poll tabs in the main window |
| `SetupDialog`, `WriteDialog`, `Inspector` | Connection setup (F3), write (F6), value inspector (F4) |
| `PresetView`, `PresetsDialog`, `PresetEditor` | Preset view and editor (F7) |
| `TrafficView`, `TrafficWindow` | Traffic monitor (F8) |
| `SimWindow` | Slave simulator (F9) with Signals and Faults tabs |
| `TrendWindow`, `AlertsWindow` | Trend (F10), alerts |
| `DevicesWindow`, `DeviceTab`, `CycleTab`, `CalcTab`, `HealthTab`, `DeviceDialog`, `ScanAddDialog` | Devices window (F11) and its tabs |
| `BusMapWindow` | RS-485 bus map (Ctrl+M) |
| `ExportWindow`, `ReportDialog`, `LogOptionsDialog` | Data export, commissioning report, CSV log options |
| `AiShare`, `AiAssistWindow` | AI Assist (F12): MCP over HTTP sharing the live connection, diagnostic report |
| `HelpWindow` | The manual (F1) |

## Threads and the GUI

Tk may only be used from the GUI thread. Everything that waits for a device runs in a worker thread and reports
through **`MasterApp.q`** (a `queue.Queue`); `MasterApp._pump()` empties it every 40 ms on the GUI thread and
`_handle()` dispatches each message.

```
 worker thread                                   GUI thread
 ─────────────                                   ──────────
 Poll 1 worker (MasterApp._worker) ──┐
 BusWorker per bus / TCP device ─────┼─► MasterApp.q ─► _pump() ─► _handle() ─► DeviceStatus / grids / tabs /
 one-off calls (MasterApp.run_bg) ───┘                                         trend / alarms / logs / exporter
```

- Every start of a poll gets a **generation number**; results from an older run are dropped
  (`MultiPoller.accept`), so stopping or reconfiguring never shows stale values.
- Devices on the same COM port share **one** master through `modbus_core.HUB` (`PortHub`); `Master.request` holds a
  lock for the whole request / answer, so Poll 1, poll tabs and devices take turns like a PLC instead of fighting
  for the port.
- Long jobs with their own progress (health test, scans, export) use their own thread and queue, polled with
  `after()`; they never call Tk from the thread.
- The exporter (`export.Exporter`) receives readings on the GUI thread (`submit`) and sends them from its own thread.

## Data and settings

| What | Where |
|---|---|
| Settings (`mbtool_settings.json`) | `appinfo.settings_file()`: `%APPDATA%\mbtool` (installed), `data\` (portable), repo root (from source) |
| Workspace | `.mbw` JSON file chosen by the user: Poll 1, poll tabs, devices, windows, alarms, bus map |
| Presets | `presets/` in the repo (used directly from source); the installed / portable app copies them to its data folder on first run and on each new version (`appinfo.seed_presets`), so users can edit them |
| Logs | `logs\` in the data folder (installed / portable) or `~/.mbtool` (from source); `crash.log` for unexpected errors |
| Secrets | never saved: the AI bearer token and the MQTT password live in memory only |

`MBTOOL_HOME=<folder>` puts all writable files in one folder - the tests and `selftest` use it to stay isolated.

## Tests

`tests/` uses `unittest` style, run with pytest. Logic modules have plain unit tests; GUI tests create `MasterApp`
with a withdrawn Tk root, call the same methods a button would (`invoke()`, `start()`, ...) and pump the event loop.
Hardware is replaced by `SimSlave`, `SerialSlave` on a `VirtualCable`, `Faults`, and a small MQTT broker stand-in
(`tests/test_export.py`). See [CONTRIBUTING.md](../CONTRIBUTING.md) for how to run them.

## Where to put a change

| You want to... | Start in |
|---|---|
| support a new function code or fix framing | `modbus_core.py` (+ `tests/test_core.py`, `tests/test_rtu.py`) |
| add a preset field or data type | `preset.py`, the manual topic `preset-schema` in both languages |
| change how devices are polled or counted | `monitor.py` (`MultiPoller`, `BusWorker`, `DeviceStatus`) |
| add a window or a tab | a new class in `mbtool.py`, logic in a new module, an in-process GUI test |
| give the AI a new tool | `mbtool_mcp.py` (read-only unless it is a deliberate, guarded write) |
| change the manual | `help/*.md` and `help/en/*.md` (same ids; `tests/test_helplang.py` checks parity) |
| change the installer / exe | `packaging/` and `src/setup_app.py` |
