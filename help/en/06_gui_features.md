## Poll panel: Slave ID, Function, Address, Quantity, Scan Rate, Display
<!-- id: poll-panel | category: Program features | keywords: poll panel, slave id, function, address, quantity, scan rate, display, settings, read values -->
Defines what to read from the device.

- **Slave ID:** device number 1–247 on serial; TCP accepts 0–255 (some gateways use 0 or 255) ([[slave-id]])
- **Function:** FC01–FC04 for reading ([[data-areas]])
- **Address:** start address, based on the base selected in Address base ([[address-bases]]). The field below shows `= protocol N`, the address actually sent
- **Quantity:** number of items to read. See [[limits]]; if exception 03 appears, reduce it
- **Scan Rate:** time between read cycles (milliseconds), minimum 20 ms. On slow serial lines, set it higher than the time a request actually takes
- **Display:** display format ([[display-formats]]). Tick **Multi-type view** under this field to see several data types at once ([[multi-view]])
- **Rows per column:** number of rows per column: 10/20/30/40 or Auto. The table fills down one column at a time according to the chosen count

A field with an invalid entry shows red text below it, and polling stops until it is corrected.

To read several sets at once (different Slave IDs / different address ranges / different devices), open more poll tabs with the **+** button or Ctrl+T: see [[poll-tabs]]

## Poll tabs: multiple polls at once in the main window
<!-- id: poll-tabs | category: Program features | keywords: poll tabs, tab, multiple tabs, multiple polls, multiple windows, new tab, close tab, rename, duplicate, ctrl+t, ctrl+w, same as connection, custom -->
The main window has a tab bar below the toolbar. Each tab is one poll, like opening several poll windows: each has its own Slave ID, Function, Address, Quantity, Scan Rate, Display, Address base and Rows per column, and **all tabs read at the same time** ([[poll-panel]])

- **The first tab (Poll 1)** is the main poll, unchanged. The Connect (F5) button, Setup (F3), preset view (F7), log (Ctrl+L) and AI Assist all act on this tab. The first tab cannot be closed (it can be renamed)
- **New tab:** the **+** button at the end of the tab bar, Ctrl+T or File → New poll tab creates "Poll 2", "Poll 3"... with initial settings copied from the currently open tab (Slave ID, function, address, quantity, scan rate, display), which you can edit right away. If the source tab is reading, the new tab starts reading by itself
- Each tab has its own **Start/Stop** button and a status line below the tab: colored dot, latest message, last update time, OK / error counters and the last error. If you edit the poll definition while it is reading, the tab restarts reading with the new values once you stop typing
- **Colored dot beside the tab name:** green = reading OK, red = error, orange = no response for a while (or the first tab is connecting), gray = stopped
- **Table:** same as the first tab ([[grid]]). Changed values flash yellow, unreadable ones are gray. Double-click (or Enter) to write; works with FC01 (coil) and FC03 (holding register). Multi-type view can be ticked ([[multi-view]]). The Columns... button uses the same column set for every tab
- **F6** opens the write window ([[write-dialog]]), which writes through the connection of the currently open tab (the tab name is shown in the window title). **F4** inspects the value of the selected cell in that tab ([[inspector]])
- **Preset...:** shows values by name from a preset instead of the table (Point, Value, Unit, Raw, Status, same as [[devices-tabs]]). Read-only in this tab. The **Register grid** button returns to the normal table
- **Right-click the tab name:** Rename... (or double-click the name, type, then Enter; Esc cancels), Duplicate tab (copies the preset and connection too), Start/Stop polling, Show in Devices window, Close tab (or middle-click / Ctrl+W)
- **Closing a tab** only stops reading and removes that tab. It does not ask for confirmation and does not affect other tabs or devices. The tab's alert rules remain and can be deleted in Alerts
- **Switching tabs:** click, Ctrl+Tab / Ctrl+Shift+Tab, Ctrl+PgUp / Ctrl+PgDn. If there are more tabs than fit the window width, use the ◀ ▶ buttons or the mouse wheel over the tab bar. The ▾ button lists all tabs with their status

**Tab connection**
- **Same as Poll 1 connection** (default): uses the same settings as the first tab and follows when they are changed in Setup (F3). A tab that is reading restarts with the new values. Each tab opens its own connection (TCP socket), so there is no need to press Connect in the first tab
- **Custom connection** or the **Setup...** button: set TCP / RTU / ASCII for this tab alone, for example to read two devices at different IPs. Choose Same as... again to go back to the first tab's settings
- **The same COM port can be shared** among Poll 1, the other tabs and the devices in Devices: the program opens the port once and lets everyone **take turns sending one request at a time** (a request is sent and its response received before the next request), like a PLC polling one telegram at a time. Whoever arrives first gets the line first. All of them must use the **same baud / parity / stop bits** because it is a single line (a message appears if they differ). The port closes when everyone has stopped using it. To see the timing of each cycle, use PLC cycle mode in [[devices]] ([[plc-cycle]])

