## Quick start
<!-- id: quickstart | category: Getting started | keywords: getting started quick start how to use first time connect poll -->
Steps for reading values from a device for the first time:

1. Press **F3** (Connection Setup), choose **TCP**, **RTU** or **ASCII** mode, then fill in the connection settings (see [[connect-tcp]] and [[connect-serial]])
2. Press **F5** or the Connect button. The program starts polling using the values in the Poll panel
3. In the Poll panel set **Slave ID**, **Function**, **Address**, **Quantity**, **Scan Rate** and **Display** (see [[poll-panel]])
4. Check the results in the table. Values that change flash yellow. The status bar at the bottom shows the number of successful/failed requests
5. If you have no device, try the simulator: press **F9** (Slave Sim) or run `python src/mbtool.py sim --port 5020`

If the values do not match the device's display, see [[trouble-values-wrong]]

Is there a newer version? See [[updates]]

## Connecting over TCP (Modbus/TCP)
<!-- id: connect-tcp | category: Getting started | keywords: tcp ip host port 502 ethernet network gateway -->
Use this for devices with a network port, or for a gateway that converts RS-485 to Ethernet.

- **Host:** IP address or host name, for example `192.168.1.50`
- **Port:** the standard is **502** (some models use another port, for example Huawei SUN2000 uses 6607)
- **Slave ID (Unit ID):** most TCP devices ignore this value (usually 1 or 255), but if it is a gateway to an RS-485 line, this is the Slave ID of the device behind the gateway
- **Timeout:** how long to wait for a response. The default is about 1 second. Increase it if the network is slow

TCP has no CRC because TCP checks integrity itself. Each request carries a Transaction ID used to match it to its response. See [[frame-tcp]]

## Connecting over Serial (RTU / ASCII)
<!-- id: connect-serial | category: Getting started | keywords: serial rtu ascii com rs485 rs232 baud parity stop bits port -->
Use this for RS-485/RS-232 through a USB converter.

- **COM port:** choose from the list (press Refresh if you just plugged in the device). This field can also be typed in, for example `socket://127.0.0.1:7000` for a virtual cable ([[vcom]])
- **Baud rate, Data bits, Parity, Stop bits:** every value must match the device. See [[serial-settings]]
- **RTU or ASCII mode:** almost all devices use RTU. See [[rtu-vs-ascii]]
- If you do not know the settings, use the automatic scanner: [[scanner]]
- A COM port can be opened by only one program at a time. If it shows "busy", close the other program using that port. See [[trouble-port-busy]]

## Testing without a device (Simulator and virtual cable)
<!-- id: vcom | category: Getting started | keywords: simulator vcom null modem test socket slave sim -->
- **Slave Sim (F9):** opens a simulated slave in the GUI. You can edit values in the table. Choose TCP, RTU or ASCII
- **In the CLI:** `python src/mbtool.py sim --port 5020 --preset presets/example_generic_meter.json` makes the simulator respond according to the preset's `sim` values. Add `--signals profile` to make values move like a real device ([[sim-signals]])
- **Virtual cable (vcom):** `python src/mbtool.py vcom --port 7000` creates a null-modem over TCP with no driver installation. Enter `socket://127.0.0.1:7000` in the COM field on both the master and the slave side
- **Limitation:** the virtual cable does not enforce baud/parity, so it can test RTU framing but cannot test wrong-settings scenarios

## All keyboard shortcuts
<!-- id: shortcuts | category: Getting started | keywords: shortcut keyboard f1 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12 ctrl ctrl+t ctrl+w tab -->
| Key | Action |
|---|---|
| F1 | Open this manual |
| F3 | Connection setup (Setup) |
| F4 | Value Inspector [[inspector]] |
| F5 | Connect / disconnect |
| F6 | Write values (Write dialog) [[write-dialog]] |
| F7 | Preset [[preset-view]] |
| F8 | Traffic monitor [[traffic-monitor]] |
| F9 | Slave Sim [[slave-sim]] (testing without a device: [[vcom]]) |
| F10 | Trend graph [[trend]] |
| F11 | Multiple devices (Devices) [[devices]] |
| F12 | AI Assist [[ai-assist]] |
| Ctrl+L | Start/stop logging [[logging]] |
| Ctrl+S / Ctrl+O | Save / open workspace [[workspace]] |
| Ctrl+M | RS-485 bus map (Bus map) [[bus-map]] |
| Ctrl+T / Ctrl+W | Open a new poll tab / close the active poll tab [[poll-tabs]] |
| Ctrl+Tab, Ctrl+PgUp / Ctrl+PgDn | Switch poll tabs (double-click a tab name = rename, middle-click = close) |
| Esc | Close most dialogs / cancel editing / clear the search box |

## Updating the program (Check for updates)
<!-- id: updates | category: Getting started | keywords: update new version check for updates release download install over portable github upgrade -->
**Help > Check for updates...** asks the Releases page on GitHub whether a newer mbtool than yours exists.

- **A newer version exists:** a window shows its number, release date and what changed. **Download installer** (or **Download portable zip** when you run the portable copy) opens the download in your browser, **Release page** opens the release, **Skip this version** stops the start-up reminder for this version, **Later** closes the window.
- **You have the latest version:** it says you are up to date.
- **Check at start-up:** tick **Help > Check for updates when mbtool starts** (on by default). The program then checks by itself at most once a day; without internet it says nothing and does not slow down.

**Installing the new version:**
- Installer: run the downloaded `mbtool-setup.exe` and install into the same folder; no need to uninstall first.
- Portable: unzip the new zip and move your `data` folder next to the new `mbtool.exe`.

Your settings, devices, workspaces and edited presets are kept; the bundled preset library is refreshed automatically.

Nothing is downloaded or installed automatically. The check is one read of GitHub's public release information (it only sends the program's name and version), nothing else about you. CLI: `mbtool-cli.exe update-check` (or `python src/mbtool.py update-check`)