**Relationship with Devices (F11)**
The second tab onwards are stored as one device each in the Devices list (same name as the tab), so they appear in the Overview table and have a tab in the Devices window. You can use Trend, Alerts, per-device log files ([[logging]]) and PLC cycle ([[plc-cycle]]) as with any other device. Start all / Stop all in the Devices window include these tabs. Remove in Devices = close the tab. After changing it with Edit..., the tab shows the new values immediately

**Saving:** tabs (poll definition, connection, Display, address base, rows, multi-type) are stored in the settings and in the workspace ([[workspace]]). When you reopen the program, all tabs come back but stopped (a workspace saved while devices were reading starts reading by itself). The first tab's name and the selected tab are remembered too. Demo devices do not create tabs and are not saved

**Limitations**
- Two different programs cannot use the same COM port (for example, a manufacturer's configuration tool or another Modbus master program that has the port open). See [[trouble-port-busy]]. Sharing works only within this program, and reading several devices on one line is slower than reading them on separate lines because they must take turns
- Some TCP devices accept only a few simultaneous connections (for example 1–4). Each tab uses 1 connection. If a new tab cannot connect, reduce the number of tabs
- The Connect / F5 button, log Ctrl+L, preset view F7 and AI Assist belong to the first tab only
- A tab showing a preset cannot write by point name. Use F6 to write by address, or the preset view in the first tab

## Value grid and editing values
<!-- id: grid | category: Program features | keywords: table, grid, edit, double click, write value, flash, highlight, value change -->
- Values that changed since the previous cycle flash yellow. Values from a failed cycle are gray
- **Double-click** (or select and press Enter/F2) to edit the value in the cell. Works with FC01 (coil) and FC03 (holding register). The value is written according to the Display format (for example, Float writes 2 registers)
- A malformed or out-of-range value shows red text in the cell; press Esc to cancel
- Right-click → "Inspect value..." opens the value inspector ([[inspector]])
- FC02/FC04 are read-only

## Multi-type view: see several data types at once
<!-- id: multi-view | category: Program features | keywords: multi-type view, multi type, several types, simultaneously, display, columns, unsigned, signed, hex, binary, float, long, ascii, table -->
Tick **Multi-type view** under the Display field in the Poll panel (or menu Tools → Multi-type view). The normal table changes into a table with **1 row per register** and **1 column per data type**, such as Unsigned, Signed, Hex, Binary, Float, Long. You can see which interpretation of the same register gives a sensible value without switching Display back and forth.

- The **Columns...** button (which replaces Rows per column while this mode is on) lets you choose columns from every format in [[display-formats]], including Inverse and ASCII (2 characters per register). The selected columns and the on/off state are remembered in the settings and in the workspace ([[workspace]])
- Address is shown according to the base selected in Address base ([[address-bases]])
- Types that use several registers (Long/Float use 2, Double uses 4, for the word order of Inverse see [[word-order]]) **show a value only on the row where the value starts**, counting from the first polled register: Float shows at rows 0, 2, 4... while rows 1, 3, 5..., which are taken up by the value above, are **left blank**, as in common Modbus master tools (Double shows every 4 rows). 1-register types show on every row as usual. The values are the same as in the normal Display table. If a device's 32-bit values start at the second register of a pair, shift the alignment and the values will land on the right rows. Blank cells cannot be edited (edit at the row where the value starts), and if the registers read are not enough for the type, the last row is left blank too
- Rows whose register changed flash yellow. Values from a failed cycle or after disconnection are gray, as in [[grid]]
- **Double-click** a cell (or select and press Enter/F2) to edit the value. The value is written according to **that column's type**, starting at the row's address. 1-register types use FC06, while Long/Float/Double also write the following registers using FC16. Press Esc to cancel. Works with FC03 only
- Right-click or F4 opens the value inspector ([[inspector]]) at that row. The default register count follows the selected column. The Write... button (F6) fills in the address and type from the selected cell
- FC01/FC02 (coil, discrete input) are single bits with no data type, so the checkbox is disabled and the normal table is shown
- Not used with Preset view ([[preset-view]]), which shows values with the types the preset already defines
- Log, Trend and Alerts still use values according to the selected Display format, as when this mode is off
- The Slave Simulator window also has a Multi-type view checkbox in its Data tab ([[slave-sim]]), using the same column set as the main window

## Write window (F6)
<!-- id: write-dialog | category: Program features | keywords: write dialog, write, f6, fc05, fc06, fc15, fc16, fc22, fc23, send value -->
Supports writing with every function: FC05, 06, 15, 16, 22, 23. Each function shows only the fields it uses. The fields are filled in from the cell selected in the table. Before sending, a summary shows what will be written at which address.

**Caution:** a write changes the state of the real device immediately, especially motor drives, valves or calibration values. If a write has no effect, see [[trouble-write-fail]]

## Value Inspector (F4)
<!-- id: inspector | category: Program features | keywords: inspector, value inspector, f4, interpret, data type, float, long, word order, find type -->
Shows the value of the selected register (including 1, 2 or 4 consecutive registers) in every interpretation at once: 16-bit (unsigned, signed, hex, binary, set bits, float16, BCD, ASCII), 32-bit (ABCD/CDAB/BADC/DCBA order: unsigned, signed, float, Unix time), 64-bit (unsigned, signed, double)

The window follows the selected cell and updates live from the latest poll without extra reads. Double-click a row to copy. The "Use as preset point..." button creates a new point in a preset from the selected row. Use together with [[word-order]]

CLI: `python src/interpret.py --words 0x4348 0xCCCD` or read from a device `python src/interpret.py tcp://host:502 -u 1 -a 0 -n 2`

## Traffic Monitor (F8)
<!-- id: traffic-monitor | category: Program features | keywords: traffic monitor, f8, hex, frame, pdu, tx, rx, err, view sent data, log, line -->
Shows all communication the master sends and receives: time (to the millisecond), direction (**TX** sent, **RX** received, **ERR** error), Slave ID, PDU in hex and a description such as `Read Holding Registers addr=0 qty=10`

It has buttons to pause scrolling (data is still collected), clear, filter by direction, copy, and save as text/CSV. The window shows the latest 3,000 rows and up to 20,000 entries are kept in memory (the master's own internal buffer holds 500 entries). Use it to track down the cause when a device does not respond; you can compare the PDU directly with examples in the device manual.

## Preset view (F7)
<!-- id: preset-view | category: Program features | keywords: preset view, f7, device, register map, name, unit, verified, unverified -->
Select a device preset and the table changes to a named layout with scaled values, units and enum text (for example 1 = Run).

- A **VERIFIED / UNVERIFIED** label and a yellow banner warn when a preset is not yet verified. All presets in the library were written from manuals and have not been tested on real devices; compare values with the device's display before trusting them
- **Apply preset connection settings:** applies the preset's baud/parity/ID (you must press it yourself)
- **Try other base:** temporarily reads with the address base switched 0↔1 for comparison; if it matches, press to keep it as your own copy
- Writable values are edited by double-click. The range is checked before sending. The first time for an unverified preset, it asks for confirmation
- Points marked "w" (write-only) appear as a ▶ Send... button

For details of the preset file: [[preset-schema]]. To edit presets in the program: [[preset-editor]]

## Preset editor
<!-- id: preset-editor | category: Program features | keywords: preset editor, edit preset, add point, csv import, verified, shift address -->
Edits every field of a measurement point (name, address, fc, type, word order, byte swap, scale, offset, unit, access, value range, group, enum). Add/duplicate/delete/reorder, with validation before saving. Import from CSV is supported.

- **Shift all +1/−1:** shifts the address of every point. If "keep protocol addresses" is ticked, address_base is adjusted too so the address actually sent on the wire does not change. Fixes base problems ([[address-bases]])
- **Mark as verified:** tick only after comparing with a real device. You must enter a note saying who verified it, with which model, and when
- Saving into the library folder asks before overwriting. The default is a `_custom` copy in the `presets` folder

## Trend graph (F10)
<!-- id: trend | category: Program features | keywords: trend, graph, chart, f10, plot, export csv -->
Plots values over time. Up to 8 lines can be selected (by preset point name or address). The Y axis can be shared or separate per line. A cursor reads the value at a given time, pause/resume, and you can select the time span (1 minute, 10 minutes, 1 hour, all). Failed polls appear as gaps in the line. Export to CSV is supported. Data is kept in memory with a limited size.

## Alerts
<!-- id: alerts | category: Program features | keywords: alerts, alarm, notification, above, below, hysteresis, threshold, silent, stuck, acknowledge -->
Rules per measurement point: **above**, **below**, **outside** (outside a range), **equals**, **changed**, **no data for N s** (device silent), **stuck for N s** (value frozen)

- **Min duration (for_s):** how many seconds the condition must persist before alerting; filters out signal glitches
- **Hysteresis:** the value must return into the safe zone by more than this margin to be considered normal again; prevents bouncing back and forth
- States: normal → alert → acknowledged (Acknowledge) → normal. Events are logged, can be exported to CSV, and a red bar appears when there is an unacknowledged alert
- Rules are defined in a preset (`alarms`) or edited in the GUI. The alert sound is off by default

## Multiple devices (Devices, F11)
<!-- id: devices | category: Program features | keywords: devices, multiple devices, f11, multi poll, bus, rs485, several, simultaneous -->
Polls multiple devices at the same time. Each has a name, connection, preset, Slave ID and scan rate.

- Devices on the **same RS-485 line** share the port and are polled **one at a time** (no collisions), with a short gap between them
- Devices on different lines or different TCP hosts are polled simultaneously
- If one device is off or not responding, it does not slow the others beyond its own timeout
- The overview table shows status (ok/error/stale), last time, counters and a summary value
- Logging can be set to a separate file per device ([[logging]])
- The window is tabbed: **Overview** (this table), **Cycle**, **Calculator** and **one tab per device** showing all the latest values. The **Scan & add...** button finds devices on the line and adds them for you: see [[devices-tabs]]
- **Scan mode:** Independent (each device reads at its own scan rate, the default) or **PLC cycle** (reads every device on the line in rounds like a PLC, with timing statistics). See [[plc-cycle]]
- The **Bus map...** button draws where devices sit on the RS-485 line, the cable length and terminators, and shows requests/responses traveling. See [[bus-map]]
- Poll tabs in the main window (Poll 2, Poll 3...) are also devices in this list: see [[poll-tabs]]

## Per-device tabs and Scan & add
<!-- id: devices-tabs | category: Program features | keywords: devices tabs, tab, device, scan, add, scan add, add device, find slave id, search, identify, fc43, preset, automatic, tick -->
The Devices window (F11) has an **Overview** tab and **one tab per device**. The tab name has a colored dot showing status: green = ok, red = error, orange = no response for a while (stale), gray = stopped.

A device's tab shows:
- Header: connection, Slave ID, what is read, status, last update time, OK / error counters and the last error message
- Buttons **Start/Stop** (this device only), **Edit...**, **Show in main window** (shows it in the Trend window and selects it in Traffic / Alerts)
- With a preset: a table of **Point, Value, Unit, Raw, Status**. Scaled values and enum names come from the same read cycle as the Overview (no extra reads). Points that cannot be read appear as a red row with the reason
- Without a preset: an address / value table for the configured register range, in multi-type form ([[multi-view]]) showing several data types at once
- This tab cannot write values (use the main window's F6 or preset view)

Tabs are created/removed/renamed automatically with the device list (Add, Edit, Remove, Scan & add, opening a workspace).

**Scan & add...** (button in Overview):
1. Choose the connection (default = the main window's connection), the Slave ID range (starting 1 to 32), the timeout (300 ms is enough: an ID with no device costs this much time each) and the function used for the trial read
2. Press **Scan**. The scan runs in the background with a progress bar and **Cancel scan**. An exception response also counts as a device being present ([[scanner]])
3. For each one that responded, it tries to ask the model name with FC43 / FC17 ([[fc43]]). If it answers, the best-matching preset name in the library is suggested. You can change it in the **Preset** field (or none = read holding 0..9)
4. Tick the **Add** box (click, or Space; Select all / Select none), edit the **Name** if you like, then press **Add selected** to get the devices and tabs immediately (not started yet)

Devices that already exist (same connection and same ID) show **already added** and cannot be ticked, so scanning again does not add duplicates. If the port cannot be opened or nobody answers, the status line says so (check the cable, baud / parity: [[trouble-no-response]])

Presets from the library have not been verified against real equipment. Compare values with the device display before relying on them.

## PLC cycle mode and bus design
<!-- id: plc-cycle | category: Program features | keywords: plc cycle, scan cycle, round, cycle time, jitter, overrun, timeout, line busy, bus design, calculator, baud, response delay, rs485, read cycle -->
**Scan mode** in the Overview tab of the Devices window:
- **Independent (per-device scan rate):** each device reads at its own scan rate (the original behavior)
- **PLC cycle:** for each **bus** (one RS-485 port or one TCP host:port), all enabled devices are read **one after another** (every preset block); when finished, a new cycle starts, like a PLC task. A **Bus gap** is inserted between requests
- **Cycle time target (ms):** blank = start the next cycle immediately; a number = fixed cycle: if it finishes early, it waits for the remainder; if it runs over, it is counted as an **overrun**

The **Cycle** tab (measured with a high-resolution clock):

| Value | Meaning |
|---|---|
| Last / Avg / Min / Max ms | Execution time of one cycle (reading every device on that bus, including gaps), not including the wait to reach the target |
| Jitter ms | Max - Min; the smaller, the more consistent |
| Cycles/s | Actual cycles per second (including the wait for the target) |
| Target / Overruns | The cycle target, and the number of cycles that took longer than the target |
| Timeouts / Errors | Total requests that timed out / failed (including port open failures) |
| Line busy | bytes x bits per character / baud compared with the cycle time (% of time the line carries data). Serial only; TCP = n/a |
| Requests / cycle | Number of requests of that device per cycle (a preset with several blocks = several requests) |
| ms / cycle | Average time of that device within one cycle |
| Avg / Worst response ms | Time from sending the request to receiving the response, average and worst |

The graph at the bottom shows the cycle time of the latest ~120 cycles; the dashed line = target. The **Reset statistics** button restarts counting. **Copy report** copies the report as text (including estimates).

The **Calculator** tab (usable without polling): enter the line type RTU/ASCII/TCP, baud, parity, stop bits, master gap, timeout, USB adapter latency, device response delay, retries and target. The device list comes from what is configured (preset blocks or register ranges). Response delay and Online can be edited per device. Results: total cycle time, per device, cycles/s, line busy, a baud what-if table and recommendations. The **Compare with measured** button compares the estimate (using each bus's real connection and Bus gap) with the average measured in the Cycle tab, as a %.

**Designing a bus from these numbers:**
1. Enter all the devices to be connected and look at the Calculator: does the cycle time exceed what you need?
2. **Low line busy** (below ~35 %): the time is spent waiting (response delay, gap, USB latency). Raising baud helps little; reduce gap / latency or split devices onto another port. **High line busy:** raise baud if every device supports it
3. A device that **does not respond** costs the full timeout every cycle: remove it or reduce the timeout
4. A preset with several blocks = several requests: group points that lie close together into a single block
5. Turn on PLC cycle mode with the real hardware or a simulator, then **Compare with measured**. If the measured time is much slower, adjust the response delay in the Calculator to match (see Worst response), then continue designing from numbers that agree with reality
6. Set the target comfortably above the measured Max (allowing for jitter and retries), then check that Overruns stays at 0

Experiment without hardware: `python src/mbtool.py vcom --baud 38400 --bits 10` (simulates line speed) and `python src/mbtool.py sim --serial socket://127.0.0.1:7000 -u 1 --baud 38400 --delay 10` (delays responses by 10 ms), or the **Response delay (ms)** field in the Slave Simulator. See [[vcom]] and [[slave-sim]]

To see where devices sit on the line, how long the cable is, and how requests/responses travel, see [[bus-map]] (Ctrl+M)

To measure real response times and find the timeout / gap that suits this line, see [[bus-health]]

## Bus health test and error types
<!-- id: bus-health | category: Program features | keywords: health test, bus health, cable test, timeout, gap, recommendation, response time, p95, crc error, error types, line quality, sweep -->
The **Health test** tab in the Devices window tests every device on the selected bus (one COM port or one TCP host:port). While the test runs, the program temporarily pauses polling of the devices on that bus and resumes it automatically when finished.

1. **Response time measurement:** reads each device **Requests per device** times (default 20) with a long **Probe timeout** (2000 ms), so slow answers are timed instead of being cut off. For a preset, the first readable block is used.
2. **Gap sweep** (serial only): reads all devices in turn while reducing the gap step by step (50, 20, 10, 5, 2, 0 ms) to see how fast this line can be polled without errors.

| Column | Meaning |
|---|---|
| Grade | good = all answered, consistent timing; warn = some errors, or timing varies, or close to the timeout; bad = 5 % errors or more; no answer = no valid answer at all |
| Avg / p95 / Max ms | Round-trip time of one request (p95 = 95 % of requests are faster than this value) |
| Device delay ms | Average time minus the time the frames spend on the wire = the time the device takes to think before answering |
| Timeout needed | A value that works for that device: slowest answer x 1.5 (at least +50 ms), rounded up in steps of 50 ms, minimum 100 ms |

The **Apply recommended timeout + gap** button sets the timeout of every device on this bus and sets the Bus gap to the recommended value. **Use measured delays in Calculator** copies the measured Device delay values into the Calculator tab (devices that did not answer are set to offline) so the estimate matches the real line. **Copy report** copies the result as text.

Baud rate is not swept, because baud is set in the device itself. If the master changes baud on its own, it cannot talk to the devices at all. To try another baud, set it on every device first, then run the test again. The most recent test result is included automatically in the commissioning report, see [[commission-report]].

**Error types** (the Error types column in Overview and Errors by type in a device's tab):

| Abbr. | Type | Usually caused by |
|---|---|---|
| T | Timeout | No answer: wrong Slave ID / baud / parity, broken cable, A-B swapped, no power, or timeout too short |
| C | CRC / LRC | Frame corrupted in transit: terminator, shield/ground, cable too long for the baud, long stub, noise |
| E | Exception | Device rejected the request: unsupported address / count / function code (a configuration issue, not the cable) |
| F | Bad frame | Incomplete or malformed answer: noise, or a USB adapter truncating frames |
| W | Wrong ID | Answer came from a different Slave ID: two devices share the same ID, or a late answer arriving after a timeout |
| L | No connection | Could not open the port or connect over TCP |
| O | Other | Anything else |

The **Resp. ms avg/max** column is the round-trip time of one request, counted from the start of polling or from the last press of **Reset counters** in the device's tab. **Line quality** is the proportion of failed requests: good = 0 %, fair below 1 %, poor below 5 %, bad 5 % or more.

## Data export to MQTT / database
<!-- id: data-export | category: Program features | keywords: export, data export, mqtt, broker, mosquitto, node-red, home assistant, sqlite, database, grafana, scada, iot, cloud, topic, payload, json, retain, tls -->
The menu **Tools > Data export (MQTT / database)...** sends every reading of the devices in the Devices window (F11) to an MQTT broker, to an SQLite database file, or to both at once. Export runs in the background, so a slow broker or slow disk does not slow down device reads.

**MQTT:** enter Broker host, Port (1883, or 8883 if using TLS), User name / Password (if the broker requires them) and a Topic prefix such as `plant1/line3`, then press **Test connection** first. The program sends a test message to `<prefix>/_test`.

| Payload | Message sent |
|---|---|
| one JSON per device | topic `<prefix>/<device>`, data `{"ts": "...", "ok": true, "values": {"Voltage": 230.1, ...}}`; if the read fails it sends `"ok": false` with `"error"` |
| one topic per point | `<prefix>/<device>/<point>` = value and `<prefix>/<device>/status` = status |

- **Retain:** the broker keeps the latest message of each topic for clients that subscribe later.
- **Min. interval (s):** sends each device no more often than this value (0 = send on every read). Helps reduce the amount of data sent to the cloud.
- Uses QoS 0. If the broker drops, the program queues messages (up to 5000 items; beyond that the oldest are discarded and counted as dropped) and reconnects automatically, waiting longer step by step up to 30 seconds.
- **The password is not saved to any file** (same as the AI token). You must enter it again after restarting the program.
- Device and point names containing `/ + #` are replaced with `_` in the topic.

**SQLite:** choose a `.db` file (created if it does not exist). Every value is one row in the `readings` table (ts = numeric time, time = ISO text time, device, point, value = number, text = text, ok = 1/0). A failed read is a row with ok = 0 and text = the error message. The file can be opened with DB Browser for SQLite, Python, Grafana (SQLite plugin) or Excel through ODBC.

Points of a device that uses a preset take the point names from the preset, while a device read as a register range uses address names in the selected base (for example `40001`). Data from Poll 1 of the main window is not exported; if you want it, add it as a device in Devices. Other databases such as SQL Server, PostgreSQL and InfluxDB are not supported directly; use MQTT to feed those systems instead (for example with Node-RED or Telegraf).

## Commissioning report
<!-- id: commission-report | category: Program features | keywords: report, commissioning, handover, pdf, html, print, sign off, customer, document -->
The menu **File > Commissioning report...** or the **Report...** button in the Devices window produces a one-page report for handing over a job or keeping as on-site evidence. To export values continuously to other systems, see [[data-export]].

Fill in **Project, Site, Customer, Commissioned by** and **Notes** (the program remembers them for next time), choose the language **English** or **ไทย**, then press **Save report...**. This gives an HTML file, and if **Also save a PDF** is ticked, a PDF too (printed with Microsoft Edge, which is available on every Windows 10/11 machine). The **Preview** button opens the report in a browser before saving.

The report contains:
- **Summary:** pass (all enabled devices answered and errors below 1 %) or needs review
- **Read settings and each bus:** connection, measured cycle time in PLC cycle mode compared with the estimate, overruns, line busy
- **Every device:** status, OK and errors, error rate, error types, response time (average / p95 / maximum), last error
- **Values read at the time of the report** (first 16 values per device)
- **Latest bus health test result** ([[bus-health]]) with recommended values and remarks
- **Bus map** ([[bus-map]]): drawing, cable length, recommended baud, unit load and wiring remarks
- **Sign-off fields** for the tester and the receiver

The report covers only devices in the Devices window (including poll tabs), not Poll 1 of the main window. For reliable figures, poll for a while and run the bus health test before creating the report.

## RS-485 bus map (Bus map, Ctrl+M)
<!-- id: bus-map | category: Program features | keywords: bus map, diagram, bus, cable, rs485, cable length, stub, terminator, 120 ohm, bias, wiring, daisy chain, topology, animation, signal, propagation delay, unit load, voltage drop -->
Open it from the menu **Tools > Bus map...** (Ctrl+M) or the **Bus map...** button in the Devices window. This page lets you **draw** where each device sits on the RS-485 cable and then **simulate** how a request from the master (this program) travels to a slave and how the answer travels back.

**The program cannot see the real cable:** Modbus does not report distance or position, so you must draw the map yourself. If the order and lengths are entered correctly, the program can calculate many more things from them.

**How to enter the map:**
1. Press **Import from Devices** to create devices from those configured in Devices (only those using RS-485, sorted by Slave ID), or **Add device** to add them manually. The **Not on the map yet** box lists devices that are configured but not yet on the map (press **Add to map**).
2. Arrange the rows with **Move up / Move down** to match the real order on the cable. Row 1 = closest to the master.
3. Double-click a cell in the table to edit (Enter = apply, Esc = cancel): **Segment (m)** = cable length from the previous device (row 1 = from the master), **Stub (m)** = branch cable from the trunk to the device (0 = daisy chain, with the cable entering and leaving at the device), **Unit load**, **Supply (mA)** (devices drawing power from the same cable). Click the **Terminator** cell to toggle whether that device has a 120 Ω terminator. Invalid values (negative, duplicate Slave ID) are not used and are reported in the status line.
4. At the top, choose **Cable** (typical cable values; **Custom** takes datasheet values: vf, Ω/km, pF/m, dB/100m), **Baud** (the default for a new map = the baud of the main window connection), **Master at one end**, **120 Ω at master end / far end**, **Bias**, **Supply V** and the transceiver **Rise time (ns)**. Every change is recalculated and redrawn immediately.

**Reading the drawing:**
- The **MASTER** box is at the far left (or attached to the cable with a stub if Master at one end is not ticked). The thick horizontal line is the **trunk**. The boxes hanging below it are the devices, in table order.
- Numbers on the trunk = the length of each segment. Numbers beside the hanging lines = stub lengths. The bracket line at the bottom = total trunk length and the distance to the farthest device.
- The zigzag **120 Ω** symbol = a terminator at the end of the cable (open end = none). The **bias** label = bias is present at the master.
- Box colors: green = polling and answering normally, red = error, orange = no answer for a long time (stale), gray = stopped (name matches a device in Devices), white = not in Devices. A blue frame = the selected device.
- The drawing is **not to scale**. Boxes are evenly spaced for readability. With many devices, scroll left and right or tick **Fit to width**.

**Animation:** press **Play**, then choose which device to query (or **All in turn**, which queries one at a time like a scan cycle), FC, count, **Delay ms** (the time the device "thinks" before answering), parity and stop bits. The sequence you see: idle line t3.5 → a **blue request box** leaves the master and passes every device (the taps of all devices blink because all of them hear it, but only the addressed one is highlighted) → the device thinks for the delay time → a **green response box** travels back to the master (the others stay silent) → the master blinks, then the next device follows.
- Time scale: **1 second on screen = 50 ms on the wire at 1x** (very short intervals are stretched so they remain visible). The timeline bar under the drawing shows the true proportions in ms: t3.5 | request | device delay | response | gap/usb, and the total time.
- **Cycle time for all devices on the map** = the estimated cycle time if every device on the map is polled (buscalc). Devices configured in Devices use their own preset block / register range and response delay from the Calculator. Try changing the baud to see how much the cycle changes.
- **Follow real traffic:** while the main window or Devices is polling, it shows the real requests/responses of the devices on the map (read from the traffic log only, without changing protocol behavior). If events occur faster than they can be drawn, events older than ~1 second are skipped so the picture does not lag behind reality.

**What is calculated (Results box):** checks (red = error, orange = warn, gray = info), trunk / stub / farthest-device lengths, the distance and travel time of the signal to each device (us), the maximum recommended baud for this length compared with the selected one, the critical length and whether the cable counts as "electrically long" (must be terminated), the allowed stub length, capacitance, loop resistance, attenuation, total unit load (compared with 32), and the minimum voltage when devices draw power from the cable. The **Copy report** button copies the report as text.

**Why distance does not make Modbus slow:** at 9600 baud one bit is about 20-odd kilometers of cable long, so the signal reaches every device almost simultaneously (on the order of microseconds). What really takes time is frame transmission, device thinking time, the gap and USB latency ([[plc-cycle]]). Cable length affects **reliability**: usable baud, reflections (terminator), stubs and voltage drop. See [[rs485-wiring]] and [[serial-settings]].

All figures are **estimates from general rules** and typical cable values. Enter datasheet values of your real cable in Custom mode and always verify against the real installation.

**Files:** the map is stored in the program settings and in the workspace (.mbw) alongside the device list ([[workspace]]). The menu **Map > Save map as JSON... / Load map from JSON...** saves/opens it as a separate file, and `python src/mbtool.py busmap map.json` prints the same report from the command line.

## Logging to CSV
<!-- id: logging | category: Program features | keywords: log, logging, csv, record, ctrl+l, file rotation, rotation, excel, delimiter, utf-8, bom -->
Press **Ctrl+L** to start/stop logging, one row per poll. The first column is the time. A failed poll is an `ERROR` row.

Options (Log options...):
- **File rotation:** none / daily (`name_2026-11-03.csv`) / by size (`name_date_001.csv`)
- **Keep the last N files:** deletes only the older files of this log (off by default)
- **Delimiter** (, ; tab |), **decimal** (point or comma), **time format** (date time, with milliseconds, Unix, Excel date number), **UTF-8 with BOM** so that Excel reads Thai correctly
- Separate files per device in the Devices window

## Workspace file (.mbw)
<!-- id: workspace | category: Program features | keywords: workspace, mbw, save, open, ctrl+s, ctrl+o, project -->
Saves the whole state in a single file: devices, connections, presets, alarm rules, chart series, log options, address base and window positions. It can be reopened immediately (Ctrl+S / Ctrl+O, recent list). If a preset is missing or that port does not exist, it reports a list of problems instead of crashing. No token is stored.

## Slave Simulator
<!-- id: slave-sim | category: Program features | keywords: slave sim, simulator, f9, tcp, rtu, ascii, simulated device, test -->
Simulates a Modbus device in the GUI (F9): choose TCP, RTU or ASCII and the Slave ID, and edit values in the table across all 4 areas. It has a request counter, and a Traffic tab shows the requests received and the responses sent. Use it to test your own master-side software.

**Response delay (ms):** the simulator waits this long before every answer, like a real device that needs time to think (typically 5-50 ms). It takes effect immediately even while running. Use it with PLC cycle mode to measure a realistic cycle time ([[plc-cycle]]).

CLI: `python src/mbtool.py sim --port 5020 [--preset file.json] [--signals random|loop|sine|profile] [--serial COM4 --mode rtu] [--delay 10]`. See [[vcom]]. To make the values move: [[sim-signals]]. To make it answer wrongly on purpose: [[sim-faults]].

## Fault injection (Faults tab in Slave Sim)
<!-- id: sim-faults | category: Program features | keywords: faults, fault injection, simulate, error, no reply, slow answer, bad crc, exception, garbage, wrong id, duplicate id, disconnect, test, plc, scada, master, handling -->
The **Faults** tab in the Slave Sim window (F9) makes the simulated slave answer wrongly on purpose, so you can see whether your master, PLC or SCADA copes: does it show the error correctly, does it retry, does it hang, and does it recover by itself when the line is good again.

Tick **Inject faults**, then enter the **% of requests** for each type (0-100). It takes effect immediately even while the slave is running. The **Injected** column counts how many times each fault has occurred. The program does not save these values: a newly opened window always starts with them off.

| Type | What the master sees |
|---|---|
| No reply | timeout |
| Slow answer | The answer is delayed by **Slow answer delay**; if slower than the master's timeout it appears as a timeout (on RTU a late answer may get mixed up with the next request) |
| Exception | An exception with the selected **Exception code** |
| Bad CRC | Wrong CRC / LRC, like noise (RTU / ASCII only) |
| Cut off | Incomplete answer |
| Garbage | Receives junk bytes |
| Wrong Slave ID | The answer comes from a different ID, like two devices sharing the same ID |
| Wrong transaction ID | TCP: mismatched Transaction ID |
| Drop connection | TCP: closes the connection instead of answering |

Shortcut buttons: **Noisy line** (bad CRC 2 %, cut off 1 %), **Flaky device** (no reply 5 %), **Slow device** (slow answer 10 %), **Duplicate ID** (answers as another ID 20 %), **Unstable network** (drop connection 3 %, no reply 3 %) and **Clear all**.

To try it with mbtool itself: open Slave Sim answering with faults, then read it with the Devices window and watch the Error types column, or press Health test ([[bus-health]]). You will see error types matching what you configured.

## Moving simulated values (Signals)
<!-- id: sim-signals | category: Program features | keywords: signals, simulate, moving values, random, random walk, loop, ramp, sine, behavior, device profile, device type, sim_pattern, follow preset -->
The simulated slave can make values move like a real device, reading the list of points from a preset (the Signals tab in the Slave Sim window, F9). There are 6 ways to make them move:

| Mode | Resulting value |
|---|---|
| Static | Constant value per the preset's `sim` |
| Random walk | Wanders randomly within min..max without large jumps; booleans toggle occasionally |
| Loop | Steps from min up to max, then wraps around (sawtooth); enum/boolean cycle through one state at a time |
| Sine | A smooth sine wave, each point with a different phase |
| Device behaviour | Behaves according to the device type, see below |
| Preset's own patterns | Moves only the points for which the preset defines its own `sim_pattern` |

**Device behaviour:**
- **meter:** voltage steady around the starting value with slight ripple, current and power follow a slowly varying load, accumulated energy only increases
- **drive:** motor cycle: stopped 5 seconds, accelerate 10 seconds, run 25 seconds, decelerate 10 seconds. Frequency, current and speed follow the velocity; temperature rises with load
- **level:** tank level rises and falls slowly (2 minutes per cycle)
- **pressure:** random walk around the setpoint **flow:** fluctuating flow rate, the volume totalizer only increases
- **temperature:** slow drift with noise **io:** booleans toggling in several rhythms, analog values as waves
- Configuration values (baud, address, measuring range, etc.) do not move. Counters (errors, hours) only increase.

The device type is guessed from the preset's names/units, or you can put `"device_type": "drive"` in the preset (meter, drive, level, pressure, flow, temperature, io, generic).

**Points the master can write (access rw) and text values are not moved**, so a setpoint written by the master stays in place.

**Defining the movement yourself in the preset file:**
```
{"name": "Flow", "address": 0, "type": "float32", "unit": "m3/h", "sim": 12.5,
 "sim_pattern": {"kind": "random_walk", "min": 5, "max": 20, "step": 0.05}}
```
Kinds (`kind`): `fixed`, `noise`, `random_walk`, `sine`, `ramp`, `triangle`, `square`, `sequence` (`values`, `hold` seconds), `counter` (`start`, `rate` per second), `follow` (`signal` = load or speed). Common options: `min`, `max`, `period` (seconds), `phase`, `noise`.

**Use from the CLI and AI:** `python src/mbtool.py sim --preset file.json --signals profile [--device-type drive] [--speed 2]`, or the MCP tool `sim_start` and then `sim_load_preset`. See [[slave-sim]] [[preset-schema]].

Note: the values produced are a simulation for testing your software, not data from a real device, and all library presets are still UNVERIFIED.

## AI Assist (F12)
<!-- id: ai-assist | category: Program features | keywords: ai assist, f12, claude, chatgpt, mcp, token, share, analysis, report -->
There are two ways to let an AI help analyze your devices.

1. **Share the live connection:** turn on the MCP server in the program. An MCP-capable AI can connect and read values from the device you are connected to (read-only). A token is required.
   - **Who can connect:** "Only this PC" (default) or "Other computers on my network", which shows a confirmation dialog because traffic crosses the network as plain HTTP; a VPN such as Tailscale is recommended
   - **Port:** default 8765 (a message appears if the port is in use)
   - **"Let the AI choose the device"** is off by default. When off, the AI cannot change device, connect, disconnect or scan ports by itself, and writing values is always disabled
   - **Token:** can be hidden/shown, copied and regenerated (after regenerating, the old token no longer works). It is not saved to any file
   - There is a live call-log view (failed logins in red) and a yellow badge on the status bar
   See [[mcp-security]].
2. **Copy a diagnostic report:** generates a report (sample values, interpretation, traffic, errors and a checklist) to paste into the chat of any AI. Review the report before sending it to a cloud service, because it contains device values and traffic.
