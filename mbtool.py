"""mbtool - a Modbus master (GUI + CLI) with a built-in simulator slave.

  python mbtool.py                      GUI  (F3 setup, F4 value inspector, F5 connect/disconnect, F6 write,
                                              F7 presets, F8 traffic monitor, F9 slave sim, F10 trend,
                                              F11 devices, F12 AI assist, Ctrl+L log, Ctrl+O / Ctrl+S workspace)
  python mbtool.py --workspace plant.mbw   GUI with a workspace
  python mbtool.py sim [--port 5020]    run a Modbus/TCP simulator slave
  python mbtool.py read tcp://127.0.0.1:5020 -u 1 -f 3 -a 0 -n 10 [--fmt Float] [--scan 1000] [--log out.csv]
  python mbtool.py write tcp://127.0.0.1:5020 -u 1 -f 6 -a 5 123
  python mbtool.py read serial:COM3?baud=19200&parity=E&mode=rtu -u 1 -f 4 -a 0 -n 4
"""
import argparse
import collections
import csv
import json
import math
import os
import queue
import re
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

try:  # the CLI works without Tk
    import tkinter as tk
    from tkinter import filedialog, ttk
    from tkinter import font as tkfont
except ImportError:
    tk = None

from modbus_core import (FORMATS, HUB, Faults, ModbusError, SerialMaster, SerialSlave, SimSlave, TcpMaster,
                         encode_value, format_registers)
from preset import TYPE_WORDS, Preset, PresetError, from_csv, load_preset
import signals as simsig
import appinfo
import monitor as mon
from datalog import TIME_FORMATS, CsvLog, DeviceLogs, LogError

FUNCS = {1: "01 Read Coils (0x)", 2: "02 Read Discrete Inputs (1x)",
         3: "03 Read Holding Registers (4x)", 4: "04 Read Input Registers (3x)"}


def connect(url, timeout=1.0):
    u = urlparse(url)
    if u.scheme == "tcp":
        return TcpMaster(u.hostname, u.port or 502, timeout)
    if u.scheme == "serial":
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        return SerialMaster(u.path or u.netloc, int(q.get("baud", 9600)), q.get("parity", "N").upper(),
                            int(q.get("stop", 1)), int(q.get("data", 8)), q.get("mode", "rtu"), timeout)
    raise SystemExit("url must be tcp://host:port or serial:COMx?baud=..&parity=..&mode=rtu|ascii")


def poll_once(m, unit, fc, addr, qty):
    return m.request(unit, fc, addr=addr, qty=qty)


def render(vals, fc, fmt):
    if fc in (1, 2):
        return [(i, "1" if v else "0") for i, v in enumerate(vals)]
    return format_registers(vals, fmt)


# ---------------- CLI ----------------

def cli_read(a):
    if a.func not in (1, 2, 3, 4):
        raise SystemExit("read function must be 1, 2, 3 or 4")
    m = connect(a.url)
    logf = open(a.log, "a", newline="") if a.log else None
    w = csv.writer(logf) if logf else None
    try:
        while True:
            t = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                rows = render(poll_once(m, a.unit, a.func, a.addr, a.n), a.func, a.fmt)
                print(t, " ".join(f"[{a.addr + i}]={v}" for i, v in rows))
                if w: w.writerow([t] + [v for _, v in rows]); logf.flush()
            except (ModbusError, OSError) as e:
                print(t, "ERROR:", e)
                if w: w.writerow([t, "ERROR", str(e)]); logf.flush()
            if a.scan <= 0:
                break
            time.sleep(a.scan / 1000)
    except KeyboardInterrupt:
        pass
    finally:
        m.close()
        if logf: logf.close()


def cli_write(a):
    m = connect(a.url)
    try:
        if a.func == 5:
            m.write_coil(a.unit, a.addr, a.values[0] not in ("0", "false"))
        elif a.func == 6:
            m.write_register(a.unit, a.addr, encode_value(a.values[0], "Unsigned")[0])
        elif a.func == 15:
            m.write_coils(a.unit, a.addr, [v not in ("0", "false") for v in a.values])
        elif a.func == 16:
            words = [w for v in a.values for w in encode_value(v, a.fmt)]
            m.write_registers(a.unit, a.addr, words)
        else:
            raise SystemExit("write function must be 5, 6, 15 or 16")
        print("OK")
    finally:
        m.close()


def cli_vcom(a):
    from modbus_core import VirtualCable
    baud = getattr(a, "baud", 0) or None
    bits = getattr(a, "bits", 11)
    if baud is not None and (baud < 50 or not 7 <= bits <= 12):
        raise SystemExit("--baud must be 50 or more and --bits 7..12")
    c = VirtualCable("127.0.0.1", a.port, baud=baud, bits_per_char=bits)
    print("virtual null-modem cable ready.")
    if baud:
        print(f"line speed emulated: {baud} baud, {bits} bits per character "
              f"({1000.0 * bits / baud:.3f} ms per byte)")
    else:
        print("line speed: not emulated (as fast as possible); use --baud N to emulate a real bus")
    print(f"Use {c.url} as the COM port on BOTH sides, e.g.:")
    print(f"  python mbtool.py sim --serial {c.url} -u 1 --baud 19200 --parity E")
    print(f"  python mbtool.py read serial:{c.url}?baud=19200&parity=E -f 3 -a 0 -n 5")
    print("In the GUI, type the URL into the COM port box. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def seed(s):
    for i in range(100):
        s.holding[i] = i * 10
        s.input[i] = 1000 + i
        s.coils[i] = bool(i % 2)
        s.discrete[i] = bool(i % 3 == 0)


def _sim_signals(a, preset, store):
    """--signals MODE: start moving the simulated values (see signals.py)."""
    if not a.signals or a.signals == "static":
        return None
    from signals import SignalEngine
    eng = SignalEngine(preset, store, mode=a.signals, device_type=a.device_type, speed=a.speed)
    eng.tick(0.0)
    eng.start()
    print(f"signals: mode {a.signals}, device type {eng.device_type}, {len(eng.plans)} moving points")
    return eng


def _sim_delay(a, store):
    """--delay MS: the simulator answers after this many milliseconds (like a real device)."""
    ms = float(getattr(a, "delay", 0) or 0)
    if not 0 <= ms <= 60000:
        raise SystemExit("--delay must be 0..60000 ms")
    store.response_delay = ms / 1000.0
    print(f"response delay: {ms:g} ms" + ("" if ms else " (answers at once)"))


def cli_sim(a):
    if a.serial:
        s = SimSlave(listen=False); seed(s)
        _sim_delay(a, s)
        if a.preset:
            pr = load_preset(a.preset)
            pr.fill_simulator(s)
            print(f"simulator filled from preset {a.preset}")
            _sim_signals(a, pr, s)
        SerialSlave(a.serial, a.unit, a.baud, a.parity, store=s, mode=a.mode)
        print(f"{a.mode.upper()} slave id {a.unit} on {a.serial} @ {a.baud} - Ctrl+C to stop")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            return
        return
    s = SimSlave("0.0.0.0" if a.public else "127.0.0.1", a.port)
    _sim_delay(a, s)
    if a.preset:
        from preset import load_preset
        pr = load_preset(a.preset)
        pr.fill_simulator(s)
        print(f"simulator filled from preset {a.preset}")
        _sim_signals(a, pr, s)
    else:
        seed(s)
    print(f"simulator on port {s.port} (all unit ids) - Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

# ---------------- presets (CLI) ----------------

def preset_dir():
    return appinfo.preset_dir()


def cli_preset_list(a):
    import os
    from preset import load_preset
    for root, _d, files in os.walk(preset_dir()):
        for f in sorted(files):
            if f.endswith(".json"):
                path = os.path.join(root, f)
                try:
                    pr = load_preset(path)
                    flag = "verified" if pr.verified else "UNVERIFIED"
                    print(f"{os.path.relpath(path, preset_dir())}\t{pr.name}\t{len(pr.points)} points\t{flag}")
                except Exception as e:
                    print(f"{f}\tINVALID: {e}")


def cli_preset_read(a):
    from preset import load_preset
    pr = load_preset(a.preset)
    if not pr.verified:
        print("NOTE: preset is not verified against a real device", file=sys.stderr)
    m = connect(a.url)
    try:
        while True:
            res = pr.read(m, a.unit, a.group)
            print(time.strftime("%H:%M:%S"))
            for n, d in res.items():
                print(f"  {n:<28} {d.get('error') or d['text']}")
            if a.scan <= 0:
                break
            time.sleep(a.scan / 1000)
    except KeyboardInterrupt:
        pass
    finally:
        m.close()


def cli_preset_write(a):
    from preset import PresetError, load_preset
    pr = load_preset(a.preset)
    m = connect(a.url)
    try:
        pr.write(m, a.point, float(a.value) if a.value.lower() not in ("true", "false") else a.value.lower() == "true", a.unit)
        print("OK")
    except (PresetError, ModbusError) as e:
        raise SystemExit(f"ERROR: {e}")
    finally:
        m.close()


# ---------------- GUI ----------------
#
# Threading rule: Tk widgets are only touched on the main thread. Network/serial I/O runs on
# worker threads, which report back through self.q (a queue drained by _pump() via after()).
# The poll thread carries a generation token: once self.gen changes, its results are ignored
# and it exits.

SETTINGS_FILE = appinfo.settings_file()
HOME_SETTINGS = os.path.join(os.path.expanduser("~"), ".mbtool_settings.json")

DEFAULTS = {
    "conn": {"mode": "TCP", "host": "127.0.0.1", "port": "502", "com": "COM1", "baud": "9600",
             "parity": "N", "stop": "1", "data": "8", "timeout": "1000"},
    "poll": {"unit": "1", "fc": "3", "addr": "0", "qty": "10", "scan": "1000", "fmt": "Unsigned",
             "plc": False, "base": "", "rows": "Auto",  # base "" = derive from the old boolean "plc"
             "multi": False, "multi_cols": list(mon.MULTI_DEFAULT), "multi_align": "0"},  # multi-type view on/off and its columns
    "sim": {"mode": "TCP", "port": "5020", "remote": False, "com": "COM2", "baud": "9600",
            "parity": "N", "unit": "1", "area": "Holding Registers (4x)", "addr": "0", "qty": "20",
            "fmt": "Unsigned", "sig_mode": "profile", "sig_device": "", "sig_speed": "1", "sig_preset": "",
            "multi": False,  # Data tab in the multi-type view (columns are shared with the main window)
            "delay": "0"},  # response delay in ms (SimSlave.response_delay)
    "geometry": "",
    "log": dict(mon.LOG_DEFAULT),
    "workspace": {"recent": [], "autoload": False},
    "devices": {"list": [], "gap_ms": "50", "log_folder": "", "log_on": False,
                "scan_mode": "independent", "cycle_target_ms": "",  # PLC cycle mode (Devices > Overview)
                "calc": json.loads(json.dumps(mon.CALC_DEFAULT))},   # Devices > Calculator inputs
    "alerts": {"beep": False},
    "ai": dict(mon.AI_DEFAULT),  # AI sharing options - the bearer token is never saved
    "help": {"geometry": "", "topic": "", "mode": "category", "sash": 0, "lang": "th"},  # lang: th | en
    "busmap": mon.busmap_default(),  # Tools > Bus map: busmap.default_map() shape (+ cable_custom), checked on load
    "busmap_ui": dict(mon.BUSMAP_UI_DEFAULT),  # bus map animation inputs (parity, FC, quantity, delay, speed...)
    "export": {"mqtt": {"on": False, "host": "", "port": "1883", "tls": False, "user": "", "client_id": "",
                        "prefix": "mbtool", "style": "json", "retain": False, "min_interval_s": "0"},
               "sqlite": {"on": False, "path": ""}},  # Tools > Data export (the MQTT password is never saved)
    "report": {"project": "", "site": "", "customer": "", "engineer": "", "notes": "", "lang": "en", "pdf": True,
               "folder": ""},  # File > Commissioning report: the form is remembered
    "tabs": {"main_name": "Poll 1", "active": 0},  # poll tabs of the main window: first tab's title, selected tab
}
AI_AUDIT_LOG = appinfo.audit_log()

BAUDS = ["1200", "2400", "4800", "9600", "14400", "19200", "38400", "57600", "115200"]
PARITY_NAMES = {"N": "None", "E": "Even", "O": "Odd"}
WRITE_FUNCS = {5: "05 Write Single Coil", 6: "06 Write Single Register",
               15: "15 Write Multiple Coils", 16: "16 Write Multiple Registers",
               22: "22 Mask Write Register", 23: "23 Read/Write Multiple Registers"}
SIM_AREAS = {"Coils (0x)": "coils", "Discrete Inputs (1x)": "discrete",
             "Input Registers (3x)": "input", "Holding Registers (4x)": "holding"}
PLC_PREFIX = mon.PLC_PREFIX
ROW_CHOICES = ["Auto", "10", "20", "30", "40"]
FMT_CHARS = {"Binary": 16, "Double": 24, "Float": 14, "Long": 11}
COL = dict(bg="#ffffff", alt="#f4f6f9", addr="#eceff3", addrfg="#59606b", line="#d7dbe1",
           head="#e2e6ec", headfg="#2b2f36", fg="#111111", flash="#ffe37a", sel="#1a64d6",
           stale="#a0a5ad", ok="#2e9d44", bad="#d93025", warn="#e69500", off="#9aa0a6")
AI_AMBER = "#ffc247"


def load_settings():
    """Defaults merged with the saved JSON file; a missing or broken file is ignored."""
    cfg = json.loads(json.dumps(DEFAULTS))
    for path in (SETTINGS_FILE, HOME_SETTINGS):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for sec, d in DEFAULTS.items():
            v = data.get(sec)
            if sec == "busmap":  # a whole map: validated with busmap; anything invalid -> the default map
                cfg[sec] = mon.norm_busmap(v)[0]
                continue
            if isinstance(d, dict) and isinstance(v, dict):
                for k, dv in d.items():
                    if k in v and type(v[k]) is type(dv):
                        cfg[sec][k] = v[k]
            elif isinstance(d, str) and isinstance(v, str):
                cfg[sec] = v
        break
    return cfg


def save_settings(cfg):
    for path in (SETTINGS_FILE, HOME_SETTINGS):  # next to the script, else the user profile
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            return path
        except OSError:
            continue
    return None


def int_in(text, lo, hi):
    s = str(text).strip()
    if not s:
        raise ValueError("required")
    try:
        n = int(s, 16) if s.lower().lstrip("-").startswith("0x") else int(s)
    except ValueError:
        raise ValueError("not a whole number")
    if not lo <= n <= hi:
        raise ValueError(f"range {lo}…{hi}")
    return n


def addr_label(fc, addr, base):
    """Protocol address -> label in the address base (0, 1 or "plc"; the old boolean is accepted too)."""
    return mon.addr_label(fc, addr, base)


def parse_addr(text, fc, base):
    """User text -> protocol (0-based) address in the address base (0, 1 or "plc")."""
    return mon.parse_addr(text, fc, base)


BASE_CHOICES = {0: "0 - protocol address (first register = 0)",
                1: "1 - first register = 1 (plain numbering, no prefix)",
                "plc": "PLC 4xxxx / 3xxxx / 1xxxx / 0xxxx (1-based with area prefix)"}
BASE_TIP = ("How addresses are typed and shown:\n"
            "0 - protocol address: the number sent on the wire; the first register is 0.\n"
            "1 - first register = 1: plain 1-based numbering as in many manuals (no prefix).\n"
            "PLC: 1-based with an area prefix, 40001 = first holding register, 30001 = first input register,\n"
            "      10001 = first discrete input, 00001 = first coil.\n"
            "A wrong base is the most common reason for values being off by one register.")


def fmt_width(fmt):
    return 4 if fmt.startswith("Double") else 2 if fmt.startswith(("Long", "Float")) else 1


def fmt_chars(fmt):
    return next((v for k, v in FMT_CHARS.items() if fmt.startswith(k)), 7)


def parse_bit(text):
    t = str(text).strip().lower()
    if t in ("1", "on", "true"):
        return True
    if t in ("0", "off", "false"):
        return False
    raise ValueError("enter 0 or 1")


def split_values(text):
    return [t for t in re.split(r"[\s,;]+", str(text).strip()) if t]


def encode_many(text, fmt):
    words = []
    for tok in split_values(text):
        try:
            words += encode_value(tok, fmt)
        except ValueError:
            raise ValueError(f"'{tok}' is not a valid {fmt} value")
    if not words:
        raise ValueError("enter at least one value")
    return words


def list_com_ports():
    """[(device, description)] from pyserial; [] if pyserial or ports are missing."""
    try:
        from serial.tools import list_ports
        return [(p.device, p.description or "") for p in sorted(list_ports.comports(), key=lambda p: p.device)]
    except Exception:
        return []


def serial_error_text(port, e):
    """Turn pyserial's open errors into something a user can act on."""
    s = str(e)
    if "PermissionError" in s or "Access is denied" in s or "busy" in s.lower():
        return f"{port} is busy - it is already open in another program"
    if "FileNotFoundError" in s or "cannot find" in s or "No such file" in s:
        return f"{port} does not exist - check the adapter and press Refresh"
    return s


def master_factory(c):
    """Validate connection settings (strings) -> (factory, summary). Raises ValueError."""
    timeout = int_in(c["timeout"], 50, 60000) / 1000
    if c["mode"] == "TCP":
        host = c["host"].strip()
        if not host:
            raise ValueError("host is required")
        port = int_in(c["port"], 1, 65535)
        return (lambda: TcpMaster(host, port, timeout)), f"TCP {host}:{port}"
    com = c["com"].strip().split(" ")[0]
    if not com:
        raise ValueError("choose a COM port")
    baud = int_in(c["baud"], 50, 4000000)
    parity, stop, data, mode = c["parity"], int(c["stop"]), int(c["data"]), c["mode"].lower()

    def open_port():
        try:
            return SerialMaster(com, baud, parity, stop, data, mode, timeout)
        except ImportError:
            raise ModbusError("pyserial is not installed (pip install pyserial)")
        except Exception as e:
            raise ModbusError(serial_error_text(com, e))

    def make():  # shared: Poll 1, poll tabs and devices on this COM port take turns on one master
        return HUB.acquire(com, (baud, parity, stop, data, mode), open_port)
    return make, f"{c['mode']} {com} {baud} {data}{parity}{stop}"


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + f".{int(t % 1 * 1000):03d}"


def setup_style(root):
    st = ttk.Style(root)
    for t in ("vista", "clam"):
        if t in st.theme_names():
            st.theme_use(t)
            break
    base = tkfont.nametofont("TkDefaultFont")
    size = base.cget("size")
    small = base.copy()
    small.configure(size=size - 1 if size > 0 else size + 1)
    bold = base.copy()
    bold.configure(weight="bold")
    root._mb_fonts = [small, bold]  # keep references alive
    st.configure("Hint.TLabel", foreground=COL["bad"], font=small)
    st.configure("Info.TLabel", foreground="#6b7280", font=small)
    st.configure("Warn.TLabel", foreground="#a15c00", font=small)
    st.configure("Conn.TLabel", font=bold)
    st.configure("SOk.TLabel", foreground="#1e7b34")
    st.configure("SBad.TLabel", foreground=COL["bad"])
    st.configure("SWarn.TLabel", foreground="#a15c00")
    st.configure("SInfo.TLabel", foreground="#333333")
    return st


class Tooltip:
    def __init__(self, widget, text):
        self.w, self.text, self.tw, self.job = widget, text, None, None
        widget.bind("<Enter>", self._sched, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _sched(self, _e=None):
        self._hide()
        self.job = self.w.after(500, self._show)

    def _show(self):
        self.job = None
        try:
            x = self.w.winfo_rootx() + 6
            y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        except tk.TclError:
            return
        self.tw = tk.Toplevel(self.w)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tw, text=self.text, bg="#ffffe1", fg="#222", relief="solid", bd=1,
                 padx=6, pady=3, justify="left").pack()

    def _hide(self, _e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tw:
            self.tw.destroy()
            self.tw = None


class Lamp:
    """Round connection / running indicator."""

    def __init__(self, parent, s):
        d = int(14 * s)
        bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.c = tk.Canvas(parent, width=d + 2, height=d + 2, highlightthickness=0, bg=bg)
        self.o = self.c.create_oval(1, 1, d, d, fill=COL["off"], outline="")
        self.color = COL["off"]

    def set(self, color):
        self.color = color
        self.c.itemconfig(self.o, fill=color)


def vfield(parent, col, label, widget, tip=None, row=0):
    """Label above widget above an (initially blank) inline hint. Returns the hint label."""
    ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 12))
    widget.grid(row=row + 1, column=col, sticky="w", padx=(0, 12))
    width = max(widget.winfo_reqwidth(), int(72 * parent.winfo_fpixels("1i") / 96))
    parent.columnconfigure(col, minsize=width + 12)  # a hint never widens (shifts) the row
    hint = ttk.Label(parent, text=" ", style="Hint.TLabel", wraplength=width)
    hint.grid(row=row + 2, column=col, sticky="nw", padx=(0, 12))
    if tip:
        Tooltip(widget, tip)
    return hint


def set_hints(hints, errs, info=None):
    info = info or {}
    for k, h in hints.items():
        if k in errs:
            h.configure(text=errs[k], style="Hint.TLabel")
        elif k in info:
            h.configure(text=info[k], style="Info.TLabel")
        else:
            h.configure(text=" ", style="Hint.TLabel")


class RegGrid:
    """Address/value grid on a Canvas: wraps into as many column pairs as fit,
    scrolls vertically, flashes changed cells, supports selection and in-place editing."""
    ROWS_MIN = 10

    def __init__(self, parent, s, on_edit=None):
        self.s, self.on_edit = s, on_edit
        self.frame = ttk.Frame(parent)
        self.font = tkfont.Font(family="Consolas", size=10)
        self.hfont = tkfont.nametofont("TkDefaultFont").copy()
        self.hfont.configure(weight="bold")
        self.cw = self.font.measure("0")
        self.rh = self.font.metrics("linespace") + int(8 * s)
        self.aw = self.cw * 6 + int(16 * s)
        self.vw = self.cw * 8 + int(16 * s)
        self.pad = int(4 * s)
        self.head = tk.Canvas(self.frame, height=self.rh, bg=COL["head"], highlightthickness=0)
        self.cv = tk.Canvas(self.frame, bg=COL["bg"], highlightthickness=0, takefocus=1,
                            yscrollincrement=self.rh)
        self.sb = ttk.Scrollbar(self.frame, orient="vertical", command=self.cv.yview)
        self.hsb = ttk.Scrollbar(self.frame, orient="horizontal", command=self._xview)
        self.cv.configure(yscrollcommand=self.sb.set, xscrollcommand=self._xset)
        self.rows_setting = "Auto"  # or "10" / "20" / "30" / "40": fixed rows per column
        self.head.grid(row=0, column=0, sticky="ew")
        self.cv.grid(row=1, column=0, sticky="nsew")
        self.sb.grid(row=0, column=1, rowspan=2, sticky="ns")
        self.hsb.grid(row=2, column=0, sticky="ew")
        self.hsb.grid_remove()
        self.frame.rowconfigure(1, weight=1)
        self.frame.columnconfigure(0, weight=1)
        self.labels, self.values, self.items = [], [], []
        self.rows = self.ncols = 0
        self.sel = None
        self.on_context = None  # on_context(i, event): right-click on a cell
        self.cv.bind("<Button-3>", self._ctx)
        self.flash, self.flash_job = {}, None
        self.stale = False
        self.editor = self.edit_err = None
        self.cv.bind("<Configure>", lambda e: self._layout())
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Double-1>", self._dbl)
        self.cv.bind("<Key>", self._key)
        self.cv.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.cv.bind("<Shift-MouseWheel>", lambda e: self._xview("scroll", -1 if e.delta > 0 else 1, "units"))

    def _xview(self, *args):
        self.cv.xview(*args)
        self.head.xview_moveto(self.cv.xview()[0])

    def _xset(self, lo, hi):
        self.hsb.set(lo, hi)
        self.head.xview_moveto(float(lo))
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            if self.hsb.winfo_ismapped():
                self.hsb.grid_remove()
        elif not self.hsb.winfo_ismapped():
            self.hsb.grid()

    # ----- structure -----
    def set_cells(self, labels, chars=7):
        self.end_edit()
        self.labels = list(labels)
        self.values = [""] * len(self.labels)
        self.vw = self.cw * max(chars, 6) + int(16 * self.s)
        self.flash.clear()
        if self.sel is not None and self.sel >= len(self.labels):
            self.sel = None
        self._layout(force=True)

    def _layout(self, force=False):
        n = len(self.labels)
        pw = self.aw + self.vw
        fit = max(1, (max(self.cv.winfo_width(), 1) - self.pad) // pw)
        rows, ncols = mon.grid_layout(n, fit, self.rows_setting, self.ROWS_MIN)
        if not force and rows == self.rows:
            return
        self.rows, self.ncols = rows, ncols
        self._draw()

    def _xy(self, i):
        c, r = divmod(i, self.rows)
        return self.pad + c * (self.aw + self.vw), r * self.rh

    def _draw(self):
        self.end_edit()
        cv, hd, s = self.cv, self.head, self.s
        aw, vw, rh = self.aw, self.vw, self.rh
        pw = aw + vw
        cv.delete("all")
        hd.delete("all")
        self.items = []
        for c in range(self.ncols):
            x = self.pad + c * pw
            hd.create_text(x + aw - 6 * s, rh / 2, text="Address", anchor="e", font=self.hfont, fill=COL["headfg"])
            hd.create_text(x + pw - 6 * s, rh / 2, text="Value", anchor="e", font=self.hfont, fill=COL["headfg"])
            hd.create_line(x + pw, 3, x + pw, rh - 3, fill="#c3c8cf")
        fg = COL["stale"] if self.stale else COL["fg"]
        for i, lab in enumerate(self.labels):
            x, y = self._xy(i)
            band = COL["alt"] if (i % self.rows) % 2 else COL["bg"]
            cv.create_rectangle(x, y, x + aw, y + rh, fill=COL["addr"], outline=COL["line"])
            cv.create_text(x + aw - 6 * s, y + rh / 2, text=lab, anchor="e", font=self.font, fill=COL["addrfg"])
            rect = cv.create_rectangle(x + aw, y, x + pw, y + rh, outline=COL["line"],
                                       fill=COL["flash"] if i in self.flash else band)
            txt = cv.create_text(x + pw - 6 * s, y + rh / 2, text=self.values[i], anchor="e",
                                 font=self.font, fill=fg)
            self.items.append((rect, txt, band))
        width = self.pad * 2 + self.ncols * pw
        cv.configure(scrollregion=(0, 0, width, self.rows * rh + 1))
        hd.configure(scrollregion=(0, 0, max(width, cv.winfo_width()), rh))
        if width <= cv.winfo_width():
            cv.xview_moveto(0)
        self._draw_sel()

    # ----- values -----
    def set_values(self, vals, flash=True):
        was_stale, self.stale = self.stale, False
        now = time.monotonic()
        for i, v in enumerate(vals[:len(self.items)]):
            rect, txt, _band = self.items[i]
            if v != self.values[i]:
                if flash and self.values[i] != "":
                    self.flash[i] = now
                    self.cv.itemconfig(rect, fill=COL["flash"])
                self.values[i] = v
                self.cv.itemconfig(txt, text=v)
            if was_stale:
                self.cv.itemconfig(txt, fill=COL["fg"])
        if self.flash and not self.flash_job:
            self.flash_job = self.cv.after(300, self._unflash)

    def _unflash(self):
        self.flash_job = None
        now = time.monotonic()
        for i, t in list(self.flash.items()):
            if now - t >= 1.2:
                del self.flash[i]
                if i < len(self.items):
                    self.cv.itemconfig(self.items[i][0], fill=self.items[i][2])
        if self.flash:
            self.flash_job = self.cv.after(300, self._unflash)

    def set_stale(self):
        """Grey out the values (last poll failed / disconnected)."""
        self.stale = True
        for _r, txt, _b in self.items:
            self.cv.itemconfig(txt, fill=COL["stale"])

    # ----- selection & keyboard -----
    def _hit(self, e):
        x, y = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        pw = self.aw + self.vw
        if not self.rows or x < self.pad:
            return None
        c, r = int((x - self.pad) // pw), int(y // self.rh)
        if not (0 <= c < self.ncols and 0 <= r < self.rows):
            return None
        i = c * self.rows + r
        return i if i < len(self.labels) else None

    def _click(self, e):
        self.cv.focus_set()
        i = self._hit(e)
        if i is not None:
            self.select(i)

    def _ctx(self, e):
        self.cv.focus_set()
        i = self._hit(e)
        if i is not None:
            self.select(i)
            if self.on_context:
                self.on_context(i, e)

    def _dbl(self, e):
        i = self._hit(e)
        if i is not None:
            self.select(i)
            if self.on_edit:
                self.on_edit(i)

    def select(self, i):
        self.sel = i
        self._draw_sel()
        self._see(i)

    def _draw_sel(self):
        self.cv.delete("sel")
        if self.sel is None or self.sel >= len(self.labels):
            return
        x, y = self._xy(self.sel)
        self.cv.create_rectangle(x + 1, y + 1, x + self.aw + self.vw - 1, y + self.rh - 1,
                                 outline=COL["sel"], width=2, tags="sel")

    def _see(self, i):
        total = self.rows * self.rh
        if total <= 0:
            return
        x, y = self._xy(i)
        top, h = self.cv.canvasy(0), self.cv.winfo_height()
        if y < top:
            self.cv.yview_moveto(y / total)
        elif y + self.rh > top + h:
            self.cv.yview_moveto((y + self.rh - h) / total)
        width = self.pad * 2 + self.ncols * (self.aw + self.vw)
        left, w = self.cv.canvasx(0), self.cv.winfo_width()
        if width > w:
            if x < left:
                self._xview("moveto", max(x - self.pad, 0) / width)
            elif x + self.aw + self.vw > left + w:
                self._xview("moveto", (x + self.aw + self.vw + self.pad - w) / width)

    def _key(self, e):
        if not self.labels:
            return None
        i = self.sel if self.sel is not None else 0
        d = {"Up": -1, "Down": 1, "Left": -self.rows, "Right": self.rows}.get(e.keysym)
        if d is not None:
            j = i + d if self.sel is not None else 0
            if 0 <= j < len(self.labels):
                self.select(j)
            return "break"
        if e.keysym in ("Return", "KP_Enter", "F2") and self.sel is not None and self.on_edit:
            self.on_edit(self.sel)
            return "break"
        return None

    # ----- in-place editor -----
    def start_edit(self, i, commit):
        """commit(i, text) -> None on success, or an error string (editor stays open, turns red)."""
        self.end_edit()
        self._see(i)
        x, y = self._xy(i)
        e = tk.Entry(self.cv, font=self.font, justify="right", relief="solid", bd=1, highlightthickness=0)
        e.insert(0, self.values[i])
        e.select_range(0, "end")
        self.cv.create_window(x + self.aw, y, window=e, anchor="nw", width=self.vw, height=self.rh, tags="editor")
        self.editor = e

        def ok(_e=None):
            err = commit(i, e.get())
            if err:
                e.configure(bg="#ffd6d6")
                self.cv.delete("edit_err")
                if self.edit_err is None:
                    self.edit_err = tk.Label(self.cv, bg=COL["bad"], fg="white", padx=4, pady=1)
                self.edit_err.configure(text=err + "   (Esc = cancel)")
                self.cv.create_window(x + self.aw, y + self.rh, window=self.edit_err, anchor="nw",
                                      tags=("editor", "edit_err"))
            else:
                self.end_edit()
                self.cv.focus_set()
            return "break"

        def cancel(_e=None):
            self.end_edit()
            self.cv.focus_set()
            return "break"
        e.bind("<Return>", ok)
        e.bind("<KP_Enter>", ok)
        e.bind("<Escape>", cancel)
        e.bind("<FocusOut>", lambda _e: self.end_edit())
        e.bind("<Key>", lambda _e: e.configure(bg="white"), add="+")
        e.mb_commit, e.mb_cancel = ok, cancel  # Enter / Esc without key events (tests)
        e.focus_set()
        return e

    def end_edit(self):
        if self.editor:
            ed, self.editor = self.editor, None
            self.cv.delete("editor")
            ed.destroy()
            if self.edit_err is not None:
                self.edit_err.destroy()
                self.edit_err = None


MULTI_CHARS = {"Binary": 16, "Double": 24, "Float": 15, "Long": 11}
MULTI_TIP = ("Multi-type view: one row per register, one column per data type\n"
             "(Unsigned, Signed, Hex, Binary, Float, Long... - pick them with 'Columns...').\n"
             "Long / Float use the register of the row and the next one, Double the next three:\n"
             "'-' = not enough registers left in this poll.\n"
             "Not for FC 01 / 02 (coils and discrete inputs are single bits).")


class MultiGrid:
    """Multi-type view on a Canvas: Address column + one column per data type, one row per register.
    Same look as RegGrid (flash on change, grey when stale, in-place editor). Cells are (row, column index)."""

    def __init__(self, parent, s, on_edit=None):
        self.s, self.on_edit = s, on_edit  # on_edit(row, col)
        self.frame = ttk.Frame(parent)
        self.font = tkfont.Font(family="Consolas", size=10)
        self.hfont = tkfont.nametofont("TkDefaultFont").copy()
        self.hfont.configure(weight="bold")
        self.cw = self.font.measure("0")
        self.rh = self.font.metrics("linespace") + int(8 * s)
        self.aw = self.cw * 6 + int(16 * s)
        self.pad = int(4 * s)
        self.head = tk.Canvas(self.frame, height=self.rh, bg=COL["head"], highlightthickness=0)
        self.cv = tk.Canvas(self.frame, bg=COL["bg"], highlightthickness=0, takefocus=1, yscrollincrement=self.rh)
        self.sb = ttk.Scrollbar(self.frame, orient="vertical", command=self.cv.yview)
        self.hsb = ttk.Scrollbar(self.frame, orient="horizontal", command=self._xview)
        self.cv.configure(yscrollcommand=self.sb.set, xscrollcommand=self._xset)
        self.head.grid(row=0, column=0, sticky="ew")
        self.cv.grid(row=1, column=0, sticky="nsew")
        self.sb.grid(row=0, column=1, rowspan=2, sticky="ns")
        self.hsb.grid(row=2, column=0, sticky="ew")
        self.hsb.grid_remove()
        self.frame.rowconfigure(1, weight=1)
        self.frame.columnconfigure(0, weight=1)
        self.cols, self.widths = [], []
        self.labels, self.values, self.raw, self.items = [], [], [], []
        self.sel, self.sel_col = None, 0
        self.on_context = None  # on_context(row, event)
        self.flash, self.flash_job = {}, None
        self.stale = False
        self.editor = self.edit_err = None
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Double-1>", self._dbl)
        self.cv.bind("<Button-3>", self._ctx)
        self.cv.bind("<Key>", self._key)
        self.cv.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.cv.bind("<Shift-MouseWheel>", lambda e: self._xview("scroll", -1 if e.delta > 0 else 1, "units"))
        self.cv.bind("<Configure>", lambda e: self._fit_head())

    def _xview(self, *args):
        self.cv.xview(*args)
        self.head.xview_moveto(self.cv.xview()[0])

    def _xset(self, lo, hi):
        self.hsb.set(lo, hi)
        self.head.xview_moveto(float(lo))
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            if self.hsb.winfo_ismapped():
                self.hsb.grid_remove()
        elif not self.hsb.winfo_ismapped():
            self.hsb.grid()

    def _fit_head(self):
        self.head.configure(scrollregion=(0, 0, max(self._width(), self.cv.winfo_width()), self.rh))

    # ----- structure -----
    def set_structure(self, labels, cols):
        """Row labels (one per register) and the data-type columns; clears the values."""
        self.end_edit()
        self.labels, self.cols = list(labels), list(cols)
        self.values = [[""] * len(self.cols) for _ in self.labels]
        self.raw = [None] * len(self.labels)
        m = int(16 * self.s)
        self.widths = [max(self.hfont.measure(c) + m, self.cw * next(
            (v for k, v in MULTI_CHARS.items() if c.startswith(k)), 6) + m) for c in self.cols]
        self.flash.clear()
        if self.sel is not None and self.sel >= len(self.labels):
            self.sel = None
        self.sel_col = min(self.sel_col, max(len(self.cols) - 1, 0))
        self._draw()

    def _width(self):
        return self.pad * 2 + self.aw + sum(self.widths)

    def _colx(self, c):
        return self.pad + self.aw + sum(self.widths[:c])

    def _draw(self):
        self.end_edit()
        cv, hd, s, rh, aw = self.cv, self.head, self.s, self.rh, self.aw
        cv.delete("all")
        hd.delete("all")
        self.items = []
        x0 = self.pad
        hd.create_text(x0 + aw - 6 * s, rh / 2, text="Address", anchor="e", font=self.hfont, fill=COL["headfg"])
        for c, name in enumerate(self.cols):
            x = self._colx(c)
            hd.create_text(x + self.widths[c] - 6 * s, rh / 2, text=name, anchor="e", font=self.hfont,
                           fill=COL["headfg"])
            hd.create_line(x, 3, x, rh - 3, fill="#c3c8cf")
        width = self._width()
        for i, lab in enumerate(self.labels):
            y = i * rh
            band = COL["alt"] if i % 2 else COL["bg"]
            cv.create_rectangle(x0, y, x0 + aw, y + rh, fill=COL["addr"], outline=COL["line"])
            cv.create_text(x0 + aw - 6 * s, y + rh / 2, text=lab, anchor="e", font=self.font, fill=COL["addrfg"])
            rect = cv.create_rectangle(x0 + aw, y, width - self.pad, y + rh, outline=COL["line"],
                                       fill=COL["flash"] if i in self.flash else band)
            txts = []
            for c in range(len(self.cols)):
                x = self._colx(c)
                v = self.values[i][c]
                txts.append(cv.create_text(x + self.widths[c] - 6 * s, y + rh / 2, text=v, anchor="e",
                                           font=self.font, fill=self._fg(v)))
            self.items.append((rect, txts, band))
        for c in range(1, len(self.cols)):
            x = self._colx(c)
            cv.create_line(x, 0, x, len(self.labels) * rh, fill=COL["line"])
        cv.configure(scrollregion=(0, 0, width, len(self.labels) * rh + 1))
        self._fit_head()
        if width <= cv.winfo_width():
            cv.xview_moveto(0)
        self._draw_sel()

    def _fg(self, v):
        return COL["stale"] if self.stale else ("#b4b9c0" if v == mon.MULTI_NONE else COL["fg"])

    # ----- values -----
    def set_values(self, rows, raw=None, flash=True):
        """rows: one list of texts (one per column) per register; raw: the register words (a row flashes when
        its own register changed, like RegGrid)."""
        was_stale, self.stale = self.stale, False
        now = time.monotonic()
        for i, texts in enumerate(rows[:len(self.items)]):
            rect, txts, _band = self.items[i]
            r = raw[i] if raw is not None and i < len(raw) else tuple(texts)
            if flash and self.raw[i] is not None and r != self.raw[i]:
                self.flash[i] = now
                self.cv.itemconfig(rect, fill=COL["flash"])
            self.raw[i] = r
            for c, v in enumerate(texts[:len(txts)]):
                if v != self.values[i][c] or was_stale:
                    self.values[i][c] = v
                    self.cv.itemconfig(txts[c], text=v, fill=self._fg(v))
        if self.flash and not self.flash_job:
            self.flash_job = self.cv.after(300, self._unflash)

    def _unflash(self):
        self.flash_job = None
        now = time.monotonic()
        for i, t in list(self.flash.items()):
            if now - t >= 1.2:
                del self.flash[i]
                if i < len(self.items):
                    self.cv.itemconfig(self.items[i][0], fill=self.items[i][2])
        if self.flash:
            self.flash_job = self.cv.after(300, self._unflash)

    def set_stale(self):
        """Grey out the values (last poll failed / disconnected)."""
        self.stale = True
        for _r, txts, _b in self.items:
            for t in txts:
                self.cv.itemconfig(t, fill=COL["stale"])

    # ----- selection & keyboard -----
    def _hit(self, e):
        """Event -> (row, column index or None for the Address column) or None."""
        x, y = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        r = int(y // self.rh)
        if not 0 <= r < len(self.labels) or x < self.pad:
            return None
        for c in range(len(self.cols)):
            if self._colx(c) <= x < self._colx(c) + self.widths[c]:
                return r, c
        return (r, None) if x < self.pad + self.aw else None

    def _click(self, e):
        self.cv.focus_set()
        h = self._hit(e)
        if h:
            self.select(h[0], h[1])

    def _ctx(self, e):
        self.cv.focus_set()
        h = self._hit(e)
        if h:
            self.select(h[0], h[1])
            if self.on_context:
                self.on_context(h[0], e)

    def _dbl(self, e):
        h = self._hit(e)
        if h and h[1] is not None:
            self.select(*h)
            if self.on_edit:
                self.on_edit(h[0], h[1])

    def select(self, row, col=None):
        self.sel = row
        if col is not None and self.cols:
            self.sel_col = max(0, min(col, len(self.cols) - 1))
        self._draw_sel()
        self._see(row, self.sel_col)

    def sel_format(self):
        return self.cols[self.sel_col] if self.cols and 0 <= self.sel_col < len(self.cols) else None

    def _draw_sel(self):
        self.cv.delete("sel")
        if self.sel is None or self.sel >= len(self.labels):
            return
        y, rh = self.sel * self.rh, self.rh
        self.cv.create_rectangle(self.pad + 1, y + 1, self._width() - self.pad - 1, y + rh - 1,
                                 outline="#9db8e6", width=1, tags="sel")
        if self.cols:
            x = self._colx(self.sel_col)
            self.cv.create_rectangle(x + 1, y + 1, x + self.widths[self.sel_col] - 1, y + rh - 1,
                                     outline=COL["sel"], width=2, tags="sel")

    def _see(self, row, col):
        total = len(self.labels) * self.rh
        if total <= 0:
            return
        y = row * self.rh
        top, h = self.cv.canvasy(0), self.cv.winfo_height()
        if y < top:
            self.cv.yview_moveto(y / total)
        elif y + self.rh > top + h:
            self.cv.yview_moveto((y + self.rh - h) / total)
        width = self._width()
        left, w = self.cv.canvasx(0), self.cv.winfo_width()
        if width > w and self.cols:
            x, cw = self._colx(col), self.widths[col]
            if x < left + self.aw:
                self._xview("moveto", max(x - self.aw - self.pad, 0) / width)
            elif x + cw > left + w:
                self._xview("moveto", (x + cw + self.pad - w) / width)

    def _key(self, e):
        if not self.labels:
            return None
        if e.keysym in ("Up", "Down", "Left", "Right"):
            if self.sel is None:
                self.select(0, 0)
            elif e.keysym in ("Up", "Down"):
                j = self.sel + (1 if e.keysym == "Down" else -1)
                if 0 <= j < len(self.labels):
                    self.select(j)
            else:
                self.select(self.sel, self.sel_col + (1 if e.keysym == "Right" else -1))
            return "break"
        if e.keysym in ("Return", "KP_Enter", "F2") and self.sel is not None and self.cols and self.on_edit:
            self.on_edit(self.sel, self.sel_col)
            return "break"
        return None

    # ----- in-place editor -----
    def start_edit(self, row, col, commit):
        """commit(row, col, text) -> None on success, or an error string (editor stays open, turns red)."""
        self.end_edit()
        self.select(row, col)
        cur = self.values[row][col]
        if cur == mon.MULTI_NONE and mon.multi_width(self.cols[col]) > 1:
            self.cv.bell()  # blank cell of a 2/4-register type: its value starts on another row
            return
        x, y, w = self._colx(col), row * self.rh, self.widths[col]
        e = tk.Entry(self.cv, font=self.font, justify="right", relief="solid", bd=1, highlightthickness=0)
        e.insert(0, "" if cur == mon.MULTI_NONE else cur)
        e.select_range(0, "end")
        self.cv.create_window(x, y, window=e, anchor="nw", width=w, height=self.rh, tags="editor")
        self.editor = e

        def ok(_e=None):
            err = commit(row, col, e.get())
            if err:
                e.configure(bg="#ffd6d6")
                self.cv.delete("edit_err")
                if self.edit_err is None:
                    self.edit_err = tk.Label(self.cv, bg=COL["bad"], fg="white", padx=4, pady=1)
                self.edit_err.configure(text=err + "   (Esc = cancel)")
                self.cv.create_window(x, y + self.rh, window=self.edit_err, anchor="nw", tags=("editor", "edit_err"))
            else:
                self.end_edit()
                self.cv.focus_set()
            return "break"

        def cancel(_e=None):
            self.end_edit()
            self.cv.focus_set()
            return "break"
        e.bind("<Return>", ok)
        e.bind("<KP_Enter>", ok)
        e.bind("<Escape>", cancel)
        e.bind("<FocusOut>", lambda _e: self.end_edit())
        e.bind("<Key>", lambda _e: e.configure(bg="white"), add="+")
        e.mb_commit, e.mb_cancel = ok, cancel  # Enter / Esc without key events (tests)
        e.focus_set()
        return e

    def end_edit(self):
        if self.editor:
            ed, self.editor = self.editor, None
            self.cv.delete("editor")
            ed.destroy()
            if self.edit_err is not None:
                self.edit_err.destroy()
                self.edit_err = None


class MasterApp:
    def __init__(self, root):
        self.root = root
        self.cfg = load_settings()
        self.s = s = root.winfo_fpixels("1i") / 96.0
        setup_style(root)
        root.title(appinfo.APP_TITLE)
        root.minsize(int(660 * s), int(420 * s))
        try:
            root.geometry(self.cfg["geometry"] or f"{int(940 * s)}x{int(600 * s)}")
        except tk.TclError:
            root.geometry(f"{int(940 * s)}x{int(600 * s)}")
        self.q = queue.Queue()
        self.poke = threading.Event()
        self.ai = self.ai_win = None  # AI Assist: in-process MCP server controller / window
        self.help_win = None          # F1 manual
        self.busmap_win = None        # Tools > Bus map (Ctrl+M)
        self.cfg["busmap"] = mon.norm_busmap(self.cfg.get("busmap"))[0]
        self.cfg["busmap_ui"] = mon.norm_busmap_ui(self.cfg.get("busmap_ui"))
        self.cfg["help"] =dict(DEFAULTS["help"], **{k: v for k, v in self.cfg.get("help", {}).items()
                                                     if k in DEFAULTS["help"]})
        import helpdocs
        self.cfg["help"]["lang"] = helpdocs.set_lang(self.cfg["help"].get("lang"))
        self.cfg["ai"] = mon.norm_ai_settings(self.cfg.get("ai"))
        self.conn_url = ""  # tcp://host:port or serial:COMx?... of the current connection (shown to the AI)
        self.gen, self.m, self.state, self.summary = 0, None, "off", ""
        self.link_bad = self.poll_bad = False
        self.p, self.cell_addrs, self.layout_key, self.invalid = None, [], None, False
        self.ok = self.err = 0
        self.last_err = None
        self.log = None
        self.sim = self.write_dlg = self.setup_dlg = None
        # presets: {"obj": Preset, "path": str|None, "data": dict} while the named table is shown
        self.preset, self.pview, self.preset_regs = None, None, {}
        self.write_confirmed = set()  # unverified presets the user already agreed to write to
        self.presets_dlg = self.traffic_win = self.inspector = None
        self.last_poll = None  # (p, vals) of the latest successful register-grid poll
        # monitoring: trend buffer, alarm engine, multi-device poller (all used on the Tk thread only)
        self.trend_buf = mon.TrendBuffer()
        self.trend_cfg = {"source": "", "series": [], "window": "10 min", "ymode": "Shared"}
        self.alarms = mon.AlarmEngine()
        self.alarm_beep = tk.BooleanVar(value=bool(self.cfg["alerts"].get("beep")))
        self.trend_win = self.alerts_win = self.devices_win = self.log_dlg = None
        self.devices, self.dev_status, self.dev_presets = [], {}, {}
        for d in self.cfg["devices"].get("list", []):
            dv = mon.norm_device(d)
            if dv["name"] and "name" not in mon.device_errors(dv, self.devices):
                self.devices.append(dv)
        self.demo = None  # demo.Demo while the simulated demo devices run
        self.demo_names = set()  # their device names: shown and polled, never saved
        self.exporter = None     # Tools > Data export: MQTT / SQLite (export.Exporter) while running
        self.export_pw = ""      # MQTT password, this session only
        self.export_win = None
        self.devpoll = mon.MultiPoller(self._dev_factory, self.q.put, gap_s=self._gap_s())
        self.cycle_stats = mon.CycleStats()  # PLC cycle mode measurements (Devices > Cycle)
        self._norm_dev_cfg()
        self._apply_scan_cfg()
        self.devlogs = None
        self.ws_path = None
        self.trial = None  # "Try other base": {"orig": (pr, path, data), "base": n}
        self.autoload = tk.BooleanVar(value=bool(self.cfg["workspace"].get("autoload")))
        self.multi_var = tk.BooleanVar(value=bool(self.cfg["poll"].get("multi")))
        self.multi_cols = mon.norm_multi_cols(self.cfg["poll"].get("multi_cols"))
        self.multi_align = mon.norm_multi_align(self.cfg["poll"].get("multi_align"))
        self._build_menu()
        self._build_toolbar()
        self._build_alarm_banner()
        # poll tabs: the first tab ("Poll 1") is the classic single poll below; every other tab is a PollTab backed
        # by a device of self.devices with tab=True (polled by the Devices engine). Own tab strip (scrolls when
        # there are many tabs) over a ttk.Notebook whose own tabs are hidden.
        self.cfg["tabs"] = dict(DEFAULTS["tabs"], **{k: v for k, v in self.cfg.get("tabs", {}).items()
                                                     if k in DEFAULTS["tabs"]})
        self.poll_tabs = {}  # device name -> PollTab
        self.cur_tab = None  # shown tab: None = the first tab, else a device name
        st = ttk.Style(root)
        st.layout("Pages.TNotebook.Tab", [])
        st.configure("Pages.TNotebook", borderwidth=0, tabmargins=0, padding=0)
        self.tabstrip = TabStrip(root, self)
        self.pages = ttk.Notebook(root, style="Pages.TNotebook")
        self.main_page = ttk.Frame(self.pages)
        self.pages.add(self.main_page)
        help_key(self.tabstrip.frame, "poll.tabs")
        self._build_poll()
        self._build_status()
        self.tabstrip.frame.pack(fill="x", padx=(8, 8), pady=(4, 0))
        self.pages.pack(fill="both", expand=True)
        self.ai = AiShare(self)
        cf = self.cap_frame = ttk.Frame(self.main_page)
        cf.pack(fill="x", padx=10, pady=(2, 2))
        self.caption = ttk.Label(cf, style="Info.TLabel")
        self.caption.pack(side="left", fill="x", expand=True)
        self.rows_var = tk.StringVar(value=self.cfg["poll"].get("rows") or "Auto")
        if self.rows_var.get() not in ROW_CHOICES:
            self.rows_var.set("Auto")
        self.cb_rows = ttk.Combobox(cf, textvariable=self.rows_var, values=ROW_CHOICES, width=6, state="readonly")
        self.cb_rows.pack(side="right")
        self.l_rows = ttk.Label(cf, text="Rows per column:")
        self.l_rows.pack(side="right", padx=(12, 4))
        Tooltip(self.cb_rows, "Rows in each address / value column of the grid.\n"
                              "Auto: as many columns as fit the window width.\n"
                              "10 / 20 / 30 / 40: fixed rows; more columns scroll sideways.")
        self.grid = RegGrid(self.main_page, s, on_edit=self.edit_cell)
        self.grid.rows_setting = self.rows_var.get()
        self.grid.on_context = lambda i, e: self.context_menu(e)
        self.grid.frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.rows_var.trace_add("write", lambda *_: self._rows_changed())
        # multi-type view: replaces the grid (not in preset mode, not for FC 01/02); the grid keeps being fed
        self.b_mcols = ttk.Button(cf, text="Columns...", command=self.open_multi_cols)
        Tooltip(self.b_mcols, "Choose the data types shown as columns in the multi-type view")
        self.mgrid = MultiGrid(self.main_page, s, on_edit=self.edit_mcell)
        self.mgrid.on_context = lambda i, e: self.context_menu(e)
        self.multi_shown = False
        self.mlayout_key = None
        for key, fn in (("<F3>", self.open_setup), ("<F4>", self.open_inspector), ("<F5>", self.toggle_connect),
                        ("<F6>", self.open_write), ("<F7>", self.open_presets), ("<F8>", self.open_traffic),
                        ("<F9>", self.open_sim), ("<F10>", self.open_trend), ("<F11>", self.open_devices),
                        ("<F12>", self.open_ai), ("<Control-l>", self.toggle_log), ("<Control-s>", self.save_workspace),
                        ("<Control-o>", self.open_workspace), ("<Control-m>", self.open_busmap),
                        ("<Control-t>", self.new_poll_tab), ("<Control-w>", self.close_active_tab),
                        ("<Control-Next>", lambda: self.next_poll_tab(1)),
                        ("<Control-Prior>", lambda: self.next_poll_tab(-1)),
                        ("<Control-Tab>", lambda: self.next_poll_tab(1)),
                        ("<Control-Shift-Tab>", lambda: self.next_poll_tab(-1))):
            root.bind(key, lambda e, fn=fn: (fn(), "break")[1])
        root.bind_all("<F1>", self._f1)  # context help in every window
        self._help_keys()
        self._validate()
        self._refresh_conn_ui()
        self._status("Not connected - press F5 (Connect) or F3 (Setup) to choose TCP or a COM port", "info")
        self.sync_poll_tabs()
        keys = self.poll_tab_keys()
        act = self.cfg["tabs"].get("active", 0)
        if isinstance(act, int) and 0 < act < len(keys):
            self.select_poll_tab(keys[act])
        root.after(40, self._pump)
        root.after(500, self._monitor_tick)
        root.protocol("WM_DELETE_WINDOW", self.close)

    @property
    def m(self):
        """The main window's master. Every replacement (connect, auto-reconnect, disconnect) re-shares it with
        the AI server when that runs."""
        return self.__dict__.get("_m")

    @m.setter
    def m(self, master):
        self.__dict__["_m"] = master
        if self.__dict__.get("ai") is not None:
            self.ai.sync()

    # ---------- layout ----------
    def _build_toolbar(self):
        tb = ttk.Frame(self.root, padding=(8, 6, 8, 6))
        tb.pack(fill="x")
        def b(text, cmd, tip, width=None):
            w = ttk.Button(tb, text=text, command=cmd, width=width or len(text))
            w.pack(side="left", padx=(0, 4))
            Tooltip(w, tip)
            return w
        self.b_conn = b("Connect", self.toggle_connect, "Connect / disconnect   (F5)", 11)
        self.b_setup = b("Setup...", self.open_setup, "Connection setup: Modbus TCP, RTU or ASCII,\nCOM port, baud, parity...   (F3)")
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        self.b_write = b("Write...", self.open_write,
                         "Write to the slave: FC 05, 06, 15, 16, 22, 23   (F6)\nTip: double-click a value in the grid to edit it in place")
        self.b_log = b("Start Log", self.toggle_log, "Log every poll to a CSV file   (Ctrl+L)", 10)
        self.b_log.pack_configure(padx=0)
        self.b_logopt = b("▾", self.log_menu, "Log options: rotation, delimiter, decimal separator,\n"
                                              "time format, encoding (Excel)...", 2)
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        self.b_presets = b("Presets...", self.open_presets,
                           "Device presets: show named, scaled values from a register map   (F7)")
        self.b_traffic = b("Traffic...", self.open_traffic, "Traffic monitor: every request, reply and error   (F8)\n"
                                                            "Value inspector: F4 or right-click a value")
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        self.b_trend = b("Trend", self.open_trend, "Trend graph of selected points over time   (F10)")
        self.b_alerts = b("Alerts", self.open_alerts, "Alarm rules, active alarms and the event log")
        self.badge = tk.Label(tb, text="", bg=COL["bad"], fg="white", font=self.root._mb_fonts[1] if hasattr(
            self.root, "_mb_fonts") else None, padx=4)
        Tooltip(self.badge, "Active alarms - click to open the Alerts window")
        self.badge.bind("<Button-1>", lambda e: self.open_alerts())
        self.b_devices = b("Devices", self.open_devices, "Poll several devices at once (TCP and RS-485 buses)   (F11)")
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        self.b_sim = b("Slave Sim...", self.open_sim, "Built-in slave simulator (TCP, RTU or ASCII)   (F9)")
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=6)
        self.b_ai = b("AI Assist", self.open_ai, "Let an AI analyse this device   (F12):\n"
                                                  "share the live connection with an MCP client (read-only),\n"
                                                  "or copy a diagnostic report into any AI chat")
        self.b_help = b("?", lambda: self.open_help(), "Manual: function codes, errors, data types, every window\n"
                                                         "(F1 - on a field it opens that field's topic)", 3)
        for wdg in tb.pack_slaves():  # compact spacing: the toolbar must fit the default window at 125 % scaling
            if isinstance(wdg, ttk.Separator):
                wdg.pack_configure(padx=4)
            elif wdg.pack_info().get("padx") not in (0, "0"):
                wdg.pack_configure(padx=(0, 3))
        right = ttk.Frame(tb)  # lamp + state first; the settings summary is the first thing to be clipped
        right.pack(side="right", padx=(8, 0))
        self.lamp = Lamp(right, self.s)
        self.lamp.c.pack(side="left", padx=(0, 6))
        self.l_conn = ttk.Label(right, text="Disconnected", style="Conn.TLabel")
        self.l_conn.pack(side="left")
        self.l_sum = ttk.Label(right, style="Info.TLabel")
        self.l_sum.pack(side="left", padx=(8, 0))
        self.toolbar = tb
        tb.bind("<Configure>", lambda e: self._fit_toolbar())
        ttk.Separator(self.root).pack(fill="x")

    def _fit_toolbar(self):
        """Hide the settings summary (it is also in the status bar) when the toolbar is too narrow for it,
        so the connection state next to the lamp is never clipped."""
        tb, lab = self.toolbar, self.l_sum
        shown = lab.winfo_manager() == "pack"
        need = tb.winfo_reqwidth() + (0 if shown else lab.winfo_reqwidth() + int(8 * self.s))
        fits = need <= tb.winfo_width()
        if shown and not fits:
            lab.pack_forget()
        elif not shown and fits:
            lab.pack(side="left", padx=(8, 0))

    def _build_poll(self):
        pc = self.cfg["poll"]
        lf = self.poll_lf = ttk.LabelFrame(self.main_page, text="Poll definition", padding=(10, 4, 10, 0))
        lf.pack(fill="x", padx=8, pady=(6, 2))
        self.pv = {k: tk.StringVar(value=pc[k]) for k in ("unit", "addr", "qty", "scan", "fmt")}
        fc = int(pc["fc"]) if pc["fc"].isdigit() and int(pc["fc"]) in FUNCS else 3
        self.pv["func"] = tk.StringVar(value=FUNCS[fc])
        base = mon.norm_base(pc["base"]) if str(pc.get("base", "")).strip() else mon.norm_base(pc["plc"])
        self.base_var = tk.StringVar(value=BASE_CHOICES[base])
        self.pw, self.hints = {}, {}
        spec = [("unit", "Slave ID", 6, None, "Slave address 1-247 (0 = broadcast, 255 often used for TCP)"),
                ("func", "Function", 30, list(FUNCS.values()), "Read function code"),
                ("addr", "Address", 9, None, "Start address, typed in the chosen Address base.\n"
                                             "The hint below shows the protocol address that is sent."),
                ("qty", "Quantity", 7, None, "Registers (1-125) or coils/inputs (1-2000) to read"),
                ("scan", "Scan Rate (ms)", 9, None, "Time between polls in milliseconds (20 or more)"),
                ("fmt", "Display", 15, FORMATS, "How registers are shown.\nLong/Float use 2 registers, Double uses 4.\n'Inverse' = word-swapped")]
        for c, (k, lab, wd, vals, tip) in enumerate(spec):
            w = (ttk.Combobox(lf, textvariable=self.pv[k], values=vals, width=wd, state="readonly") if vals
                 else ttk.Entry(lf, textvariable=self.pv[k], width=wd))
            self.pw[k] = w
            self.hints[k] = vfield(lf, c, lab, w, tip)
        st = ttk.Style(self.root)
        st.configure("Wide.TCombobox", postoffset=(0, 0, int(200 * self.s), 0))  # wider drop-down list
        cb = ttk.Combobox(lf, textvariable=self.base_var, values=list(BASE_CHOICES.values()), width=27,
                          state="readonly", style="Wide.TCombobox")
        self.pw["base"] = cb
        self.hints["base"] = vfield(lf, 6, "Address base", cb, BASE_TIP)
        # the Display column's hint row is never used (Display has no errors): the multi-type toggle goes there
        self.hints["fmt"].grid_remove()
        self.cb_multi = ttk.Checkbutton(lf, text="Multi-type view", variable=self.multi_var,
                                        command=self._multi_changed)
        self.cb_multi.grid(row=2, column=5, sticky="nw", padx=(0, 12))
        Tooltip(self.cb_multi, MULTI_TIP)
        for var in list(self.pv.values()) + [self.base_var]:
            var.trace_add("write", lambda *_: self._validate())

    def addr_base(self):
        """0, 1 or "plc" - how addresses are typed and shown everywhere in the GUI."""
        return next((k for k, v in BASE_CHOICES.items() if v == self.base_var.get()), 0)

    def set_addr_base(self, base):
        self.base_var.set(BASE_CHOICES[mon.norm_base(base)])

    def _rows_changed(self):
        self.grid.rows_setting = self.rows_var.get()
        self.grid._layout(force=True)
        self.cfg["poll"]["rows"] = self.rows_var.get()

    def _build_status(self):
        sb = ttk.Frame(self.root, padding=(8, 2, 8, 4))
        sb.pack(fill="x", side="bottom")
        ttk.Separator(self.root).pack(fill="x", side="bottom")
        sb.columnconfigure(0, weight=1)
        mf = ttk.Frame(sb)
        mf.grid(row=0, column=0, sticky="w")
        self.l_msg = ttk.Label(mf, style="SInfo.TLabel")
        self.l_msg.pack(side="left")
        st = ttk.Style(self.root)
        self._link_font = lf = tkfont.nametofont("TkDefaultFont").copy()
        lf.configure(underline=True)
        st.configure("Link.TLabel", foreground=COL["sel"], font=lf)
        self.l_what = ttk.Label(mf, text="What does this mean?", style="Link.TLabel", cursor="hand2")
        self.l_what.bind("<Button-1>", lambda e: self._error_help())
        Tooltip(self.l_what, "Open the manual page for this kind of error   (F1 in the manual: search)")
        self._err_key = None
        self.l_cnt = ttk.Label(sb)
        self.l_cnt.grid(row=0, column=1, sticky="e")
        self.l_last = ttk.Label(sb, style="Info.TLabel")
        self.l_last.grid(row=1, column=0, sticky="w")
        self.l_log = ttk.Label(sb, style="SBad.TLabel")
        self.l_log.grid(row=1, column=1, sticky="e")
        # amber "AI: sharing ..." indicator, shown while the AI server runs (click = AI Assist window)
        self.l_ai = tk.Label(sb, text="", bg=AI_AMBER, fg="#3b2500", font=self.root._mb_fonts[1],
                             padx=int(8 * self.s), pady=int(2 * self.s), cursor="hand2")
        self.l_ai.grid(row=0, column=2, rowspan=2, sticky="nse", padx=(10, 0))
        self.l_ai.grid_remove()
        self.l_ai.bind("<Button-1>", lambda e: self.open_ai())
        Tooltip(self.l_ai, "An AI client may read this device through the built-in MCP server.\n"
                           "Click to open AI Assist (stop sharing there).")
        self._counters()

    def _status(self, text, kind="info", main=True):
        """main: the message is about the first tab - named so while another poll tab is shown."""
        shown = text if not main or self.__dict__.get("cur_tab") is None else f"{self.main_tab_name()}:  {text}"
        self.l_msg.configure(text=shown, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel",
                                                "warn": "SWarn.TLabel"}.get(kind, "SInfo.TLabel"))
        key = mon.error_help_key(text) if kind in ("bad", "warn") else None
        if key != self._err_key:
            self._err_key = key
            if key:
                self.l_what.pack(side="left", padx=(12, 0))
            else:
                self.l_what.pack_forget()

    def _counters(self):
        who = "" if self.__dict__.get("cur_tab") is None else f"{self.main_tab_name()}:  "
        self.l_cnt.configure(text=f"{who}Polls OK: {self.ok}     Errors: {self.err}")
        if self.last_err:
            t, msg = self.last_err
            self.l_last.configure(text=f"Last error {time.strftime('%H:%M:%S', time.localtime(t))}: {msg}")
        else:
            self.l_last.configure(text="")

    def _refresh_conn_ui(self):
        st = self.state
        self.b_conn.configure(text="Disconnect" if st != "off" else "Connect")
        color = {"off": COL["bad"], "connecting": COL["warn"], "on": COL["ok"]}[st]
        if st == "on" and (self.link_bad or self.poll_bad):
            color = COL["warn"]
        self.lamp.set(color)
        text = {"off": "Disconnected", "connecting": "Connecting...", "on": "Connected"}[st]
        if st == "on" and self.link_bad:
            text = "Link lost - retrying"
        elif st == "on" and self.poll_bad:
            text = "Connected - no valid reply"
        self.l_conn.configure(text=text)
        if st == "off":
            try:
                summary = master_factory(self.cfg["conn"])[1]
            except ValueError:
                summary = "(check Setup)"
        else:
            summary = self.summary
        self.l_sum.configure(text=summary)
        self.root.after_idle(self._fit_toolbar)
        self.tabstrip.set_state(None, self.main_tab_state())

    # ---------- poll definition ----------
    def _validate(self):
        if self.preset:
            return self._validate_preset()
        self.poll_lf.configure(text="Poll definition")
        for k, st in (("func", "readonly"), ("addr", "normal"), ("qty", "normal")):
            self.pw[k].configure(state=st)
        pv = self.pv
        fc = int(pv["func"].get()[:2])
        plc = self.addr_base()
        bits = fc in (1, 2)
        fmt = "Unsigned" if bits else pv["fmt"].get()
        errs, vals, info = {}, {}, {}

        def chk(k, fn):
            try:
                vals[k] = fn(pv[k].get())
            except ValueError as e:
                errs[k] = str(e)
        chk("unit", lambda t: int_in(t, 0, 255))
        chk("addr", lambda t: parse_addr(t, fc, plc))
        chk("qty", lambda t: int_in(t, 1, 2000 if bits else 125))
        chk("scan", lambda t: int_in(t, 20, 3600000))
        if "addr" in vals and "qty" in vals and vals["addr"] + vals["qty"] > 65536:
            errs["qty"] = f"max {65536 - vals['addr']} here"
        if not bits and "qty" in vals and "qty" not in errs:
            w = fmt_width(fmt)
            if vals["qty"] < w:
                errs["qty"] = f"{fmt} needs {w}+"
            elif vals["qty"] % w:
                info["qty"] = f"not a multiple of {w}"
        if "addr" in vals:
            info["addr"] = f"= protocol {vals['addr']}"
        self.pw["fmt"].configure(state="disabled" if bits else "readonly")
        self.cb_multi.configure(state="disabled" if bits else "normal")
        self._apply_view()
        set_hints(self.hints, errs, info)
        if self.multi_shown:
            self.caption.configure(text=(
                "Multi-type view: each register in several data types ('-' = not enough registers left).  "
                + ("Read-only data (FC 04)." if fc == 4 else
                   "Double-click a cell (or select + Enter) to write it in that column's type.")
                + "  Right-click / F4: inspect."))
        else:
            self.caption.configure(text=(
                "Read-only data (FC 02 / 04).  Changed values flash yellow."
                if fc in (2, 4) else
                "Double-click a value (or select it and press Enter) to write it.  Changed values flash yellow."
                "  Right-click / F4: inspect."))
        if errs:
            self.p = None
            self.invalid = True
            self._status("Poll definition has errors - see the red hints", "bad")
            return
        if self.invalid:
            self.invalid = False
            self._status("Poll definition OK", "info")
        p = dict(unit=vals["unit"], fc=fc, addr=vals["addr"], qty=vals["qty"], scan=vals["scan"],
                 fmt=fmt, plc=plc)
        key = (fc, p["addr"], p["qty"], fmt, plc)
        if key != self.layout_key:
            self.layout_key = key
            if bits:
                offs, chars = range(p["qty"]), 1
            else:
                w = fmt_width(fmt)
                offs, chars = range(0, p["qty"] - w + 1, w), fmt_chars(fmt)
            self.cell_addrs = [p["addr"] + o for o in offs]
            self.grid.set_cells([addr_label(fc, a, plc) for a in self.cell_addrs], chars)
        self.p = p
        self._multi_layout()
        self.cfg["poll"] = {"unit": pv["unit"].get(), "fc": str(fc), "addr": pv["addr"].get(),
                            "qty": pv["qty"].get(), "scan": pv["scan"].get(), "fmt": pv["fmt"].get(),
                            "plc": plc == "plc", "base": str(plc), "rows": self.rows_var.get(),
                            "multi": bool(self.multi_var.get()), "multi_cols": list(self.multi_cols),
                            "multi_align": str(self.multi_align)}
        self.poke.set()  # poll now with the new definition

    # ---------- multi-type view ----------
    def _apply_view(self):
        """Show the multi-type table or the normal grid (register grid mode only; preset mode hides both)."""
        if self.preset:
            return
        bits = int(self.pv["func"].get()[:2]) in (1, 2)
        want = bool(self.multi_var.get()) and not bits
        if want == self.multi_shown:
            return
        self.multi_shown = want
        self.mlayout_key = None
        old, new = (self.grid, self.mgrid) if want else (self.mgrid, self.grid)
        old.end_edit()
        old.frame.pack_forget()
        new.frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self._show_rows_selector(not want)
        if want:
            self.b_mcols.pack(side="right")
        else:
            self.b_mcols.pack_forget()

    def _multi_layout(self):
        """(Re)build the multi-type table for the current poll definition and fill it from the last poll."""
        p = self.p
        if not self.multi_shown or not p or p.get("preset"):
            return
        key = (p["fc"], p["addr"], p["qty"], p["plc"], tuple(self.multi_cols), self.multi_align)
        if key == self.mlayout_key:
            return
        self.mlayout_key = key
        self.mgrid.set_structure([addr_label(p["fc"], p["addr"] + i, p["plc"]) for i in range(p["qty"])],
                                 self.multi_cols)
        lp = self.last_poll
        if lp and lp[0] == p and self.state == "on":
            self._multi_fill(p, lp[1], flash=False)
            if self.poll_bad or self.link_bad:
                self.mgrid.set_stale()

    def _multi_fill(self, p, vals, flash=True):
        rows = mon.multi_rows(p["fc"], p["addr"], vals, self.mgrid.cols, p["plc"], align=self.multi_align)
        self.mgrid.set_values([r[1:] for r in rows], raw=list(vals), flash=flash)

    def _multi_changed(self):
        on = bool(self.multi_var.get())
        self.cfg["poll"]["multi"] = on
        if on and not self.preset and int(self.pv["func"].get()[:2]) in (1, 2):
            self._status("Multi-type view is for registers (FC 03 / 04) - coils and inputs are single bits", "warn")
        elif on and self.preset:
            self._status("Multi-type view applies to the register grid - leave the preset to see it", "warn")
        self._validate()

    def open_multi_cols(self):
        dlg = getattr(self, "mcols_dlg", None)
        if dlg and dlg.alive():
            dlg.w.lift()
            return dlg
        self.mcols_dlg = MultiColumnsDialog(self, self.multi_cols, self.set_multi_cols, self.multi_align,
                                            self.set_multi_align)
        return self.mcols_dlg

    def set_multi_align(self, n):
        """Where 2- and 4-register values start counting from the first polled register (0..3)."""
        n = mon.norm_multi_align(n)
        if n == self.multi_align:
            return
        self.multi_align = n
        self.cfg["poll"]["multi_align"] = str(n)
        self.mlayout_key = None
        self._validate()
        for tab in self.poll_tabs.values():
            tab.refresh(full=True)

    def set_multi_cols(self, cols):
        self.multi_cols = mon.norm_multi_cols(cols)
        self.cfg["poll"]["multi_cols"] = list(self.multi_cols)
        self.mlayout_key = None
        self._validate()
        for tab in self.poll_tabs.values():  # the columns are shared by every poll tab
            tab.refresh(full=True)

    def edit_mcell(self, row, col):
        p = self.p
        if not p or p.get("preset") or not self.multi_shown or row >= len(self.mgrid.labels):
            return
        if p["fc"] != 3:
            self._status(f"FC {p['fc']:02d} data is read-only - switch Function to 03 to edit", "warn")
            return
        if self.state != "on":
            self._status("Not connected - press F5 to connect, then edit", "warn")
            return
        self.mgrid.start_edit(row, col, lambda r, c, text: self._commit_mcell(p, r, c, text))

    def _commit_mcell(self, p, row, col, text):
        """Write the cell text in its column's data type, starting at the row's register."""
        fmt = self.mgrid.cols[col]
        addr = p["addr"] + row
        unit, lab = p["unit"], addr_label(p["fc"], addr, p["plc"])
        try:
            words = mon.multi_encode(text, fmt)
        except ValueError:
            err = ("enter 1 or 2 ASCII characters" if fmt == "ASCII"
                   else f"'{text.strip()}' is not a valid {fmt} value")
            self._status(f"Register {lab}: {err}", "bad")
            return err
        if addr + len(words) > 65536:
            err = f"{fmt} needs {len(words)} registers - past the last address"
            self._status(f"Register {lab}: {err}", "bad")
            return err
        shown = text if fmt == "ASCII" else text.strip()
        if len(words) == 1:
            fn, desc = (lambda m: m.write_register(unit, addr, words[0])), f"FC06 register {lab} = {shown} ({fmt})"
        else:
            fn, desc = (lambda m: m.write_registers(unit, addr, words)), f"FC16 registers {lab} = {shown} ({fmt})"
        self._status(f"Writing {desc}...", "info")
        self.run_bg(fn, lambda r, err: self.write_done(desc, err))
        return None

    def _validate_preset(self):
        """Preset mode: only Slave ID (overrides the preset's default unit) and Scan Rate apply."""
        pv, errs, vals = self.pv, {}, {}
        for k, fn in (("unit", lambda t: int_in(t, 0, 255)), ("scan", lambda t: int_in(t, 20, 3600000))):
            try:
                vals[k] = fn(pv[k].get())
            except ValueError as e:
                errs[k] = str(e)
        for k in ("func", "addr", "qty", "fmt"):
            self.pw[k].configure(state="disabled")
        self.poll_lf.configure(text="Poll definition  -  preset mode: Slave ID and Scan Rate apply; "
                                    "the register range comes from the preset")
        set_hints(self.hints, errs)
        self.caption.configure(text="Double-click a value with access 'rw' (or select it and press Enter) to write it; "
                                    "'▶ Send...' rows are write-only commands.  Changed values flash yellow.  "
                                    "Right-click: inspect.")
        pr = self.preset["obj"]
        self.pview.set_unit_note(vals.get("unit"), pr.connection.get("unit"))
        if errs:
            self.p = None
            self.invalid = True
            self._status("Poll definition has errors - see the red hints", "bad")
            return
        if self.invalid:
            self.invalid = False
            self._status("Poll definition OK", "info")
        g = self.pview.group.get()
        plc = self.addr_base()
        p = dict(unit=vals["unit"], fc=0, scan=vals["scan"], plc=plc, preset=pr,
                 group=None if g in ("", "All") else g)
        key = ("preset", id(pr), p["group"], plc)
        if key != self.layout_key:
            self.layout_key = key
            self.pview.set_points(pr, p["group"], plc)
        self.p = p
        self.cfg["poll"]["unit"], self.cfg["poll"]["scan"] = pv["unit"].get(), pv["scan"].get()
        self.cfg["poll"].update(base=str(plc), plc=plc == "plc")
        self.poke.set()

    # ---------- presets ----------
    def open_presets(self):
        if self.presets_dlg and self.presets_dlg.alive():
            self.presets_dlg.w.deiconify()
            self.presets_dlg.w.lift()
            return
        self.presets_dlg = PresetsDialog(self)

    def use_preset(self, pr, path, data, trial=False, load_alarms=True):
        if not trial:
            self.trial = None
        self.preset = {"obj": pr, "path": path, "data": data}
        if self.pview is None:
            self.pview = PresetView(self.main_page, self)
            help_key(self.pview.frame, "preset")
        for g in (self.grid, self.mgrid):
            g.end_edit()
            g.frame.pack_forget()
        self.multi_shown = None  # neither view: leave_preset picks one again
        self.b_mcols.pack_forget()
        self._show_rows_selector(False)
        self.pview.frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.layout_key = None
        self.preset_regs = {}
        self.pview.load(pr, path, data)  # resets the group filter (which re-validates)
        self.pview.show_trial(self.trial)
        self.root.title(f"{appinfo.APP_TITLE} - preset: {pr.name}")
        self._validate()
        note = ""
        if load_alarms and not trial:
            self.alarms.remove_origin("", "preset")
            added, errors = self.alarms.load_dicts(mon.preset_alarms(data), device="", origin="preset",
                                                   points=[p.name for p in pr.points])
            if added or errors:
                note = f"  -  {len(added)} alarm rule(s) from the preset" + (
                    f", {len(errors)} invalid: {errors[0]}" if errors else "")
            self._alarms_changed()
        self.trend_sources_changed()
        self._status(f"Preset '{pr.name}' active: {len(pr.points)} points" + note
                     + ("" if self.state == "on" else "  -  press F5 to connect"), "warn" if "invalid" in note else "info")

    def leave_preset(self):
        if not self.preset:
            return
        self.preset = None
        self.trial = None
        self.alarms.remove_origin("", "preset")
        self._alarms_changed()
        self.pview.end_edit()
        self.pview.frame.pack_forget()
        self.multi_shown = None
        self._apply_view()  # the normal grid or the multi-type table
        self.layout_key = None
        self.root.title(appinfo.APP_TITLE)
        self._validate()
        self.trend_sources_changed()
        self._status("Register grid", "info")

    def _show_rows_selector(self, on):
        for wd in (self.cb_rows, self.l_rows):  # same order as the first pack: label left of the combobox
            if on and not wd.winfo_ismapped():
                wd.pack(side="right", padx=(12, 4) if wd is self.l_rows else 0)
            elif not on:
                wd.pack_forget()

    # ----- "Try other base": read the preset with address_base flipped, without saving -----
    def try_other_base(self):
        if not self.preset:
            return
        if self.trial:
            pr, path, data = self.trial["orig"]
        else:
            pr, path, data = self.preset["obj"], self.preset["path"], self.preset["data"]
        base = int(data.get("address_base", 0) or 0)
        new = 1 - base
        try:
            trial_pr = Preset(mon.with_base(data, new))
        except (PresetError, ValueError) as e:
            self._status(f"Cannot read with address_base {new}: {e}", "bad")
            return
        self.trial = {"orig": (pr, path, data), "base": new}
        self.use_preset(trial_pr, path, mon.with_base(data, new), trial=True)
        self._status(f"TRYING address_base {new} (not saved): every point is read one register "
                     f"{'lower' if new == 1 else 'higher'} than in the file. Compare with the device, then "
                     "'Keep this base' or 'Back to file'.", "warn")

    def revert_trial(self):
        if not self.trial:
            return
        pr, path, data = self.trial["orig"]
        self.trial = None
        self.use_preset(pr, path, data, load_alarms=False)
        self._status("Back to the preset as saved in the file", "info")

    def keep_trial(self):
        """Write the tried base into a copy (the editor suggests a '_custom' name; library needs confirmation)."""
        if not self.trial:
            return None
        _pr, path, data = self.trial["orig"]
        new = mon.with_base(data, self.trial["base"])
        ed = PresetEditor(self, new, path, on_saved=(self.presets_dlg._saved if self.presets_dlg and
                                                     self.presets_dlg.alive() else None),
                          origin=f"address_base changed to {self.trial['base']}")
        ed.dirty = True
        ed._title()
        ed.l_msg.configure(text=f"address_base is now {self.trial['base']} - Save as... to keep it in a copy",
                           style="SWarn.TLabel")
        self.after_editor = ed
        self.root.after(50, ed.save_as)
        return ed

    def preset_key(self):
        pr = self.preset["obj"]
        return (self.preset["path"] or "(unsaved)", pr.name)

    def apply_preset_connection(self, pc):
        """Copy a preset's connection block into the Setup settings (+ Slave ID). Returns a summary."""
        conn, unit, desc = preset_conn_settings(self.cfg["conn"], pc)
        if unit is not None:
            self.pv["unit"].set(str(unit))
        self.apply_conn(conn, connect=self.state != "off")  # reconnect only if already connected
        self._status(f"Connection settings taken from the preset: {desc}", "ok")
        return desc

    def edit_point(self, name):
        pr = self.preset["obj"]
        pt = pr.by_name.get(name)
        if pt is None:
            return
        if pt.access not in ("rw", "w"):
            self.pview.inline_error(name, f"{name} is read-only (access 'r') - only 'rw' / 'w' points can be written")
            self._status(f"{name} is read-only in this preset", "warn")
            return
        if self.state != "on":
            self._status("Not connected - press F5 to connect, then edit", "warn")
            return
        if pt.type == "bool":
            if pt.access == "w":  # write-only command: no current value, choose what to send
                choices = [pt.enum.get("1", "ON"), pt.enum.get("0", "OFF")]
                self.pview.start_edit(name, lambda text: self._commit_point(name, text), choices)
                return
            cur = self.pview.raw.get(name)
            if cur is None:
                self.pview.inline_error(name, "value not read yet - wait for a successful poll")
                return
            new = not cur
            label = pt.enum.get(str(int(new)), "ON" if new else "OFF")
            self._write_point(name, new, f"{name} = {label}")
            return
        choices = list(pt.enum.values()) if pt.enum else None
        self.pview.start_edit(name, lambda text: self._commit_point(name, text), choices)

    def _commit_point(self, name, text):
        pt = self.preset["obj"].by_name[name]
        try:
            value, desc = mon.parse_point_input(pt, text)
            pt.encode(value)  # min / max / type range / scale check, before anything is sent
        except (ValueError, PresetError) as e:
            self._status(f"Not written: {e}", "bad")
            return str(e)
        self._write_point(name, value, desc)
        return None

    def _write_point(self, name, value, desc):
        pr, key = self.preset["obj"], self.preset_key()
        if not pr.verified and key not in self.write_confirmed:
            def ok():
                self.write_confirmed.add(key)
                self._do_write(pr, name, value, desc)
            ConfirmDialog(self, "Write with an unverified preset?",
                          f"This preset is not verified against a real device:\n'{pr.name}'\n\n"
                          "Its addresses, data types or scaling may be wrong, so the value could land in the wrong "
                          "register or with the wrong scale.\n\n"
                          f"Write  {desc}  to slave {self.p['unit'] if self.p else '?'}?\n\n"
                          "(Asked once per preset in this session.)",
                          "Write anyway", ok, lambda: self._status("Write cancelled", "info"))
            return
        self._do_write(pr, name, value, desc)

    def _do_write(self, pr, name, value, desc):
        unit = self.p["unit"] if self.p else None
        self._status(f"Writing {desc}...", "info")
        self.run_bg(lambda m: pr.write(m, name, value, unit), lambda r, err: self._point_written(name, desc, err))

    def _point_written(self, name, desc, err):
        self.write_done(desc, err)
        if err and self.pview and self.preset:
            self.pview.inline_error(name, f"write failed: {err}")

    # ---------- traffic / inspector ----------
    def open_traffic(self):
        if self.traffic_win and self.traffic_win.alive():
            self.traffic_win.w.deiconify()
            self.traffic_win.w.lift()
            return self.traffic_win
        self.traffic_win = TrafficWindow(self)
        return self.traffic_win

    def open_inspector(self):
        if self.inspector and self.inspector.alive():
            self.inspector.w.deiconify()
            self.inspector.w.lift()
            self.inspector.refresh(force=True)
            return
        self.inspector = Inspector(self)

    def context_menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Inspect value...", accelerator="F4", command=self.open_inspector)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def inspect_source(self):
        """What the inspector should show: dict(key, title, fc, addr, words, default_n) or (None, reason)."""
        tab = self.active_poll_tab()
        if tab is not None:  # an extra poll tab is shown: inspect its selected cell
            return tab.inspect_source()
        if self.preset and self.pview:
            name = self.pview.selected_name()
            if name is None:
                return None, "Select a point in the preset table."
            pt = self.preset["obj"].by_name.get(name)
            if pt is None:
                return None, "Select a point in the preset table."
            if pt.fc not in (3, 4):
                return None, f"{name} is a coil / discrete input - only registers can be inspected."
            words = []
            for k in range(4):
                w = self.preset_regs.get((pt.fc, pt.address + k))
                if w is None:
                    break
                words.append(w)
            title = f"{name}  -  {('holding' if pt.fc == 3 else 'input')} register {pt.address} (FC{pt.fc:02d})"
            return dict(key=("preset", name, pt.fc, pt.address), title=title, fc=pt.fc, addr=pt.address,
                        words=words, default_n=pt.words, point=pt), None
        p = self.p
        if not p or p.get("preset"):
            return None, "Fix the poll definition first."
        if self.multi_shown:  # multi-type view: the selected row's register, as many words as its column uses
            i = self.mgrid.sel
            if i is None or i >= len(self.mgrid.labels):
                return None, "Select a value in the multi-type table (click it)."
            addr, default_n = p["addr"] + i, mon.multi_width(self.mgrid.sel_format() or "Unsigned")
        else:
            i = self.grid.sel
            if i is None or i >= len(self.cell_addrs):
                return None, "Select a value in the register grid (click it)."
            addr, default_n = None, fmt_width(p["fmt"])
        if p["fc"] in (1, 2):
            return None, "Coils / discrete inputs are single bits - choose FC 03 or 04 to inspect registers."
        if addr is None:
            addr = self.cell_addrs[i]
        words = []
        lp = self.last_poll
        if lp and lp[0] == p:
            off = addr - p["addr"]
            words = list(lp[1][off:off + 4])
        lab = addr_label(p["fc"], addr, p["plc"])
        title = f"{('Holding' if p['fc'] == 3 else 'Input')} register {lab} (FC{p['fc']:02d}, address {addr})"
        key = ("grid", p["fc"], addr) + ((self.mgrid.sel_format(),) if self.multi_shown else ())
        return dict(key=key, title=title, fc=p["fc"], addr=addr, words=words,
                    default_n=default_n, point=None), None

    # ---------- connection ----------
    def toggle_connect(self):
        if self.state == "off":
            self.connect()
        else:
            self.disconnect()

    def connect(self):
        if self.state != "off":
            return
        try:
            factory, self.summary = master_factory(self.cfg["conn"])
            self.conn_url = mon.conn_url(self.cfg["conn"])
        except ValueError as e:
            self._status(f"Connection settings: {e}", "bad")
            self.open_setup()
            return
        self.gen += 1
        self.state, self.link_bad, self.poll_bad = "connecting", False, False
        self.ok = self.err = 0
        self.last_err = None
        self._counters()
        self._status(f"Connecting to {self.summary}...", "info")
        self._refresh_conn_ui()
        threading.Thread(target=self._worker, args=(self.gen, factory), daemon=True).start()

    def disconnect(self, quiet=False):
        self.gen += 1  # retires the running poll thread
        self.poke.set()
        if self.m:
            try:
                self.m.close()
            except Exception:
                pass
            self.m = None
        was = self.state
        self.state = "off"
        self.alarms.suspend("")  # stopped on purpose: no "device silent" alarm for the main window
        if self.trend_cfg["source"] == "":
            self.trend_buf.add_gap(time.time())
        self.grid.set_stale()
        self.mgrid.set_stale()
        if self.pview:
            self.pview.set_stale()
        self._refresh_conn_ui()
        if was != "off" and not quiet:
            self._status("Disconnected", "info")

    def _wait(self, secs):
        self.poke.wait(secs)
        self.poke.clear()

    def _worker(self, gen, factory):
        """Poll thread: owns connect/reconnect and polling. Never touches Tk."""
        m, first = None, True
        try:
            while self.gen == gen:
                if m is None:
                    try:
                        m = factory()
                    except Exception as e:
                        self.q.put(("connfail", gen, str(e) or repr(e), first))
                        if first:
                            return
                        self._wait(2.0)
                        continue
                    if self.gen != gen:
                        break
                    self.q.put(("master", gen, m, first))
                    first = False
                p = self.p
                if p is None:
                    self._wait(0.3)
                    continue
                t0 = time.time()
                try:
                    if p.get("preset"):
                        rec = _Recorder(m)  # keeps the raw blocks for the value inspector
                        res = p["preset"].read(rec, p["unit"], p["group"])
                        self.q.put(("ppoll", gen, p, res, t0, rec.blocks))
                        if any(str(d.get("error", "")).startswith(("connection", "MBAP", "Modbus Connection"))
                               for d in res.values()):
                            try:
                                m.close()
                            except Exception:
                                pass
                            m = None  # link lost: reconnect on the next round
                    else:
                        vals = poll_once(m, p["unit"], p["fc"], p["addr"], p["qty"])
                        self.q.put(("poll", gen, p, vals, t0))
                except Exception as e:
                    msg = str(e) or repr(e)
                    self.q.put(("pollerr", gen, p, msg, t0))
                    if isinstance(e, OSError) or msg.startswith(("connection", "MBAP")):
                        try:
                            m.close()
                        except Exception:
                            pass
                        m = None  # link lost: reconnect on the next round
                self._wait(p["scan"] / 1000)
        finally:
            if m is not None:
                try:
                    m.close()
                except Exception:
                    pass

    def run_bg(self, fn, done):
        """Run fn(master) on a worker thread; done(result, error_text) is called on the Tk thread."""
        m = self.m
        if m is None or self.state != "on":
            done(None, "Not connected - press F5 to connect")
            return

        def work():
            try:
                r = fn(m)
                self.q.put(("call", None, done, r, None))
            except Exception as e:
                self.q.put(("call", None, done, None, str(e) or repr(e)))
        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        try:
            while True:
                try:
                    msg = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(msg)
                except Exception:
                    import traceback
                    traceback.print_exc()
        finally:
            try:
                self.root.after(40, self._pump)
            except tk.TclError:
                pass

    def _handle(self, msg):
        kind, gen = msg[0], msg[1]
        if kind == "call":
            msg[2](msg[3], msg[4])
            return
        if kind == "dev":
            self._on_dev_result(msg)
            return
        if kind == "cycle":
            self._on_cycle(msg)
            return
        if gen != self.gen:
            return  # stale result from a retired poll thread
        if kind == "master":
            self.m, self.state, self.link_bad = msg[2], "on", False
            self._status(f"Connected to {self.summary}" if msg[3] else f"Reconnected to {self.summary}", "ok")
            self._refresh_conn_ui()
        elif kind == "connfail":
            err, first = msg[2], msg[3]
            self.err += 1
            self.last_err = (time.time(), err)
            self._counters()
            if first:
                self.gen += 1
                self.state, self.m = "off", None
                self._status(f"Connection failed: {err}   (F3 to change settings)", "bad")
            else:
                self.link_bad = True
                self._status(f"Link lost, retrying: {err}", "warn")
                self.feed("", time.time(), {}, ok=False)
                self._log_error(f"link lost: {err}", time.time())
            self._refresh_conn_ui()
        elif kind == "poll":
            self._on_poll(*msg[2:])
        elif kind == "ppoll":
            self._on_ppoll(*msg[2:])
        elif kind == "pollerr":
            self._on_pollerr(*msg[2:])

    def _on_poll(self, p, vals, t):
        self.ok += 1
        self._counters()
        if self.link_bad or self.poll_bad:
            self.link_bad = self.poll_bad = False
            self._refresh_conn_ui()
        if p != self.p:
            return  # definition changed while this poll was in flight
        self.last_poll = (p, list(vals))
        texts = [txt for _i, txt in render(vals, p["fc"], p["fmt"])]
        self.grid.set_values(texts)
        if self.multi_shown:
            self._multi_fill(p, vals)
        what = {1: "coils", 2: "inputs"}.get(p["fc"], "registers")
        self._status(f"OK  {time.strftime('%H:%M:%S', time.localtime(t))}   {len(vals)} {what} read", "ok")
        values, tmap = mon.register_values(p["fc"], p["addr"], p["fmt"], vals)
        self.feed("", t, values, ok=True)
        keys = [str(a) for a in self.cell_addrs]
        self._log_row([addr_label(p["fc"], a, p["plc"]) for a in self.cell_addrs],
                      mon.log_values(keys, values, tmap), t)

    def _on_pollerr(self, p, msg, t):
        self.err += 1
        self.last_err = (t, msg)
        self._counters()
        self.grid.set_stale()
        self.mgrid.set_stale()
        if self.pview:
            self.pview.set_stale()
        if not self.poll_bad:
            self.poll_bad = True
            self._refresh_conn_ui()
        self._status(f"Error: {msg}", "bad")
        self.feed("", t, {}, ok=False)
        self._log_error(msg, t)

    def _on_ppoll(self, p, res, t, blocks):
        errs = [d["error"] for d in res.values() if "error" in d]
        all_bad = bool(res) and len(errs) == len(res)
        if all_bad:
            self.err += 1
            self.last_err = (t, errs[0])
            if not self.poll_bad:
                self.poll_bad = True
                self._refresh_conn_ui()
        else:
            self.ok += 1
            if self.link_bad or self.poll_bad:
                self.link_bad = self.poll_bad = False
                self._refresh_conn_ui()
        self._counters()
        if p != self.p or not self.preset:
            return
        regs = {}
        for fc, start, data in blocks:
            if fc in (3, 4):
                for k, w in enumerate(data):
                    regs[(fc, start + k)] = w
        self.preset_regs = regs
        self.pview.set_results(res)
        values = {n: (None if "error" in d else d["value"]) for n, d in res.items()}
        self.feed("", t, values, ok=not all_bad)
        ts = time.strftime('%H:%M:%S', time.localtime(t))
        if all_bad:
            self._status(f"Error: {errs[0]}", "bad")
            self._log_error(errs[0], t)
            return
        elif errs:
            self._status(f"{ts}   {len(res) - len(errs)} of {len(res)} points read, {len(errs)} failed - "
                         "see the red rows", "warn")
        else:
            self._status(f"OK  {ts}   {len(res)} points read", "ok")
        pts = [pt for pt in self.pview.points if pt.access != "w"]
        texts = {n: ("ERROR: " + d["error"] if "error" in d else str(d["text"])) for n, d in res.items()}
        self._log_row([f"{pt.name} [{pt.unit}]" if pt.unit else pt.name for pt in pts],
                      mon.log_values([pt.name for pt in pts], values, texts), t)

    # ---------- monitoring: trend / alarms feed ----------
    def feed(self, source, t, values, ok=True):
        """Every poll result of the main window ('') or a device ends here (Tk thread): alarms + trend."""
        events = self.alarms.update(source, values, ok, now=t)
        if source == self.trend_cfg["source"] and self.trend_buf.series:
            if ok:
                self.trend_buf.add_sample(t, values)
            else:
                self.trend_buf.add_gap(t)
        if events:
            self._alarm_events(events)

    def _monitor_tick(self):
        try:
            ev = self.alarms.tick(time.time())
            if ev:
                self._alarm_events(ev)
            self._devices_tick()
        except Exception:
            import traceback
            traceback.print_exc()
        try:
            self.root.after(500, self._monitor_tick)
        except tk.TclError:
            pass

    # ---------- writing ----------
    def edit_cell(self, i):
        p = self.p
        if not p or p.get("preset") or i >= len(self.cell_addrs):
            return
        if p["fc"] not in (1, 3):
            self._status(f"FC {p['fc']:02d} data is read-only - switch Function to 01 or 03 to edit", "warn")
            return
        if self.state != "on":
            self._status("Not connected - press F5 to connect, then edit", "warn")
            return
        self.grid.start_edit(i, lambda i, text: self._commit_cell(p, i, text))

    def _commit_cell(self, p, i, text):
        addr = self.cell_addrs[i]
        unit, lab = p["unit"], addr_label(p["fc"], addr, p["plc"])
        if p["fc"] == 1:
            try:
                v = parse_bit(text)
            except ValueError as e:
                self._status(f"Coil {lab}: {e}", "bad")
                return str(e)
            fn, desc = (lambda m: m.write_coil(unit, addr, v)), f"FC05 coil {lab} = {int(v)}"
        else:
            try:
                words = encode_value(text.strip(), p["fmt"])
            except ValueError:
                err = f"'{text.strip()}' is not a valid {p['fmt']} value"
                self._status(f"Register {lab}: {err}", "bad")
                return err
            if len(words) == 1:
                fn, desc = (lambda m: m.write_register(unit, addr, words[0])), f"FC06 register {lab} = {text.strip()}"
            else:
                fn, desc = (lambda m: m.write_registers(unit, addr, words)), f"FC16 registers {lab} = {text.strip()}"
        self._status(f"Writing {desc}...", "info")
        self.run_bg(fn, lambda r, err: self.write_done(desc, err))
        return None

    def write_done(self, desc, err):
        if err:
            self.last_err = (time.time(), f"write failed: {err}")
            self._counters()
            self._status(f"Write failed ({desc}): {err}", "bad")
        else:
            self._status(f"Written: {desc}", "ok")
            self.poke.set()  # re-poll right away to show the new value

    def open_write(self):
        target = self.active_poll_tab() or self  # F6 writes through the poll tab that is shown
        if self.write_dlg and self.write_dlg.alive():
            if self.write_dlg.t is target:
                self.write_dlg.w.deiconify()
                self.write_dlg.w.lift()
                self.write_dlg.prefill()
                return self.write_dlg
            self.write_dlg.close()
        self.write_dlg = WriteDialog(self, None if target is self else target)
        return self.write_dlg

    # ---------- setup / sim ----------
    def open_setup(self):
        if self.setup_dlg and self.setup_dlg.alive():
            self.setup_dlg.w.lift()
            return
        self.setup_dlg = SetupDialog(self)

    def apply_conn(self, conn, connect):
        self.cfg["conn"] = conn
        self.linked_conn_changed()
        save_settings(self.cfg)
        if connect:
            if self.state != "off":
                self.disconnect(quiet=True)
            self.connect()
        self._refresh_conn_ui()

    def open_sim(self):
        if self.sim and self.sim.alive():
            self.sim.w.deiconify()
            self.sim.w.lift()
            return self.sim
        self.sim = SimWindow(self)
        return self.sim

    # ---------- logging (datalog.CsvLog) ----------
    def toggle_log(self):
        if self.log:
            self.stop_log()
            return
        last = getattr(self, "last_log_path", "") or ""
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Log polls to CSV file", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=os.path.dirname(last) if last else None,
            initialfile=time.strftime("mbtool_%Y%m%d_%H%M%S.csv"))
        if path:
            self.start_log(path)

    def start_log(self, path):
        kw, errs = mon.log_kwargs(self.cfg["log"])
        if errs:
            self._status("Log options are not valid: " + "; ".join(errs.values()) + "  (Log options...)", "bad")
            return False
        try:
            lg = CsvLog(path, **kw)
        except (OSError, ValueError) as e:
            self._status(f"Cannot log to {path}: {e}", "bad")
            return False
        self.log = {"log": lg, "path": path}
        self.last_log_path = path
        self.b_log.configure(text="Stop Log")
        self.l_log.configure(text=f"● Logging to {os.path.basename(path)}", style="SBad.TLabel")
        self._status(f"Logging to {path}  ({self._log_desc()})", "info")
        return True

    def _log_desc(self):
        o = self.cfg["log"]
        rot = {"none": "one file", "daily": "new file every day", "size": f"new file every {o['size_mb']} MB"}[o["rotate"]]
        keep = f", keep last {o['keep']}" if o["keep_on"] and o["rotate"] != "none" else ""
        delim = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(o["delimiter"], o["delimiter"])
        return f"{rot}{keep}, {delim}-separated, decimal '{o['decimal']}'"

    def stop_log(self, error=None):
        if not self.log:
            return
        L, self.log = self.log, None
        L["log"].close()
        self.b_log.configure(text="Start Log")
        self.l_log.configure(text="")
        if error:
            self._status(f"Log write failed, logging stopped: {error}", "bad")
        else:
            where = L["log"].current_path or L["path"]
            self._status(f"Log stopped: {L['log'].rows} rows (last file {where})", "info")

    def _log_row(self, header, values, t):
        L = self.log
        if not L:
            return
        try:
            L["log"].write(header, values, t)
        except LogError as e:
            self.stop_log(error=e)
            return
        self.l_log.configure(text=f"● Logging to {os.path.basename(L['log'].current_path or L['path'])}  "
                                  f"({L['log'].rows} rows)")

    def _log_error(self, msg, t):
        L = self.log
        if not L:
            return
        try:
            L["log"].write_error(msg, t)
        except LogError as e:
            self.stop_log(error=e)

    def log_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Stop log" if self.log else "Start log...", accelerator="Ctrl+L", command=self.toggle_log)
        m.add_command(label="Log options...", command=self.open_log_options)
        try:
            m.tk_popup(self.b_logopt.winfo_rootx(), self.b_logopt.winfo_rooty() + self.b_logopt.winfo_height())
        finally:
            m.grab_release()

    def open_report(self):
        """File > Commissioning report: form + HTML / PDF of the Devices, health test and bus map."""
        return ReportDialog(self)

    def commission_data(self, form=None):
        """Everything the commissioning report shows, as plain dicts (commission.render)."""
        import busmap
        form = dict(self.cfg.get("report") or {}, **(form or {}))
        now = time.time()
        base = self.addr_base()
        devs = []
        for d in self.devices:
            st = self.dev_status.get(d["name"])
            state = st.state(now) if st else "off"
            if d["preset"]:
                what = os.path.basename(d["preset"])
            else:
                try:
                    fc = int(d["fc"])
                    what = f"FC{fc:02d} {addr_label(fc, int(d['addr']), base)} x{d['qty']}"
                except ValueError:
                    what = ""
            allv = list((st.texts if st else {}).items())
            vals = [[k, v] for k, v in allv[:16]]
            devs.append({"name": d["name"], "unit": d["unit"], "conn": mon.conn_text(d["conn"]), "what": what,
                         "state": state, "enabled": bool(d["enabled"]), "ok": st.ok if st else 0,
                         "errors": st.errors if st else 0, "quality": st.quality.summary() if st else {},
                         "last_t": st.last_t if st else None, "last_error": st.last_error if st else None,
                         "values": vals, "values_total": len(allv)})
        groups = collections.OrderedDict()
        for d in self.devices:
            groups.setdefault(mon.bus_key(d, "cycle"), []).append(d)
        est = {}
        win = self.devices_win
        if win is not None and win.alive():
            try:
                est = win.calc_tab.estimates_by_bus()
            except Exception:  # noqa: BLE001 - an estimate is optional in the report
                est = {}
        buses = []
        for key, ds in groups.items():
            b = self.cycle_stats.buses.get(key)
            buses.append({"label": mon.bus_label(key), "conn": mon.conn_text(ds[0]["conn"]),
                          "devices": [d["name"] for d in ds], "cycle": b.summary() if b is not None else None,
                          "estimate_ms": est.get(key)})
        bm = None
        m = self.cfg.get("busmap")
        if m and m.get("nodes"):
            try:
                bm = {"map": m, "analysis": busmap.analyze(m)}
            except Exception:  # noqa: BLE001
                bm = {"map": m, "analysis": None}
        dc = self.cfg["devices"]
        return {"project": form.get("project", ""), "site": form.get("site", ""), "customer": form.get("customer", ""),
                "engineer": form.get("engineer", ""), "notes": form.get("notes", ""), "generated": now,
                "version": appinfo.VERSION,
                "settings": {"scan_mode": self.devpoll.mode, "gap_ms": dc.get("gap_ms", "50"),
                             "target_ms": dc.get("cycle_target_ms", "")},
                "buses": buses, "devices": devs, "health": getattr(self, "last_health", None), "busmap": bm}

    def open_log_options(self):
        if self.log_dlg and self.log_dlg.alive():
            self.log_dlg.w.lift()
            return self.log_dlg
        self.log_dlg = LogOptionsDialog(self)
        return self.log_dlg

    def apply_log_options(self, opts):
        self.cfg["log"] = mon.norm_log_options(opts)
        save_settings(self.cfg)
        if self.log:  # restart with the new format (a header row starts the new session)
            path = self.log["path"]
            self.stop_log()
            self.start_log(path)

    # ---------- menu ----------
    def _build_menu(self):
        mb = tk.Menu(self.root)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="New poll tab", accelerator="Ctrl+T", command=self.new_poll_tab)
        fm.add_command(label="Duplicate poll tab", command=lambda: self.duplicate_poll_tab(self.active_tab_key()))
        fm.add_command(label="Rename poll tab...", command=lambda: self.tabstrip.start_rename(self.active_tab_key()))
        fm.add_command(label="Close poll tab", accelerator="Ctrl+W", command=self.close_active_tab)
        fm.add_separator()
        fm.add_command(label="Open workspace...", accelerator="Ctrl+O", command=self.open_workspace)
        fm.add_command(label="Save workspace", accelerator="Ctrl+S", command=self.save_workspace)
        fm.add_command(label="Save workspace as...", command=lambda: self.save_workspace(as_new=True))
        self.recent_menu = tk.Menu(fm, tearoff=0, postcommand=self._fill_recent)
        fm.add_cascade(label="Recent workspaces", menu=self.recent_menu)
        fm.add_checkbutton(label="Open the last workspace at start", variable=self.autoload,
                           command=self._autoload_changed)
        fm.add_separator()
        fm.add_command(label="Start / stop log", accelerator="Ctrl+L", command=self.toggle_log)
        fm.add_command(label="Log options...", command=self.open_log_options)
        fm.add_separator()
        fm.add_command(label="Commissioning report...", command=self.open_report)
        fm.add_separator()
        fm.add_command(label="Exit", command=self.close)
        mb.add_cascade(label="File", menu=fm)
        vm = tk.Menu(mb, tearoff=0)
        for lab, acc, fn in (("Connection setup...", "F3", self.open_setup), ("Value inspector", "F4", self.open_inspector),
                             ("Write...", "F6", self.open_write), ("Presets...", "F7", self.open_presets),
                             ("Traffic monitor", "F8", self.open_traffic), ("Slave simulator", "F9", self.open_sim),
                             (None, None, None), ("Trend", "F10", self.open_trend), ("Alerts", "", self.open_alerts),
                             ("Devices", "F11", self.open_devices), ("Bus map...", "Ctrl+M", self.open_busmap),
                             ("Data export (MQTT / database)...", "", self.open_export),
                             (None, None, None),
                             ("AI Assist...", "F12", self.open_ai)):
            if lab is None:
                vm.add_separator()
            else:
                vm.add_command(label=lab, accelerator=acc, command=fn)
        vm.add_separator()
        vm.add_checkbutton(label="Multi-type view", variable=self.multi_var, command=self._multi_changed)
        vm.add_command(label="Multi-type view columns...", command=self.open_multi_cols)
        mb.add_cascade(label="Tools", menu=vm)
        hm = tk.Menu(mb, tearoff=0)
        hm.add_command(label="Manual", accelerator="F1", command=lambda: self.open_help())
        self.help_lang_var = tk.StringVar(value=self.cfg["help"].get("lang", "th"))
        lm = tk.Menu(hm, tearoff=0)
        for code, name in (("th", "ไทย (Thai)"), ("en", "English")):
            lm.add_radiobutton(label=name, value=code, variable=self.help_lang_var,
                               command=lambda c=code: self.set_help_lang(c))
        hm.add_cascade(label="Manual language", menu=lm)
        hm.add_command(label="Quick start", command=lambda: self.open_help(topic="quickstart"))
        hm.add_command(label="Keyboard shortcuts", command=lambda: self.open_help(topic="shortcuts"))
        hm.add_command(label="Poll tabs", command=lambda: self.open_help(key="poll.tabs"))
        hm.add_command(label="Workspaces", command=lambda: self.open_help(key="workspace"))
        hm.add_command(label="Exception codes", command=lambda: self.open_help(key="error.exception"))
        hm.add_separator()
        hm.add_command(label="Open data folder", command=self.open_data_folder)
        hm.add_command(label="About mbtool...", command=self.show_about)
        mb.add_cascade(label="Help", menu=hm)
        self.root.configure(menu=mb)
        self.menubar = mb

    def _fill_recent(self):
        m = self.recent_menu
        m.delete(0, "end")
        recent = self.cfg["workspace"].get("recent", [])
        if not recent:
            m.add_command(label="(none yet)", state="disabled")
        for p in recent:
            m.add_command(label=p, command=lambda p=p: self.open_workspace(p))

    def _autoload_changed(self):
        self.cfg["workspace"]["autoload"] = bool(self.autoload.get())
        save_settings(self.cfg)

    # ---------- alarms UI ----------
    def _build_alarm_banner(self):
        s = self.s
        f = self.banner = tk.Frame(self.root, bg=COL["bad"])
        self.l_banner = tk.Label(f, text="", bg=COL["bad"], fg="white", anchor="w", font=self.root._mb_fonts[1],
                                 padx=int(10 * s), pady=int(4 * s))
        self.l_banner.pack(side="left", fill="x", expand=True)
        self.b_banner_open = ttk.Button(f, text="Alerts...", command=self.open_alerts)
        self.b_banner_open.pack(side="right", padx=(4, 8), pady=int(3 * s))
        self.b_banner_ack = ttk.Button(f, text="Acknowledge all", command=self.ack_all)
        self.b_banner_ack.pack(side="right", pady=int(3 * s))
        self.l_banner.bind("<Button-1>", lambda e: self.open_alerts())

    def _alarm_events(self, events):
        if self.alarm_beep.get() and any(e["state"] == "ALARM" for e in events):
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONHAND)
            except Exception:
                self.root.bell()
        self._alarms_changed()
        if self.alerts_win and self.alerts_win.alive():
            self.alerts_win.add_events(events)

    def _alarms_changed(self):
        act, un = self.alarms.active(), self.alarms.unacked()
        n = len(act)
        if act or un:
            self.badge.configure(text=f" {n} " if n else " ! ", bg=COL["bad"] if any(
                self.alarms.state(r.id) == "ALARM" for r in un) or n else COL["warn"])
            if not self.badge.winfo_ismapped():
                self.badge.pack(side="left", padx=(0, 6), after=self.b_alerts)
            first = next((r for r in self.alarms.rules if self.alarms.state(r.id) == "ALARM"), None) or \
                (act[0] if act else un[0])
            st = self.alarms.states[first.id]
            val = "" if st.value is None or first.point == "*" else f"  (value {mon.fmt_num(mon.numeric(st.value)) if mon.numeric(st.value) is not None else st.value})"
            src = f"{first.device}: " if first.device else ""
            since = time.strftime("%H:%M:%S", time.localtime(st.t_raised)) if st.t_raised else ""
            more = len(set(r.id for r in act + un)) - 1
            text = (f"⚠ {self.alarms.state(first.id)}  {src}{first.message or first.describe()}{val}  since {since}"
                    + (f"   (+{more} more)" if more > 0 else "")
                    + (f"   -   {len(un)} not acknowledged" if un else "   -   acknowledged"))
            color = COL["bad"] if un else "#b06000"
            self.banner.configure(bg=color)
            self.l_banner.configure(text=text, bg=color)
            if not self.banner.winfo_ismapped():
                self.banner.pack(fill="x", before=self.tabstrip.frame)
            self.b_banner_ack.state(["!disabled"] if un else ["disabled"])
        else:
            self.badge.pack_forget()
            self.banner.pack_forget()
        if self.alerts_win and self.alerts_win.alive():
            self.alerts_win.refresh_rules()

    def ack_all(self):
        ev = self.alarms.ack(now=time.time())
        if ev:
            self._alarm_events(ev)
        self._alarms_changed()

    def open_alerts(self):
        if self.alerts_win and self.alerts_win.alive():
            self.alerts_win.w.deiconify()
            self.alerts_win.w.lift()
            return self.alerts_win
        self.alerts_win = AlertsWindow(self)
        return self.alerts_win

    # ---------- sources (main window + devices) for trend / alerts / traffic ----------
    def source_names(self):
        return [""] + [d["name"] for d in self.devices]

    @staticmethod
    def source_label(src):
        return "Main window" if src == "" else src

    def source_from_label(self, label):
        return "" if label == "Main window" else label

    def device_preset(self, dv):
        """Preset object of a device (cached by path + mtime); raises OSError / ValueError / PresetError."""
        path = dv["preset"]
        key = (path, os.path.getmtime(path))
        hit = self.dev_presets.get(path)
        if hit and hit[0] == key:
            return hit[1], hit[2]
        data, pr = read_preset_file(path)
        self.dev_presets[path] = (key, pr, data)
        return pr, data

    def source_points(self, src):
        """[(key, label, unit)] that can be trended / used in alarm rules for a source."""
        base = self.addr_base()
        if src == "":
            if self.preset:
                return [(p.name, p.name, p.unit) for p in self.preset["obj"].points if p.access != "w"]
            p = self.p
            if not p:
                return []
            return [(str(a), "Address " + addr_label(p["fc"], a, base), "") for a in self.cell_addrs]
        dv = self.dev_by_name(src)
        if dv is None:
            return []
        if dv["preset"]:
            try:
                pr, _d = self.device_preset(dv)
            except (OSError, ValueError, PresetError):
                return []
            return [(p.name, p.name, p.unit) for p in pr.points if p.access != "w"]
        fc, addr, qty = int(dv["fc"]), int(dv["addr"]), int(dv["qty"])
        w = 1 if fc in (1, 2) else fmt_width(dv["fmt"])
        return [(str(addr + o), "Address " + addr_label(fc, addr + o, base), "") for o in range(0, qty - w + 1, w)]

    def set_trend(self, source, keys):
        pts = {k: (k, lab, u) for k, lab, u in self.source_points(source)}
        specs = [pts[k] for k in keys if k in pts][:mon.MAX_SERIES]
        if source != self.trend_cfg["source"]:
            self.trend_buf = mon.TrendBuffer()
        self.trend_cfg["source"] = source
        self.trend_cfg["series"] = [s[0] for s in specs]
        self.trend_buf.set_series(specs)
        return specs

    def trend_sources_changed(self):
        if self.trend_win and self.trend_win.alive():
            self.trend_win.refresh_candidates()
        if self.alerts_win and self.alerts_win.alive():
            self.alerts_win.refresh_sources()

    def activate_source(self, src):
        """Make a device (or '' = main window) the one shown in the Trend, Traffic and Alerts windows."""
        tw = self.open_trend()
        tw.srcvar.set(self.source_label(src))
        tw.refresh_candidates()
        if not self.trend_cfg["series"]:
            tw.select_keys([k for k, _l, _u in self.source_points(src)][:4])
        if self.traffic_win and self.traffic_win.alive():
            self.traffic_win.select_source(src)
        if self.alerts_win and self.alerts_win.alive() and self.alerts_win.cur is None:
            self.alerts_win.new_rule(device=src)

    def open_trend(self):
        if self.trend_win and self.trend_win.alive():
            self.trend_win.w.deiconify()
            self.trend_win.w.lift()
            return self.trend_win
        self.trend_win = TrendWindow(self)
        return self.trend_win

    # ---------- devices (multi-device polling) ----------
    def _gap_s(self):
        try:
            return int_in(self.cfg["devices"].get("gap_ms", "50"), 0, 5000) / 1000
        except ValueError:
            return 0.05

    @staticmethod
    def _dev_factory(conn):
        """Worker thread: open one master for a device / bus (raises on failure)."""
        factory, _summary = master_factory(conn)
        return factory()

    def dev_by_name(self, name):
        return next((d for d in self.devices if d["name"] == name), None)

    def _saved_devices(self):
        return json.loads(json.dumps([d for d in self.devices if d["name"] not in self.demo_names]))

    def start_demo(self, line="rtu"):
        """Start simulated devices with moving values and add them (one tab each) so every feature can be tried
        without hardware. Returns the names added; raises on failure."""
        import demo as demomod
        if self.demo is not None:
            return []
        d = demomod.Demo(line=line)
        devs = d.start()
        taken = {x["name"].lower() for x in self.devices}
        names = []
        for dv in devs:
            dv = mon.norm_device(dv)
            while dv["name"].lower() in taken:
                dv["name"] += " (demo)"
            taken.add(dv["name"].lower())
            self.devices.append(dv)
            names.append(dv["name"])
        self.demo, self.demo_names = d, set(names)
        self.trend_sources_changed()
        for n in names:
            self.start_device(n, load_alarms=False)
        return names

    def stop_demo(self):
        if self.demo is None:
            return
        for n in list(self.demo_names):
            self.stop_device(n)
        self.devices = [d for d in self.devices if d["name"] not in self.demo_names]
        for n in self.demo_names:
            self.dev_status.pop(n, None)
        self.demo.stop()
        self.demo, self.demo_names = None, set()
        self.trend_sources_changed()

    def save_devices(self):
        self.sync_poll_tabs()  # the device list changed: poll tabs follow (added / removed / edited)
        self.cfg["devices"]["list"] = self._saved_devices()
        save_settings(self.cfg)
        self.trend_sources_changed()

    def start_device(self, name, load_alarms=True):
        """Start polling one device. Returns None or an error text (also kept in its status)."""
        dv = self.dev_by_name(name)
        if dv is None:
            return "unknown device"
        if dv.get("tab") and dv.get("tab_link"):  # a poll tab on "same as Poll 1": take its current connection
            dv["conn"] = mon.norm_conn(self.cfg["conn"])
        st = self.dev_status.setdefault(name, mon.DeviceStatus(name))
        others = [d for d in self.devices if d is not dv]
        errs = mon.device_errors(dv, others)
        if errs:
            st.last_error = "; ".join(f"{k}: {v}" for k, v in errs.items())
            return st.last_error
        pr = data = None
        if dv["preset"]:
            try:
                pr, data = self.device_preset(dv)
            except (OSError, ValueError, PresetError) as e:
                st.last_error = f"preset not usable: {e}"
                return st.last_error
        self.devpoll.gap_s = self._gap_s()
        try:
            self.devpoll.start_device(dv, mon.make_reader(dv, pr))
        except ValueError as e:
            st.last_error = str(e)
            return st.last_error
        timeout_s = int_in(dv["conn"]["timeout"], 50, 60000) / 1000
        if self.devpoll.mode == "cycle":  # one cycle may read every device of the bus (and wait for timeouts)
            n = sum(1 for d in self.devices if mon.bus_key(d, "cycle") == mon.bus_key(dv, "cycle"))
            st.start(max(int(dv["scan"]) / 1000, (self.devpoll.target_s or 0), 1.0), timeout_s * max(n, 1) * 2)
        else:
            st.start(int(dv["scan"]) / 1000, timeout_s)
        st.last_error = None
        if load_alarms:
            self.alarms.remove_origin(name, "preset")
            if data is not None:
                self.alarms.load_dicts(mon.preset_alarms(data), device=name, origin="preset",
                                       points=[p.name for p in pr.points])
            self._alarms_changed()
        tab = self.poll_tabs.get(name)
        if tab is not None:
            tab.refresh()
        return None

    def stop_device(self, name):
        self.devpoll.stop_device(name)
        st = self.dev_status.get(name)
        if st:
            st.stop()
        self.alarms.suspend(name)
        if self.trend_cfg["source"] == name:
            self.trend_buf.add_gap(time.time())
        tab = self.poll_tabs.get(name)
        if tab is not None:
            tab.refresh()

    def start_all_devices(self, load_alarms=True):
        errors = {}
        if self.cfg["devices"].get("log_on") and self.devlogs is None:
            self._open_devlogs()
        for d in self.devices:
            if d["enabled"] and not self.devpoll.running(d["name"]):
                err = self.start_device(d["name"], load_alarms)
                if err:
                    errors[d["name"]] = err
        return errors

    def stop_all_devices(self):
        for d in self.devices:
            self.stop_device(d["name"])
        for w in self.devpoll.stop_all():
            pass
        self._close_devlogs()

    def devices_running(self):
        return any(self.devpoll.running(d["name"]) for d in self.devices)

    def _open_devlogs(self):
        folder = self.cfg["devices"].get("log_folder", "")
        kw, errs = mon.log_kwargs(self.cfg["log"])
        if not folder or errs:
            self._status("Device logging not started: " + ("choose a folder" if not folder else
                                                           "; ".join(errs.values())), "bad")
            return False
        self.devlogs = DeviceLogs(folder, **kw)
        return True

    def _close_devlogs(self):
        if self.devlogs is not None:
            self.devlogs.close()
            self.devlogs = None

    def named_values(self, name, res):
        """A device result -> ({point name: value}, {point name: text}); register blocks use the address labels."""
        dv = self.dev_by_name(name)
        values, texts = res.get("values") or {}, res.get("texts") or {}
        if dv is None or dv["preset"]:
            return dict(values), dict(texts)
        try:
            fc, base = int(dv["fc"]), self.addr_base()
            lab = {k: addr_label(fc, int(k), base) for k in values}
        except (TypeError, ValueError):
            return dict(values), dict(texts)
        return {lab[k]: v for k, v in values.items()}, {lab[k]: texts.get(k) for k in values if k in texts}

    def open_export(self):
        if self.export_win is not None and self.export_win.alive():
            self.export_win.w.deiconify()
            self.export_win.w.lift()
            return self.export_win
        self.export_win = ExportWindow(self)
        return self.export_win

    def start_export(self, cfg, password=""):
        """Start sending every device reading (raises export.ExportError with a readable text)."""
        import export
        self.stop_export()
        ex = export.Exporter(cfg, password=password)
        ex.start()
        self.exporter = ex
        return ex

    def stop_export(self):
        ex, self.exporter = self.exporter, None
        if ex is not None:
            ex.stop()
        return ex

    def _on_dev_result(self, msg):
        if not self.devpoll.accept(msg):
            return  # from a stopped / restarted run
        _k, name, _gen, t, res = msg
        st = self.dev_status.get(name)
        if st is None or not st.running:
            return
        st.on_result(t, res)
        self.feed(name, t, res["values"], res["ok"])
        if self.exporter is not None:
            vals, texts = self.named_values(name, res) if res["ok"] else ({}, {})
            self.exporter.submit(name, t, vals, texts, res["ok"], res.get("error"))
        if self.devlogs is not None:
            try:
                if res["ok"]:
                    keys = list(res["texts"])
                    dv = self.dev_by_name(name)
                    if dv and not dv["preset"]:
                        hdr = [addr_label(int(dv["fc"]), int(k), self.addr_base()) for k in keys]
                    else:
                        hdr = keys
                    self.devlogs.write(name, hdr, mon.log_values(keys, res["values"], res["texts"]), t)
                else:
                    self.devlogs.write_error(name, res["error"] or "error", t)
            except (LogError, OSError) as e:
                self._close_devlogs()
                self.cfg["devices"]["log_on"] = False
                self._status(f"Device log write failed, device logging stopped: {e}", "bad")
                if self.devices_win and self.devices_win.alive():
                    self.devices_win.log_stopped(str(e))
        if self.devices_win and self.devices_win.alive():
            self.devices_win.update_row(name)
        tab = self.poll_tabs.get(name)
        if tab is not None:
            tab.refresh()

    def _devices_tick(self):
        if self.devices_win and self.devices_win.alive():
            self.devices_win.refresh_states()
        for tab in list(self.poll_tabs.values()):  # stale detection + the dots on the tab strip
            tab.refresh()

    # ---------- PLC cycle mode ----------
    def _norm_dev_cfg(self):
        dc = self.cfg["devices"]
        dc["scan_mode"] = mon.norm_scan_mode(dc.get("scan_mode"))
        try:
            mon.parse_target_ms(dc.get("cycle_target_ms", ""))
        except ValueError:
            dc["cycle_target_ms"] = ""
        dc["calc"] = mon.norm_calc(dc.get("calc"))

    def _apply_scan_cfg(self):
        """cfg['devices'] scan mode / target -> the poller (the mode is used by workers started from now on)."""
        dc = self.cfg["devices"]
        self.devpoll.mode = mon.norm_scan_mode(dc.get("scan_mode"))
        try:
            ms = mon.parse_target_ms(dc.get("cycle_target_ms", ""))
        except ValueError:
            ms = None
        self.devpoll.target_s = ms / 1000.0 if ms else None

    def set_scan_mode(self, mode):
        """Switch Independent <-> PLC cycle. Running devices are restarted in the new mode; the statistics start
        again. Returns {device: error} of devices that could not be restarted."""
        mode = mon.norm_scan_mode(mode)
        if mode == self.devpoll.mode and mode == self.cfg["devices"].get("scan_mode"):
            return {}
        running = [d["name"] for d in self.devices if self.devpoll.running(d["name"])]
        for n in running:
            self.devpoll.stop_device(n)
        self.cfg["devices"]["scan_mode"] = mode
        self._apply_scan_cfg()
        self.cycle_stats.reset()
        errors = {}
        for n in running:
            err = self.start_device(n, load_alarms=False)
            if err:
                errors[n] = err
        return errors

    def _on_cycle(self, msg):
        _k, key, _g, _t, info = msg
        if self.devpoll.mode != "cycle":
            return  # from a worker of the previous mode
        self.cycle_stats.add(key, info)

    def open_devices(self):
        if self.devices_win and self.devices_win.alive():
            self.devices_win.w.deiconify()
            self.devices_win.w.lift()
            return self.devices_win
        self.devices_win = DevicesWindow(self)
        return self.devices_win

    def open_busmap(self):
        if self.busmap_win and self.busmap_win.alive():
            self.busmap_win.w.deiconify()
            self.busmap_win.w.lift()
            return self.busmap_win
        self.busmap_win = BusMapWindow(self)
        return self.busmap_win

    def set_busmap(self, m, save=True):
        """Replace the bus map (already normalised) and show it in the Bus map window when open."""
        self.cfg["busmap"] = json.loads(json.dumps(m))
        if save:
            save_settings(self.cfg)
        if self.busmap_win and self.busmap_win.alive():
            self.busmap_win.reload()

    # ---------- poll tabs of the main window ----------
    def tab_devices(self):
        """Devices shown as poll tabs (demo devices never are)."""
        return [d for d in self.devices if d.get("tab") and d["name"] not in self.demo_names]

    def poll_tab_keys(self):
        """Tab order: None = the first tab (the classic poll), then one key (device name) per poll tab."""
        return [None] + [d["name"] for d in self.tab_devices()]

    def main_tab_name(self):
        return self.cfg["tabs"]["main_name"]

    def tab_title(self, key):
        return self.main_tab_name() if key is None else key

    def active_tab_key(self):
        return self.cur_tab

    def active_poll_tab(self):
        """The PollTab that is shown, or None while the first tab is shown."""
        return self.poll_tabs.get(self.cur_tab) if self.cur_tab is not None else None

    def active_tab_index(self):
        keys = self.poll_tab_keys()
        return keys.index(self.cur_tab) if self.cur_tab in keys else 0

    def main_tab_state(self):
        """Dot of the first tab: off / ok / error (no valid reply) / stale (connecting, link lost)."""
        if self.state == "off":
            return "off"
        if self.state == "connecting" or self.link_bad:
            return "stale"
        return "error" if self.poll_bad else "ok"

    def _name_taken(self, name, skip_main=False, skip_dev=None):
        """A device or the first tab already uses this name (case-insensitive)."""
        low = name.strip().lower()
        if not skip_main and low == self.main_tab_name().strip().lower():
            return True
        return any(d["name"].lower() == low for d in self.devices if d["name"] != skip_dev)

    def _unique_tab_name(self, base=None):
        if base is None:
            n = 2
            while self._name_taken(f"Poll {n}"):
                n += 1
            return f"Poll {n}"
        name, n = base, 2
        while self._name_taken(name):
            name = f"{base} ({n})"
            n += 1
        return name

    def sync_poll_tabs(self, renamed=None):
        """One PollTab per tab device, in device order; created / removed / renamed / reloaded as the list changes."""
        if not hasattr(self, "tabstrip"):
            return
        if renamed:
            old, new = renamed
            t = self.poll_tabs.pop(old, None)
            if t is not None and new not in self.poll_tabs:
                t.name = new
                self.poll_tabs[new] = t
            if self.cur_tab == old:
                self.cur_tab = new
            if self.trend_cfg["source"] == old:
                self.trend_cfg["source"] = new
        want = [d["name"] for d in self.tab_devices()]
        for n in list(self.poll_tabs):
            if n not in want:
                self.poll_tabs.pop(n).destroy()
        for n in want:
            dv = self.dev_by_name(n)
            if dv["tab_link"] and not self.devpoll.running(n):
                dv["conn"] = mon.norm_conn(self.cfg["conn"])
            t = self.poll_tabs.get(n)
            if t is None:
                self.poll_tabs[n] = PollTab(self, n)
            else:
                t.reload()  # picks up an edit made in the Devices window
        self.tabstrip.set_tabs(self.poll_tab_keys())
        self.tabstrip.set_state(None, self.main_tab_state())
        for n, t in self.poll_tabs.items():
            self.tabstrip.set_state(n, t.state or "off")
        if self.cur_tab is not None and self.cur_tab not in self.poll_tabs:
            self.select_poll_tab(None)
        else:
            self.tabstrip.select(self.cur_tab)

    def select_poll_tab(self, key):
        if key is not None and key not in self.poll_tabs:
            key = None
        tab = self.poll_tabs.get(key) if key is not None else None
        self.cur_tab = key
        try:
            self.pages.select(self.main_page if tab is None else tab.frame)
        except tk.TclError:
            return
        self.tabstrip.select(key)
        self._counters()  # the global status bar names the first tab while another tab is shown
        if tab is not None:
            tab.refresh(full=True)
        if self.inspector and self.inspector.alive():
            self.inspector.refresh(force=True)

    def next_poll_tab(self, step):
        keys = self.poll_tab_keys()
        i = keys.index(self.cur_tab) if self.cur_tab in keys else 0
        self.select_poll_tab(keys[(i + step) % len(keys)])

    def _tab_device_from(self, key, name, duplicate):
        """New poll tab device: poll definition (and, when duplicating, preset + connection) of tab `key`."""
        d = {"name": name, "tab": True, "tab_link": True, "conn": dict(self.cfg["conn"])}
        if key is None:
            p = self.p
            d.update(tab_base=str(self.addr_base()), tab_rows=self.rows_var.get(), tab_multi=bool(self.multi_var.get()),
                     fmt=self.pv["fmt"].get())
            if p and not p.get("preset"):
                d.update(unit=str(p["unit"]), fc=str(p["fc"]), addr=str(p["addr"]), qty=str(p["qty"]),
                         scan=str(p["scan"]))
            elif p:
                d.update(unit=str(p["unit"]), scan=str(p["scan"]))
                if duplicate and self.preset:
                    d["preset"] = (self.trial["orig"][1] if self.trial else self.preset["path"]) or ""
        else:
            src = self.poll_tabs.get(key)
            if src is not None:
                src.flush()
            sd = self.dev_by_name(key)
            if sd is not None:
                for k in ("unit", "fc", "addr", "qty", "scan", "fmt", "tab_base", "tab_rows", "tab_multi"):
                    d[k] = sd[k]
                if duplicate:
                    d.update(preset=sd["preset"], tab_link=sd["tab_link"], conn=dict(sd["conn"]),
                             enabled=sd["enabled"])
        return mon.norm_device(d)

    def new_poll_tab(self, from_key="active", duplicate=False, name=None):
        """Ctrl+T / '+': a new poll tab with the poll definition of the shown tab (or of from_key). It starts
        polling at once when that tab polls. Returns the new tab's name."""
        key = self.cur_tab if from_key == "active" else from_key
        if key is not None and key not in self.poll_tabs:
            key = None
        if name is None:
            name = self._unique_tab_name(f"{self.tab_title(key)} copy" if duplicate else None)
        dv = self._tab_device_from(key, name, duplicate)
        self.devices.append(dv)
        self.save_devices()
        self.select_poll_tab(name)
        running = (self.state == "on") if key is None else self.devpoll.running(key)
        tab = self.poll_tabs.get(name)
        if running and tab is not None:
            tab.start()
        if self.devices_win and self.devices_win.alive():
            self.devices_win.reload()
        return name

    def duplicate_poll_tab(self, key):
        return self.new_poll_tab(from_key=key, duplicate=True)

    def close_poll_tab(self, key):
        """Close a poll tab: stops its polling and removes its device. The first tab cannot be closed."""
        if key is None:
            self._status(f"'{self.main_tab_name()}' is the main poll and cannot be closed", "warn")
            return False
        if key not in self.poll_tabs:
            return False
        keys = self.poll_tab_keys()
        if self.cur_tab == key:
            i = keys.index(key)
            self.select_poll_tab(keys[i - 1] if i > 0 else None)
        self.stop_device(key)
        self.devices = [d for d in self.devices if d["name"] != key]
        self.dev_status.pop(key, None)
        self.save_devices()
        if self.devices_win and self.devices_win.alive():
            self.devices_win.reload()
        self._status(f"Poll tab '{key}' closed (its alarm rules stay; delete them in Alerts)", "info", main=False)
        return True

    def close_active_tab(self):
        return self.close_poll_tab(self.cur_tab)

    def rename_poll_tab(self, key, new):
        """-> None or an error text. Renaming a poll tab renames its device (alarm rules, trend follow)."""
        new = str(new).strip()
        if not new:
            return "enter a name"
        if key is None:
            if self._name_taken(new, skip_main=True):
                return "name already used"
            self.cfg["tabs"]["main_name"] = new
            self.tabstrip.redraw()
            for t in self.poll_tabs.values():
                t.show_conn()
            return None
        if new == key:
            return None
        dv = self.dev_by_name(key)
        if dv is None:
            return "unknown tab"
        if self._name_taken(new, skip_dev=key):
            return "name already used"
        running = self.devpoll.running(key)
        self.stop_device(key)
        dv["name"] = new
        st = self.dev_status.pop(key, None)
        if st is not None:
            st.name = new
            self.dev_status[new] = st
        for r in self.alarms.rules:
            if r.device == key:
                r.device = new
        calc = self.cfg["devices"].get("calc", {}).get("dev", {})
        if key in calc:
            calc[new] = calc.pop(key)
        self.sync_poll_tabs((key, new))
        self.save_devices()
        if self.devices_win and self.devices_win.alive():
            self.devices_win.sync_tabs((key, new))
            self.devices_win.reload()
        if running:
            self.start_device(new, load_alarms=False)
        return None

    def show_tab_in_devices(self, key):
        win = self.open_devices()
        if key is not None:
            win.show_tab(key)
        return win

    def linked_conn_changed(self):
        """The first tab's connection changed: poll tabs on 'same as Poll 1' follow (running ones restart)."""
        new = mon.norm_conn(self.cfg["conn"])
        for d in self.tab_devices():
            if d["tab_link"] and d["conn"] != new:
                d["conn"] = dict(new)
                if self.devpoll.running(d["name"]):
                    self.start_device(d["name"], load_alarms=False)
        for t in self.poll_tabs.values():
            t.show_conn()

    # ---------- manual (F1) ----------
    def _help_keys(self):
        for k, key in (("unit", "poll.unit"), ("func", "poll.function"), ("addr", "poll.address"),
                       ("qty", "poll.quantity"), ("scan", "poll.scan"), ("fmt", "poll.display"), ("base", "poll.base")):
            help_key(self.pw[k], key)
        help_key(self.cb_rows, "poll.rows")
        help_key(self.l_rows, "poll.rows")
        help_key(self.cb_multi, "poll.multi")
        help_key(self.b_mcols, "poll.multi")
        help_key(self.mgrid.frame, "poll.multi")

    def _f1(self, e):
        w = e.widget
        if isinstance(w, str):
            try:
                w = self.root.nametowidget(w)
            except (KeyError, tk.TclError):
                w = None
        if self.help_win and self.help_win.alive() and w is not None:
            try:
                if w.winfo_toplevel() is self.help_win.w:
                    self.help_win.focus_search()
                    return "break"
            except tk.TclError:
                pass
        self.open_help(key=widget_help_key(w) if w is not None else None)
        return "break"

    def open_data_folder(self):
        try:
            os.startfile(appinfo.data_dir())
        except (OSError, AttributeError) as e:
            self._status(f"Cannot open folder: {e}", "bad")

    def show_about(self):
        from tkinter import messagebox
        messagebox.showinfo("About mbtool", appinfo.about_text(), parent=self.root)

    def set_help_lang(self, lang):
        """Manual language th / en: saved, and the open manual switches at once (same topic)."""
        import helpdocs
        lang = helpdocs.set_lang(lang)
        self.cfg["help"]["lang"] = lang
        if hasattr(self, "help_lang_var") and self.help_lang_var.get() != lang:
            self.help_lang_var.set(lang)
        if self.help_win and self.help_win.alive():
            self.help_win.reload_lang()
        save_settings(self.cfg)
        return lang

    def open_help(self, key=None, topic=None):
        """Open (or bring up) the manual at the topic for a context key / topic id. Non-modal, one instance."""
        import helpdocs
        tid = topic
        if tid is None and key:
            try:
                tid = helpdocs.topic_for(key).id
            except (KeyError, helpdocs.HelpError):
                tid = None
        g = self.root.grab_current()
        if g is not None:  # a modal dialog would block the manual: let both be used
            try:
                g.grab_release()
            except tk.TclError:
                pass
        if self.help_win and self.help_win.alive():
            self.help_win.show(tid)
            return self.help_win
        try:
            self.help_win = HelpWindow(self)
        except (OSError, helpdocs.HelpError) as e:
            self._status(f"The manual cannot be opened: {e}", "bad")
            return None
        if tid:
            self.help_win.open(tid)
        return self.help_win

    def _error_help(self):
        key = getattr(self, "_err_key", None)
        if key:
            self.open_help(key=key)

    # ---------- AI Assist ----------
    def open_ai(self):
        if self.ai_win and self.ai_win.alive():
            self.ai_win.w.deiconify()
            self.ai_win.w.lift()
            return self.ai_win
        self.ai_win = AiAssistWindow(self)
        return self.ai_win

    # ---------- workspace ----------
    WIN_ATTRS = {"trend": "trend_win", "alerts": "alerts_win", "devices": "devices_win", "traffic": "traffic_win",
                 "busmap": "busmap_win"}

    def gather_workspace(self):
        ws = mon.new_workspace()
        main_preset = ""
        if self.preset:
            main_preset = (self.trial["orig"][1] if self.trial else self.preset["path"]) or ""
        base = self.addr_base()
        self.cfg["poll"].update(base=str(base), plc=base == "plc", rows=self.rows_var.get())
        for tab in list(self.poll_tabs.values()):
            tab.flush()
        ws["main"] = {"conn": dict(self.cfg["conn"]), "poll": dict(self.cfg["poll"]), "preset": main_preset,
                      "group": self.pview.group.get() if self.preset and self.pview else "All",
                      "connected": self.state != "off"}
        ws["devices"] = json.loads(json.dumps(self.devices))
        ws["devices_running"] = self.devices_running()
        ws["bus_gap_ms"] = int(self._gap_s() * 1000)
        ws["device_log"] = {"enabled": bool(self.cfg["devices"].get("log_on")),
                            "folder": self.cfg["devices"].get("log_folder", "")}
        self._norm_dev_cfg()
        dc = self.cfg["devices"]
        ws["devices_cfg"] = {"scan_mode": dc["scan_mode"], "cycle_target_ms": dc["cycle_target_ms"],
                             "calc": json.loads(json.dumps(dc["calc"]))}
        ws["trend"] = dict(self.trend_cfg)
        ws["alarms"] = [r.to_dict() for r in self.alarms.rules]
        ws["alerts"] = {"beep": bool(self.alarm_beep.get())}
        ws["windows"] = {"main": {"geometry": self.root.geometry(), "open": True}}
        for name, attr in self.WIN_ATTRS.items():
            win = getattr(self, attr)
            alive = bool(win and win.alive())
            geo = ""
            if alive:
                try:
                    geo = win.w.geometry()
                except tk.TclError:
                    pass
            ws["windows"][name] = {"geometry": geo, "open": alive}
        ws["display"] = {"address_base": str(self.addr_base()), "rows_per_column": self.rows_var.get(),
                         "format": self.cfg["poll"].get("fmt", "Unsigned"),
                         # poll tabs: the extra tabs are the devices with tab=true (in "devices")
                         "tab_name": self.cfg["tabs"]["main_name"], "active_tab": self.active_tab_index()}
        ws["log"] = dict(self.cfg["log"])
        ws["log_file"] = getattr(self, "last_log_path", "") or ""
        ws["ai"] = mon.norm_ai_settings(self.cfg["ai"])  # sharing options only, never the token
        ws["busmap"] = json.loads(json.dumps(self.cfg["busmap"]))  # Tools > Bus map, next to the devices
        return ws

    def save_workspace(self, as_new=False, path=None):
        path = path or (None if as_new else self.ws_path)
        if not path:
            path = filedialog.asksaveasfilename(parent=self.root, title="Save workspace", defaultextension=mon.WS_EXT,
                                                filetypes=[("mbtool workspace", "*" + mon.WS_EXT), ("All files", "*.*")],
                                                initialfile=os.path.basename(self.ws_path) if self.ws_path else
                                                "workspace" + mon.WS_EXT)
            if not path:
                return None
        try:
            mon.save_workspace(path, self.gather_workspace())
        except (OSError, TypeError, ValueError) as e:
            self._status(f"Workspace not saved: {e}", "bad")
            return None
        self._ws_opened(path)
        self._status(f"Workspace saved: {path}", "ok")
        return path

    def _ws_opened(self, path):
        self.ws_path = os.path.abspath(path)
        self.cfg["workspace"]["recent"] = mon.add_recent(self.cfg["workspace"].get("recent", []), path)
        save_settings(self.cfg)
        self.root.title(f"{appinfo.APP_TITLE} - {os.path.basename(path)}"
                        + (f" - preset: {self.preset['obj'].name}" if self.preset else ""))

    def open_workspace(self, path=None):
        path = path or filedialog.askopenfilename(parent=self.root, title="Open workspace",
                                                  filetypes=[("mbtool workspace", "*" + mon.WS_EXT),
                                                             ("All files", "*.*")])
        if not path:
            return None
        try:
            ws, warnings = mon.load_workspace(path)
        except (OSError, ValueError) as e:
            self._status(f"Cannot open workspace: {e}", "bad")
            return None
        warnings += mon.workspace_problems(ws, [d for d, _ in list_com_ports()])
        self.apply_workspace(ws)
        self._ws_opened(path)
        if warnings:
            self._status(f"Workspace loaded with {len(warnings)} problem(s): {warnings[0]}", "warn")
            self.ws_msg = MessageDialog(self, "Workspace loaded with problems",
                                        "Some parts of the workspace could not be restored as saved:\n\n"
                                        + "\n".join("•  " + w for w in warnings), help="workspace")
        else:
            self._status(f"Workspace loaded: {path}", "ok")
        return ws

    def apply_workspace(self, ws):
        """Restore a normalised workspace. Never raises for missing files / ports (they show as errors)."""
        self.stop_all_devices()
        self.disconnect(quiet=True)
        self.stop_log()
        m = ws["main"]
        self.cfg["conn"] = dict(m["conn"])
        pc = m["poll"]
        for k in ("unit", "addr", "qty", "scan", "fmt"):
            if isinstance(pc.get(k), str):
                self.pv[k].set(pc[k])
        fc = str(pc.get("fc", "3"))
        if fc.isdigit() and int(fc) in FUNCS:
            self.pv["func"].set(FUNCS[int(fc)])
        disp = ws.get("display", {})
        base = pc.get("base") if str(pc.get("base", "")).strip() else disp.get("address_base", pc.get("plc", False))
        self.set_addr_base(base)
        rows = str(disp.get("rows_per_column", pc.get("rows", "Auto")))
        self.rows_var.set(rows if rows in ROW_CHOICES else "Auto")
        if isinstance(pc.get("multi_cols"), list):  # multi-type view (workspaces from before it keep the current one)
            self.multi_cols = mon.norm_multi_cols(pc["multi_cols"])
            self.multi_align = mon.norm_multi_align(pc.get("multi_align"))
            self.mlayout_key = None
        if isinstance(pc.get("multi"), bool):
            self.multi_var.set(pc["multi"])
        self._validate()
        if m["preset"] and os.path.isfile(m["preset"]):
            try:
                data, pr = read_preset_file(m["preset"])
                self.use_preset(pr, m["preset"], data, load_alarms=False)
                if m["group"] in ["All"] + pr.groups():
                    self.pview.group.set(m["group"])
            except (OSError, ValueError, PresetError) as e:
                self._status(f"Preset {m['preset']} not usable: {e}", "bad")
                self.leave_preset()
        else:
            self.leave_preset()
        self.devices = [mon.norm_device(d) for d in ws["devices"]]
        self.dev_status = {}
        name = disp.get("tab_name")
        if isinstance(name, str) and name.strip() and not self._name_taken(name.strip()):
            self.cfg["tabs"]["main_name"] = name.strip()
        self.cfg["devices"]["gap_ms"] = str(ws["bus_gap_ms"])
        self.cfg["devices"]["log_on"] = bool(ws["device_log"]["enabled"])
        self.cfg["devices"]["log_folder"] = ws["device_log"]["folder"]
        dcfg = ws.get("devices_cfg") or {}
        self.cfg["devices"]["scan_mode"] = mon.norm_scan_mode(dcfg.get("scan_mode"))
        self.cfg["devices"]["cycle_target_ms"] = str(dcfg.get("cycle_target_ms", "") or "")
        self.cfg["devices"]["calc"] = mon.norm_calc(dcfg.get("calc"))
        self._norm_dev_cfg()
        self._apply_scan_cfg()  # all devices are stopped here: the next start uses the workspace's mode
        self.cycle_stats.reset()
        self.save_devices()
        rules = []
        for d in ws["alarms"]:
            r, _e = mon.validate_rule(d)
            if r:
                rules.append(r)
        self.alarms.set_rules(rules)
        self.alarm_beep.set(bool(ws["alerts"]["beep"]))
        self.cfg["log"] = mon.norm_log_options(ws["log"])
        self.last_log_path = ws["log_file"]
        self.cfg["ai"] = mon.norm_ai_settings(ws.get("ai"))  # used at the next start of AI sharing
        if self.ai_win and self.ai_win.alive():
            self.ai_win.reload()
        if ws.get("busmap") is not None:  # older workspaces have none: the current map stays
            self.set_busmap(ws["busmap"], save=False)
        tr = ws["trend"]
        self.trend_cfg["window"], self.trend_cfg["ymode"] = tr["window"], tr["ymode"]
        src = tr["source"] if tr["source"] in self.source_names() else ""
        self.set_trend(src, tr["series"])
        wins = ws.get("windows", {})
        g = wins.get("main", {}).get("geometry")
        if g:
            try:
                self.root.geometry(g)
            except tk.TclError:
                pass
        openers = {"trend": self.open_trend, "alerts": self.open_alerts, "devices": self.open_devices,
                   "traffic": self.open_traffic, "busmap": self.open_busmap}
        for name, attr in self.WIN_ATTRS.items():
            win = getattr(self, attr)
            info = wins.get(name)
            if info and info.get("open"):
                openers[name]()
                win = getattr(self, attr)
                if info.get("geometry"):
                    try:
                        win.w.geometry(info["geometry"])
                    except tk.TclError:
                        pass
            elif win and win.alive():
                win.close()
        for name, attr in self.WIN_ATTRS.items():
            win = getattr(self, attr)
            if win and win.alive() and hasattr(win, "reload"):
                win.reload()
        self._alarms_changed()
        save_settings(self.cfg)
        self._refresh_conn_ui()
        if ws["devices_running"]:
            self.start_all_devices(load_alarms=False)
            if self.devices_win and self.devices_win.alive():
                self.devices_win.reload()
        self.tabstrip.redraw()
        keys = self.poll_tab_keys()
        act = disp.get("active_tab", 0)
        self.select_poll_tab(keys[act] if isinstance(act, int) and 0 <= act < len(keys) else None)
        if m["connected"]:
            self.connect()

    def close(self):
        if self.help_win and self.help_win.alive():
            self.help_win.save_state()  # size, position and last topic of the manual
        if self.ai is not None:
            self.ai.shutdown()  # stop the AI server first: no AI request may use the closing connection
        self.stop_log()
        self.stop_all_devices()
        self.stop_export()
        self.stop_demo()
        self.disconnect(quiet=True)
        if self.sim and self.sim.alive():
            self.sim.close()
        if self.busmap_win and self.busmap_win.alive():
            self.busmap_win.close()  # cancels its after() jobs and keeps the map in cfg
        try:
            self.cfg["geometry"] = self.root.geometry()
        except tk.TclError:
            pass
        self.cfg["alerts"]["beep"] = bool(self.alarm_beep.get())
        for tab in list(self.poll_tabs.values()):
            tab.flush()  # a poll definition still being typed is kept
        self.cfg["devices"]["list"] = self._saved_devices()
        self.cfg["tabs"]["active"] = self.active_tab_index()
        for tab in list(self.poll_tabs.values()):
            tab.destroy()
        self.poll_tabs.clear()
        save_settings(self.cfg)
        self.root.destroy()


class Dialog:
    """Common bits for Toplevel dialogs: transient, Esc closes, Enter = default action."""

    def __init__(self, app, title, modal=False):
        self.app = app
        self.w = w = tk.Toplevel(app.root)
        w.title(title)
        w.transient(app.root)
        w.resizable(False, False)
        w.bind("<Escape>", lambda e: self.close())
        w.protocol("WM_DELETE_WINDOW", self.close)
        if modal:
            w.grab_set()
        self.body = ttk.Frame(w, padding=12)
        self.body.pack(fill="both", expand=True)

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def place(self):
        r = self.app.root
        self.w.update_idletasks()
        x = r.winfo_rootx() + max((r.winfo_width() - self.w.winfo_reqwidth()) // 2, 0)
        y = r.winfo_rooty() + int(60 * self.app.s)
        self.w.geometry(f"+{x}+{y}")

    def close(self):
        if self.alive():
            self.w.destroy()


class MultiColumnsDialog(Dialog):
    """Checklist of the data types shown as columns in the multi-type view."""

    def __init__(self, app, current, apply, align=0, apply_align=None):
        super().__init__(app, "Multi-type view - columns")
        self.apply_fn = apply
        self.apply_align = apply_align
        self.align0 = mon.norm_multi_align(align)
        self.order = list(current)
        add_help_button(app, self.w, "poll.multi")
        ttk.Label(self.body, text="Show these data types as columns:").pack(anchor="w", pady=(0, 6))
        gf = ttk.Frame(self.body)
        gf.pack(fill="x")
        self.vars = {}
        for k, f in enumerate(mon.MULTI_FORMATS):
            v = tk.BooleanVar(value=f in current)
            self.vars[f] = v
            n = mon.multi_width(f)
            note = {1: "", 2: "  (2 registers)", 4: "  (4 registers)"}[n] + ("  (2 characters)" if f == "ASCII" else "")
            ttk.Checkbutton(gf, text=f + note, variable=v).grid(row=k % 6, column=k // 6, sticky="w", padx=(0, 18), pady=1)
        af = ttk.Frame(self.body)
        af.pack(fill="x", pady=(8, 0))
        ttk.Label(af, text="Pair alignment:").pack(side="left")
        self.align = tk.StringVar(value=str(self.align0))
        cb = ttk.Combobox(af, textvariable=self.align, values=["0", "1", "2", "3"], width=3, state="readonly")
        cb.pack(side="left", padx=(6, 8))
        ttk.Label(af, text="Long / Float / Double show their value on the row where they start. 0 = the first\n"
                           "polled register starts a pair; 1 = the second one does (32-bit values at odd registers).",
                  style="Hint.TLabel", justify="left").pack(side="left")
        Tooltip(cb, "Which register starts a 2- or 4-register value, counting from the first polled register")
        self.l_err = ttk.Label(self.body, text=" ", style="Hint.TLabel")
        self.l_err.pack(anchor="w", pady=(6, 0))
        bf = ttk.Frame(self.body)
        bf.pack(fill="x", pady=(6, 0))
        ttk.Button(bf, text="Default", command=self.default).pack(side="left")
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        self.b_ok = ttk.Button(bf, text="OK", command=self.ok, default="active")
        self.b_ok.pack(side="right", padx=(0, 6))
        self.w.bind("<Return>", lambda e: self.ok())
        self.place()

    def default(self):
        self.order = list(mon.MULTI_DEFAULT)
        for f, v in self.vars.items():
            v.set(f in mon.MULTI_DEFAULT)
        self.align.set("0")

    def picked(self):
        """Checked types: the current columns keep their order, newly checked ones follow in list order."""
        on = [f for f in mon.MULTI_FORMATS if self.vars[f].get()]
        return [f for f in self.order if f in on] + [f for f in on if f not in self.order]

    def ok(self):
        cols = self.picked()
        if not cols:
            self.l_err.configure(text="Pick at least one data type")
            return
        self.apply_fn(cols)
        if self.apply_align:
            self.apply_align(self.align.get())
        self.close()


def form_row(parent, r, label, widget, tip=None, hint=True):
    """Label | widget | hint (to the right). Returns (label, hint)."""
    lab = ttk.Label(parent, text=label)
    lab.grid(row=r, column=0, sticky="e", padx=(0, 8), pady=3)
    widget.grid(row=r, column=1, sticky="w", pady=3)
    h = ttk.Label(parent, text="", style="Hint.TLabel") if hint else None
    if h:
        h.grid(row=r, column=2, sticky="w", padx=(8, 0))
    if tip:
        Tooltip(widget, tip)
    return lab, h


class SetupDialog(Dialog):
    MODES = [("TCP", "Modbus TCP/IP"), ("RTU", "Modbus RTU (serial)"), ("ASCII", "Modbus ASCII (serial)")]

    def __init__(self, app, conn=None, on_done=None, title="Connection Setup", parent=None):
        super().__init__(app, title, modal=True)
        if parent is not None:
            self.w.transient(parent)
        self.on_done = on_done
        c = conn or app.cfg["conn"]
        b = self.body
        self.v = {k: tk.StringVar(value=c[k]) for k in c}
        self.v["parity"].set(PARITY_NAMES.get(c["parity"], "None"))
        mf = ttk.Frame(b)
        mf.pack(fill="x", pady=(0, 8))
        ttk.Label(mf, text="Connection:").pack(side="left", padx=(0, 8))
        for val, text in self.MODES:
            ttk.Radiobutton(mf, text=text, value=val, variable=self.v["mode"],
                            command=self._mode).pack(side="left", padx=(0, 10))
        self.hints = {}
        # TCP
        self.tcp = ttk.LabelFrame(b, text="TCP/IP", padding=10)
        _, self.hints["host"] = form_row(self.tcp, 0, "IP address / host", ttk.Entry(self.tcp, textvariable=self.v["host"], width=24))
        _, self.hints["port"] = form_row(self.tcp, 1, "Port", ttk.Entry(self.tcp, textvariable=self.v["port"], width=8),
                                         "TCP port of the slave (502 is the Modbus standard)")
        # serial
        self.ser = ttk.LabelFrame(b, text="Serial port", padding=10)
        pf = ttk.Frame(self.ser)
        self.com = ttk.Combobox(pf, textvariable=self.v["com"], width=34)
        self.com.pack(side="left")
        ttk.Button(pf, text="Refresh", command=self.refresh_ports, width=8).pack(side="left", padx=(6, 0))
        _, self.hints["com"] = form_row(self.ser, 0, "COM port", pf)
        Tooltip(self.com, "Serial port. The list shows the ports Windows reports; you may also type a name.")
        cb_baud = ttk.Combobox(self.ser, textvariable=self.v["baud"], values=BAUDS, width=10)
        _, self.hints["baud"] = form_row(self.ser, 1, "Baud rate", cb_baud)
        _, self.hints["data"] = form_row(self.ser, 2, "Data bits", ttk.Combobox(self.ser, textvariable=self.v["data"], values=["7", "8"], width=10, state="readonly"),
                                         "RTU needs 8 data bits; ASCII commonly uses 7")
        cb_par = ttk.Combobox(self.ser, textvariable=self.v["parity"], values=list(PARITY_NAMES.values()), width=10,
                              state="readonly")
        _, self.hints["parity"] = form_row(self.ser, 3, "Parity", cb_par)
        cb_stop = ttk.Combobox(self.ser, textvariable=self.v["stop"], values=["1", "2"], width=10, state="readonly")
        _, self.hints["stop"] = form_row(self.ser, 4, "Stop bits", cb_stop)
        for wdg, key in ((mf, "setup.mode"), (self.tcp, "setup.tcp"), (self.ser, "setup.serial"),
                         (cb_baud, "setup.baud"), (cb_par, "setup.parity"), (cb_stop, "setup.stop")):
            help_key(wdg, key)
        self.help_fields = {"mode": mf, "tcp": self.tcp, "serial": self.ser, "baud": cb_baud, "parity": cb_par,
                            "stop": cb_stop}
        self._help = lambda: "setup.tcp" if self.v["mode"].get() == "TCP" else "setup.serial"
        self.ser.columnconfigure(2, minsize=int(170 * app.s))
        self.tcp.columnconfigure(2, minsize=int(170 * app.s))
        # common
        self.common = ttk.Frame(b, padding=(10, 8, 10, 0))
        _, self.hints["timeout"] = form_row(self.common, 0, "Response timeout (ms)",
                                            ttk.Entry(self.common, textvariable=self.v["timeout"], width=8),
                                            "How long to wait for each reply (50-60000 ms)")
        bf = ttk.Frame(b)
        bf.pack(side="bottom", fill="x", pady=(12, 0))
        self.b_help = add_help_button(app, self.w, self._help, parent=bf, side="left")
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        ttk.Button(bf, text="Save" if on_done is None else "OK", command=lambda: self.ok(False)).pack(side="right", padx=6)
        self.b_ok = ttk.Button(bf, text="Connect", command=lambda: self.ok(True), default="active")
        if on_done is None:  # the device editor only collects settings
            self.b_ok.pack(side="right")
        Tooltip(self.b_ok, "Save these settings and connect   (Enter)")
        self.w.bind("<Return>", lambda e: self.ok(True))
        self.refresh_ports(initial=True)
        self._mode()
        self.place()

    def refresh_ports(self, initial=False):
        ports = list_com_ports()
        self.com.configure(values=[f"{d}  -  {desc}" if desc else d for d, desc in ports])
        if not ports:
            self.hints["com"].configure(text="No serial ports found - plug in the adapter, then Refresh",
                                        style="Hint.TLabel")
        else:
            self.hints["com"].configure(text=f"{len(ports)} port(s) found", style="Info.TLabel")
            cur = self.v["com"].get().split(" ")[0]
            if not initial or cur not in [d for d, _ in ports]:
                if cur not in [d for d, _ in ports]:
                    self.v["com"].set(ports[0][0])
        return ports

    def _mode(self):
        serial_mode = self.v["mode"].get() != "TCP"
        self.tcp.pack_forget()
        self.ser.pack_forget()
        self.common.pack_forget()
        (self.ser if serial_mode else self.tcp).pack(fill="x")
        self.common.pack(fill="x")
        if self.v["mode"].get() == "ASCII" and self.v["data"].get() == "8":
            pass  # leave the user's choice; hint only
        self.w.update_idletasks()

    def collect(self):
        """Validate; returns (conn dict, errors)."""
        v = self.v
        conn = {k: var.get().strip() for k, var in v.items()}
        conn["com"] = conn["com"].split(" ")[0]
        conn["parity"] = {n: k for k, n in PARITY_NAMES.items()}.get(conn["parity"], "N")
        errs = {}
        try:
            int_in(conn["timeout"], 50, 60000)
        except ValueError as e:
            errs["timeout"] = str(e)
        if conn["mode"] == "TCP":
            if not conn["host"]:
                errs["host"] = "required"
            try:
                int_in(conn["port"], 1, 65535)
            except ValueError as e:
                errs["port"] = str(e)
        else:
            if not conn["com"]:
                errs["com"] = "choose a COM port"
            try:
                int_in(conn["baud"], 50, 4000000)
            except ValueError as e:
                errs["baud"] = str(e)
            if conn["mode"] == "RTU" and conn["data"] != "8":
                errs["data"] = "RTU requires 8 data bits"
        return conn, errs

    def ok(self, connect):
        conn, errs = self.collect()
        keep = {"com": self.hints["com"].cget("text")} if "com" not in errs else {}
        for k, h in self.hints.items():
            if k in errs:
                h.configure(text=errs[k], style="Hint.TLabel")
            elif k not in keep:
                h.configure(text="")
        if errs:
            return
        self.close()
        if self.on_done:
            self.on_done(conn)
            return
        self.app.apply_conn(conn, connect)


class WriteDialog(Dialog):
    SINGLE = ["Unsigned", "Signed", "Hex", "Binary"]

    def __init__(self, app, target=None):
        # target: what the dialog writes through - the main window (default) or a PollTab (same attributes:
        # p, grid, mgrid, multi_shown, cell_addrs, addr_base(), run_bg(), write_done())
        self.t = target or app
        super().__init__(app, "Write" if target is None else f"Write - {target.name}")
        self.b_help = add_help_button(app, self.w, "write")
        b = self.body
        s = app.s
        self.v = {k: tk.StringVar(value=d) for k, d in dict(
            func=WRITE_FUNCS[6], unit="1", addr="0", coil="1", value="0", values="", fmt="Unsigned",
            and_="0xFFFF", or_="0x0000", raddr="0", rqty="1").items()}
        self.hints, self.rows = {}, []
        fcb = ttk.Combobox(b, textvariable=self.v["func"], values=list(WRITE_FUNCS.values()), width=34, state="readonly")
        form_row(b, 0, "Function", fcb, hint=False)
        fcb.bind("<<ComboboxSelected>>", lambda e: self._func())
        r = 1

        def row(key, label, widget, fcs, tip=None):
            nonlocal r
            lab, h = form_row(b, r, label, widget, tip)
            self.hints[key] = h
            self.rows.append((fcs, (lab, widget, h)))
            r += 1
            return lab
        ALL = (5, 6, 15, 16, 22, 23)
        row("unit", "Slave ID", ttk.Entry(b, textvariable=self.v["unit"], width=8), ALL)
        self.l_addr = row("addr", "Address", ttk.Entry(b, textvariable=self.v["addr"], width=10), ALL,
                          "Start address (follows the 'PLC addresses' setting of the main window)")
        cf = ttk.Frame(b)
        ttk.Radiobutton(cf, text="ON  (1)", value="1", variable=self.v["coil"]).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(cf, text="OFF  (0)", value="0", variable=self.v["coil"]).pack(side="left")
        row("coil", "Value", cf, (5,))
        row("value", "Value", ttk.Entry(b, textvariable=self.v["value"], width=16), (6,))
        row("raddr", "Read address", ttk.Entry(b, textvariable=self.v["raddr"], width=10), (23,))
        row("rqty", "Read quantity", ttk.Entry(b, textvariable=self.v["rqty"], width=8), (23,), "1-125 registers")
        self.l_vals = row("values", "Values", ttk.Entry(b, textvariable=self.v["values"], width=36), (15, 16, 23),
                          "Several values separated by spaces or commas")
        self.fmt_cb = ttk.Combobox(b, textvariable=self.v["fmt"], values=FORMATS, width=16, state="readonly")
        row("fmt", "Format", self.fmt_cb, (6, 16, 23), "How the typed values are converted to registers")
        row("and_", "AND mask", ttk.Entry(b, textvariable=self.v["and_"], width=10), (22,), "0x0000-0xFFFF")
        row("or_", "OR mask", ttk.Entry(b, textvariable=self.v["or_"], width=10), (22,), "0x0000-0xFFFF")
        self.l_help = ttk.Label(b, style="Info.TLabel", wraplength=int(380 * s), justify="left")
        self.l_help.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.l_res = ttk.Label(b, text="", wraplength=int(440 * s), justify="left")
        self.l_res.grid(row=r + 1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        b.columnconfigure(2, minsize=int(170 * s))
        bf = ttk.Frame(b)
        bf.grid(row=r + 2, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.b_send = ttk.Button(bf, text="Send", command=self.send, default="active")
        self.b_send.pack(side="left", padx=(0, 6))
        ttk.Button(bf, text="Close", command=self.close).pack(side="left")
        Tooltip(self.b_send, "Send the request   (Enter)")
        self.w.bind("<Return>", lambda e: self.send())
        self.prefill()
        self.place()

    def prefill(self):
        """Use the main window's (or the poll tab's) Slave ID and selected cell (if any)."""
        app = self.t
        p = app.p
        if p:
            self.v["unit"].set(str(p["unit"]))
        sel = app.grid.sel
        mg = app.mgrid
        if (app.multi_shown and p and not p.get("preset") and p["fc"] not in (1, 2)
                and mg.sel is not None and mg.sel < len(mg.labels) and mg.cols):
            a, fmt = p["addr"] + mg.sel, mg.sel_format()  # multi-type view: the selected cell's row and type
            cur = mg.values[mg.sel][mg.sel_col]
            if fmt == "ASCII":
                raw = mg.raw[mg.sel]
                fmt, cur = "Hex", (f"0x{raw:04X}" if isinstance(raw, int) else "")
            if cur == mon.MULTI_NONE:
                cur = ""
            fc = 6 if fmt_width(fmt) == 1 else 16
            self.v["func"].set(WRITE_FUNCS[fc])
            self.v["addr"].set(addr_label(fc, a, p["plc"]))
            self.v["fmt"].set(fmt)
            self.v["value" if fc == 6 else "values"].set(cur)
        elif p and not p.get("preset") and sel is not None and sel < len(app.cell_addrs):
            a = app.cell_addrs[sel]
            cur = app.grid.values[sel]
            if p["fc"] in (1, 2):
                self.v["func"].set(WRITE_FUNCS[5])
                self.v["addr"].set(addr_label(5, a, p["plc"]))
                self.v["coil"].set("0" if cur == "1" else "1")
            else:
                fc = 6 if fmt_width(p["fmt"]) == 1 else 16
                self.v["func"].set(WRITE_FUNCS[fc])
                self.v["addr"].set(addr_label(fc, a, p["plc"]))
                self.v["fmt"].set(p["fmt"])
                self.v["value" if fc == 6 else "values"].set(cur)
        self._func()

    def _fc(self):
        return int(self.v["func"].get()[:2])

    def _func(self):
        fc = self._fc()
        for fcs, widgets in self.rows:
            for wd in widgets:
                if fc in fcs:
                    wd.grid()
                else:
                    wd.grid_remove()
        self.l_addr.configure(text="Write address" if fc == 23 else "Address")
        self.l_vals.configure(text="Values (0/1)" if fc == 15 else "Values")
        self.fmt_cb.configure(values=self.SINGLE if fc == 6 else FORMATS)
        if fc == 6 and self.v["fmt"].get() not in self.SINGLE:
            self.v["fmt"].set("Unsigned")
        self.l_help.configure(text={
            5: "Sets one coil ON or OFF.",
            6: "Writes one 16-bit holding register.",
            15: "Writes several coils, e.g.  1 0 1 1",
            16: "Writes several holding registers, e.g.  10 20 30   (Float/Long values use 2 registers each)",
            22: "Result = (Current AND And_Mask) OR (Or_Mask AND NOT And_Mask)",
            23: "Writes the values, then reads 'Read quantity' registers from 'Read address' in one request.",
        }[fc])
        self.l_res.configure(text="")
        for h in self.hints.values():
            h.configure(text="")

    def _result(self, text, kind):
        self.l_res.configure(text=text, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel"}.get(kind, "SInfo.TLabel"))

    def send(self):
        fc, plc, v = self._fc(), self.t.addr_base(), self.v
        errs, val = {}, {}

        def chk(k, fn):
            try:
                val[k] = fn(v[k].get())
            except ValueError as e:
                errs[k] = str(e)
        chk("unit", lambda t: int_in(t, 0, 255))
        chk("addr", lambda t: parse_addr(t, fc, plc))
        fmt = v["fmt"].get()
        if fc == 6:
            def one(t):
                try:
                    return encode_value(t.strip(), fmt)[0]
                except ValueError:
                    raise ValueError(f"not a valid {fmt} value")
            chk("value", one)
        elif fc == 15:
            def bits(t):
                toks = split_values(t)
                if not 1 <= len(toks) <= 1968:
                    raise ValueError("enter 1-1968 values (0/1)")
                return [parse_bit(x) for x in toks]
            chk("values", bits)
        elif fc in (16, 23):
            def words(t):
                w = encode_many(t, fmt)
                lim = 123 if fc == 16 else 121
                if len(w) > lim:
                    raise ValueError(f"too many registers ({len(w)} > {lim})")
                return w
            chk("values", words)
        elif fc == 22:
            chk("and_", lambda t: int_in(t, 0, 0xFFFF))
            chk("or_", lambda t: int_in(t, 0, 0xFFFF))
        if fc == 23:
            chk("raddr", lambda t: parse_addr(t, 23, plc))
            chk("rqty", lambda t: int_in(t, 1, 125))
        n = len(val.get("values", [])) or 1
        if "addr" in val and val["addr"] + n > 65536:
            errs["values"] = "runs past address 65535"
        for k, h in self.hints.items():
            h.configure(text=errs.get(k, ""))
        if errs:
            self._result("Please fix the fields marked in red.", "bad")
            return
        unit, addr = val["unit"], val["addr"]
        lab = addr_label(fc, addr, plc)
        if fc == 5:
            on = v["coil"].get() == "1"
            fn, desc = (lambda m: m.write_coil(unit, addr, on)), f"FC05 coil {lab} = {'ON' if on else 'OFF'}"
        elif fc == 6:
            fn, desc = (lambda m: m.write_register(unit, addr, val["value"])), f"FC06 register {lab} = {v['value'].get().strip()}"
        elif fc == 15:
            fn, desc = (lambda m: m.write_coils(unit, addr, val["values"])), f"FC15 {len(val['values'])} coils from {lab}"
        elif fc == 16:
            fn, desc = (lambda m: m.write_registers(unit, addr, val["values"])), f"FC16 {len(val['values'])} registers from {lab}"
        elif fc == 22:
            fn, desc = (lambda m: m.mask_write(unit, addr, val["and_"], val["or_"])), \
                f"FC22 register {lab} AND 0x{val['and_']:04X} OR 0x{val['or_']:04X}"
        else:
            fn = lambda m: m.read_write(unit, val["raddr"], val["rqty"], addr, val["values"])
            desc = f"FC23 {len(val['values'])} registers to {lab}, read {val['rqty']} from {addr_label(23, val['raddr'], plc)}"
        self._result("Sending...", "info")
        self.t.run_bg(fn, lambda r, err: self._done(fc, fmt, desc, r, err))

    def _done(self, fc, fmt, desc, r, err):
        self.t.write_done(desc, err)
        if not self.alive():
            return
        if err:
            self._result(f"Failed: {err}", "bad")
        elif fc == 23 and r is not None:
            shown = ", ".join(t for _i, t in format_registers(r, fmt if fmt in FORMATS else "Unsigned"))
            self._result(f"OK - {desc}\nRead back: {shown}", "ok")
        else:
            self._result(f"OK - {desc}", "ok")


SIG_MODES = {"Static (fixed values)": "static", "Random walk": "random", "Loop (ramp up, wrap around)": "loop",
             "Sine wave": "sine", "Device behaviour (by device type)": "profile", "Preset's own patterns": "preset"}
SIG_MODE_LABEL = {v: k for k, v in SIG_MODES.items()}
SIG_AUTO = "Auto (guessed)"
SIG_SPEEDS = ["0.5x", "1x", "2x", "5x", "10x"]
SIG_HELP = ("Static: values stay at the preset's sim values.   Random walk: each value wanders inside its range.   "
            "Loop: each value ramps from min to max and wraps around.   Sine wave: smooth waves, each point out of "
            "phase.   Device behaviour: moves like the device type (drive speed cycle, meter load, tank level...).   "
            "Preset's own patterns: only what the preset's sim_pattern fields describe.")
# pattern dialog: field -> (label, tooltip); which fields each kind uses (required ones first)
SIG_FIELDS = {"value": ("Value", "The fixed value (engineering units)"),
              "min": ("Min", "Lowest value (engineering units)"),
              "max": ("Max", "Highest value (engineering units)"),
              "period": ("Period (s)", "Seconds for one full cycle (default 20)"),
              "step": ("Step", "Random walk: typical move per second as a fraction of the span\n(0.05 = 5 %, default)"),
              "noise": ("Noise", "Random noise added, as a fraction of the span (0.01 = 1 %).\nEmpty = none."),
              "hold": ("Hold (s)", "Seconds each value of the list is held (default 3)"),
              "rate": ("Rate (/s)", "How much the counter increases per second"),
              "start": ("Start", "Counter start value (default: Min)"),
              "values": ("Values", "Comma-separated list the point steps through, e.g. 0, 1, 2"),
              "signal": ("Follows", "load: a slowly varying process / mains load\n"
                                    "speed: a motor cycle (stop, accelerate, run, decelerate)")}
SIG_KIND_FIELDS = {"fixed": (("value",), ()),
                   "noise": (("min", "max"), ("noise",)),
                   "random_walk": (("min", "max"), ("step", "noise")),
                   "sine": (("min", "max"), ("period", "noise")),
                   "ramp": (("min", "max"), ("period", "noise")),
                   "triangle": (("min", "max"), ("period", "noise")),
                   "square": (("min", "max"), ("period",)),
                   "sequence": (("values",), ("hold",)),
                   "counter": (("rate",), ("start", "min", "max")),
                   "follow": (("signal", "min", "max"), ("noise",))}


def sig_num_text(v):
    """Compact number text: 12.5, 230.01, 1.2e+07, 3."""
    if v is None:
        return ""
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        return str(v)
    if abs(v) >= 1e7 or (v and abs(v) < 1e-3):
        return f"{v:.4g}"
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def sig_value_text(point, v):
    """Engine value -> what the Signals list shows (enum name when the raw value is a key)."""
    if v is None or point is None:
        return ""
    if point.type == "bool":
        return "1 (on)" if round(v) else "0 (off)"
    try:  # what a master reads back: clipped / rounded to the register type
        v = float(point.decode(point.encode_sim(v))["value"])
    except Exception:
        pass
    if point.enum:
        try:
            raw = int(round((v - point.offset) / point.scale)) if point.scale else int(round(v))
            if str(raw) in point.enum:
                return f"{raw} = {point.enum[str(raw)]}"
        except (TypeError, ValueError, OverflowError):
            pass
    return f"{sig_num_text(v)} {point.unit}".strip()


def parse_sig_spec(kind, texts):
    """Pattern dialog fields -> spec dict for SignalEngine.set_override. Raises ValueError((field, message))."""
    if kind not in simsig.KINDS:
        raise ValueError(("kind", f"choose one of {', '.join(simsig.KINDS)}"))
    need, opt = SIG_KIND_FIELDS[kind]
    spec = {"kind": kind}
    for k in need + opt:
        t = str(texts.get(k, "")).strip()
        if not t:
            if k in need:
                raise ValueError((k, "required"))
            continue
        if k == "signal":
            if t not in ("load", "speed"):
                raise ValueError((k, "load or speed"))
            spec[k] = t
        elif k == "values":
            try:
                spec[k] = [float(x) for x in t.replace(";", ",").split(",") if x.strip()]
            except ValueError:
                raise ValueError((k, "numbers separated by commas"))
            if not spec[k]:
                raise ValueError((k, "required"))
        else:
            try:
                x = float(t)
            except ValueError:
                raise ValueError((k, "not a number"))
            if x != x or x in (float("inf"), float("-inf")):
                raise ValueError((k, "not a number"))
            if k in ("period", "hold") and x <= 0:
                raise ValueError((k, "must be > 0"))
            if k in ("step", "noise") and not 0 <= x <= 1:
                raise ValueError((k, "0 .. 1 (fraction of the span)"))
            spec[k] = x
    if "min" in spec and "max" in spec and spec["min"] > spec["max"]:
        raise ValueError(("max", "must be >= Min"))
    return spec


class SimPresetPicker(Dialog):
    """Signals tab: choose a preset from the library tree (or any JSON file) for the simulator."""

    def __init__(self, app, parent, on_pick, current="", title="Load preset into the simulator",
                 fail_text="Could not load this file - see the simulator status line"):
        super().__init__(app, title)
        self.fail_text = fail_text
        self.w.transient(parent)
        self.w.resizable(True, True)
        s = app.s
        self.on_pick, self.paths = on_pick, {}
        b = self.body
        ttk.Label(b, text=f"Presets in {preset_dir()}   -   double-click to load one",
                  style="Info.TLabel").pack(fill="x", pady=(0, 6))
        bf = ttk.Frame(b)
        bf.pack(side="bottom", fill="x", pady=(8, 0))
        tf = ttk.Frame(b)
        tf.pack(fill="both", expand=True)
        self.tree = t = ttk.Treeview(tf, columns=("name", "status"), show="tree headings", selectmode="browse",
                                     height=14, style=tree_style(app.root, s))
        t.heading("#0", text="File", anchor="w")
        t.heading("name", text="Name", anchor="w")
        t.heading("status", text="Status", anchor="w")
        t.column("#0", width=int(340 * s), stretch=False)
        t.column("name", width=int(400 * s), stretch=True)
        t.column("status", width=int(110 * s), stretch=False)
        t.tag_configure("verified", foreground="#1e7b34")
        t.tag_configure("unverified", foreground="#8a5a00")
        t.tag_configure("invalid", foreground=COL["bad"])
        t.tag_configure("folder", foreground="#59606b")
        vsb = ttk.Scrollbar(tf, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vsb.set)
        t.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        folders = {}
        for rel, fname, path in scan_presets(preset_dir()):
            parent_iid = ""
            if rel != ".":
                if rel not in folders:
                    folders[rel] = t.insert("", "end", text=rel.replace(os.sep, "/") + "/", open=True,
                                            tags=("folder",))
                parent_iid = folders[rel]
            try:
                _d, pr = read_preset_file(path)
                st, vals = ("verified", (pr.name, "✔ verified")) if pr.verified else \
                    ("unverified", (pr.name, "⚠ unverified"))
            except (OSError, ValueError, TypeError, KeyError):
                st, vals = "invalid", ("(cannot load)", "✖ invalid")
            iid = t.insert(parent_iid, "end", text=fname, values=vals, tags=(st,))
            self.paths[iid] = path
            if current and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(current)):
                t.selection_set(iid)
                t.see(iid)
        ttk.Button(bf, text="Browse file...", command=self.browse).pack(side="left")
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        ttk.Button(bf, text="Load", command=self.load, default="active").pack(side="right", padx=(0, 6))
        self.l_msg = ttk.Label(bf, text="", style="SBad.TLabel")
        self.l_msg.pack(side="left", padx=(12, 0))
        t.bind("<Double-1>", lambda e: t.identify_row(e.y) in self.paths and self.load())
        self.w.bind("<Return>", lambda e: self.load())
        self.place()

    def pick(self, path):
        if self.on_pick(path):
            self.close()
            return True
        self.l_msg.configure(text=self.fail_text)
        return False

    def load(self):
        sel = self.tree.selection()
        path = self.paths.get(sel[0]) if sel else None
        if not path:
            self.l_msg.configure(text="Select a preset file first")
            return False
        return self.pick(path)

    def browse(self):
        path = filedialog.askopenfilename(parent=self.w, title="Open preset JSON file", initialdir=preset_dir(),
                                          filetypes=[("Preset JSON", "*.json"), ("All files", "*.*")])
        return self.pick(path) if path else False


class SigPatternDialog(Dialog):
    """Signals tab: give one point its own pattern (SignalEngine.set_override)."""

    def __init__(self, sim, name):
        super().__init__(sim.app, "Edit signal pattern")
        self.w.transient(sim.w)
        self.sim, self.name = sim, name
        eng = sim.engine
        pt = eng.preset.by_name[name]
        plan = next((pl for pl in eng.plans if pl.point.name == name), None)
        spec = dict(eng.overrides.get(name) or (plan.pattern.spec if plan else {}) or {"kind": "fixed"})
        b = self.body
        ttk.Label(b, text=name, style="Conn.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(b, text=f"Unit: {pt.unit or '-'}    Now: {plan.label if plan else '-'}"
                          f"{'    (own pattern)' if name in eng.overrides else ''}",
                  style="Info.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.kind = tk.StringVar(value=spec.get("kind", "fixed") if spec.get("kind") in simsig.KINDS else "fixed")
        cb = ttk.Combobox(b, textvariable=self.kind, values=list(simsig.KINDS), width=14, state="readonly")
        form_row(b, 2, "Kind", cb, "fixed, noise, random_walk, sine, ramp (sawtooth), triangle, square,\n"
                                   "sequence (steps through a list), counter (always increasing),\n"
                                   "follow (tracks the device's load or motor speed)")
        self.vars, self.rows = {}, {}
        dmin = pt.min if pt.min is not None else None
        dmax = pt.max if pt.max is not None else None
        for r, (k, (lab, tip)) in enumerate(SIG_FIELDS.items(), start=3):
            val = spec.get(k)
            if val is None and k == "min":
                val = dmin
            if val is None and k == "max":
                val = dmax
            if k == "values":
                txt = ", ".join(sig_num_text(x) for x in (val or []))
            elif k == "signal":
                txt = str(val or "load")
            else:
                txt = sig_num_text(val) if isinstance(val, (int, float)) else ("" if val is None else str(val))
            self.vars[k] = v = tk.StringVar(value=txt)
            if k == "signal":
                wd = ttk.Combobox(b, textvariable=v, values=["load", "speed"], width=10, state="readonly")
            else:
                wd = ttk.Entry(b, textvariable=v, width=28 if k == "values" else 12)
            lab_w, hint = form_row(b, r, lab, wd, tip)
            self.rows[k] = (lab_w, wd, hint)
        b.columnconfigure(2, minsize=int(170 * sim.app.s))
        self.l_msg = ttk.Label(b, text="", style="SBad.TLabel", wraplength=int(380 * sim.app.s))
        self.l_msg.grid(row=20, column=0, columnspan=3, sticky="w", pady=(6, 0))
        bf = ttk.Frame(b)
        bf.grid(row=21, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(bf, text="Reset to mode", command=self.reset).pack(side="left")
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        ttk.Button(bf, text="OK", command=self.ok, default="active").pack(side="right", padx=(0, 6))
        self.kind.trace_add("write", lambda *_: self._show_fields())
        self.w.bind("<Return>", lambda e: self.ok())
        self._show_fields()
        self.place()

    def _show_fields(self):
        need, opt = SIG_KIND_FIELDS.get(self.kind.get(), ((), ()))
        for k, ws in self.rows.items():
            for wd in ws:
                if wd is not None:
                    (wd.grid() if k in need + opt else wd.grid_remove())
            if ws[2] is not None:
                ws[2].configure(text="" if k in need else ("(optional)" if k in opt else ""), style="Info.TLabel")

    def ok(self):
        try:
            spec = parse_sig_spec(self.kind.get(), {k: v.get() for k, v in self.vars.items()})
        except ValueError as e:
            field, msg = e.args[0] if isinstance(e.args[0], tuple) else ("", str(e))
            self._show_fields()
            if field in self.rows and self.rows[field][2] is not None:
                self.rows[field][2].configure(text=msg, style="Hint.TLabel")
            label = SIG_FIELDS.get(field, (field,))[0]
            self.l_msg.configure(text=f"{label}: {msg}")
            self.sim._status(f"Pattern not applied - {label}: {msg}", "bad")
            return False
        err = self.sim.set_sig_override(self.name, spec)
        if err:
            self.l_msg.configure(text=err)
            return False
        self.close()
        return True

    def reset(self):
        err = self.sim.set_sig_override(self.name, None)
        if err:
            self.l_msg.configure(text=err)
            return False
        self.close()
        return True


class SimWindow:
    """Slave simulator window: TCP (SimSlave) or RTU on a COM port (SerialSlave) over one data store."""

    def __init__(self, app):
        self.app = app
        s = app.s
        c = app.cfg["sim"]
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Slave Simulator")
        help_key(w, "slavesim")
        w.geometry(f"{int(820 * s)}x{int(640 * s)}")
        w.minsize(int(600 * s), int(380 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.store = SimSlave(listen=False)
        seed(self.store)
        self.delay_s = 0.0  # response delay (s) applied to every store this window serves
        self.tcp = self.ser = None
        self._states = []
        self.v = {k: tk.StringVar(value=c[k]) for k in
                  ("mode", "port", "com", "baud", "parity", "unit", "area", "addr", "qty", "fmt")}
        self.v["parity"].set(PARITY_NAMES.get(c["parity"], "None"))
        if self.v["area"].get() not in SIM_AREAS:
            self.v["area"].set("Holding Registers (4x)")
        self.remote = tk.BooleanVar(value=c["remote"])
        self.multi = tk.BooleanVar(value=bool(c.get("multi")))
        self.multi_shown, self.mkey = False, None
        self.view, self.cell_addrs = None, []
        # signals (moving values from a preset, see signals.py)
        self.engine = self.sig_preset = self.sig_job = None
        self.sig_path = ""
        self.sig_rows = {}  # tree iid -> point name
        self.sig_v = {"mode": tk.StringVar(value=SIG_MODE_LABEL.get(c["sig_mode"], SIG_MODE_LABEL["profile"])),
                      "device": tk.StringVar(value=c["sig_device"] if c["sig_device"] in simsig.DEVICE_TYPES
                                             else SIG_AUTO),
                      "speed": tk.StringVar(value=f"{c['sig_speed']}x" if f"{c['sig_speed']}x" in SIG_SPEEDS
                                            else "1x")}
        self.sig_pause = tk.BooleanVar(value=False)
        # toolbar
        tb = ttk.Frame(w, padding=(8, 6))
        tb.pack(fill="x")
        self.b_run = ttk.Button(tb, text="Start", width=10, command=self.toggle)
        self.b_run.pack(side="left")
        Tooltip(self.b_run, "Start / stop the simulated slave   (F5)")
        right = ttk.Frame(tb)
        right.pack(side="right")
        self.lamp = Lamp(right, s)
        self.lamp.set(COL["bad"])
        self.lamp.c.pack(side="left", padx=(0, 6))
        self.l_state = ttk.Label(right, text="Stopped", style="Conn.TLabel")
        self.l_state.pack(side="left")
        self.l_req = ttk.Label(right, text="Requests: 0", style="Info.TLabel")
        self.l_req.pack(side="left", padx=(12, 0))
        self.b_help = add_help_button(app, w, "slavesim", parent=right, side="left", padx=(12, 0))
        ttk.Separator(w).pack(fill="x")
        # slave settings
        lf = ttk.LabelFrame(w, text="Slave settings", padding=(10, 4, 10, 0))
        lf.pack(fill="x", padx=8, pady=(6, 2))
        self.hints = {}
        self.setting_widgets = []
        mode_cb = ttk.Combobox(lf, textvariable=self.v["mode"], values=["TCP", "RTU", "ASCII"], width=7,
                               state="readonly")
        vfield(lf, 0, "Mode", mode_cb, "TCP: listen on a TCP port.\nRTU / ASCII: answer on a serial (COM) port\n"
                                       "(or socket://127.0.0.1:PORT from 'mbtool.py vcom').")
        mode_cb.bind("<<ComboboxSelected>>", lambda e: self._mode())
        self.tcpf = ttk.Frame(lf)
        e_port = ttk.Entry(self.tcpf, textvariable=self.v["port"], width=8)
        self.hints["port"] = vfield(self.tcpf, 0, "TCP port", e_port, "Port to listen on (502 is standard)")
        cb_remote = ttk.Checkbutton(self.tcpf, text="Allow other PCs (0.0.0.0)", variable=self.remote)
        cb_remote.grid(row=1, column=1, sticky="w")
        Tooltip(cb_remote, "Off: only this PC can connect (127.0.0.1).\nOn: listen on all network interfaces.")
        self.serf = ttk.Frame(lf)
        cf = ttk.Frame(self.serf)
        self.com = ttk.Combobox(cf, textvariable=self.v["com"], width=26)
        self.com.pack(side="left")
        self.b_refresh = ttk.Button(cf, text="Refresh", width=8, command=self.refresh_ports)
        self.b_refresh.pack(side="left", padx=(4, 0))
        self.hints["com"] = vfield(self.serf, 0, "COM port", cf, "Serial port the RTU slave answers on")
        e_baud = ttk.Combobox(self.serf, textvariable=self.v["baud"], values=BAUDS, width=8)
        self.hints["baud"] = vfield(self.serf, 1, "Baud", e_baud)
        e_par = ttk.Combobox(self.serf, textvariable=self.v["parity"], values=list(PARITY_NAMES.values()), width=6, state="readonly")
        vfield(self.serf, 2, "Parity", e_par)
        self.tcpf.grid(row=0, column=1, rowspan=3, sticky="nw")
        self.serf.grid(row=0, column=1, rowspan=3, sticky="nw")
        e_unit = ttk.Entry(lf, textvariable=self.v["unit"], width=6)
        self.hints["unit"] = vfield(lf, 2, "Slave ID", e_unit, "Slave address this simulator answers.\nTCP: leave empty to answer every ID.")
        self.delay = tk.StringVar(value=str(c.get("delay", "0")))
        self.e_delay = ttk.Entry(lf, textvariable=self.delay, width=7)
        self.hints["delay"] = vfield(lf, 3, "Response delay (ms)", self.e_delay,
                                     "The simulator waits this long before every answer, like a real device that\n"
                                     "needs time to think (typical 5-50 ms). Applies at once, also while running.\n"
                                     "Use it with Devices > PLC cycle to measure realistic cycle times.")
        self.delay.trace_add("write", lambda *_: self._apply_delay())
        self.setting_widgets = [mode_cb, e_port, cb_remote, self.com, self.b_refresh, e_baud, e_par, e_unit]
        # data view
        df = ttk.LabelFrame(w, text="Data view", padding=(10, 4, 10, 0))
        df.pack(fill="x", padx=8, pady=(2, 2))
        self.vhints = {}
        spec = [("area", "Area", ttk.Combobox(df, textvariable=self.v["area"], values=list(SIM_AREAS), width=22, state="readonly")),
                ("addr", "Address (0-based)", ttk.Entry(df, textvariable=self.v["addr"], width=8)),
                ("qty", "Quantity", ttk.Entry(df, textvariable=self.v["qty"], width=7)),
                ("fmt", "Display", ttk.Combobox(df, textvariable=self.v["fmt"], values=FORMATS, width=15, state="readonly"))]
        for col, (k, lab, wd) in enumerate(spec):
            self.vhints[k] = vfield(df, col, lab, wd)
        self.fmt_cb = spec[3][2]
        self.vhints["fmt"].grid_remove()  # Display never has an error: its hint row holds the multi-type toggle
        self.cb_multi = ttk.Checkbutton(df, text="Multi-type view", variable=self.multi, command=self._validate_view)
        self.cb_multi.grid(row=2, column=3, sticky="nw", padx=(0, 12))
        Tooltip(self.cb_multi, MULTI_TIP.replace("FC 01 / 02", "coils / discrete inputs")
                + "\nColumns: Tools > Multi-type view columns... in the main window.")
        help_key(self.cb_multi, "poll.multi")
        for k in ("area", "addr", "qty", "fmt"):
            self.v[k].trace_add("write", lambda *_: self._validate_view())
        # status (packed before the grid so it stays visible)
        sb = ttk.Frame(w, padding=(8, 2, 8, 4))
        sb.pack(fill="x", side="bottom")
        ttk.Separator(w).pack(fill="x", side="bottom")
        self.l_msg = ttk.Label(sb, style="SInfo.TLabel")
        self.l_msg.pack(side="left")
        self.nb = ttk.Notebook(w)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(4, 4))
        dataf = ttk.Frame(self.nb)
        ttk.Label(dataf, text="Double-click a value (or select + Enter) to change it.  Values changed by a master flash "
                              "yellow.", style="Info.TLabel").pack(fill="x", padx=2, pady=(4, 2))
        self.grid = RegGrid(dataf, s, on_edit=self._edit)
        self.grid.frame.pack(fill="both", expand=True, pady=(0, 2))
        self.mgrid = MultiGrid(dataf, s, on_edit=self._medit)  # packed instead of the grid in the multi-type view
        self.nb.add(dataf, text="Data")
        self.traffic = TrafficView(self.nb, app, self._traffic_source, w,
                                   rx_tip="Slave side: RX = request received from the master,\n"
                                          "TX = response sent back.")
        self.nb.add(self.traffic.frame, text="Traffic  (RX = request received, TX = response sent)")
        self._build_signals()
        self._build_faults()
        w.bind("<F5>", lambda e: (self.toggle(), "break")[1])
        self.refresh_ports(initial=True)
        self._mode()
        self._validate_view()
        self._apply_delay()
        self._status("Stopped - choose TCP or RTU and press Start", "info")
        last = c["sig_preset"]
        if last and os.path.isfile(last):
            self.load_sig_preset(last, quiet=True)  # reload the last preset, but never auto-start the signals
        self._tick()
        self._sig_refresh()

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def _status(self, text, kind="info"):
        self.l_msg.configure(text=text, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel",
                                               "warn": "SWarn.TLabel"}.get(kind, "SInfo.TLabel"))

    def refresh_ports(self, initial=False):
        ports = list_com_ports()
        self.com.configure(values=[f"{d}  -  {desc}" if desc else d for d, desc in ports])
        if not ports:
            self.hints["com"].configure(text="No serial ports found", style="Hint.TLabel")
        else:
            self.hints["com"].configure(text=f"{len(ports)} port(s) found", style="Info.TLabel")
            if self.v["com"].get().split(" ")[0] not in [d for d, _ in ports]:
                self.v["com"].set(ports[0][0])
        return ports

    def _mode(self):
        if self.v["mode"].get() == "TCP":
            self.serf.grid_remove()
            self.tcpf.grid()
        else:
            self.tcpf.grid_remove()
            self.serf.grid()

    def running(self):
        return bool(self.tcp or self.ser)

    def _apply_delay(self):
        """Response delay entry -> SimSlave.response_delay of the shared store (live, also while running)."""
        try:
            ms = float(self.delay.get().strip().replace(",", ".") or "0")
            if not 0 <= ms <= 10000:
                raise ValueError
        except ValueError:
            self.hints["delay"].configure(text="0 - 10000 ms", style="Hint.TLabel")
            return None
        self.hints["delay"].configure(text=" ", style="Hint.TLabel")
        self.delay_s = ms / 1000.0
        self.store.response_delay = self.delay_s
        self.app.cfg["sim"]["delay"] = self.delay.get().strip() or "0"
        return ms

    def _traffic_source(self):
        return (self.store.traffic, f"Requests answered: {self.store.requests}", self.running(),
                "Simulator stopped - press Start; requests received (RX) and responses sent (TX) are listed here.")

    def toggle(self):
        self.stop() if self.running() else self.start()

    def start(self):
        if self.running():
            return
        v, errs = self.v, {}
        tcp = v["mode"].get() == "TCP"
        unit = port = baud = None
        com = v["com"].get().strip().split(" ")[0]
        try:
            unit = None if tcp and not v["unit"].get().strip() else int_in(v["unit"].get(), 0 if tcp else 1, 255 if tcp else 247)
        except ValueError as e:
            errs["unit"] = str(e)
        if tcp:
            try:
                port = int_in(v["port"].get(), 1, 65535)
            except ValueError as e:
                errs["port"] = str(e)
        else:
            if not com:
                errs["com"] = "choose a COM port"
            try:
                baud = int_in(v["baud"].get(), 50, 4000000)
            except ValueError as e:
                errs["baud"] = str(e)
        for k, h in self.hints.items():
            if k in errs:
                h.configure(text=errs[k], style="Hint.TLabel")
            elif k != "com":
                h.configure(text=" ")
        if errs:
            self._status("Fix the settings marked in red", "bad")
            return
        parity = {n: k for k, n in PARITY_NAMES.items()}.get(v["parity"].get(), "N")
        try:
            if tcp:
                host = "0.0.0.0" if self.remote.get() else "127.0.0.1"
                srv = SimSlave(host, port, unit)
                for a in ("coils", "discrete", "holding", "input"):
                    setattr(srv, a, getattr(self.store, a))
                srv.requests = self.store.requests
                srv.response_delay = self.delay_s
                srv.faults = self.store.faults
                self.store = self.tcp = srv
                where = f"TCP {host}:{port}, slave ID {'any' if unit is None else unit}"
            else:
                mode = v["mode"].get().lower()
                self.ser = SerialSlave(com, unit, baud, parity, store=self.store, mode=mode)
                where = f"{mode.upper()} {com} {baud} 8{parity}1, slave ID {unit}"
        except ImportError:
            self._status("pyserial is not installed (pip install pyserial)", "bad")
            return
        except Exception as e:
            msg = serial_error_text(com, e) if not tcp else (
                f"TCP port {port} is already in use" if getattr(e, "winerror", None) == 10048 or
                getattr(e, "errno", None) in (98, 10048) else str(e))
            self._status(f"Could not start: {msg}", "bad")
            self.lamp.set(COL["bad"])
            return
        self.where = where
        self._states = [(wd, str(wd.cget("state"))) for wd in self.setting_widgets]
        for wd in self.setting_widgets:
            wd.configure(state="disabled")
        self.b_run.configure(text="Stop")
        self.lamp.set(COL["ok"])
        self.l_state.configure(text="Running")
        self._status(f"Running on {where}", "ok")
        self._save_cfg()

    def stop(self):
        was = self.running()
        if self.tcp:
            self.tcp.stop()
            self.tcp = None
        if self.ser:
            self.ser.stop()
            self.ser = None
        for wd, state in self._states:
            wd.configure(state=state)
        self._states = []
        self.b_run.configure(text="Start")
        self.lamp.set(COL["bad"])
        self.l_state.configure(text="Stopped")
        if was:
            self._status("Stopped", "info")

    def _validate_view(self):
        v = self.v
        area = SIM_AREAS.get(v["area"].get(), "holding")
        bits = area in ("coils", "discrete")
        fmt = "Unsigned" if bits else v["fmt"].get()
        errs, val = {}, {}
        try:
            val["addr"] = int_in(v["addr"].get(), 0, 65535)
        except ValueError as e:
            errs["addr"] = str(e)
        try:
            val["qty"] = int_in(v["qty"].get(), 1, 2000)
        except ValueError as e:
            errs["qty"] = str(e)
        if not errs and val["addr"] + val["qty"] > 65536:
            errs["qty"] = f"max {65536 - val['addr']} here"
        if not errs and val["qty"] < fmt_width(fmt):
            errs["qty"] = f"{fmt} needs {fmt_width(fmt)}+"
        self.fmt_cb.configure(state="disabled" if bits else "readonly")
        self.cb_multi.configure(state="disabled" if bits else "normal")
        want = bool(self.multi.get()) and not bits
        if want != self.multi_shown:
            self.multi_shown, self.mkey = want, None
            old, new = (self.grid, self.mgrid) if want else (self.mgrid, self.grid)
            old.end_edit()
            old.frame.pack_forget()
            new.frame.pack(fill="both", expand=True, pady=(0, 2))
        set_hints(self.vhints, errs)
        if errs:
            return
        w = 1 if bits else fmt_width(fmt)
        offs = range(0, val["qty"] - w + 1, w)
        self.view = dict(area=area, bits=bits, fmt=fmt, addr=val["addr"], qty=val["qty"])
        self.cell_addrs = [val["addr"] + o for o in offs]
        self.grid.set_cells([str(a) for a in self.cell_addrs], 1 if bits else fmt_chars(fmt))
        self._refresh(flash=False)
        self._save_cfg()

    def _texts(self):
        vw = self.view
        data = getattr(self.store, vw["area"])[vw["addr"]:vw["addr"] + vw["qty"]]
        if vw["bits"]:
            return ["1" if x else "0" for x in data]
        return [t for _i, t in format_registers(list(data), vw["fmt"])]

    def _refresh(self, flash=True):
        if not self.view:
            return
        if not self.multi_shown:
            self.grid.set_values(self._texts(), flash=flash)
            return
        vw = self.view
        key = (vw["area"], vw["addr"], vw["qty"], tuple(self.app.multi_cols), self.app.multi_align)  # follow main window
        if key != self.mkey:
            self.mkey = key
            self.mgrid.set_structure([str(vw["addr"] + i) for i in range(vw["qty"])], self.app.multi_cols)
            flash = False
        data = list(getattr(self.store, vw["area"])[vw["addr"]:vw["addr"] + vw["qty"]])
        rows = mon.multi_rows(3, vw["addr"], data, self.mgrid.cols, 0, align=self.app.multi_align)
        self.mgrid.set_values([r[1:] for r in rows], raw=data, flash=flash)

    def _edit(self, i):
        if self.view:
            self.grid.start_edit(i, self._commit)

    def _medit(self, row, col):
        if self.view and self.multi_shown:
            self.mgrid.start_edit(row, col, self._mcommit)

    def _mcommit(self, row, col, text):
        """Multi-type view: store the cell text in its column's type from the row's register on."""
        vw, fmt = self.view, self.mgrid.cols[col]
        a = vw["addr"] + row
        try:
            words = mon.multi_encode(text, fmt)
            if a + len(words) > 65536:
                raise ValueError
        except ValueError:
            err = "enter 1 or 2 ASCII characters" if fmt == "ASCII" else f"'{text.strip()}' is not a valid {fmt} value"
            self._status(f"Address {a}: {err}", "bad")
            return err
        getattr(self.store, vw["area"])[a:a + len(words)] = words
        self._refresh(flash=False)
        self._status(f"Address {a} set to {text if fmt == 'ASCII' else text.strip()} ({fmt})", "ok")
        return None

    def _commit(self, i, text):
        vw = self.view
        a = self.cell_addrs[i]
        arr = getattr(self.store, vw["area"])
        try:
            if vw["bits"]:
                arr[a] = parse_bit(text)
            else:
                words = encode_value(text.strip(), vw["fmt"])
                arr[a:a + len(words)] = words
        except ValueError:
            err = "enter 0 or 1" if vw["bits"] else f"'{text.strip()}' is not a valid {vw['fmt']} value"
            self._status(f"Address {a}: {err}", "bad")
            return err
        self._refresh(flash=False)
        self._status(f"Address {a} set to {text.strip()}", "ok")
        return None

    def _tick(self):
        if not self.alive():
            return
        self.l_req.configure(text=f"Requests: {self.store.requests}")
        self._fault_counts()
        if self.ser and self.ser.error:
            err = self.ser.error
            self.stop()
            self._status(f"Serial port error, slave stopped: {err}", "bad")
        if self.grid.editor is None and self.mgrid.editor is None:
            self._refresh()
        self.w.after(400, self._tick)

    def _save_cfg(self):
        v = self.v
        self.app.cfg["sim"] = {k: v[k].get() for k in ("mode", "port", "com", "baud", "unit", "area",
                                                     "addr", "qty", "fmt")}
        self.app.cfg["sim"]["com"] = v["com"].get().split(" ")[0]
        self.app.cfg["sim"]["parity"] = {n: k for k, n in PARITY_NAMES.items()}.get(v["parity"].get(), "N")
        self.app.cfg["sim"]["remote"] = self.remote.get()
        self.app.cfg["sim"]["multi"] = bool(self.multi.get())
        self.app.cfg["sim"]["delay"] = self.delay.get().strip() or "0"
        sv = self.sig_v
        dev = sv["device"].get()
        self.app.cfg["sim"].update(sig_mode=SIG_MODES.get(sv["mode"].get(), "profile"),
                                   sig_device=dev if dev in simsig.DEVICE_TYPES else "",
                                   sig_speed=sv["speed"].get().rstrip("x"), sig_preset=self.sig_path)

    # ---------- faults tab ----------
    FAULTS = (("no_reply", "No reply", "say nothing - the master times out", "all"),
              ("slow", "Slow answer", "answer after the delay below (later than a short timeout)", "all"),
              ("exception", "Exception", "answer with the Modbus exception code below", "all"),
              ("bad_crc", "Bad CRC", "damaged frame: wrong CRC / LRC (like noise on the line)", "serial"),
              ("truncate", "Cut off", "send only part of the answer", "all"),
              ("garbage", "Garbage", "send random bytes instead of an answer", "all"),
              ("wrong_unit", "Wrong Slave ID", "answer as another Slave ID (like a duplicate address)", "all"),
              ("wrong_tid", "Wrong transaction ID", "TCP: answer with another transaction ID", "tcp"),
              ("drop_conn", "Drop connection", "TCP: close the connection instead of answering", "tcp"))
    FAULT_PRESETS = (("Noisy line", {"bad_crc": "2", "truncate": "1"}),
                     ("Flaky device", {"no_reply": "5"}),
                     ("Slow device", {"slow": "10"}),
                     ("Duplicate ID", {"wrong_unit": "20"}),
                     ("Unstable network", {"drop_conn": "3", "no_reply": "3"}))
    EXC_CODES = ("1 Illegal function", "2 Illegal data address", "3 Illegal data value", "4 Slave device failure",
                 "6 Slave device busy", "10 Gateway path unavailable", "11 Gateway target failed to respond")

    def _build_faults(self):
        s = self.app.s
        f = ttk.Frame(self.nb, padding=(8, 6, 8, 4))
        help_key(f, "slavesim.faults")
        self.nb.add(f, text="Faults")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.fault_on = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(top, text="Inject faults", variable=self.fault_on, command=self._apply_faults)
        cb.pack(side="left")
        Tooltip(cb, "Make this simulated slave misbehave on purpose, to see how your master / PLC / SCADA copes.\n"
                    "Off = every request is answered normally. Never saved: off again when the window opens.")
        self.l_fault = ttk.Label(top, text="", style="Warn.TLabel")
        self.l_fault.pack(side="left", padx=(10, 0))
        help_btn(self.app, top, "slavesim.faults", side="right", padx=(6, 0))
        pf = ttk.Frame(f)
        pf.pack(fill="x", pady=(6, 2))
        ttk.Label(pf, text="Quick:").pack(side="left", padx=(0, 4))
        for name, rates in self.FAULT_PRESETS:
            ttk.Button(pf, text=name, command=lambda r=rates: self.fault_preset(r)).pack(side="left", padx=(0, 4))
        ttk.Button(pf, text="Clear all", command=lambda: self.fault_preset({})).pack(side="left", padx=(8, 0))
        gf = ttk.Frame(f)
        gf.pack(fill="x", pady=(6, 0))
        for col, text in enumerate(("Fault", "% of requests", "Injected", "What it does")):
            ttk.Label(gf, text=text, style="Info.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 12))
        self.fault_v, self.fault_n, self.fault_hint = {}, {}, {}
        for r, (key, lab, what, where) in enumerate(self.FAULTS, start=1):
            ttk.Label(gf, text=lab).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=1)
            v = self.fault_v[key] = tk.StringVar(value="0")
            e = ttk.Spinbox(gf, from_=0, to=100, increment=1, textvariable=v, width=6)
            e.grid(row=r, column=1, sticky="w", padx=(0, 12), pady=1)
            self.fault_n[key] = ttk.Label(gf, text="0", style="Info.TLabel", width=8)
            self.fault_n[key].grid(row=r, column=2, sticky="w", padx=(0, 12))
            extra = {"serial": "  (RTU / ASCII only)", "tcp": "  (TCP only)"}.get(where, "")
            ttk.Label(gf, text=what + extra, style="Info.TLabel").grid(row=r, column=3, sticky="w")
            v.trace_add("write", lambda *_: self._apply_faults())
        of = ttk.Frame(f)
        of.pack(fill="x", pady=(8, 0))
        ttk.Label(of, text="Slow answer delay (ms):").pack(side="left")
        self.fault_slow = tk.StringVar(value="1500")
        ttk.Entry(of, textvariable=self.fault_slow, width=7).pack(side="left", padx=(4, 16))
        ttk.Label(of, text="Exception code:").pack(side="left")
        self.fault_exc = tk.StringVar(value=self.EXC_CODES[3])
        ttk.Combobox(of, textvariable=self.fault_exc, values=self.EXC_CODES, state="readonly", width=32).pack(
            side="left", padx=(4, 0))
        self.fault_slow.trace_add("write", lambda *_: self._apply_faults())
        self.fault_exc.trace_add("write", lambda *_: self._apply_faults())
        self.l_fhint = ttk.Label(f, text="", style="Hint.TLabel")
        self.l_fhint.pack(fill="x", pady=(4, 0))
        ttk.Label(f, text="Every request rolls the dice for each fault that has a rate; the first one that hits "
                          "decides what happens to that request. Watch the result in Traffic here and in the master "
                          "(Devices > Error types, Health test).",
                  style="Info.TLabel", wraplength=int(760 * s), justify="left").pack(fill="x", pady=(8, 0))
        self._apply_faults()

    def fault_rates(self):
        """{fault: probability 0..1} from the % fields; raises ValueError with a message for a bad field."""
        out = {}
        for key, lab, _what, _where in self.FAULTS:
            txt = self.fault_v[key].get().strip().replace(",", ".") or "0"
            try:
                pct = float(txt)
            except ValueError:
                raise ValueError(f"{lab}: not a number")
            if not 0 <= pct <= 100:
                raise ValueError(f"{lab}: 0 - 100 %")
            if pct:
                out[key] = pct / 100.0
        return out

    def _apply_faults(self):
        """Fields -> Faults on the store the slave answers from (live, also while running)."""
        try:
            rates = self.fault_rates()
            slow = float(self.fault_slow.get().strip().replace(",", ".") or "0")
            if not 0 <= slow <= 60000:
                raise ValueError("Slow answer delay: 0 - 60000 ms")
        except ValueError as e:
            self.l_fhint.configure(text=str(e))
            return None
        self.l_fhint.configure(text="")
        code = int(self.fault_exc.get().split(" ")[0])
        if not self.fault_on.get() or not rates:
            self.store.faults = None
            self.l_fault.configure(text="⚠ faults ON but every rate is 0 %" if self.fault_on.get() else "")
            return None
        old = self.store.faults
        fl = Faults(exception_code=code, slow_ms=slow, **rates)
        if old is not None:
            fl.counts.update(old.counts)
        self.store.faults = fl
        self.l_fault.configure(text="⚠ faults ON: " + ", ".join(
            f"{lab} {rates[k] * 100:g} %" for k, lab, _w, _x in self.FAULTS if k in rates))
        return fl

    def fault_preset(self, rates):
        for key, v in self.fault_v.items():
            v.set(str(rates.get(key, "0")))
        if rates:
            self.fault_on.set(True)
        self._apply_faults()

    def _fault_counts(self):
        fl = self.store.faults
        for key, lab in self.fault_n.items():
            n = str(fl.counts.get(key, 0)) if fl is not None else "0"
            if lab.cget("text") != n:
                lab.configure(text=n)

    # ---------- signals tab ----------
    def _build_signals(self):
        s = self.app.s
        f = ttk.Frame(self.nb, padding=(4, 4, 4, 2))
        self.nb.add(f, text="Signals")
        self.sig_tab = f
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.b_sig_load = ttk.Button(top, text="Load preset...", command=self.pick_sig_preset)
        self.b_sig_load.pack(side="left")
        Tooltip(self.b_sig_load, "Fill the simulator from a device preset (its 'sim' values)\n"
                                 "and let the values move like that device.")
        self.l_sig_name = ttk.Label(top, text="No preset loaded", style="Conn.TLabel")
        self.l_sig_name.pack(side="left", padx=(10, 0))
        self.l_sig_note = ttk.Label(f, text="", style="Warn.TLabel")
        self.l_sig_note.pack(fill="x", pady=(2, 0))
        cf = ttk.Frame(f)
        cf.pack(fill="x", pady=(4, 0))
        self.cb_sig_mode = ttk.Combobox(cf, textvariable=self.sig_v["mode"], values=list(SIG_MODES), width=31,
                                        state="readonly")
        vfield(cf, 0, "Mode", self.cb_sig_mode, "How the values move (see the line below)")
        self.cb_sig_dev = ttk.Combobox(cf, textvariable=self.sig_v["device"],
                                       values=[SIG_AUTO] + list(simsig.DEVICE_TYPES), width=14, state="readonly")
        self.h_sig_dev = vfield(cf, 1, "Device type", self.cb_sig_dev,
                                "Only for 'Device behaviour': which kind of device to imitate.\n"
                                "Auto uses the type guessed from the preset's name and units.")
        self.cb_sig_speed = ttk.Combobox(cf, textvariable=self.sig_v["speed"], values=SIG_SPEEDS, width=6,
                                         state="readonly")
        vfield(cf, 2, "Speed", self.cb_sig_speed, "Time scale: 2x = everything moves twice as fast")
        self.b_sig_run = ttk.Button(cf, text="Start signals", width=14, command=self.toggle_signals)
        self.b_sig_run.grid(row=1, column=3, sticky="w", padx=(0, 12))
        Tooltip(self.b_sig_run, "Start / stop moving the values.\nThe slave itself is started with Start (F5).")
        self.cb_sig_pause = ttk.Checkbutton(cf, text="Pause", variable=self.sig_pause, command=self._sig_paused)
        self.cb_sig_pause.grid(row=1, column=4, sticky="w")
        Tooltip(self.cb_sig_pause, "Freeze the values where they are (the engine keeps running)")
        for k in ("mode", "device"):
            self.sig_v[k].trace_add("write", lambda *_: self._sig_mode_changed())
        self.sig_v["speed"].trace_add("write", lambda *_: self._sig_speed_changed())
        self.l_sig_help = ttk.Label(f, text=SIG_HELP, style="Info.TLabel", justify="left",
                                    wraplength=int(760 * s))
        self.l_sig_help.pack(fill="x", pady=(0, 4))
        f.bind("<Configure>", lambda e: self.l_sig_help.configure(wraplength=max(e.width - int(16 * s), 200)))
        bf = ttk.Frame(f)
        bf.pack(side="bottom", fill="x", pady=(4, 0))
        self.b_sig_edit = ttk.Button(bf, text="Edit pattern...", command=self.edit_sig_pattern)
        self.b_sig_edit.pack(side="left")
        Tooltip(self.b_sig_edit, "Give the selected point its own pattern   (or double-click a row)")
        self.l_sig_left = ttk.Label(bf, text="", style="Info.TLabel")
        self.l_sig_left.pack(side="left", padx=(10, 0))
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True)
        cols = ("point", "unit", "beh", "value")
        self.sig_tree = t = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse",
                                         style=tree_style(self.app.root, s))
        for c, title, wd, st in (("point", "Point", 270, True), ("unit", "Unit", 60, False),
                                 ("beh", "Behaviour", 190, True), ("value", "Current value", 150, False)):
            t.heading(c, text=title, anchor="w")
            t.column(c, width=int(wd * s), minwidth=int(40 * s), stretch=st, anchor="w")
        t.tag_configure("custom", foreground="#1a64d6")
        t.tag_configure("error", foreground=COL["bad"])
        vsb = ttk.Scrollbar(tf, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vsb.set)
        t.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        t.bind("<Double-1>", lambda e: t.identify_row(e.y) and self.edit_sig_pattern(t.identify_row(e.y)))
        t.bind("<Return>", lambda e: self.edit_sig_pattern())
        self._sig_controls()

    def _sig_controls(self):
        """Enable / disable the signal controls for the current state."""
        eng = self.engine
        profile = SIG_MODES.get(self.sig_v["mode"].get()) == "profile"
        self.cb_sig_dev.configure(state="readonly" if profile else "disabled")
        if self.sig_preset is not None:
            guess = simsig.guess_device_type(self.sig_preset)
            self.h_sig_dev.configure(text=f"guessed: {guess}", style="Info.TLabel")
        else:
            self.h_sig_dev.configure(text=" ", style="Info.TLabel")
        run = bool(eng and eng.running())
        self.b_sig_run.configure(text="Stop signals" if run else "Start signals",
                                 state="normal" if eng else "disabled")
        self.cb_sig_pause.configure(state="normal" if run else "disabled")
        self.b_sig_edit.configure(state="normal" if eng else "disabled")

    def pick_sig_preset(self):
        return SimPresetPicker(self.app, self.w, self.load_sig_preset, self.sig_path)

    def load_sig_preset(self, path, quiet=False):
        """Fill the store from a preset file and make a (stopped) SignalEngine for it. Returns True on success."""
        try:
            _data, pr = read_preset_file(path)
            eng = simsig.SignalEngine(pr, self.store, mode=SIG_MODES.get(self.sig_v["mode"].get(), "profile"),
                                      device_type=self._sig_device(), speed=self._sig_speed())
        except (OSError, ValueError, TypeError, KeyError) as e:  # PresetError is a ValueError
            if not quiet:
                self._status(f"Could not load preset: {e}", "bad")
            return False
        self.stop_signals()
        try:
            pr.fill_simulator(self.store)
        except (ValueError, TypeError, KeyError) as e:
            self._status(f"Could not load preset values: {e}", "bad")
            return False
        self.engine, self.sig_preset, self.sig_path = eng, pr, os.path.abspath(path)
        self.sig_pause.set(False)
        self.l_sig_name.configure(text=pr.name)
        self.l_sig_note.configure(text="" if pr.verified else
                                  "⚠ Unverified preset - values are simulated from an unverified preset "
                                  "(addresses / scaling not checked against a real device).")
        self._sig_rebuild_rows()
        self._sig_controls()
        self._refresh(flash=False)
        self._save_cfg()
        if not quiet:
            self._status(f"Preset loaded: {pr.name} ({len(pr.points)} points) - press Start signals to move "
                         f"the values", "ok")
        return True

    def _sig_device(self):
        d = self.sig_v["device"].get()
        return d if d in simsig.DEVICE_TYPES else None

    def _sig_speed(self):
        try:
            return float(self.sig_v["speed"].get().rstrip("x"))
        except ValueError:
            return 1.0

    def toggle_signals(self):
        if self.engine and self.engine.running():
            self.stop_signals()
            self._status("Signals stopped - values stay where they are", "info")
        else:
            self.start_signals()

    def start_signals(self):
        if not self.engine:
            self._status("Load a preset first (Load preset...)", "bad")
            return False
        self.engine.paused = bool(self.sig_pause.get())
        self.engine.start()
        self._sig_controls()
        self._status(f"Signals running: {self.sig_v['mode'].get()}"
                     + ("" if self.running() else " - press Start (F5) so a master can read them"), "ok")
        return True

    def stop_signals(self):
        if self.engine:
            self.engine.stop()
        if hasattr(self, "b_sig_run"):
            self._sig_controls()

    def _sig_paused(self):
        if self.engine:
            self.engine.paused = bool(self.sig_pause.get())

    def _sig_mode_changed(self):
        mode = SIG_MODES.get(self.sig_v["mode"].get(), "profile")
        if self.engine:
            try:  # "Auto" goes back to the guessed type
                self.engine.set_mode(mode, self._sig_device() or simsig.guess_device_type(self.engine.preset))
            except ValueError as e:
                self._status(f"Signals: {e}", "bad")
            self._sig_rebuild_rows()
        self._sig_controls()
        self._save_cfg()

    def _sig_speed_changed(self):
        if self.engine:
            self.engine.speed = self._sig_speed()
        self._save_cfg()

    def _sig_rebuild_rows(self):
        t = self.sig_tree
        t.delete(*t.get_children())
        self.sig_rows = {}
        eng = self.engine
        if not eng:
            self.l_sig_left.configure(text="")
            return
        custom = set(eng.overrides)
        for i, (name, unit, label) in enumerate(eng.describe()):
            iid = f"p{i}"
            t.insert("", "end", iid=iid, values=(name, unit, label, ""),
                     tags=("custom",) if name in custom else ())
            self.sig_rows[iid] = name
        moving = {pl.point.name for pl in eng.plans}
        left = [p.name for p in eng.preset.points if p.name not in moving]
        self.l_sig_left.configure(text=(f"Setpoints and text points are left to the master ({len(left)}: "
                                        f"{', '.join(left[:4])}{', ...' if len(left) > 4 else ''})") if left else "")
        self._sig_values()

    def _sig_values(self):
        eng = self.engine
        if not eng:
            return
        vals, errs = eng.values(), dict(eng.errors)
        for iid, name in self.sig_rows.items():
            p = eng.preset.by_name.get(name)
            txt = f"error: {errs[name]}" if name in errs else sig_value_text(p, vals.get(name))
            try:
                if self.sig_tree.set(iid, "value") != txt:
                    self.sig_tree.set(iid, "value", txt)
                    tags = ("error",) if name in errs else (("custom",) if name in eng.overrides else ())
                    self.sig_tree.item(iid, tags=tags)
            except tk.TclError:
                pass

    def _sig_refresh(self):
        self.sig_job = None
        if not self.alive():
            return
        self._sig_values()
        if self.engine and not self.engine.running() and self.b_sig_run.cget("text") == "Stop signals":
            self._sig_controls()
        self.sig_job = self.w.after(500, self._sig_refresh)

    def edit_sig_pattern(self, iid=None):
        if not self.engine:
            self._status("Load a preset first (Load preset...)", "bad")
            return None
        sel = self.sig_tree.selection()
        iid = iid or (sel[0] if sel else None)
        name = self.sig_rows.get(iid)
        if not name:
            self._status("Select a point in the list first", "bad")
            return None
        return SigPatternDialog(self, name)

    def set_sig_override(self, name, spec):
        """spec dict or None (back to the mode). Returns an error text or None."""
        try:
            self.engine.set_override(name, spec)
        except (KeyError, ValueError, TypeError) as e:
            msg = f"{name}: {e}"
            self._status(f"Pattern not applied - {msg}", "bad")
            return msg
        self._sig_rebuild_rows()
        self._status(f"{name}: {'own pattern ' + spec['kind'] if spec else 'back to the mode behaviour'}", "ok")
        return None

    def close(self):
        if self.sig_job:
            try:
                self.w.after_cancel(self.sig_job)
            except tk.TclError:
                pass
            self.sig_job = None
        if self.engine:
            self.engine.stop()
        self.stop()
        self._save_cfg()
        save_settings(self.app.cfg)
        if self.alive():
            self.w.destroy()


# ---------------- presets / traffic / inspector (GUI) ----------------

BANNER = dict(bg="#ffe066", fg="#4d3800")
BADGE = {"verified": ("VERIFIED", "#cdeccf", "#1e5e2c"), "unverified": ("UNVERIFIED", "#ffe066", "#4d3800"),
         "invalid": ("INVALID", "#f8d0d0", "#8a1c13")}
FC_CHOICES = ["1 - Coil (0x)", "2 - Discrete input (1x)", "3 - Holding register (4x)", "4 - Input register (3x)"]


class _Recorder:
    """Stands in for a master during Preset.read and keeps the raw blocks it fetched (worker thread only)."""

    def __init__(self, m):
        self.m, self.blocks = m, []

    def request(self, unit, fc, **kw):
        data = self.m.request(unit, fc, **kw)
        if data is not None:
            self.blocks.append((fc, kw.get("addr", 0), list(data)))
        return data


def in_dir(path, folder):
    p, d = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    return p.startswith(d.rstrip("\\/") + os.sep)


def conn_summary(pc):
    if not pc:
        return "not given"
    t = str(pc.get("type", "?")).upper()
    if t == "TCP":
        s = (f"TCP {pc.get('host', '(host not set)')}:{pc.get('port', '(port not set)')}"
             if ("host" in pc or "port" in pc) else "TCP (host / port not given)")
    else:
        s = (f"{t} {pc.get('baud', '?')} baud, {pc.get('data', 8)}{str(pc.get('parity', 'N')).upper()}"
             f"{pc.get('stop', 1)}")
    if "unit" in pc:
        s += f", default slave ID {pc['unit']}"
    if "timeout" in pc:
        try:
            s += f", timeout {int(round(float(pc['timeout']) * 1000))} ms"
        except (TypeError, ValueError):
            pass
    return s


def preset_conn_settings(conn, pc):
    """(Setup settings, preset 'connection' block) -> (new settings, slave id or None, description)."""
    new, parts = dict(conn), []
    pc = pc or {}
    t = str(pc.get("type", "")).upper()
    if t in ("TCP", "RTU", "ASCII"):
        new["mode"] = t
        parts.append(t)
    try:
        for k, lab in (("baud", "baud"), ("data", "data bits"), ("stop", "stop bits")):
            if k in pc:
                new[k] = str(int(pc[k]))
                parts.append(f"{lab} {new[k]}")
        if "parity" in pc:
            new["parity"] = str(pc["parity"]).upper()[:1] or "N"
            parts.append(f"parity {PARITY_NAMES.get(new['parity'], new['parity'])}")
        if "host" in pc:
            new["host"] = str(pc["host"])
            parts.append(f"host {new['host']}")
        if "port" in pc:
            new["port"] = str(int(pc["port"]))
            parts.append(f"port {new['port']}")
        if "timeout" in pc:
            new["timeout"] = str(int(round(float(pc["timeout"]) * 1000)))
            parts.append(f"timeout {new['timeout']} ms")
        unit = int(pc["unit"]) if "unit" in pc else None
    except (TypeError, ValueError):
        raise ValueError("the preset's connection block has an invalid value")
    if unit is not None:
        parts.append(f"Slave ID {unit}")
    if new["mode"] != "TCP" and parts:
        parts.append(f"COM port unchanged ({conn.get('com', '?')})")
    return new, unit, (", ".join(parts) or "nothing - the preset has no connection settings")


def tree_style(root, s):
    """A Treeview style with a row height that fits the font at any DPI."""
    st = ttk.Style(root)
    f = tkfont.nametofont("TkDefaultFont")
    st.configure("MB.Treeview", rowheight=f.metrics("linespace") + int(7 * s))
    return "MB.Treeview"


def fill_info_text(txt, data, path):
    """Preset header, notes and source URLs into a (read-only, selectable) Text widget."""
    import webbrowser
    txt.configure(state="normal")
    txt.delete("1.0", "end")
    for tg in txt.tag_names():
        if tg.startswith("url"):
            txt.tag_delete(tg)
    ins = lambda s, *tags: txt.insert("end", s, tags)
    ins(str(data.get("name", "unnamed")) + "\n", "h1")
    for k, lab in (("manufacturer", "Manufacturer"), ("model", "Model"), ("doc_revision", "Document")):
        if data.get(k):
            ins(f"{lab}: ", "b")
            ins(f"{data[k]}\n")
    ver = bool(data.get("verified"))
    ins("Status: ", "b")
    ins("VERIFIED\n" if ver else "UNVERIFIED - not checked against a real device\n", "ok" if ver else "warn")
    if data.get("verified_note"):
        ins("Verified note: ", "b")
        ins(f"{data['verified_note']}\n")
    ins("Connection: ", "b")
    ins(conn_summary(data.get("connection")) + "\n")
    ins("Points: ", "b")
    ins(f"{len(data.get('points') or [])}    address_base: {data.get('address_base', 0)}\n")
    if path:
        ins("File: ", "b")
        ins(path + "\n")
    notes = data.get("notes")
    if notes:
        ins("\nNotes\n", "h2")
        ins(("\n".join(map(str, notes)) if isinstance(notes, list) else str(notes)) + "\n")
    src = data.get("source")
    if src:
        ins("\nSources\n", "h2")
        for i, u in enumerate(src if isinstance(src, list) else [src]):
            u = str(u)
            if u.startswith(("http://", "https://")):
                tag = f"url{i}"
                ins(u, "url", tag)
                txt.tag_bind(tag, "<Button-1>", lambda e, u=u: webbrowser.open(u))
                txt.tag_bind(tag, "<Enter>", lambda e: txt.configure(cursor="hand2"))
                txt.tag_bind(tag, "<Leave>", lambda e: txt.configure(cursor=""))
                ins("\n")
            else:
                ins(u + "\n")
    txt.configure(state="disabled")


def info_text(parent, s):
    """Read-only, selectable Text with a scrollbar; returns (frame, text)."""
    f = ttk.Frame(parent)
    base = tkfont.nametofont("TkDefaultFont")
    txt = tk.Text(f, wrap="word", font=base, relief="flat", bg=COL["bg"], padx=int(8 * s), pady=int(6 * s),
                  highlightthickness=1, highlightbackground=COL["line"], width=34, height=10, cursor="")
    sb = ttk.Scrollbar(f, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")
    f.rowconfigure(0, weight=1)
    f.columnconfigure(0, weight=1)
    h1 = base.copy()
    h1.configure(weight="bold", size=base.cget("size") + (1 if base.cget("size") > 0 else -1))
    b = base.copy()
    b.configure(weight="bold")
    txt._fonts = (h1, b)
    txt.tag_configure("h1", font=h1, spacing3=4)
    txt.tag_configure("h2", font=b, spacing1=4)
    txt.tag_configure("b", font=b)
    txt.tag_configure("ok", foreground="#1e7b34", font=b)
    txt.tag_configure("warn", foreground="#a15c00", font=b)
    txt.tag_configure("url", foreground=COL["sel"], underline=True)
    txt.bind("<Control-a>", lambda e: (txt.tag_add("sel", "1.0", "end"), "break")[1])
    txt.configure(state="disabled")
    return f, txt


class ConfirmDialog(Dialog):
    """Modal yes/no with a warning; on_ok() / on_cancel() run after it closes. Cancel is the default."""

    def __init__(self, app, title, text, ok_text, on_ok, on_cancel=None, parent=None):
        super().__init__(app, title, modal=True)
        self.on_ok, self.on_cancel, self.done = on_ok, on_cancel, False
        self.parent = parent
        if parent is not None:
            self.w.transient(parent)
        s = app.s
        tk.Label(self.body, text="⚠", font=("Segoe UI", 22), fg="#a15c00").grid(row=0, column=0, sticky="n",
                                                                                   padx=(0, 12))
        self.l_text = ttk.Label(self.body, text=text, wraplength=int(430 * s), justify="left")
        self.l_text.grid(row=0, column=1, sticky="w")
        bf = ttk.Frame(self.body)
        bf.grid(row=1, column=0, columnspan=2, sticky="e", pady=(14, 0))
        self.b_ok = ttk.Button(bf, text=ok_text, command=self.ok)
        self.b_ok.pack(side="left", padx=(0, 6))
        self.b_cancel = ttk.Button(bf, text="Cancel", command=self.close, default="active")
        self.b_cancel.pack(side="left")
        self.w.bind("<Return>", lambda e: (e.widget.invoke() if isinstance(e.widget, ttk.Button) else None))
        app.last_confirm = self
        self.place()
        self.b_cancel.focus_set()

    def place(self):
        r = self.parent or self.app.root
        self.w.update_idletasks()
        x = r.winfo_rootx() + max((r.winfo_width() - self.w.winfo_reqwidth()) // 2, 0)
        y = r.winfo_rooty() + max((r.winfo_height() - self.w.winfo_reqheight()) // 3, 0)
        self.w.geometry(f"+{x}+{y}")

    def ok(self):
        self.done = True
        super().close()
        self.on_ok()

    def close(self):
        if self.done:
            return
        self.done = True
        super().close()
        if self.on_cancel:
            self.on_cancel()


class PresetView:
    """Named table that replaces the register grid while a preset is active."""
    # the Name width is set by _fit_cols
    COLS = (("name", "Name", 180, "w", False), ("value", "Value", 132, "e", False), ("raw", "Raw", 80, "e", False),
            ("addr", "Address / FC", 128, "w", False), ("group", "Group", 76, "w", False),
            ("access", "Access", 52, "center", False))

    def __init__(self, parent, app):
        self.app, s = app, app.s
        self.s = s
        self.frame = ttk.Frame(parent)
        small, bold = app.root._mb_fonts
        self.banner = tk.Label(self.frame, text="⚠  Preset not verified against a real device  -  compare the "
                               "values with the device's own display or manual before relying on them",
                               anchor="w", padx=int(10 * s), pady=int(4 * s), font=bold, **BANNER)
        hb = ttk.Frame(self.frame)
        hb.pack(fill="x", pady=(0, 4))
        self.hb = hb
        self.l_name = ttk.Label(hb, style="Conn.TLabel")
        self.l_name.pack(side="left")
        self.badge = tk.Label(hb, padx=int(6 * s), pady=0, font=small)
        self.badge.pack(side="left", padx=(8, 0))
        self.b_back = ttk.Button(hb, text="Back to register grid", command=app.leave_preset)
        self.b_back.pack(side="right")
        Tooltip(self.b_back, "Leave the preset view and show the plain address / value grid again")
        self.b_base = ttk.Button(hb, text="Try other base", command=app.try_other_base)
        self.b_base.pack(side="right", padx=(0, 6))
        Tooltip(self.b_base, "Read this preset as if its address_base were flipped (0 <-> 1), without saving.\n"
                             "Use it when every value looks shifted by one register: compare with the device,\n"
                             "then 'Keep this base' (saved into a copy) or 'Back to file'.")
        # shown while trying the other base
        self.trial_bar = tk.Frame(self.frame, bg="#dbe7fb")
        self.l_trial = tk.Label(self.trial_bar, text="", bg="#dbe7fb", fg="#0b3a82", anchor="w",
                                padx=int(10 * s), pady=int(3 * s), font=bold)
        self.l_trial.pack(side="left", fill="x", expand=True)
        ttk.Button(self.trial_bar, text="Back to file", command=app.revert_trial).pack(side="right", padx=(4, 8),
                                                                                       pady=int(2 * s))
        ttk.Button(self.trial_bar, text="Keep this base...", command=app.keep_trial).pack(side="right",
                                                                                         pady=int(2 * s))
        self.group = tk.StringVar(value="All")
        self.cb_group = ttk.Combobox(hb, textvariable=self.group, state="readonly", width=16)
        self.cb_group.pack(side="right", padx=(4, 12))
        ttk.Label(hb, text="Group:").pack(side="right")
        self.l_unit = ttk.Label(hb, style="Info.TLabel")
        self.l_unit.pack(side="right", padx=(0, 14))
        self.group.trace_add("write", lambda *_: self.app.preset and self.app._validate())
        pw = ttk.Panedwindow(self.frame, orient="horizontal")
        pw.pack(fill="both", expand=True)
        tf = ttk.Frame(pw)
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in self.COLS], show="headings", selectmode="browse",
                                 style=tree_style(app.root, s))
        for cid, lab, wd, anc, stretch in self.COLS:
            self.tree.heading(cid, text=lab, anchor=anc if anc != "e" else "e")
            self.tree.column(cid, width=int(wd * s), anchor=anc, stretch=stretch,
                             minwidth=int((110 if cid == "name" else 40) * s))
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        for tg, opt in (("even", dict(background=COL["bg"])), ("odd", dict(background=COL["alt"])),
                        ("flash", dict(background=COL["flash"])), ("fg", dict(foreground=COL["fg"])),
                        ("err", dict(foreground=COL["bad"])), ("stale", dict(foreground=COL["stale"])),
                        ("wo", dict(foreground=COL["sel"]))):
            self.tree.tag_configure(tg, **opt)
        inf = ttk.Frame(pw)
        self.l_point = ttk.Label(inf, text="", wraplength=int(300 * s), justify="left", style="Info.TLabel")
        self.l_point.pack(fill="x", padx=(6, 0), pady=(0, 4))
        itf, self.info = info_text(inf, s)
        itf.pack(fill="both", expand=True, padx=(6, 0))
        pw.add(tf, weight=3)
        pw.add(inf, weight=1)
        self.pw = pw
        self._sash_done = False
        pw.bind("<Configure>", self._sash)
        inf.bind("<Configure>", lambda e: self.l_point.configure(wraplength=max(e.width - int(12 * s), 100)))
        self.tree.bind("<Double-1>", self._dbl)
        self.tree.bind("<Return>", lambda e: self._edit_sel())
        self.tree.bind("<F2>", lambda e: self._edit_sel())
        self.tree.bind("<Button-3>", self._ctx)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_point())
        self.tree.bind("<MouseWheel>", lambda e: self.end_edit(), add="+")
        self.tree.bind("<Configure>", self._fit_cols, add="+")
        self.points, self.names, self.iid, self.byiid = [], [], {}, {}
        self.pr, self.path, self.data = None, None, {}
        self.vals, self.raw, self.value, self.errs, self.flash = {}, {}, {}, {}, {}
        self.flash_job = None
        self.stale = False
        self.editor = self.err_lbl = self.msg_lbl = None
        self.msg_job = None

    def _fit_cols(self, e):
        """The Name column takes whatever the fixed columns leave (so they never get pushed out of view)."""
        self.end_edit()
        fixed = sum(int(c[2] * self.s) for c in self.COLS[1:])
        self.tree.column("name", width=max(int(110 * self.s), e.width - fixed - 2))

    def _sash(self, e):
        """Give the info pane a fixed share once the window has its real size (table keeps the rest)."""
        if self._sash_done or e.width < 200:
            return
        self._sash_done = True
        info = min(int(300 * self.s), e.width // 4)
        self.pw.after_idle(lambda: self.pw.sashpos(0, e.width - info))

    # ----- content -----
    def load(self, pr, path, data):
        self.end_edit()
        self.pr, self.path, self.data = pr, path, data
        self.l_name.configure(text=pr.name)
        text, bg, fg = BADGE["verified" if pr.verified else "unverified"]
        self.badge.configure(text=text, bg=bg, fg=fg)
        if pr.verified:
            self.banner.pack_forget()
        else:
            self.banner.pack(fill="x", pady=(0, 6), before=self.hb)
        self.cb_group.configure(values=["All"] + pr.groups())
        self.group.set("All")
        fill_info_text(self.info, data, path)
        self.l_point.configure(text="Select a row for the point's details.")

    def show_trial(self, trial):
        if trial:
            self.l_trial.configure(text=f"TRYING address_base {trial['base']} (file says {1 - trial['base']}) - "
                                        "nothing is saved; compare the values with the device")
            self.trial_bar.pack(fill="x", pady=(0, 6), before=self.hb)
            self.b_base.configure(text="Try other base")
        else:
            self.trial_bar.pack_forget()

    def set_unit_note(self, unit, default):
        d = "not given" if default is None else default
        self.l_unit.configure(text=f"Slave ID {unit if unit is not None else '?'}  (preset default: {d})")
        if not getattr(self, "_unit_tip", None):
            self._unit_tip = Tooltip(self.l_unit, "The Slave ID field of the poll definition is used;\n"
                                                  "it overrides the preset's default slave ID.")

    def set_points(self, pr, group, plc):
        self.end_edit()
        self.tree.delete(*self.tree.get_children())
        self.points = [p for p in pr.points if group in (None, p.group)]
        self.names = [p.name for p in self.points]
        self.iid, self.byiid = {}, {}
        self.vals, self.raw, self.value, self.errs, self.flash = {}, {}, {}, {}, {}
        self.stale = False
        for i, p in enumerate(self.points):
            iid = f"p{i}"
            self.iid[p.name], self.byiid[iid] = iid, p
            lab = addr_label(p.fc, p.address, plc)
            addr = f"{lab}  FC{p.fc:02d}" + (f"  ({p.words} regs)" if p.words > 1 else "")
            wo = p.access == "w"
            self.tree.insert("", "end", iid=iid, values=(p.name, mon.point_cell_text(p), "", addr, p.group,
                                                         "w (cmd)" if wo else p.access),
                             tags=("odd" if i % 2 else "even", "wo" if wo else "fg"))

    def _tags(self, i, iid):
        bg = "flash" if iid in self.flash else ("odd" if i % 2 else "even")
        if self.byiid[iid].access == "w":
            return (bg, "wo")
        fg = "err" if self.errs.get(iid) else ("stale" if self.stale else "fg")
        return (bg, fg)

    def set_results(self, res):
        now = time.monotonic()
        self.stale = False
        for i, p in enumerate(self.points):
            iid = self.iid[p.name]
            d = res.get(p.name)
            if d is None:
                continue
            if "error" in d:
                text, raw, err = "ERROR: " + d["error"], "", True
                self.raw.pop(p.name, None)
                self.value.pop(p.name, None)
            else:
                r = d["raw"]
                raw = str(int(r)) if isinstance(r, bool) else (f"{r:.7g}" if isinstance(r, float) else str(r))
                text, err = str(d["text"]), False
                self.raw[p.name], self.value[p.name] = r, d["value"]
            old = self.vals.get(iid)
            if old is not None and old != text and not err and not self.errs.get(iid):
                self.flash[iid] = now
            self.vals[iid], self.errs[iid] = text, err
            vals = list(self.tree.item(iid, "values"))
            vals[1], vals[2] = text, raw
            self.tree.item(iid, values=vals, tags=self._tags(i, iid))
        if self.flash and not self.flash_job:
            self.flash_job = self.tree.after(300, self._unflash)
        self._show_point()

    def _unflash(self):
        self.flash_job = None
        now = time.monotonic()
        for i, p in enumerate(self.points):
            iid = self.iid[p.name]
            if iid in self.flash and now - self.flash[iid] >= 1.2:
                del self.flash[iid]
                self.tree.item(iid, tags=self._tags(i, iid))
        if self.flash:
            self.flash_job = self.tree.after(300, self._unflash)

    def set_stale(self):
        self.stale = True
        for i, p in enumerate(self.points):
            self.tree.item(self.iid[p.name], tags=self._tags(i, self.iid[p.name]))

    def selected_name(self):
        sel = self.tree.selection()
        p = self.byiid.get(sel[0]) if sel and hasattr(self, "byiid") else None
        return p.name if p else None

    def select(self, name):
        iid = self.iid.get(name)
        if iid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)

    def _show_point(self):
        name = self.selected_name()
        if not name:
            return
        p = self.pr.by_name[name]
        raw_pt = next((d for d in self.data.get("points", []) if str(d.get("name")) == name), {})
        bits = [f"{name}:  {p.type}" + (f", {p.words} registers ({2 * p.words} characters)" if p.type == "string"
                                        else (f", word order {p.word_order}" if p.words > 1 else ""))
                + (", byte_swap" if p.byte_swap and p.type != "string" else "")
                + (", WRITE-ONLY command (never read; double-click / Enter to send)" if p.access == "w" else "")
                + (f", scale {p.scale:g}" if p.scale != 1 else "") + (f", offset {p.offset:g}" if p.offset else "")
                + (f", range {p.min}..{p.max}" if p.min is not None or p.max is not None else "")]
        if p.enum:
            bits.append("Values: " + ", ".join(f"{k} = {v}" for k, v in p.enum.items()))
        if raw_pt.get("note"):
            bits.append("Note: " + str(raw_pt["note"]))
        if self.errs.get(self.iid[name]):
            bits.append(self.vals.get(self.iid[name], ""))
        self.l_point.configure(text="\n".join(bits))

    # ----- editing -----
    def _dbl(self, e):
        if self.tree.identify_region(e.x, e.y) != "cell":
            return
        iid = self.tree.identify_row(e.y)
        if iid in self.byiid:
            self.tree.selection_set(iid)
            self.app.edit_point(self.byiid[iid].name)

    def _edit_sel(self):
        n = self.selected_name()
        if n:
            self.app.edit_point(n)
        return "break"

    def _ctx(self, e):
        iid = self.tree.identify_row(e.y)
        if iid in self.byiid:
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.app.context_menu(e)

    def start_edit(self, name, commit, choices=None):
        """commit(text) -> None on success or an error string (shown under the editor, which stays open)."""
        self.end_edit()
        iid = self.iid[name]
        self.select(name)
        self.tree.update_idletasks()
        bb = self.tree.bbox(iid, "value")
        if not bb:
            return None
        x, y, w, h = bb
        if choices:
            e = ttk.Combobox(self.tree, values=choices, state="readonly")
            cur = self.vals.get(iid, "")
            if cur in choices:
                e.set(cur)
            e.bind("<<ComboboxSelected>>", lambda _e: ok())
        else:
            e = tk.Entry(self.tree, justify="right", relief="solid", bd=1, highlightthickness=0)
            v = self.value.get(name)
            e.insert(0, "" if v is None else (f"{v:.10g}" if isinstance(v, float) else str(v)))
            e.select_range(0, "end")
            e.bind("<Key>", lambda _e: e.configure(bg="white"), add="+")
            e.bind("<FocusOut>", lambda _e: self.end_edit())
        e.place(x=x, y=y, width=w, height=h)
        self.editor = e

        def ok(_e=None):
            err = commit(e.get())
            if err:
                if isinstance(e, tk.Entry):
                    e.configure(bg="#ffd6d6")
                if self.err_lbl is None:
                    self.err_lbl = tk.Label(self.tree, bg=COL["bad"], fg="white", padx=4, pady=1)
                self.err_lbl.configure(text=err + "   (Esc = cancel)")
                self.err_lbl.place(x=x, y=y + h)
                self.err_lbl.lift()
            else:
                self.end_edit()
                self.tree.focus_set()
            return "break"

        def cancel(_e=None):
            self.end_edit()
            self.tree.focus_set()
            return "break"
        e.bind("<Return>", ok)
        e.bind("<KP_Enter>", ok)
        e.bind("<Escape>", cancel)
        e.focus_set()
        return e

    def end_edit(self):
        if self.editor is not None:
            ed, self.editor = self.editor, None
            ed.destroy()
        if self.err_lbl is not None:
            self.err_lbl.destroy()
            self.err_lbl = None

    def inline_error(self, name, text):
        """A red note under the row for a few seconds (instead of a message box)."""
        iid = self.iid.get(name)
        if not iid:
            return
        self.select(name)
        self.tree.update_idletasks()
        bb = self.tree.bbox(iid, "value") or self.tree.bbox(iid)
        if not bb:
            return
        if self.msg_lbl is None:
            self.msg_lbl = tk.Label(self.tree, bg=COL["bad"], fg="white", padx=4, pady=1)
        self.msg_lbl.configure(text=text)
        x, y, _w, h = bb
        self.msg_lbl.place(x=max(x - int(120 * self.s), 0), y=y + h)
        self.msg_lbl.lift()
        if self.msg_job:
            self.tree.after_cancel(self.msg_job)
        self.msg_job = self.tree.after(4000, self._hide_msg)

    def _hide_msg(self):
        self.msg_job = None
        if self.msg_lbl is not None:
            self.msg_lbl.place_forget()


def scan_presets(folder):
    """[(relative dir, file name, path)] for every .json under folder, sorted, top level first."""
    out = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for f in sorted(files):
            if f.lower().endswith(".json"):
                out.append((os.path.relpath(root, folder), f, os.path.join(root, f)))
    return sorted(out, key=lambda t: (t[0] != ".", t[0], t[1].lower()))


def read_preset_file(path):
    """-> (raw dict, Preset) ; raises ValueError / OSError / PresetError with a readable message."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data, Preset(data)


class PresetsDialog(Dialog):
    """F7: pick a preset from the presets folder tree (or a JSON file), see its notes, apply its connection."""

    def __init__(self, app):
        super().__init__(app, "Device Presets")
        help_key(self.w, "preset")
        s = app.s
        w = self.w
        w.resizable(True, True)
        w.geometry(f"{int(1000 * s)}x{int(600 * s)}")
        w.minsize(int(760 * s), int(440 * s))
        b = self.body
        self.entries = {}
        ttk.Label(b, text=f"Presets in {preset_dir()}   -   double-click to use one", style="Info.TLabel").pack(
            fill="x", pady=(0, 6))
        bf = ttk.Frame(b)
        bf.pack(side="bottom", fill="x", pady=(10, 0))
        self.b_help = add_help_button(app, self.w, "preset", parent=bf, side="left", padx=(0, 8))
        pw = ttk.Panedwindow(b, orient="horizontal")
        pw.pack(fill="both", expand=True)
        lf = ttk.Frame(pw)
        self.tree = ttk.Treeview(lf, columns=("name", "points", "status"), show="tree headings", selectmode="browse",
                                 style=tree_style(app.root, s))
        self.tree.heading("#0", text="File", anchor="w")
        self.tree.heading("name", text="Name", anchor="w")
        self.tree.heading("points", text="Points", anchor="e")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.column("#0", width=int(230 * s), stretch=False)
        self.tree.column("name", width=int(230 * s), stretch=True)
        self.tree.column("points", width=int(56 * s), anchor="e", stretch=False)
        self.tree.column("status", width=int(118 * s), stretch=False)
        self.tree.tag_configure("verified", foreground="#1e7b34")
        self.tree.tag_configure("unverified", foreground="#8a5a00")
        self.tree.tag_configure("invalid", foreground=COL["bad"])
        self.tree.tag_configure("folder", foreground="#59606b")
        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        rf = ttk.Frame(pw, padding=(8, 0, 0, 0))
        self.badge = tk.Label(rf, text="", anchor="w", padx=int(8 * s), pady=int(4 * s), font=app.root._mb_fonts[1])
        self.badge.pack(fill="x")
        cf = ttk.LabelFrame(rf, text="Connection settings in this preset", padding=(8, 4, 8, 6))
        cf.pack(side="bottom", fill="x", pady=(6, 0))
        self.l_conn = ttk.Label(cf, text="", wraplength=int(330 * s), justify="left")
        self.l_conn.pack(fill="x")
        self.b_apply = ttk.Button(cf, text="Apply preset connection settings", command=self.apply_conn)
        self.b_apply.pack(anchor="w", pady=(6, 0))
        Tooltip(self.b_apply, "Copies type (TCP/RTU), baud, parity, stop/data bits, host/port and timeout\n"
                              "into Setup, and the default slave ID into the Slave ID field.\n"
                              "The COM port is not changed.")
        self.l_applied = ttk.Label(cf, text="", wraplength=int(330 * s), justify="left", style="Info.TLabel")
        self.l_applied.pack(fill="x", pady=(4, 0))
        itf, self.info = info_text(rf, s)
        itf.pack(fill="both", expand=True, pady=(6, 0))
        pw.add(lf, weight=3)
        pw.add(rf, weight=2)

        def wrap(e):
            wl = max(e.width - int(28 * s), 120)
            for lab in (self.l_conn, self.l_applied):
                lab.configure(wraplength=wl)
            self.badge.configure(wraplength=e.width - int(20 * s))
        rf.bind("<Configure>", wrap)
        self._sash_done = False

        def sash(e):
            if not self._sash_done and e.width > 300:
                self._sash_done = True
                pw.after_idle(lambda: pw.sashpos(0, int(e.width * 0.6)))
        pw.bind("<Configure>", sash)
        ttk.Button(bf, text="Open JSON file...", command=self.open_file).pack(side="left")
        ttk.Button(bf, text="Import CSV...", command=self.import_csv).pack(side="left", padx=(6, 0))
        self.b_edit = ttk.Button(bf, text="Edit...", command=self.edit)
        self.b_edit.pack(side="left", padx=(6, 0))
        Tooltip(self.b_edit, "Fix points, add / remove points, mark as verified, save a copy")
        ttk.Button(bf, text="Refresh", command=self.refresh).pack(side="left", padx=(6, 0))
        ttk.Button(bf, text="Close", command=self.close).pack(side="right")
        self.b_use = ttk.Button(bf, text="Use preset", command=self.use, default="active")
        self.b_use.pack(side="right", padx=(0, 6))
        Tooltip(self.b_use, "Show this preset's named values in the main window   (Enter)")
        self.l_msg = ttk.Label(bf, text="", style="SBad.TLabel")
        self.l_msg.pack(side="left", padx=(12, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show())
        self.tree.bind("<Double-1>", lambda e: self.tree.identify_row(e.y) in self.entries and self.use())
        self.w.bind("<Return>", lambda e: self.use())
        self.refresh()
        self.place()

    def refresh(self, select_path=None):
        cur = self.current()
        select_path = select_path or (cur["path"] if cur else None)
        opened = [e for e in self.entries.values() if e.get("opened")]
        self.tree.delete(*self.tree.get_children())
        self.entries = {}
        folders = {}
        for rel, fname, path in scan_presets(preset_dir()):
            parent = ""
            if rel != ".":
                if rel not in folders:
                    folders[rel] = self.tree.insert("", "end", text=rel.replace(os.sep, "/") + "/", open=True,
                                                    tags=("folder",))
                parent = folders[rel]
            self._add(parent, fname, path)
        if opened:
            node = self.tree.insert("", "end", text="(opened files)", open=True, tags=("folder",))
            for e in opened:
                self._add(node, os.path.basename(e["path"]), e["path"], opened=True)
        if select_path:
            self.select_path(select_path)

    def _add(self, parent, fname, path, opened=False):
        e = {"path": path, "data": None, "preset": None, "error": None, "opened": opened}
        try:
            e["data"], e["preset"] = read_preset_file(path)
            pr = e["preset"]
            st = "verified" if pr.verified else "unverified"
            vals = (pr.name, len(pr.points), "✔ VERIFIED" if pr.verified else "⚠ UNVERIFIED")
        except (OSError, ValueError, TypeError, KeyError) as ex:
            e["error"] = str(ex) or repr(ex)
            st, vals = "invalid", ("(cannot load)", "", "✖ INVALID")
        iid = self.tree.insert(parent, "end", text=fname, values=vals, tags=(st,))
        self.entries[iid] = e
        return iid

    def select_path(self, path):
        for iid, e in self.entries.items():
            if os.path.normcase(os.path.abspath(e["path"])) == os.path.normcase(os.path.abspath(path)):
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self._show()
                return iid
        return None

    def current(self):
        sel = self.tree.selection() if hasattr(self, "tree") else ()
        return self.entries.get(sel[0]) if sel else None

    def _show(self):
        e = self.current()
        self.l_applied.configure(text="")
        self.l_msg.configure(text="")
        if not e:
            return
        if e["error"]:
            t, bg, fg = BADGE["invalid"]
            self.badge.configure(text=f"✖ {t}: {e['error']}", bg=bg, fg=fg, justify="left")
            self.info.configure(state="normal")
            self.info.delete("1.0", "end")
            self.info.insert("end", e["path"])
            self.info.configure(state="disabled")
            self.l_conn.configure(text="-")
            self.b_apply.state(["disabled"])
            return
        pr = e["preset"]
        key = "verified" if pr.verified else "unverified"
        t, bg, fg = BADGE[key]
        self.badge.configure(text=(f"✔ {t}" if pr.verified else f"⚠ {t}  -  not checked against a real device"),
                             bg=bg, fg=fg, justify="left")
        fill_info_text(self.info, e["data"], e["path"])
        try:
            _c, _u, desc = preset_conn_settings(self.app.cfg["conn"], pr.connection)
            self.l_conn.configure(text=f"{conn_summary(pr.connection)}\n\nApplying sets: {desc}")
            self.b_apply.state(["!disabled"] if pr.connection else ["disabled"])
        except ValueError as ex:
            self.l_conn.configure(text=str(ex))
            self.b_apply.state(["disabled"])

    def apply_conn(self):
        e = self.current()
        if not e or not e["preset"]:
            return
        try:
            desc = self.app.apply_preset_connection(e["preset"].connection)
        except ValueError as ex:
            self.l_applied.configure(text=str(ex), style="SBad.TLabel")
            return
        self.l_applied.configure(text=f"Applied: {desc}" + (
            "  -  reconnecting with the new settings" if self.app.state != "off" else
            "  -  press F5 in the main window to connect"), style="SOk.TLabel")

    def use(self):
        e = self.current()
        if not e:
            self.l_msg.configure(text="Select a preset first")
            return
        if e["error"]:
            self.l_msg.configure(text="This file is not a valid preset - see the red message")
            return
        self.app.use_preset(e["preset"], e["path"], e["data"])
        self.close()

    def open_file(self, path=None):
        path = path or filedialog.askopenfilename(parent=self.w, title="Open preset JSON file",
                                                  initialdir=preset_dir(),
                                                  filetypes=[("Preset JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return None
        if self.select_path(path):
            return path
        node = next((i for i in self.tree.get_children() if self.tree.item(i, "text") == "(opened files)"), None)
        if node is None:
            node = self.tree.insert("", "end", text="(opened files)", open=True, tags=("folder",))
        iid = self._add(node, os.path.basename(path), path, opened=True)
        self.tree.selection_set(iid)
        self.tree.see(iid)
        self._show()
        return path

    def edit(self):
        e = self.current()
        if e and e["error"]:
            self.l_msg.configure(text="Cannot edit an invalid file here - fix the JSON in a text editor")
            return
        if e:
            data, path = json.loads(json.dumps(e["data"])), e["path"]
        else:
            data, path = {"name": "New device", "manufacturer": "", "model": "", "verified": False,
                          "connection": {"type": "rtu", "baud": 9600, "parity": "N", "stop": 1, "data": 8, "unit": 1},
                          "address_base": 0, "points": []}, None
        return PresetEditor(self.app, data, path, on_saved=self._saved)

    def import_csv(self, path=None):
        path = path or filedialog.askopenfilename(parent=self.w, title="Import a register map (CSV)",
                                                  filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return None
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            data = from_csv(path, name=stem)
        except (OSError, ValueError, KeyError, csv.Error) as ex:
            self.l_msg.configure(text=f"CSV import failed: {ex}")
            return None
        self.l_msg.configure(text="")
        return PresetEditor(self.app, data, None, stem=stem, on_saved=self._saved,
                            origin=f"imported from {os.path.basename(path)}")

    def _saved(self, path):
        if self.alive():
            self.refresh(select_path=path)


def _num(text, what):
    t = text.strip()
    try:
        v = float(t)
    except ValueError:
        raise ValueError(f"{what}: not a number")
    if not math.isfinite(v):
        raise ValueError(f"{what}: not a number")
    return int(v) if v.is_integer() and not any(c in t for c in ".eE") else v


def parse_enum(text):
    out = {}
    for part in re.split(r"[;\n]+", text):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("use  0=Stop; 1=Run")
        k, v = part.split("=", 1)
        try:
            k = str(int(k.strip(), 0))
        except ValueError:
            raise ValueError(f"'{k.strip()}' is not a whole number")
        if not v.strip():
            raise ValueError(f"text missing for {k}")
        out[k] = v.strip()
    return out


class PresetEditor:
    """Edit a preset's points (validated by building Preset(...)), mark it verified, save a copy."""
    FIELDS = [("name", "Name"), ("address", "Address"), ("fc", "Function"), ("type", "Type"),
              ("length", "Length (regs)"), ("word_order", "Word order"), ("byte_swap", "Byte swap"), ("scale", "Scale"), ("offset", "Offset"), ("unit", "Unit"),
              ("access", "Access"), ("min", "Min"), ("max", "Max"), ("group", "Group"), ("enum", "Enum"),
              ("note", "Note")]

    def __init__(self, app, data, path, stem=None, on_saved=None, origin=None, select=None):
        self.app, self.data, self.path, self.stem = app, data, path, stem
        self.on_saved = on_saved
        data.setdefault("points", [])
        s = app.s
        self.s = s
        self.w = w = tk.Toplevel(app.root)
        w.transient(app.root)
        w.geometry(f"{int(1040 * s)}x{int(700 * s)}")
        w.minsize(int(860 * s), int(600 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: self.close())
        self.origin = origin or (path if path else "new preset")
        self.dirty = False
        self.cur = None
        self._loading = False
        self.valid = False
        app.last_editor = self
        body = ttk.Frame(w, padding=10)
        body.pack(fill="both", expand=True)
        # header
        hf = ttk.Frame(body)
        hf.pack(fill="x")
        self.b_help = add_help_button(app, w, "preset.editor")
        self.hv = {k: tk.StringVar(value=str(data.get(k, "") or "")) for k in ("name", "manufacturer", "model")}
        for c, (k, lab, wd) in enumerate((("name", "Preset name", 34), ("manufacturer", "Manufacturer", 20),
                                          ("model", "Model", 30))):
            ttk.Label(hf, text=lab).grid(row=0, column=c, sticky="w", padx=(0, 12))
            ttk.Entry(hf, textvariable=self.hv[k], width=wd).grid(row=1, column=c, sticky="w", padx=(0, 12))
            self.hv[k].trace_add("write", lambda *_: self._changed())
        bf0 = ttk.Frame(hf)
        bf0.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(bf0, text="address_base").grid(row=0, column=0, sticky="w")
        self.base_v = tk.StringVar(value=str(data.get("address_base", 0)))
        self.cb_base = ttk.Combobox(bf0, textvariable=self.base_v, values=["0", "1"], width=3, state="readonly")
        self.cb_base.grid(row=0, column=1, sticky="w", padx=(6, 0))
        Tooltip(self.cb_base, "0: the addresses in this file are protocol addresses (first register = 0).\n"
                              "1: they are 1-based manual numbers (first register = 1); mbtool subtracts 1.\n"
                              "A wrong base is the most common reason for values being off by one register.")
        self.base_v.trace_add("write", lambda *_: self._base_changed())
        sf = ttk.Frame(bf0)
        sf.grid(row=0, column=2, sticky="w", padx=(12, 0))
        for txt, dl in (("Shift all +1", 1), ("Shift all -1", -1)):
            bt = ttk.Button(sf, text=txt, command=lambda dl=dl: self.shift_all(dl))
            bt.pack(side="left", padx=(0, 4))
            Tooltip(bt, "Add / subtract 1 to every point address in this file.")
        self.keep_proto = tk.BooleanVar(value=True)
        cbk = ttk.Checkbutton(sf, text="keep protocol addresses (also change address_base)", variable=self.keep_proto)
        cbk.pack(side="left", padx=(4, 0))
        Tooltip(cbk, "On: addresses AND address_base change together, so the registers that are read stay the same\n"
                     "(e.g. convert a file from manual 1-based numbers to protocol addresses).\n"
                     "Off: only the addresses move, so every point reads the next / previous register.")
        ttk.Label(bf0, text="Off by one register? A wrong address base is the most common cause - try "
                            "'Try other base' in the main window first.", style="Info.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w")
        # bottom (packed first so it always stays visible)
        bf = ttk.Frame(body)
        bf.pack(side="bottom", fill="x", pady=(8, 0))
        self.b_save = ttk.Button(bf, text="Save as...", command=self.save_as, default="active")
        self.b_save.pack(side="right")
        ttk.Button(bf, text="Close", command=self.close).pack(side="right", padx=(0, 6))
        self.l_msg = ttk.Label(bf, text="", style="SInfo.TLabel", wraplength=int(760 * s), justify="left")
        self.l_msg.pack(side="left", fill="x", expand=True)
        vf = ttk.LabelFrame(body, text="Verification", padding=(10, 4, 10, 6))
        vf.pack(side="bottom", fill="x", pady=(8, 0))
        self.verified = tk.BooleanVar(value=bool(data.get("verified")))
        self.cb_ver = ttk.Checkbutton(vf, text='Mark as verified  (sets "verified": true)', variable=self.verified,
                                      command=self._toggle_verified)
        self.cb_ver.grid(row=0, column=0, sticky="w")
        ttk.Label(vf, text="Verified note (who, which device / serial / firmware, date):").grid(
            row=0, column=1, sticky="e", padx=(16, 6))
        self.vnote = tk.StringVar(value=str(data.get("verified_note", "")))
        self.e_vnote = ttk.Entry(vf, textvariable=self.vnote, width=48)
        self.e_vnote.grid(row=0, column=2, sticky="we")
        vf.columnconfigure(2, weight=1)
        self.vnote.trace_add("write", lambda *_: self._changed())
        ttk.Label(vf, text="⚠ Only mark a preset verified after you have read every point from the REAL device and "
                           "compared the values with its own display, software or manual. A verified preset hides the "
                           "'not verified' banner and the write confirmation for everyone who uses the file.",
                  style="Hint.TLabel", wraplength=int(980 * s), justify="left").grid(row=1, column=0, columnspan=3,
                                                                                    sticky="w", pady=(4, 0))
        self.l_valid = ttk.Label(body, text="", wraplength=int(980 * s), justify="left")
        self.l_valid.pack(side="bottom", fill="x", pady=(6, 0))
        # points list + form
        mid = ttk.Frame(body)
        mid.pack(fill="both", expand=True, pady=(8, 0))
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        lf = ttk.Frame(mid)
        lf.grid(row=0, column=0, sticky="nsew")
        cols = (("name", "Name", 180), ("address", "Addr", 60), ("fc", "FC", 34), ("type", "Type", 66),
                ("access", "Acc", 40), ("group", "Group", 80))
        self.tree = ttk.Treeview(lf, columns=[c[0] for c in cols], show="headings", selectmode="browse",
                                 style=tree_style(app.root, s))
        for cid, lab, wd in cols:
            self.tree.heading(cid, text=lab, anchor="w")
            self.tree.column(cid, width=int(wd * s), stretch=cid == "name")
        self.tree.tag_configure("bad", foreground=COL["bad"])
        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        lb = ttk.Frame(lf)
        lb.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        for text, cmd, tip in (("Add", self.add, "Add a new point"), ("Duplicate", self.duplicate, "Copy the selected point"),
                               ("Remove", self.remove, "Delete the selected point"),
                               ("▲", lambda: self.move(-1), "Move up"), ("▼", lambda: self.move(1), "Move down")):
            bt = ttk.Button(lb, text=text, command=cmd, width=len(text) + 3 if len(text) > 1 else 3)
            bt.pack(side="left", padx=(0, 4))
            Tooltip(bt, tip)
        ff = ttk.LabelFrame(mid, text="Point", padding=(10, 4, 10, 6))
        ff.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.ff = ff
        self.fv, self.fh, self.fw = {}, {}, {}
        for r, (k, lab) in enumerate(self.FIELDS):
            var = tk.StringVar()
            if k == "fc":
                wd = ttk.Combobox(ff, textvariable=var, values=FC_CHOICES, width=24, state="readonly")
            elif k == "type":
                wd = ttk.Combobox(ff, textvariable=var, values=list(TYPE_WORDS), width=12, state="readonly")
            elif k == "word_order":
                wd = ttk.Combobox(ff, textvariable=var, values=["big", "little"], width=12, state="readonly")
            elif k == "access":
                wd = ttk.Combobox(ff, textvariable=var, values=["r", "rw", "w"], width=12, state="readonly")
            elif k == "byte_swap":
                wd = ttk.Checkbutton(ff, variable=var, onvalue="1", offvalue="0",
                                     text="swap the 2 bytes in each register")
            else:
                wd = ttk.Entry(ff, textvariable=var, width={"name": 30, "enum": 34, "note": 34}.get(k, 14))
            tips = {"address": "Register / coil address as written in the file (see address_base).\nDecimal or 0x hex.",
                    "word_order": "Multi-register types: big = high word first, little = low word first",
                    "scale": "Engineering value = raw * scale + offset   (empty = 1)",
                    "offset": "Engineering value = raw * scale + offset   (empty = 0)",
                    "min": "Lowest value that may be written (empty = no limit)",
                    "max": "Highest value that may be written (empty = no limit)",
                    "access": "r = read only, rw = may be written, w = write-only command that is never read\n"
                              "(rw / w only on FC 01 / 03)",
                    "length": "string type only: number of registers (2 ASCII characters each, 1-60)",
                    "byte_swap": "Swap the two bytes inside every register: ABCD -> BADC;\n"
                                 "with word order 'little' this gives DCBA",
                    "type": "uint16 / int16 / uint32 / int32 / uint64 / int64 / float32 / float64,\n"
                            "bool (coils, discrete inputs) or string (ASCII, see Length)",
                    "enum": "Value texts, e.g.  0=Stop; 1=Run; 2=Standby",
                    "note": "Free text (kept in the file as the point's 'note')"}
            _l, h = form_row(ff, r, lab, wd, tips.get(k))
            h.configure(wraplength=int(150 * s))
            self.fv[k], self.fh[k], self.fw[k] = var, h, wd
            var.trace_add("write", lambda *_a, k=k: self._field(k))
        ff.columnconfigure(2, minsize=int(150 * s))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._select())
        self.w.bind("<Control-s>", lambda e: (self.save_as(), "break")[1])
        self._title()
        self._fill_list()
        if data["points"]:
            self.select(select if select is not None else 0)
        else:
            self._enable_form(False)
        self._validate()
        self.w.update_idletasks()
        try:
            r = app.root
            self.w.geometry(f"+{r.winfo_rootx() + int(30 * s)}+{r.winfo_rooty() + int(30 * s)}")
        except tk.TclError:
            pass

    # ----- helpers -----
    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def _title(self):
        self.w.title(f"Preset Editor - {self.hv['name'].get() or 'unnamed'}"
                     f"{' *' if self.dirty else ''}   ({self.origin})")

    def _row(self, d):
        return (d.get("name", ""), d.get("address", ""), d.get("fc", 3), d.get("type", "bool" if d.get("fc") in (1, 2) else "uint16"),
                d.get("access", "r"), d.get("group", ""))

    def _fill_list(self):
        self.tree.delete(*self.tree.get_children())
        for i, d in enumerate(self.data["points"]):
            self.tree.insert("", "end", iid=str(i), values=self._row(d))

    def _enable_form(self, on):
        for k, wd in self.fw.items():
            if isinstance(wd, ttk.Combobox):
                wd.configure(state="readonly" if on else "disabled")
            else:
                wd.configure(state="normal" if on else "disabled")
        if not on:
            self._loading = True
            for v in self.fv.values():
                v.set("")
            self._loading = False

    def select(self, i):
        if 0 <= i < len(self.data["points"]):
            self.tree.selection_set(str(i))
            self.tree.focus(str(i))
            self.tree.see(str(i))
            self._select()

    def _select(self):
        sel = self.tree.selection()
        if not sel:
            return
        self.cur = int(sel[0])
        d = self.data["points"][self.cur]
        self._enable_form(True)
        self._loading = True
        fc = d.get("fc", 3)
        vals = {"name": d.get("name", ""), "address": d.get("address", ""),
                "fc": next((c for c in FC_CHOICES if c.startswith(str(fc))), str(fc)),
                "type": d.get("type", "bool" if fc in (1, 2) else "uint16"), "word_order": d.get("word_order", "big"),
                "length": d.get("length", ""), "byte_swap": "1" if d.get("byte_swap") else "0",
                "scale": d.get("scale", ""), "offset": d.get("offset", ""), "unit": d.get("unit", ""),
                "access": d.get("access", "r"), "min": d.get("min", ""), "max": d.get("max", ""),
                "group": d.get("group", ""), "note": d.get("note", ""),
                "enum": "; ".join(f"{k}={v}" for k, v in (d.get("enum") or {}).items())}
        for k, v in vals.items():
            self.fv[k].set("" if v is None else str(v))
            self.fh[k].configure(text="")
        self._loading = False
        self._sync_form()

    def _sync_form(self):
        """Length only for string; byte swap / word order not for bool / string."""
        if self.cur is None:
            return
        ty = self.fv["type"].get()
        self.fw["length"].configure(state="normal" if ty == "string" else "disabled")
        self.fw["byte_swap"].configure(state="disabled" if ty in ("bool", "string") else "normal")
        self.fw["word_order"].configure(state="readonly" if TYPE_WORDS.get(ty, 1) > 1 else "disabled")

    def _base_changed(self):
        if self._loading:
            return
        self.data["address_base"] = int(self.base_v.get() or 0)
        self._changed()

    def shift_all(self, delta):
        try:
            new = mon.shift_addresses(self.data, delta, adjust_base=self.keep_proto.get())
        except ValueError as e:
            self.l_msg.configure(text=f"Not shifted: {e}", style="SBad.TLabel")
            return False
        cur = self.cur
        self.data.clear()
        self.data.update(new)
        self._loading = True
        self.base_v.set(str(self.data.get("address_base", 0)))
        self._loading = False
        self._fill_list()
        if cur is not None:
            self.select(cur)
        self.l_msg.configure(text=f"All addresses {delta:+d}" + (
            f", address_base now {self.data.get('address_base', 0)} - the protocol addresses are unchanged"
            if self.keep_proto.get() else " - every point now reads the " + ("next" if delta > 0 else "previous")
            + " register"), style="SInfo.TLabel")
        self._changed()
        return True

    def _field(self, k):
        if self._loading or self.cur is None:
            return
        d = self.data["points"][self.cur]
        t = self.fv[k].get()
        err = None
        try:
            if k == "name":
                if not t.strip():
                    raise ValueError("required")
                d["name"] = t.strip()
            elif k == "address":
                try:
                    d["address"] = int(t.strip(), 0)
                except ValueError:
                    raise ValueError("whole number (or 0x..)")
            elif k == "fc":
                fc = int(t.split()[0])
                d["fc"] = fc
                ty = self.fv["type"].get()
                if fc in (1, 2) and ty != "bool":
                    self.fv["type"].set("bool")
                elif fc in (3, 4) and ty == "bool":
                    self.fv["type"].set("uint16")
                if fc in (2, 4) and self.fv["access"].get() in ("rw", "w"):
                    self.fv["access"].set("r")
            elif k in ("type", "word_order", "access"):
                d[k] = t
                if k == "type":
                    if t == "string" and "length" not in d:
                        self.fv["length"].set("1")
                    if t != "string":
                        d.pop("length", None)
                        if self.fv["length"].get():
                            self._loading = True
                            self.fv["length"].set("")
                            self._loading = False
                    if t in ("bool", "string") and d.get("byte_swap"):
                        self.fv["byte_swap"].set("0")
                    self._sync_form()
            elif k == "length":
                if t.strip():
                    try:
                        n = int(t.strip())
                    except ValueError:
                        raise ValueError("whole number of registers")
                    if not 1 <= n <= 60:
                        raise ValueError("1-60 registers")
                    d["length"] = n
                else:
                    d.pop("length", None)
            elif k == "byte_swap":
                if t == "1":
                    d["byte_swap"] = True
                else:
                    d.pop("byte_swap", None)
            elif k in ("scale", "offset", "min", "max"):
                if t.strip():
                    d[k] = _num(t, k)
                else:
                    d.pop(k, None)
            elif k == "enum":
                e = parse_enum(t)
                if e:
                    d["enum"] = e
                else:
                    d.pop("enum", None)
            else:  # unit, group, note
                if t.strip():
                    d[k] = t.strip() if k != "unit" else t
                else:
                    d.pop(k, None)
        except ValueError as ex:
            err = str(ex)
        self.fh[k].configure(text=err or "")
        self.tree.item(str(self.cur), values=self._row(d))
        self._changed()

    def _changed(self):
        self.dirty = True
        self._title()
        self._validate()

    def _toggle_verified(self):
        if self.verified.get():
            ConfirmDialog(self.app, "Mark this preset as verified?",
                          "Mark as verified ONLY after comparing the values with the real device.\n\n"
                          "Read every point with this preset from the actual device and check them against the "
                          "device's own display, configuration software or documentation (including the writable "
                          "points). A verified preset no longer shows the 'not verified' warning, and writes are not "
                          "confirmed any more.\n\nRecord who checked it, which device (model, serial, firmware) and "
                          "the date in the 'Verified note'.",
                          "I compared it - mark verified", lambda: (self.e_vnote.focus_set(), self._changed()),
                          lambda: self.verified.set(False), parent=self.w)
        else:
            self._changed()

    def build(self):
        """The dict that would be saved (shares point dicts with the editor)."""
        d = self.data
        for k in ("name", "manufacturer", "model"):
            if self.hv[k].get().strip() or k in d:
                d[k] = self.hv[k].get().strip()
        d["verified"] = bool(self.verified.get())
        if d["verified"]:
            d["verified_note"] = self.vnote.get().strip()
        else:
            d.pop("verified_note", None)
        return d

    def _validate(self):
        bad = [lab for k, lab in self.FIELDS if self.fh[k].cget("text")]
        try:
            pr = Preset(json.loads(json.dumps(self.build())))
            err = None
        except (PresetError, ValueError, TypeError, KeyError) as e:
            err, pr = str(e) or repr(e), None
        if bad:
            err = "fix the field(s) marked in red: " + ", ".join(bad) + (f"   ({err})" if err else "")
        elif err is None and self.verified.get() and not self.vnote.get().strip():
            err = "a verified preset needs a 'Verified note' (who / which device / date)"
        self.valid = err is None
        if err:
            self.l_valid.configure(text=f"✖ Not valid: {err}", style="SBad.TLabel")
        else:
            self.l_valid.configure(text=f"✔ Valid preset: {len(pr.points)} points, groups: "
                                        f"{', '.join(pr.groups()) or '-'}", style="SOk.TLabel")
        return self.valid

    # ----- point list -----
    def _unique(self, base):
        names = {str(d.get("name")) for d in self.data["points"]}
        if base not in names:
            return base
        n = 2
        while f"{base} {n}" in names:
            n += 1
        return f"{base} {n}"

    def add(self, point=None):
        base = self.data.get("address_base", 0)
        if point is None:
            end = max((int(d.get("address", 0)) + (int(d.get("length", 1) or 1) if d.get("type") == "string" else
                                                    TYPE_WORDS.get(d.get("type", "uint16"), 1))
                       for d in self.data["points"] if d.get("fc", 3) == 3), default=base)
            point = {"name": "New point", "address": end, "fc": 3, "type": "uint16", "access": "r",
                     "group": (self.data["points"][self.cur].get("group", "default")
                               if self.cur is not None and self.data["points"] else "default")}
        point["name"] = self._unique(point["name"])
        self.data["points"].append(point)
        self._fill_list()
        self.select(len(self.data["points"]) - 1)
        self._changed()

    def duplicate(self):
        if self.cur is None:
            return
        d = json.loads(json.dumps(self.data["points"][self.cur]))
        d["name"] = str(d.get("name", "point")) + " copy"
        self.add(d)

    def remove(self):
        if self.cur is None:
            return
        del self.data["points"][self.cur]
        i = min(self.cur, len(self.data["points"]) - 1)
        self.cur = None
        self._fill_list()
        if i >= 0:
            self.select(i)
        else:
            self._enable_form(False)
        self._changed()

    def move(self, dlt):
        i = self.cur
        if i is None or not 0 <= i + dlt < len(self.data["points"]):
            return
        pts = self.data["points"]
        pts[i], pts[i + dlt] = pts[i + dlt], pts[i]
        self._fill_list()
        self.select(i + dlt)
        self._changed()

    # ----- saving -----
    def suggest(self):
        pdir, lib = preset_dir(), os.path.join(preset_dir(), "library")
        if self.path and in_dir(self.path, pdir) and not in_dir(self.path, lib) and \
                os.path.splitext(os.path.basename(self.path))[0].endswith("_custom"):
            return os.path.dirname(self.path), os.path.basename(self.path)  # already the user's own copy
        if self.path:
            stem = os.path.splitext(os.path.basename(self.path))[0]
        else:
            stem = self.stem or re.sub(r"[^A-Za-z0-9]+", "_", self.hv["name"].get()).strip("_").lower() or "preset"
        if not stem.endswith("_custom"):
            stem += "_custom"
        return pdir, stem + ".json"

    def save_as(self):
        if not self._validate():
            self.l_msg.configure(text="Cannot save - the preset is not valid (see the red line above)",
                                 style="SBad.TLabel")
            return None
        folder, fname = self.suggest()
        path = filedialog.asksaveasfilename(parent=self.w, title="Save preset as", initialdir=folder,
                                            initialfile=fname, defaultextension=".json",
                                            filetypes=[("Preset JSON", "*.json")])
        if path:
            return self.save_to(path)
        return None

    def save_to(self, path, confirmed=False):
        if not self._validate():
            self.l_msg.configure(text="Cannot save - the preset is not valid", style="SBad.TLabel")
            return False
        lib = os.path.join(preset_dir(), "library")
        if in_dir(path, lib) and not confirmed:
            exists = os.path.exists(path)
            ConfirmDialog(self.app, "Save into the library folder?",
                          f"{path}\n\npresets\\library holds the presets shipped with mbtool."
                          + ("  This OVERWRITES the existing library file.\n" if exists else "\n")
                          + "\nRecommended: save a copy in the presets folder with a '_custom' name instead "
                            f"({self.suggest()[1]}).",
                          "Save into library", lambda: self.save_to(path, True), parent=self.w)
            return False
        d = self.build()
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, path)
        except OSError as e:
            self.l_msg.configure(text=f"Save failed: {e}", style="SBad.TLabel")
            return False
        self.path, self.dirty = path, False
        self.origin = path
        self._title()
        self.l_msg.configure(text=f"Saved to {path}", style="SOk.TLabel")
        app = self.app
        if app.preset and app.preset["path"] and os.path.normcase(os.path.abspath(app.preset["path"])) == \
                os.path.normcase(os.path.abspath(path)):
            app.use_preset(Preset(json.loads(json.dumps(d))), path, json.loads(json.dumps(d)))  # reload the active one
        if self.on_saved:
            self.on_saved(path)
        return True

    def close(self, force=False):
        if self.dirty and not force:
            ConfirmDialog(self.app, "Discard changes?", "This preset has unsaved changes. Close the editor and "
                          "discard them?", "Discard changes", lambda: self.close(True), parent=self.w)
            return
        if self.alive():
            self.w.destroy()


class TrafficView:
    """Live list of a traffic deque (Master.traffic or SimSlave.traffic), polled from the Tk thread with after().
    source() -> (deque or None, stats text, connected bool, hint text when not connected)."""
    MAX_ROWS, MAX_KEEP = 3000, 20000
    COLS = (("time", "Time", 96, "w", False), ("dir", "Dir", 44, "center", False), ("unit", "Unit", 44, "center", False),
            ("pdu", "PDU (hex)", 330, "w", False), ("text", "Decoded (protocol addresses)", 380, "w", True))

    def __init__(self, parent, app, source, toplevel, rx_tip=None):
        self.app, s = app, app.s
        self.source, self.w = source, toplevel
        self.frame = ttk.Frame(parent)
        self.entries, self.src, self.last = [], None, None
        tb = ttk.Frame(self.frame, padding=(8, 6))
        tb.pack(fill="x")
        self.tb = tb
        self.auto = tk.BooleanVar(value=True)
        self.paused = tk.BooleanVar(value=False)
        self.filter = tk.StringVar(value="All")
        cb = ttk.Checkbutton(tb, text="Auto-scroll", variable=self.auto)
        cb.pack(side="left")
        cb2 = ttk.Checkbutton(tb, text="Pause", variable=self.paused, command=self._unpause)
        cb2.pack(side="left", padx=(10, 0))
        Tooltip(cb2, "Freeze the list. Traffic is still collected and shown when you untick Pause.")
        ttk.Label(tb, text="Show:").pack(side="left", padx=(16, 4))
        fc = ttk.Combobox(tb, textvariable=self.filter, values=["All", "TX", "RX", "ERR"], width=6, state="readonly")
        fc.pack(side="left")
        if rx_tip:
            Tooltip(fc, rx_tip)
        fc.bind("<<ComboboxSelected>>", lambda e: self.rebuild())
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(tb, text="Clear", command=self.clear).pack(side="left")
        bc = ttk.Button(tb, text="Copy", command=self.copy)
        bc.pack(side="left", padx=(4, 0))
        Tooltip(bc, "Copy the selected lines (all shown lines if none selected)   (Ctrl+C)")
        ttk.Button(tb, text="Save...", command=self.save).pack(side="left", padx=(4, 0))
        self.l_stats = ttk.Label(tb, style="Info.TLabel")
        self.l_stats.pack(side="right")
        ttk.Separator(self.frame).pack(fill="x")
        sb = ttk.Frame(self.frame, padding=(8, 2, 8, 4))
        sb.pack(fill="x", side="bottom")
        self.l_msg = ttk.Label(sb, style="SInfo.TLabel")
        self.l_msg.pack(side="left")
        self.hint = ttk.Label(self.frame, text="", style="Warn.TLabel")
        tf = ttk.Frame(self.frame)
        tf.pack(fill="both", expand=True, padx=8, pady=(4, 4))
        self.tf = tf
        mono = tkfont.Font(family="Consolas", size=10)
        self.font = mono
        st = ttk.Style(toplevel)
        st.configure("Traffic.Treeview", font=mono, rowheight=mono.metrics("linespace") + int(5 * s))
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in self.COLS], show="headings", style="Traffic.Treeview")
        for cid, lab, wd, anc, stretch in self.COLS:
            self.tree.heading(cid, text=lab, anchor="w" if anc == "w" else anc)
            self.tree.column(cid, width=int(wd * s), anchor=anc, stretch=stretch, minwidth=int(30 * s))
        self.tree.tag_configure("TX", foreground="#1a64d6")
        self.tree.tag_configure("RX", foreground="#1e7b34")
        self.tree.tag_configure("ERR", foreground="#c5221f", background="#fdecea")
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<Control-c>", lambda e: (self.copy(), "break")[1])
        self.tree.bind("<Control-a>", lambda e: (self.tree.selection_set(self.tree.get_children()), "break")[1])
        self._tick()

    def alive(self):
        try:
            return bool(self.frame.winfo_exists())
        except tk.TclError:
            return False

    @staticmethod
    def _snap(dq):
        for _ in range(3):
            try:
                return list(dq)
            except RuntimeError:  # mutated while copying (should not happen under the GIL)
                continue
        return []

    def _new_from(self, dq):
        snap = self._snap(dq)
        new = snap
        if self.last is not None:
            for i in range(len(snap) - 1, -1, -1):
                if snap[i] is self.last:
                    new = snap[i + 1:]
                    break
        if snap:
            self.last = snap[-1]
        return new

    def collect(self):
        """Entries added since the last call (also drains the previous source after a reconnect / switch)."""
        tr = self.source()[0]
        new = []
        if self.src is not None:
            new = self._new_from(self.src)
        if tr is not None and tr is not self.src:
            self.src, self.last = tr, None
            new += self._new_from(tr)
        return new

    def reset(self):
        """The user picked another source: forget the old one."""
        self.src, self.last = None, None
        self.clear()

    def _match(self, e):
        f = self.filter.get()
        return f == "All" or e["dir"] == f

    @staticmethod
    def _tstr(t):
        return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t % 1 * 1000):03d}"

    def _insert(self, e):
        self.tree.insert("", "end", values=(self._tstr(e["t"]), e["dir"], e["unit"], e["pdu_hex"], e["text"]),
                         tags=(e["dir"],))

    def _tick(self):
        if not self.alive():
            return
        try:
            new = self.collect()
            if new:
                self.entries.extend(new)
                if len(self.entries) > self.MAX_KEEP:
                    del self.entries[:len(self.entries) - self.MAX_KEEP]
                if not self.paused.get():
                    self._show(new)
            self._status()
        finally:
            self.frame.after(200, self._tick)

    def _show(self, new):
        shown = [e for e in new if self._match(e)]
        for e in shown[-self.MAX_ROWS:]:
            self._insert(e)
        kids = self.tree.get_children()
        if len(kids) > self.MAX_ROWS:
            self.tree.delete(*kids[:len(kids) - self.MAX_ROWS])
            kids = self.tree.get_children()
        if shown and self.auto.get() and kids:
            self.tree.see(kids[-1])

    def _unpause(self):
        if not self.paused.get():
            self.rebuild()

    def rebuild(self):
        self.tree.delete(*self.tree.get_children())
        self._show(self.entries)
        self._status()

    def _status(self):
        _dq, stats, connected, idle = self.source()
        if not connected:
            self.hint.configure(text=idle if not self.entries else idle.split(" - ")[0] +
                                " - showing the traffic collected so far.")
            if not self.hint.winfo_ismapped():
                self.hint.pack(fill="x", padx=10, pady=(4, 0), before=self.tf)
        elif self.hint.winfo_ismapped():
            self.hint.pack_forget()
        if stats:
            self.l_stats.configure(text=stats)
        n = len(self.tree.get_children())
        self.l_msg.configure(text=f"{n} lines shown   ({len(self.entries)} collected)"
                                  + ("   -   PAUSED" if self.paused.get() else ""), style="SInfo.TLabel")

    def clear(self):
        self.entries = []
        self.tree.delete(*self.tree.get_children())
        self._status()

    def _lines(self, items):
        return ["\t".join(str(v) for v in self.tree.item(i, "values")) for i in items]

    def copy(self):
        items = self.tree.selection() or self.tree.get_children()
        lines = self._lines(items)
        self.w.clipboard_clear()
        self.w.clipboard_append("\n".join(lines))
        self.l_msg.configure(text=f"Copied {len(lines)} line(s)")
        return lines

    def save(self, path=None):
        path = path or filedialog.asksaveasfilename(
            parent=self.w, title="Save traffic", defaultextension=".txt",
            initialfile=time.strftime("mbtool_traffic_%Y%m%d_%H%M%S.txt"),
            filetypes=[("Text", "*.txt"), ("CSV", "*.csv")])
        if not path:
            return None
        rows = [e for e in self.entries if self._match(e)]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                if path.lower().endswith(".csv"):
                    wr = csv.writer(f)
                    wr.writerow(["Time", "Direction", "Unit", "PDU", "Decoded"])
                    for e in rows:
                        wr.writerow([stamp(e["t"]), e["dir"], e["unit"], e["pdu_hex"], e["text"]])
                else:
                    for e in rows:
                        f.write(f"{stamp(e['t'])}  {e['dir']:<3}  {e['unit']:>3}  {e['pdu_hex']:<40}  {e['text']}\n")
        except OSError as e:
            self.l_msg.configure(text=f"Save failed: {e}", style="SBad.TLabel")
            return None
        self.l_msg.configure(text=f"Saved {len(rows)} lines to {path}", style="SInfo.TLabel")
        return path


class TrafficWindow:
    """F8: master traffic of the main window or of a device / RS-485 bus from the Devices window."""

    def __init__(self, app):
        self.app, s = app, app.s
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Traffic Monitor")
        help_key(w, "traffic")
        w.geometry(f"{int(920 * s)}x{int(480 * s)}")
        w.minsize(int(560 * s), int(260 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        top = ttk.Frame(w, padding=(8, 6, 8, 0))
        top.pack(fill="x")
        ttk.Label(top, text="Source:").pack(side="left", padx=(0, 4))
        self.srcvar = tk.StringVar(value="Main window")
        self.cb_src = ttk.Combobox(top, textvariable=self.srcvar, width=28, state="readonly",
                                   postcommand=self._fill_sources)
        self.cb_src.pack(side="left")
        Tooltip(self.cb_src, "Main window poll, or a device of the Devices window.\n"
                             "Devices on one RS-485 bus share a master: their traffic is shown together.")
        self.cb_src.bind("<<ComboboxSelected>>", lambda e: self.view.reset())
        self.l_src = ttk.Label(top, text="", style="Info.TLabel")
        self.l_src.pack(side="left", padx=(10, 0))
        self.b_help = add_help_button(app, w, "traffic", parent=top, side="right")
        self.view = TrafficView(w, app, self._source, w)
        self.view.frame.pack(fill="both", expand=True)
        self.tree = self.view.tree
        w.bind("<F8>", lambda e: self.close())
        w.bind("<Control-c>", lambda e: (self.view.copy(), "break")[1])

    def _fill_sources(self):
        self.cb_src.configure(values=[self.app.source_label(n) for n in self.app.source_names()])

    def select_source(self, src):
        self.srcvar.set(self.app.source_label(src))
        self.view.reset()

    def _source(self):
        src = self.app.source_from_label(self.srcvar.get())
        if src == "":
            m = self.app.m
            connected = m is not None and self.app.state == "on"
            self.l_src.configure(text="")
            idle = ("Not connected - press F5 (Connect) in the main window; every request, reply and error will be "
                    "listed here.")
        else:
            m = self.app.devpoll.master_for(src)
            connected = m is not None
            dv = self.app.dev_by_name(src)
            shared = dv is not None and dv["conn"]["mode"] != "TCP" and sum(
                1 for d in self.app.devices if mon.bus_key(d) == mon.bus_key(dv)) > 1
            self.l_src.configure(text=f"bus {dv['conn']['com']} - shared by several devices" if shared else "")
            idle = f"Device '{src}' is not being polled - start it in the Devices window."
        tr = getattr(m, "traffic", None)
        stats = None
        if m is not None:
            st = m.stats
            stats = f"Requests OK: {st.get('ok', 0)}    Errors: {st.get('errors', 0)}"
        return tr, stats, connected, idle

    # compatibility with older callers / tests
    @property
    def entries(self):
        return self.view.entries

    def collect(self):
        return self.view.collect()

    def copy(self):
        return self.view.copy()

    def save(self, path=None):
        return self.view.save(path)

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        if self.alive():
            self.w.destroy()


def inspector_point_type(group, label):
    """Interpretation row -> (preset point spec dict, None) or (None, reason). See monitor.inspector_point_spec."""
    return mon.inspector_point_spec(group, label)


def spec_text(spec):
    t = spec["type"]
    out = f"preset type {t}"
    if t == "string":
        out += f", length {spec['length']} register(s)"
    elif TYPE_WORDS[t] > 1:
        out += f", word_order {spec['word_order']}"
    if spec.get("byte_swap"):
        out += ", byte_swap"
    return out


class Inspector:
    """F4: the selected register (+ the next 1 or 3, from the latest poll) in every interpretation."""

    def __init__(self, app):
        from interpret import interpret
        self.interpret = interpret
        self.app, s = app, app.s
        self.s = s
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Value Inspector")
        help_key(w, "inspector")
        w.transient(app.root)
        w.geometry(f"{int(560 * s)}x{int(560 * s)}")
        w.minsize(int(440 * s), int(360 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: self.close())
        w.bind("<F4>", lambda e: self.close())
        body = ttk.Frame(w, padding=10)
        body.pack(fill="both", expand=True)
        self.l_title = ttk.Label(body, text="", style="Conn.TLabel", wraplength=int(520 * s), justify="left")
        self.l_title.pack(fill="x")
        nf = ttk.Frame(body)
        nf.pack(fill="x", pady=(6, 0))
        self.b_help = add_help_button(app, w, "inspector", parent=nf, side="right")
        ttk.Label(nf, text="Combine:").pack(side="left", padx=(0, 6))
        self.n = tk.IntVar(value=2)
        self.rb = {}
        for n in (1, 2, 4):
            r = ttk.Radiobutton(nf, text=f"{n} register{'s' if n > 1 else ''}", value=n, variable=self.n,
                                command=lambda: self.refresh(force=True))
            r.pack(side="left", padx=(0, 10))
            self.rb[n] = r
        self.l_words = ttk.Label(body, text="", font=("Consolas", 10))
        self.l_words.pack(fill="x", pady=(4, 0))
        self.l_hint = ttk.Label(body, text="", style="Warn.TLabel", wraplength=int(520 * s), justify="left")
        self.l_hint.pack(fill="x")
        bf = ttk.Frame(body)
        bf.pack(side="bottom", fill="x", pady=(8, 0))
        self.b_use = ttk.Button(bf, text="Use as preset point...", command=self.use_as_point)
        self.b_use.pack(side="left")
        self.tip_use = Tooltip(self.b_use, "Select an interpretation row first")
        self.b_copy = ttk.Button(bf, text="Copy", command=self.copy)
        self.b_copy.pack(side="left", padx=(6, 0))
        Tooltip(self.b_copy, "Copy the selected row's value   (double-click or Ctrl+C)")
        ttk.Button(bf, text="Close", command=self.close).pack(side="right")
        self.l_use = ttk.Label(body, text="", style="Info.TLabel", wraplength=int(520 * s), justify="left")
        self.l_use.pack(side="bottom", fill="x", pady=(4, 0))
        tf = ttk.Frame(body)
        tf.pack(fill="both", expand=True, pady=(6, 0))
        self.tree = ttk.Treeview(tf, columns=("value",), show="tree headings", selectmode="extended",
                                 style=tree_style(app.root, s))
        self.tree.heading("#0", text="Interpretation", anchor="w")
        self.tree.heading("value", text="Value", anchor="w")
        self.tree.column("#0", width=int(250 * s), stretch=False)
        self.tree.column("value", width=int(240 * s), stretch=True)
        self.tree.tag_configure("group", foreground="#2b2f36", background=COL["head"])
        self.tree.tag_configure("flash", background=COL["flash"])
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", lambda e: self.copy())
        self.tree.bind("<Control-c>", lambda e: (self.copy(), "break")[1])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_use())
        self.src_key, self.rows, self.flash, self.layout = None, {}, {}, None
        self.row_meta = {}
        self.src = None
        try:
            r = app.root
            w.geometry(f"+{r.winfo_rootx() + r.winfo_width() - int(580 * s)}+{r.winfo_rooty() + int(80 * s)}")
        except tk.TclError:
            pass
        self.refresh(force=True)
        self._tick()

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def _tick(self):
        if not self.alive():
            return
        try:
            self.refresh()
        finally:
            self.w.after(250, self._tick)

    def refresh(self, force=False):
        src, reason = self.app.inspect_source()
        if src is None:
            self.src = None
            self.src_key = None
            self.l_title.configure(text="Nothing selected")
            self.l_hint.configure(text=reason)
            self.l_words.configure(text="")
            if self.tree.get_children():
                self.tree.delete(*self.tree.get_children())
                self.layout = None
            self._update_use()
            return
        if src["key"] != self.src_key:  # followed a new cell / point: start with its natural width
            self.src_key = src["key"]
            self.n.set(src["default_n"] if src["default_n"] in (1, 2, 4) else 1)
            force = True
        self.src = src
        words = src["words"]
        avail = len(words)
        for n, r in self.rb.items():
            r.state(["!disabled"] if n <= max(avail, 1) else ["disabled"])
        n = self.n.get()
        hint = ""
        if avail == 0:
            hint = "No value yet - waiting for a successful poll (connect with F5)."
        elif n > avail:
            hint = (f"Only {avail} register(s) from this one onward were in the latest poll - "
                    f"showing {max(k for k in (1, 2, 4) if k <= avail)}.  Increase Quantity to see more.")
            n = max(k for k in (1, 2, 4) if k <= avail)
        self.l_title.configure(text=src["title"])
        self.l_hint.configure(text=hint)
        use = words[:n]
        self.l_words.configure(text=("Raw: " + "  ".join(f"0x{x:04X}" for x in use) +
                                     ("      (from the latest poll)" if use else "")) if use else "")
        rows = self.interpret(use) if use else []
        self._show(rows, force)
        self._update_use()

    def _show(self, rows, force):
        layout = tuple((g, l) for g, l, _t in rows)
        now = time.monotonic()
        if force or layout != self.layout:
            sel = [self.row_meta.get(i) for i in self.tree.selection()]
            # rows that exist before and after keep their flash when the set of rows changes
            old = {} if force else {k: self.tree.set(i, "value") for k, i in self.rows.items() if self.tree.exists(i)}
            self.tree.delete(*self.tree.get_children())
            self.rows, self.row_meta, self.flash = {}, {}, {}
            groups = {}
            for g, l, t in rows:
                if g not in groups:
                    groups[g] = self.tree.insert("", "end", text=g, open=True, tags=("group",))
                ch = (g, l) in old and old[(g, l)] != t
                iid = self.tree.insert(groups[g], "end", text=l, values=(t,), tags=("flash",) if ch else ())
                self.rows[(g, l)] = iid
                self.row_meta[iid] = (g, l)
                if ch:
                    self.flash[iid] = now
            self.layout = layout
            keep = [self.rows[k] for k in sel if k in self.rows]
            if keep:
                self.tree.selection_set(keep)
            return
        for g, l, t in rows:
            iid = self.rows[(g, l)]
            if self.tree.set(iid, "value") != t:
                self.tree.set(iid, "value", t)
                self.flash[iid] = now
                self.tree.item(iid, tags=("flash",))
        for iid, t0 in list(self.flash.items()):
            if now - t0 >= 1.2:
                del self.flash[iid]
                if self.tree.exists(iid):
                    self.tree.item(iid, tags=())

    def selected_row(self):
        sel = [i for i in self.tree.selection() if i in self.row_meta]
        return (sel[0], self.row_meta[sel[0]]) if sel else (None, None)

    def _update_use(self):
        iid, meta = self.selected_row()
        if meta is None or self.src is None:
            self.b_use.state(["disabled"])
            self.tip_use.text = "Select an interpretation row first"
            self.l_use.configure(text="")
            return
        spec, info = inspector_point_type(*meta)
        if spec is None:
            self.b_use.state(["disabled"])
            self.tip_use.text = info
            self.l_use.configure(text=f"Cannot become a preset point: {info}")
        else:
            self.b_use.state(["!disabled"])
            lab = addr_label(self.src["fc"], self.src["addr"], self.app.addr_base())
            self.tip_use.text = f"New preset point: {spec_text(spec)}, FC{self.src['fc']:02d}, address {lab}"
            self.l_use.configure(text="→ " + spec_text(spec))

    def copy(self):
        items = [i for i in self.tree.selection() if i in self.row_meta]
        if not items:
            return None
        if len(items) == 1:
            text = self.tree.set(items[0], "value")
        else:
            text = "\n".join(f"{self.row_meta[i][0]}\t{self.row_meta[i][1]}\t{self.tree.set(i, 'value')}" for i in items)
        self.w.clipboard_clear()
        self.w.clipboard_append(text)
        g, l = self.row_meta[items[0]]
        self.l_use.configure(text=f"Copied: {text}" if len(items) == 1 else f"Copied {len(items)} rows")
        return text

    def use_as_point(self):
        iid, meta = self.selected_row()
        if meta is None or self.src is None:
            return None
        spec, _why = inspector_point_type(*meta)
        if spec is None:
            return None
        src = self.src
        app = self.app
        if app.preset:
            data, path = json.loads(json.dumps(app.preset["data"])), app.preset["path"]
        else:
            data, path = {"name": "New device", "manufacturer": "", "model": "", "verified": False,
                          "connection": {"unit": app.p["unit"] if app.p else 1}, "address_base": 0, "points": []}, None
        base = int(data.get("address_base", 0) or 0)
        t = spec["type"]
        pt = {"name": f"Register {src['addr']}", "address": src["addr"] + base, "fc": src["fc"], "type": t,
              "access": "r", "group": "inspected"}
        if t == "string":
            pt["length"] = spec["length"]
        elif TYPE_WORDS[t] > 1:
            pt["word_order"] = spec["word_order"]
        if spec.get("byte_swap"):
            pt["byte_swap"] = True
        ed = PresetEditor(app, data, path, on_saved=(app.presets_dlg._saved if app.presets_dlg and
                                                     app.presets_dlg.alive() else None),
                          origin=(f"{os.path.basename(path)} + inspected point" if path else "new preset from inspector"))
        ed.add(pt)
        return ed

    def close(self):
        if self.alive():
            self.w.destroy()


# ---------------- monitoring windows: log options, trend, alerts, devices ----------------

class MessageDialog(Dialog):
    """Non-blocking information window (used instead of message boxes)."""

    def __init__(self, app, title, text, help=None):
        super().__init__(app, title)
        s = app.s
        ttk.Label(self.body, text=text, wraplength=int(560 * s), justify="left").pack(fill="x")
        bf = ttk.Frame(self.body)
        bf.pack(fill="x", pady=(12, 0))
        ttk.Button(bf, text="OK", command=self.close, default="active").pack(side="right")
        if help:
            self.b_help = add_help_button(app, self.w, help, parent=bf, side="left")
        self.w.bind("<Return>", lambda e: self.close())
        self.place()


DELIMS = {"Comma  ,": ",", "Semicolon  ;": ";", "Tab": "\t", "Pipe  |": "|"}
DECIMALS = {"Point  1.5": ".", "Comma  1,5": ","}
TIME_FMT_NAMES = {"iso": "ISO date + time", "iso_ms": "ISO date + time + ms", "unix": "Unix seconds",
                  "excel": "Excel date number"}
ENCODINGS = {"utf-8": "UTF-8", "utf-8-sig": "UTF-8 with BOM for Excel"}


class ExportWindow:
    """Tools > Data export: send every Devices reading to an MQTT broker and / or a SQLite file (export.py)."""

    def __init__(self, app):
        import export
        self.app, s = app, app.s
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Data export")
        help_key(w, "export")
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self.close)
        c = export.norm_export(app.cfg.get("export"))
        b = ttk.Frame(w, padding=12)
        b.pack(fill="both", expand=True)
        ttk.Label(b, text="Every reading of the devices in the Devices window (F11) is sent while polling runs.\n"
                          "Sending happens in the background: a slow broker or disk never slows the polling.",
                  style="Info.TLabel", justify="left").pack(anchor="w")
        self.v = {}
        mf = ttk.LabelFrame(b, text="MQTT broker", padding=(10, 4, 10, 6))
        mf.pack(fill="x", pady=(8, 0))
        self.mq_on = tk.BooleanVar(value=c["mqtt"]["on"])
        ttk.Checkbutton(mf, text="Publish to MQTT", variable=self.mq_on).grid(row=0, column=0, columnspan=4, sticky="w")
        self.hints = {}
        rows = (("host", "Broker host", 26), ("port", "Port", 7), ("user", "User name", 18),
                ("client_id", "Client ID", 18), ("prefix", "Topic prefix", 26), ("min_interval_s", "Min. interval (s)", 7))
        for i, (k, lab, wd) in enumerate(rows):
            r, col = 1 + i // 2, (i % 2) * 2
            ttk.Label(mf, text=lab + ":").grid(row=r * 2 - 1, column=col, sticky="e", padx=(0 if col == 0 else 14, 4))
            self.v[k] = tk.StringVar(value=c["mqtt"][k])
            ttk.Entry(mf, textvariable=self.v[k], width=wd).grid(row=r * 2 - 1, column=col + 1, sticky="w")
            self.hints[k] = ttk.Label(mf, text="", style="Hint.TLabel")
            self.hints[k].grid(row=r * 2, column=col + 1, sticky="w")
        ttk.Label(mf, text="Password:").grid(row=7, column=0, sticky="e", padx=(0, 4))
        self.pw = tk.StringVar(value=app.export_pw)
        ttk.Entry(mf, textvariable=self.pw, width=26, show="•").grid(row=7, column=1, sticky="w")
        ttk.Label(mf, text="kept for this session only, never saved", style="Info.TLabel").grid(
            row=7, column=2, columnspan=2, sticky="w", padx=(14, 0))
        of = ttk.Frame(mf)
        of.grid(row=8, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.tls = tk.BooleanVar(value=c["mqtt"]["tls"])
        ttk.Checkbutton(of, text="TLS (usually port 8883)", variable=self.tls).pack(side="left")
        self.retain = tk.BooleanVar(value=c["mqtt"]["retain"])
        cr = ttk.Checkbutton(of, text="Retain", variable=self.retain)
        cr.pack(side="left", padx=(12, 0))
        Tooltip(cr, "Broker keeps the last message per topic for clients that subscribe later")
        ttk.Label(of, text="Payload:").pack(side="left", padx=(16, 4))
        self.style = tk.StringVar(value=c["mqtt"]["style"])
        ttk.Radiobutton(of, text="one JSON per device", value="json", variable=self.style).pack(side="left")
        ttk.Radiobutton(of, text="one topic per point", value="topics", variable=self.style).pack(side="left",
                                                                                                  padx=(8, 0))
        self.l_topic = ttk.Label(mf, text="", style="Info.TLabel", font=("Consolas", 9))
        self.l_topic.grid(row=9, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(mf, text="Test connection", command=self.test).grid(row=10, column=0, sticky="w", pady=(6, 0))
        self.l_test = ttk.Label(mf, text="", style="Info.TLabel")
        self.l_test.grid(row=10, column=1, columnspan=3, sticky="w", pady=(6, 0), padx=(6, 0))
        sf = ttk.LabelFrame(b, text="Database (SQLite file)", padding=(10, 4, 10, 6))
        sf.pack(fill="x", pady=(8, 0))
        self.sq_on = tk.BooleanVar(value=c["sqlite"]["on"])
        ttk.Checkbutton(sf, text="Write to a SQLite database", variable=self.sq_on).grid(row=0, column=0,
                                                                                       columnspan=3, sticky="w")
        self.v["path"] = tk.StringVar(value=c["sqlite"]["path"])
        ttk.Entry(sf, textvariable=self.v["path"], width=56).grid(row=1, column=0, sticky="we")
        ttk.Button(sf, text="Browse...", command=self.browse).grid(row=1, column=1, padx=(4, 0))
        ttk.Label(sf, text="Table readings(ts, time, device, point, value, text, ok) - open it with DB Browser for "
                           "SQLite, Excel (ODBC), Python, Grafana ...", style="Info.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        bf = ttk.Frame(b)
        bf.pack(fill="x", pady=(10, 0))
        self.b_run = ttk.Button(bf, text="Start export", command=self.toggle)
        self.b_run.pack(side="left")
        add_help_button(app, w, "export", parent=bf, side="right")
        self.l_state = ttk.Label(b, text="", style="Conn.TLabel")
        self.l_state.pack(anchor="w", pady=(8, 0))
        self.l_stats = ttk.Label(b, text="", style="Info.TLabel", justify="left")
        self.l_stats.pack(anchor="w")
        self.l_err = ttk.Label(b, text="", style="Hint.TLabel", wraplength=int(560 * s), justify="left")
        self.l_err.pack(anchor="w")
        for var in list(self.v.values()) + [self.style]:
            var.trace_add("write", lambda *_: self._changed())
        self._changed()
        self.job = None
        self._tick()

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        self.save_cfg()
        if self.job is not None:
            try:
                self.w.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if self.alive():
            self.w.destroy()

    def cfg(self):
        import export
        m = {k: self.v[k].get().strip() for k in ("host", "port", "user", "client_id", "prefix", "min_interval_s")}
        m.update(on=bool(self.mq_on.get()), tls=bool(self.tls.get()), retain=bool(self.retain.get()),
                 style=self.style.get())
        return export.norm_export({"mqtt": m, "sqlite": {"on": bool(self.sq_on.get()),
                                                         "path": self.v["path"].get().strip()}})

    def save_cfg(self):
        self.app.cfg["export"] = self.cfg()
        self.app.export_pw = self.pw.get()
        save_settings(self.app.cfg)

    def _changed(self):
        import export
        c = self.cfg()["mqtt"]
        errs = export.check_mqtt(c) if self.mq_on.get() else {}
        for k, h in self.hints.items():
            h.configure(text=errs.get(k, ""))
        pre = c["prefix"] or "mbtool"
        self.l_topic.configure(text=(f"Topic: {pre}/<device>   payload {{\"ts\": ..., \"ok\": true, \"values\": {{...}}}}"
                                     if c["style"] == "json" else
                                     f"Topics: {pre}/<device>/<point> = value,   {pre}/<device>/status"))

    def browse(self):
        p = filedialog.asksaveasfilename(parent=self.w, title="SQLite database file", defaultextension=".db",
                                         filetypes=[("SQLite database", "*.db *.sqlite"), ("All files", "*.*")],
                                         confirmoverwrite=False, initialfile=os.path.basename(self.v["path"].get())
                                         or "mbtool_data.db")
        if p:
            self.v["path"].set(p)

    def test(self):
        import export
        self.l_test.configure(text="Connecting...", style="Info.TLabel")
        self.w.update_idletasks()
        err = export.test_mqtt(self.cfg(), self.pw.get())
        self.l_test.configure(text=("OK - published a test message to " + self.cfg()["mqtt"]["prefix"] + "/_test")
                              if err is None else f"Failed: {err}", style="SOk.TLabel" if err is None else "SBad.TLabel")
        return err

    def toggle(self):
        import export
        if self.app.exporter is not None:
            self.app.stop_export()
            self._tick(once=True)
            return None
        self.save_cfg()
        try:
            self.app.start_export(self.cfg(), self.pw.get())
        except (export.ExportError, OSError, ValueError) as e:
            self.l_err.configure(text=f"Not started: {e}")
            return str(e)
        self.l_err.configure(text="")
        self._tick(once=True)
        return None

    def _tick(self, once=False):
        if not self.alive():
            return
        ex = self.app.exporter
        if ex is None:
            self.b_run.configure(text="Start export")
            self.l_state.configure(text="○ Export stopped", foreground=COL["stale"])
            self.l_stats.configure(text="")
        else:
            st = ex.stats
            self.b_run.configure(text="Stop export")
            parts = []
            if ex.mqtt_on:
                parts.append("MQTT " + ("connected" if st["mqtt_connected"] else "connecting..."))
            if ex.sql_on:
                parts.append("SQLite " + os.path.basename(ex.cfg["sqlite"]["path"]))
            running = self.app.devices_running()
            self.l_state.configure(text="● Exporting: " + ",  ".join(parts) + ("" if running else
                                   "   (no device is polling - Devices > Start all)"),
                                   foreground="#1e7b34" if (st["mqtt_connected"] or not ex.mqtt_on) else "#a15c00")
            last = time.strftime("%H:%M:%S", time.localtime(st["last_sent_t"])) if st["last_sent_t"] else "-"
            self.l_stats.configure(text=f"MQTT messages {st['mqtt_sent']}  (last {last}, waiting {st['mqtt_backlog']}, "
                                        f"skipped by min. interval {st['skipped']})   SQLite rows {st['sql_rows']}   "
                                        f"dropped {st['dropped']}")
            err = st["last_error"]
            self.l_err.configure(text=(f"Last problem {time.strftime('%H:%M:%S', time.localtime(st['last_error_t']))}: "
                                       f"{err}") if err else "")
        if not once:
            self.job = self.w.after(1000, self._tick)


class ReportDialog(Dialog):
    """File > Commissioning report: project details, language, PDF; Save writes commission.save()."""
    FIELDS = (("project", "Project"), ("site", "Site"), ("customer", "Customer"), ("engineer", "Commissioned by"))
    LANGS = (("en", "English"), ("th", "ไทย (Thai)"))

    def __init__(self, app):
        super().__init__(app, "Commissioning report")
        add_help_button(app, self.w, "report")
        s = app.s
        r = dict(DEFAULTS["report"], **(app.cfg.get("report") or {}))
        b = self.body
        ttk.Label(b, text="A printable report of the Devices window: every device with state, counters, error types,\n"
                          "response times and its values now, the PLC cycle, the last health test and the bus map.",
                  style="Info.TLabel", justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.v = {}
        for i, (k, lab) in enumerate(self.FIELDS, start=1):
            ttk.Label(b, text=lab + ":").grid(row=i, column=0, sticky="e", padx=(0, 8), pady=2)
            self.v[k] = tk.StringVar(value=r.get(k, ""))
            ttk.Entry(b, textvariable=self.v[k], width=46).grid(row=i, column=1, sticky="we", pady=2)
        ttk.Label(b, text="Notes:").grid(row=5, column=0, sticky="ne", padx=(0, 8), pady=2)
        self.notes = tk.Text(b, width=46, height=5, wrap="word", font=("Segoe UI", 9))
        self.notes.grid(row=5, column=1, sticky="we", pady=2)
        self.notes.insert("1.0", r.get("notes", ""))
        lf = ttk.Frame(b)
        lf.grid(row=6, column=1, sticky="w", pady=(6, 0))
        ttk.Label(b, text="Language:").grid(row=6, column=0, sticky="e", padx=(0, 8), pady=(6, 0))
        self.lang = tk.StringVar(value=r.get("lang", "en") if r.get("lang") in ("en", "th") else "en")
        for val, text in self.LANGS:
            ttk.Radiobutton(lf, text=text, value=val, variable=self.lang).pack(side="left", padx=(0, 12))
        self.pdf = tk.BooleanVar(value=bool(r.get("pdf", True)))
        ttk.Checkbutton(b, text="Also save a PDF (printed with Microsoft Edge)", variable=self.pdf).grid(
            row=7, column=1, sticky="w", pady=(4, 0))
        n = len(app.devices)
        hl = getattr(app, "last_health", None)
        info = f"{n} device(s)" + ("" if n else " - add devices in the Devices window first (F11)")
        info += ",  health test: " + ("included" if hl else "none yet (Devices > Health test)")
        self.l_info = ttk.Label(b, text=info, style="Info.TLabel")
        self.l_info.grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.l_res = ttk.Label(b, text="", style="Hint.TLabel", wraplength=int(480 * s), justify="left")
        self.l_res.grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))
        bf = ttk.Frame(b)
        bf.grid(row=10, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        self.b_save = ttk.Button(bf, text="Save report...", command=self.save, default="active")
        self.b_save.pack(side="right", padx=(0, 6))
        ttk.Button(bf, text="Preview", command=self.preview).pack(side="right", padx=(0, 6))
        b.columnconfigure(1, weight=1)
        self.last = None
        self.place()

    def form(self):
        f = {k: v.get().strip() for k, v in self.v.items()}
        f["notes"] = self.notes.get("1.0", "end").strip()
        f["lang"] = self.lang.get()
        f["pdf"] = bool(self.pdf.get())
        return f

    def _remember(self, f, folder=None):
        r = self.app.cfg.setdefault("report", dict(DEFAULTS["report"]))
        r.update(f)
        if folder:
            r["folder"] = folder
        save_settings(self.app.cfg)

    def default_name(self, f):
        base = re.sub(r"[^\w\-]+", "_", f.get("project") or "mbtool").strip("_") or "mbtool"
        return f"{base}_commissioning_{time.strftime('%Y-%m-%d')}.html"

    def write(self, path, f=None):
        """Write the report to path (+ PDF when ticked). -> (html_path, pdf_path, pdf_error)."""
        import commission
        f = f or self.form()
        data = self.app.commission_data(f)
        html_path, _ = commission.save(data, path, f["lang"], pdf=False)
        pdf_path = pdf_err = None
        if f["pdf"]:
            try:
                self.w.configure(cursor="watch")
                self.w.update_idletasks()
                pdf_path = commission.to_pdf(html_path, os.path.splitext(html_path)[0] + ".pdf")
            except OSError as e:
                pdf_err = str(e)
            finally:
                try:
                    self.w.configure(cursor="")
                except tk.TclError:
                    pass
        self.last = (html_path, pdf_path, pdf_err)
        return self.last

    def save(self, path=None):
        f = self.form()
        if path is None:
            folder = (self.app.cfg.get("report") or {}).get("folder") or os.path.expanduser("~")
            path = filedialog.asksaveasfilename(parent=self.w, title="Save commissioning report",
                                                initialdir=folder, initialfile=self.default_name(f),
                                                defaultextension=".html", filetypes=[("HTML report", "*.html")])
            if not path:
                return None
        try:
            html_path, pdf_path, pdf_err = self.write(path, f)
        except OSError as e:
            self.l_res.configure(text=f"Could not save the report: {e}", style="Hint.TLabel")
            return None
        self._remember(f, os.path.dirname(html_path))
        msg = f"Saved {os.path.basename(html_path)}" + (f" and {os.path.basename(pdf_path)}" if pdf_path else "")
        if pdf_err:
            msg += f"   (PDF: {pdf_err})"
        self.l_res.configure(text=msg, style="Info.TLabel" if not pdf_err else "Warn.TLabel")
        self.app._status(msg, "ok" if not pdf_err else "warn")
        try:
            os.startfile(pdf_path or html_path)  # noqa: S606 - the user's own report file
        except (OSError, AttributeError):
            pass
        return self.last

    def preview(self):
        import webbrowser
        import commission
        f = self.form()
        path = os.path.join(tempfile.gettempdir(), "mbtool_report_preview.html")
        try:
            commission.save(self.app.commission_data(f), path, f["lang"], pdf=False)
        except OSError as e:
            self.l_res.configure(text=f"Could not write the preview: {e}")
            return None
        self._remember(f)
        webbrowser.open("file:///" + path.replace("\\", "/"))
        return path


class LogOptionsDialog(Dialog):
    """CSV log format and rotation (datalog.CsvLog), validated inline; stored in the settings and the workspace."""

    def __init__(self, app):
        super().__init__(app, "Log options")
        self.b_help = add_help_button(app, self.w, "log")
        s = app.s
        o = mon.norm_log_options(app.cfg["log"])
        b = self.body
        self.v = {"rotate": tk.StringVar(value=o["rotate"]), "size_mb": tk.StringVar(value=o["size_mb"]),
                  "keep": tk.StringVar(value=o["keep"]),
                  "delimiter": tk.StringVar(value=next((k for k, v in DELIMS.items() if v == o["delimiter"]), "Comma  ,")),
                  "decimal": tk.StringVar(value=next((k for k, v in DECIMALS.items() if v == o["decimal"]), "Point  1.5")),
                  "time_format": tk.StringVar(value=TIME_FMT_NAMES.get(o["time_format"], TIME_FMT_NAMES["iso_ms"])),
                  "encoding": tk.StringVar(value=o["encoding"])}
        self.keep_on = tk.BooleanVar(value=o["keep_on"])
        self.hints = {}
        rf = ttk.LabelFrame(b, text="New file", padding=(10, 4, 10, 6))
        rf.pack(fill="x")
        for i, (val, text) in enumerate((("none", "Never - append to one file"),
                                         ("daily", "Every day (name_YYYY-MM-DD.csv)"))):
            ttk.Radiobutton(rf, text=text, value=val, variable=self.v["rotate"]).grid(row=i, column=0, sticky="w")
        szf = ttk.Frame(rf)
        szf.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(szf, text="When the file reaches", value="size", variable=self.v["rotate"]).pack(side="left")
        self.e_size = ttk.Entry(szf, textvariable=self.v["size_mb"], width=7)
        self.e_size.pack(side="left", padx=(6, 0))
        ttk.Label(szf, text="MB   (name_YYYY-MM-DD_001.csv, _002, ...)").pack(side="left", padx=(4, 0))
        self.l_rot_hint = ttk.Label(rf, text="", style="Hint.TLabel")
        self.l_rot_hint.grid(row=3, column=0, columnspan=2, sticky="w")
        kf = ttk.Frame(rf)
        kf.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.cb_keep = ttk.Checkbutton(kf, text="Keep only the last", variable=self.keep_on)
        self.cb_keep.pack(side="left")
        self.e_keep = ttk.Entry(kf, textvariable=self.v["keep"], width=6)
        self.e_keep.pack(side="left", padx=(4, 4))
        ttk.Label(kf, text="files").pack(side="left")
        ttk.Label(rf, text="⚠ When on, OLDER rotated files of this log (same name pattern in the same folder) are "
                           "DELETED. Other files are never touched.", style="Warn.TLabel",
                  wraplength=int(470 * s), justify="left").grid(row=6, column=0, columnspan=2, sticky="w")
        self.hints["keep"] = ttk.Label(rf, text="", style="Hint.TLabel")
        self.hints["keep"].grid(row=7, column=0, columnspan=2, sticky="w")
        ff = ttk.LabelFrame(b, text="Format", padding=(10, 4, 10, 6))
        ff.pack(fill="x", pady=(8, 0))
        rows = (("delimiter", "Column separator", list(DELIMS)), ("decimal", "Decimal separator", list(DECIMALS)),
                ("time_format", "Time column", list(TIME_FMT_NAMES.values())))
        for r, (k, lab, vals) in enumerate(rows):
            _l, self.hints[k] = form_row(ff, r, lab, ttk.Combobox(ff, textvariable=self.v[k], values=vals, width=24,
                                                                  state="readonly"))
        ttk.Label(ff, text="Encoding").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=3)
        ef = ttk.Frame(ff)
        ef.grid(row=3, column=1, columnspan=2, sticky="w")
        for val, text in ENCODINGS.items():
            ttk.Radiobutton(ef, text=text, value=val, variable=self.v["encoding"]).pack(side="left", padx=(0, 10))
        ttk.Label(ff, text="Example:").grid(row=4, column=0, sticky="ne", padx=(0, 8), pady=(6, 0))
        self.l_ex = ttk.Label(ff, text="", font=("Consolas", 9), justify="left")
        self.l_ex.grid(row=4, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ff.columnconfigure(2, minsize=int(150 * s))
        self.l_res = ttk.Label(b, text="", style="Hint.TLabel", wraplength=int(470 * s))
        self.l_res.pack(fill="x", pady=(6, 0))
        bf = ttk.Frame(b)
        bf.pack(fill="x", pady=(8, 0))
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        self.b_ok = ttk.Button(bf, text="OK", command=self.ok, default="active")
        self.b_ok.pack(side="right", padx=(0, 6))
        self.w.bind("<Return>", lambda e: self.ok())
        for var in list(self.v.values()) + [self.keep_on]:
            var.trace_add("write", lambda *_: self.validate())
        self.validate()
        self.place()

    def options(self):
        v = self.v
        return {"rotate": v["rotate"].get(), "size_mb": v["size_mb"].get().strip(), "keep_on": self.keep_on.get(),
                "keep": v["keep"].get().strip(), "delimiter": DELIMS.get(v["delimiter"].get(), ","),
                "decimal": DECIMALS.get(v["decimal"].get(), "."),
                "time_format": next((k for k, n in TIME_FMT_NAMES.items() if n == v["time_format"].get()), "iso_ms"),
                "encoding": v["encoding"].get()}

    def validate(self):
        o = self.options()
        self.e_size.configure(state="normal" if o["rotate"] == "size" else "disabled")
        self.e_keep.configure(state="normal" if o["keep_on"] else "disabled")
        _kw, errs = mon.log_kwargs(o)
        for k, h in self.hints.items():
            h.configure(text=errs.get(k, ""))
        self.l_rot_hint.configure(text=errs.get("size_mb") or errs.get("rotate") or "")
        t = time.time()
        d = o["delimiter"]
        num = "230,4" if o["decimal"] == "," else "230.4"
        q = '"' if d == "," and o["decimal"] == "," else ""
        self.l_ex.configure(text=f"Time{d}Voltage [V]{d}Mode\n{TIME_FORMATS[o['time_format']](t)}{d}{q}{num}{q}{d}1"
                                 .replace("\t", "  →  "))
        self.b_ok.state(["disabled"] if errs else ["!disabled"])
        self.l_res.configure(text="Fix the fields marked in red." if errs else "")
        return not errs

    def ok(self):
        if not self.validate():
            return False
        self.close()
        self.app.apply_log_options(self.options())
        return True


class TrendWindow:
    """F10: Canvas trend of up to 8 points of the main window poll or of one device (no matplotlib)."""

    def __init__(self, app):
        self.app, s = app, app.s
        self.s = s
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Trend")
        help_key(w, "trend")
        w.geometry(f"{int(1000 * s)}x{int(580 * s)}")
        w.minsize(int(640 * s), int(360 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.paused_at = None
        self.cursor_x = None
        tb = ttk.Frame(w, padding=(8, 6))
        tb.pack(fill="x")
        ttk.Label(tb, text="Source:").pack(side="left", padx=(0, 4))
        self.srcvar = tk.StringVar(value=app.source_label(app.trend_cfg["source"]))
        self.cb_src = ttk.Combobox(tb, textvariable=self.srcvar, width=20, state="readonly",
                                   postcommand=lambda: self.cb_src.configure(
                                       values=[app.source_label(n) for n in app.source_names()]))
        self.cb_src.pack(side="left")
        self.cb_src.bind("<<ComboboxSelected>>", lambda e: self._source_changed())
        ttk.Label(tb, text="Window:").pack(side="left", padx=(12, 4))
        self.winvar = tk.StringVar(value=app.trend_cfg["window"])
        cbw = ttk.Combobox(tb, textvariable=self.winvar, values=list(mon.TREND_WINDOWS), width=8, state="readonly")
        cbw.pack(side="left")
        Tooltip(cbw, "Time span shown. 'All kept' shows the whole buffer\n"
                     f"(up to {app.trend_buf.max_points} samples per series; older samples are dropped).")
        ttk.Label(tb, text="Y axis:").pack(side="left", padx=(12, 4))
        self.yvar = tk.StringVar(value=app.trend_cfg["ymode"])
        cby = ttk.Combobox(tb, textvariable=self.yvar, values=["Shared", "Separate"], width=9, state="readonly")
        cby.pack(side="left")
        Tooltip(cby, "Shared: one Y axis for all series.\nSeparate: one lane per series, each auto-scaled.")
        for var in (self.winvar, self.yvar):
            var.trace_add("write", lambda *_: self._opts())
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=10)
        self.b_help = add_help_button(app, w, "trend", parent=tb, side="right")
        self.b_pause = ttk.Button(tb, text="Pause", width=8, command=self.toggle_pause)
        self.b_pause.pack(side="left")
        Tooltip(self.b_pause, "Freeze the display (data is still collected)")
        ttk.Button(tb, text="Clear", command=self.clear).pack(side="left", padx=(4, 0))
        ttk.Button(tb, text="Export CSV...", command=self.export).pack(side="left", padx=(4, 0))
        self.l_info = ttk.Label(tb, text="", style="Info.TLabel")
        self.l_info.pack(side="right")
        ttk.Separator(w).pack(fill="x")
        pw = ttk.Panedwindow(w, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=8, pady=(6, 8))
        lf = ttk.Frame(pw)
        ttk.Label(lf, text=f"Points (up to {mon.MAX_SERIES})").pack(anchor="w")
        self.pts = ttk.Treeview(lf, columns=("on", "name"), show="", selectmode="browse",
                                style=tree_style(app.root, s))
        self.pts.column("on", width=int(26 * s), stretch=False, anchor="center")
        self.pts.column("name", width=int(170 * s), stretch=True)
        vsb = ttk.Scrollbar(lf, orient="vertical", command=self.pts.yview)
        self.pts.configure(yscrollcommand=vsb.set)
        self.pts.pack(side="left", fill="both", expand=True, pady=(4, 0))
        vsb.pack(side="left", fill="y", pady=(4, 0))
        self.pts.bind("<Button-1>", self._toggle_point)
        self.pts.bind("<space>", lambda e: self._toggle_sel())
        self.l_pts = ttk.Label(w, text="", style="Hint.TLabel")
        cf = ttk.Frame(pw)
        self.cv = tk.Canvas(cf, bg=COL["bg"], highlightthickness=1, highlightbackground=COL["line"])
        self.cv.pack(fill="both", expand=True)
        pw.add(lf, weight=0)
        pw.add(cf, weight=1)
        self.l_hint = ttk.Label(lf, text="", style="Hint.TLabel", wraplength=int(190 * s))
        self.l_hint.pack(side="bottom", fill="x")
        self.cv.bind("<Configure>", lambda e: self.draw())
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<Leave>", lambda e: self._set_cursor(None))
        self.font = tkfont.nametofont("TkDefaultFont")
        self.small = app.root._mb_fonts[0]
        w.bind("<F10>", lambda e: self.close())
        self.cand = []
        self.refresh_candidates()
        self._tick()

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        if self.alive():
            self.w.destroy()

    # ----- point selection -----
    def reload(self):
        self.srcvar.set(self.app.source_label(self.app.trend_cfg["source"]))
        self.winvar.set(self.app.trend_cfg["window"])
        self.yvar.set(self.app.trend_cfg["ymode"])
        self.refresh_candidates()

    def refresh_candidates(self):
        app = self.app
        src = app.source_from_label(self.srcvar.get())
        if src not in app.source_names():
            src = ""
            self.srcvar.set(app.source_label(src))
        self.cand = app.source_points(src)
        keys = {k for k, _l, _u in self.cand}
        if src == app.trend_cfg["source"]:
            sel = [k for k in app.trend_cfg["series"] if k in keys]
        else:
            sel = []
        app.set_trend(src, sel)
        self._fill()
        self.draw()

    def _fill(self):
        self.pts.delete(*self.pts.get_children())
        sel = set(self.app.trend_cfg["series"])
        for k, lab, unit in self.cand:
            self.pts.insert("", "end", iid=k, values=("☑" if k in sel else "☐", lab + (f"  [{unit}]" if unit else "")))
        if not self.cand:
            self.l_hint.configure(text="No points: connect / choose a poll definition or a preset in the main window, "
                                       "or start a device.")
        else:
            self.l_hint.configure(text="")

    def _source_changed(self):
        self.refresh_candidates()

    def _toggle_point(self, e):
        iid = self.pts.identify_row(e.y)
        if iid:
            self.toggle(iid)

    def _toggle_sel(self):
        sel = self.pts.selection()
        if sel:
            self.toggle(sel[0])

    def toggle(self, key):
        series = list(self.app.trend_cfg["series"])
        if key in series:
            series.remove(key)
        elif len(series) >= mon.MAX_SERIES:
            self.l_hint.configure(text=f"At most {mon.MAX_SERIES} points - untick one first.")
            return False
        else:
            series.append(key)
        self.l_hint.configure(text="")
        self.app.set_trend(self.app.source_from_label(self.srcvar.get()), series)
        self.pts.set(key, "on", "☑" if key in self.app.trend_cfg["series"] else "☐")
        self.draw()
        return True

    def select_keys(self, keys):
        self.app.set_trend(self.app.source_from_label(self.srcvar.get()), keys)
        self._fill()
        self.draw()

    def _opts(self):
        self.app.trend_cfg["window"] = self.winvar.get()
        self.app.trend_cfg["ymode"] = self.yvar.get()
        self.draw()

    # ----- actions -----
    def toggle_pause(self):
        if self.paused_at is None:
            self.paused_at = time.time()
            self.b_pause.configure(text="Resume")
        else:
            self.paused_at = None
            self.b_pause.configure(text="Pause")
        self.draw()

    def clear(self):
        self.app.trend_buf.clear()
        self.draw()

    def export(self, path=None):
        path = path or filedialog.asksaveasfilename(parent=self.w, title="Export trend data", defaultextension=".csv",
                                                    initialfile=time.strftime("mbtool_trend_%Y%m%d_%H%M%S.csv"),
                                                    filetypes=[("CSV files", "*.csv")])
        if not path:
            return None
        try:
            n = self.app.trend_buf.write_csv(path)
        except OSError as e:
            self.l_info.configure(text=f"Export failed: {e}", style="SBad.TLabel")
            return None
        self.l_info.configure(text=f"Exported {n} rows to {os.path.basename(path)}", style="Info.TLabel")
        return path

    def _tick(self):
        if not self.alive():
            return
        try:
            if self.paused_at is None:
                self.draw()
        finally:
            self.w.after(500, self._tick)

    # ----- drawing -----
    def _range(self):
        buf = self.app.trend_buf
        t1 = self.paused_at or time.time()
        span = mon.TREND_WINDOWS.get(self.winvar.get(), 600)
        if span is None:
            first, last = buf.span()
            t0 = first if first is not None else t1 - 60
            t1 = max(t1, last or t1)
            if t1 - t0 < 10:
                t0 = t1 - 10
        else:
            t0 = t1 - span
        return t0, t1

    def draw(self):
        if not self.alive():
            return
        cv, s = self.cv, self.s
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 50 or H < 50:
            return
        buf = self.app.trend_buf
        keys = buf.keys()
        lh = self.font.metrics("linespace")
        sh = self.small.metrics("linespace")
        left, right = int(64 * s), int(14 * s)
        # legend (wraps)
        x, y, items = int(8 * s), int(6 * s), []
        for i, k in enumerate(keys):
            ser = buf.series[k]
            last = next((v for v in reversed(ser.v) if v is not None), None) if ser.v and ser.v[-1] is not None else None
            text = f"{ser.label}{' [' + ser.unit + ']' if ser.unit else ''} = {mon.fmt_num(last)}"
            wdt = self.font.measure(text) + int(28 * s)
            if x + wdt > W - right and x > int(8 * s):
                x, y = int(8 * s), y + lh + int(4 * s)
            items.append((x, y, text, mon.PALETTE[i % len(mon.PALETTE)]))
            x += wdt
        for x0, y0, text, col in items:
            cv.create_rectangle(x0, y0 + lh / 2 - 2 * s, x0 + 14 * s, y0 + lh / 2 + 2 * s, fill=col, outline="")
            cv.create_text(x0 + 18 * s, y0 + lh / 2, text=text, anchor="w", font=self.font, fill=COL["fg"])
        top = (y + lh + int(10 * s)) if keys else int(10 * s)
        bottom = H - sh - int(10 * s)
        pl, pr = left, W - right
        if bottom - top < 40 or pr - pl < 60:
            return
        t0, t1 = self._range()
        self.geom = (pl, pr, top, bottom, t0, t1)
        # time grid
        for t in mon.time_ticks(t0, t1, max(2, int((pr - pl) / (90 * s)))):
            xx = pl + (t - t0) / (t1 - t0) * (pr - pl)
            cv.create_line(xx, top, xx, bottom, fill="#e6e9ee")
            cv.create_text(xx, bottom + 3 * s, text=mon.fmt_hms(t), anchor="n", font=self.small, fill=COL["addrfg"])
        cv.create_rectangle(pl, top, pr, bottom, outline=COL["line"])
        if not keys:
            cv.create_text((pl + pr) / 2, (top + bottom) / 2, text="Tick points on the left to plot them",
                           font=self.font, fill=COL["stale"])
            self.l_info.configure(text="")
            return
        lanes = [(keys, top, bottom)] if self.yvar.get() == "Shared" else [
            ([k], top + i * (bottom - top) / len(keys), top + (i + 1) * (bottom - top) / len(keys))
            for i, k in enumerate(keys)]
        npts = 0
        self.lane_ranges = []
        for lk, ly0, ly1 in lanes:
            vis = {k: buf.visible(k, t0, t1) for k in lk}
            vals = [v for k in lk for t, v in vis[k] if v is not None and t >= t0]
            lo, hi = mon.auto_range(vals)
            self.lane_ranges.append((lk, ly0, ly1, lo, hi))
            pad = 4 * s
            yy0, yy1 = ly0 + pad, ly1 - pad
            nt = max(2, int((yy1 - yy0) / (40 * s)))
            for v in mon.nice_ticks(lo, hi, nt):
                yy = yy1 - (v - lo) / (hi - lo) * (yy1 - yy0)
                if yy0 - 1 <= yy <= yy1 + 1:
                    cv.create_line(pl, yy, pr, yy, fill="#eef0f3")
                    cv.create_text(pl - 4 * s, yy, text=mon.fmt_num(v), anchor="e", font=self.small,
                                   fill=COL["addrfg"])
            if len(lanes) > 1:
                cv.create_line(pl, ly1, pr, ly1, fill=COL["line"])
                ser = buf.series[lk[0]]
                cv.create_text(pl + 6 * s, ly0 + 3 * s, text=ser.label, anchor="nw", font=self.small,
                               fill=mon.PALETTE[keys.index(lk[0]) % len(mon.PALETTE)])
            for k in lk:
                col = mon.PALETTE[keys.index(k) % len(mon.PALETTE)]
                typ = buf.typical_dt(k)
                gap = max(5 * typ, 2.0) if typ else None
                dec = mon.decimate(vis[k], t0, t1, int(pr - pl))
                npts += len(vis[k])
                for seg in mon.segments(dec, gap):
                    coords = []
                    for t, v in seg:
                        coords += [pl + (t - t0) / (t1 - t0) * (pr - pl), yy1 - (v - lo) / (hi - lo) * (yy1 - yy0)]
                    if len(coords) >= 4:
                        cv.create_line(*coords, fill=col, width=max(1, int(1.5 * s)))
                    elif coords:
                        r = 2 * s
                        cv.create_oval(coords[0] - r, coords[1] - r, coords[0] + r, coords[1] + r, fill=col, outline="")
        cv.tag_raise("cursor")
        self.l_info.configure(text=("PAUSED   " if self.paused_at else "") +
                                   f"{len(keys)} series, {buf.count()} samples kept", style="Info.TLabel")
        if self.cursor_x is not None:
            self._draw_cursor(self.cursor_x)

    def _motion(self, e):
        self._set_cursor(e.x)

    def _set_cursor(self, x):
        self.cursor_x = x
        self.cv.delete("cursor")
        if x is not None:
            self._draw_cursor(x)

    def readout(self, t):
        """[(label, value)] at time t (used by the hover box)."""
        buf = self.app.trend_buf
        out = []
        for k in buf.keys():
            typ = buf.typical_dt(k)
            out.append((buf.series[k].label, buf.value_at(k, t, max_dt=(3 * typ + 1) if typ else 5)))
        return out

    def _draw_cursor(self, x):
        g = getattr(self, "geom", None)
        if not g or not self.app.trend_buf.keys():
            return
        pl, pr, top, bottom, t0, t1 = g
        if not pl <= x <= pr:
            return
        cv, s = self.cv, self.s
        t = t0 + (x - pl) / (pr - pl) * (t1 - t0)
        cv.create_line(x, top, x, bottom, fill="#444", dash=(3, 3), tags="cursor")
        lines = [time.strftime("%H:%M:%S", time.localtime(t))] + [
            f"{lab}: {mon.fmt_num(v)}" for lab, v in self.readout(t)]
        wdt = max(self.small.measure(l) for l in lines) + int(12 * s)
        hgt = len(lines) * self.small.metrics("linespace") + int(8 * s)
        bx = x + 10 * s if x + 10 * s + wdt < pr else x - 10 * s - wdt
        by = top + 6 * s
        cv.create_rectangle(bx, by, bx + wdt, by + hgt, fill="#ffffe1", outline="#888", tags="cursor")
        for i, l in enumerate(lines):
            col = "#222" if i == 0 else mon.PALETTE[(i - 1) % len(mon.PALETTE)]
            cv.create_text(bx + 6 * s, by + 4 * s + i * self.small.metrics("linespace"), text=l, anchor="nw",
                           font=self.small, fill=col, tags="cursor")


COND_LABELS = {"above": "above", "below": "below", "outside": "outside range (low..high)", "equals": "equals",
               "changed": "changed", "silent": "no valid data for N s (silent)", "stuck": "value stuck for N s"}


class AlertsWindow:
    """Alarm rules (add / edit / delete with inline validation), states, acknowledge, event log + export."""
    STATE_TAGS = {"ALARM": dict(background="#fde2e1", foreground="#a50e0e"), "ACKED": dict(foreground="#a15c00"),
                  "CLEARED": dict(foreground="#7a3db8"), "OK": dict(foreground=COL["fg"]),
                  "off": dict(foreground=COL["stale"]), "ACK": dict(foreground="#a15c00")}

    def __init__(self, app):
        self.app, s = app, app.s
        self.s = s
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Alerts")
        help_key(w, "alerts")
        w.geometry(f"{int(1060 * s)}x{int(680 * s)}")
        w.minsize(int(760 * s), int(520 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.cur = None  # rule id being edited (None = new rule)
        tb = ttk.Frame(w, padding=(8, 6))
        tb.pack(fill="x")
        self.b_ack = ttk.Button(tb, text="Acknowledge", command=self.ack_selected)
        self.b_ack.pack(side="left")
        Tooltip(self.b_ack, "Acknowledge the selected rule (ALARM -> acknowledged, returned -> OK)")
        ttk.Button(tb, text="Acknowledge all", command=app.ack_all).pack(side="left", padx=(4, 0))
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=10)
        cb = ttk.Checkbutton(tb, text="Beep on new alarm", variable=app.alarm_beep)
        cb.pack(side="left")
        ttk.Button(tb, text="Export events...", command=self.export).pack(side="left", padx=(10, 0))
        ttk.Button(tb, text="Clear log", command=self.clear_log).pack(side="left", padx=(4, 0))
        self.b_help = add_help_button(app, w, "alerts", parent=tb, side="right", padx=(8, 0))
        self.l_sum = ttk.Label(tb, text="", style="Conn.TLabel")
        self.l_sum.pack(side="right")
        ttk.Separator(w).pack(fill="x")
        pw = ttk.Panedwindow(w, orient="vertical")
        pw.pack(fill="both", expand=True, padx=8, pady=(6, 8))
        # rules
        rf = ttk.Frame(pw)
        cols = (("state", "State", 86), ("device", "Device", 110), ("rule", "Rule", 320), ("message", "Message", 220),
                ("since", "Since", 80), ("acked", "Acknowledged", 96))
        self.rules = ttk.Treeview(rf, columns=[c[0] for c in cols], show="headings", selectmode="browse",
                                  style=tree_style(app.root, s), height=6)
        for cid, lab, wd in cols:
            self.rules.heading(cid, text=lab, anchor="w")
            self.rules.column(cid, width=int(wd * s), stretch=cid in ("rule", "message"))
        for tg, opt in self.STATE_TAGS.items():
            self.rules.tag_configure(tg, **opt)
        vsb = ttk.Scrollbar(rf, orient="vertical", command=self.rules.yview)
        self.rules.configure(yscrollcommand=vsb.set)
        self.rules.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        rf.rowconfigure(0, weight=1)
        rf.columnconfigure(0, weight=1)
        self.rules.bind("<<TreeviewSelect>>", lambda e: self._select())
        # editor
        ef = ttk.LabelFrame(rf, text="Rule", padding=(10, 4, 10, 4))
        ef.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.v = {k: tk.StringVar() for k in ("device", "point", "when", "value", "high", "for_s", "hysteresis",
                                              "message")}
        self.enabled = tk.BooleanVar(value=True)
        self.hints = {}
        self.cb_dev = ttk.Combobox(ef, textvariable=self.v["device"], width=16, state="readonly",
                                   postcommand=self.refresh_sources)
        self.hints["device"] = vfield(ef, 0, "Device", self.cb_dev, "Main window poll or a device of the Devices window")
        self.cb_pt = ttk.Combobox(ef, textvariable=self.v["point"], width=22)
        self.hints["point"] = vfield(ef, 1, "Point / address", self.cb_pt,
                                     "Preset point name, or the register address of a plain poll.\n"
                                     "'*' = the whole device (only with 'silent').")
        self.cb_when = ttk.Combobox(ef, textvariable=self.v["when"], values=list(COND_LABELS.values()), width=26,
                                    state="readonly")
        self.hints["when"] = vfield(ef, 2, "Condition", self.cb_when)
        self.e_val = ttk.Entry(ef, textvariable=self.v["value"], width=9)
        self.l_val_hint = self.hints["value"] = vfield(ef, 3, "Value / low", self.e_val)
        self.e_high = ttk.Entry(ef, textvariable=self.v["high"], width=9)
        self.hints["high"] = vfield(ef, 4, "High", self.e_high, "Upper limit for 'outside range'")
        self.e_for = ttk.Entry(ef, textvariable=self.v["for_s"], width=7)
        self.hints["for_s"] = vfield(ef, 5, "For (s)", self.e_for,
                                     "Minimum time the condition must last before the alarm fires.\n"
                                     "silent / stuck: the N seconds.")
        self.e_hyst = ttk.Entry(ef, textvariable=self.v["hysteresis"], width=7)
        self.hints["hysteresis"] = vfield(ef, 6, "Hysteresis", self.e_hyst,
                                          "The value must come back this far inside the limit before the alarm\n"
                                          "clears (stuck: changes smaller than this count as 'no change').")
        self.e_msg = ttk.Entry(ef, textvariable=self.v["message"], width=40)
        ttk.Label(ef, text="Message (optional)").grid(row=3, column=0, columnspan=3, sticky="w")
        self.e_msg.grid(row=4, column=0, columnspan=3, sticky="we", padx=(0, 12))
        self.hints["message"] = ttk.Label(ef, text=" ", style="Hint.TLabel")
        self.hints["message"].grid(row=5, column=0, columnspan=3, sticky="w")
        cbe = ttk.Checkbutton(ef, text="Enabled", variable=self.enabled)
        cbe.grid(row=4, column=3, sticky="w")
        bf = ttk.Frame(ef)
        bf.grid(row=4, column=4, columnspan=3, sticky="e")
        ttk.Button(bf, text="New", command=self.new_rule).pack(side="left")
        self.b_save = ttk.Button(bf, text="Add rule", command=self.save_rule)
        self.b_save.pack(side="left", padx=(4, 0))
        self.b_del = ttk.Button(bf, text="Delete", command=self.delete_rule)
        self.b_del.pack(side="left", padx=(4, 0))
        self.l_ed = ttk.Label(ef, text="", style="Info.TLabel")
        self.l_ed.grid(row=6, column=0, columnspan=7, sticky="w")
        for k in ("device", "point", "when", "value", "high", "for_s", "hysteresis"):
            self.v[k].trace_add("write", lambda *_: self.validate())
        self.v["device"].trace_add("write", lambda *_: self._points_for_device())
        pw.add(rf, weight=3)
        # events
        lf = ttk.Frame(pw)
        ttk.Label(lf, text="Event log", style="Conn.TLabel").grid(row=0, column=0, sticky="w")
        ecols = (("time", "Time", 150), ("device", "Device", 110), ("rule", "Rule", 300), ("value", "Value", 90),
                 ("state", "State", 80), ("message", "Message", 220))
        self.ev = ttk.Treeview(lf, columns=[c[0] for c in ecols], show="headings", selectmode="extended",
                               style=tree_style(app.root, s), height=6)
        for cid, lab, wd in ecols:
            self.ev.heading(cid, text=lab, anchor="w")
            self.ev.column(cid, width=int(wd * s), stretch=cid in ("rule", "message"))
        for tg, opt in self.STATE_TAGS.items():
            self.ev.tag_configure(tg, **opt)
        evs = ttk.Scrollbar(lf, orient="vertical", command=self.ev.yview)
        self.ev.configure(yscrollcommand=evs.set)
        self.ev.grid(row=1, column=0, sticky="nsew")
        evs.grid(row=1, column=1, sticky="ns")
        lf.rowconfigure(1, weight=1)
        lf.columnconfigure(0, weight=1)
        pw.add(lf, weight=2)
        self.l_msg = ttk.Label(w, text="", style="SInfo.TLabel")
        self.l_msg.pack(fill="x", padx=10, pady=(0, 4))
        self.reload()
        self.new_rule()
        self._tick()

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        if self.alive():
            self.w.destroy()

    def reload(self):
        self.refresh_rules()
        self.ev.delete(*self.ev.get_children())
        self.add_events(list(self.app.alarms.events))

    def _tick(self):
        if not self.alive():
            return
        try:
            self.refresh_rules()
        finally:
            self.w.after(1000, self._tick)

    # ----- rules table -----
    def refresh_rules(self):
        eng = self.app.alarms
        sel = self.rules.selection()
        ids = {str(r.id) for r in eng.rules}
        for iid in self.rules.get_children():
            if iid not in ids:
                self.rules.delete(iid)
        for r in eng.rules:
            st = eng.states[r.id]
            state = st.state if r.enabled else "off"
            hm = lambda t: time.strftime("%H:%M:%S", time.localtime(t)) if t else ""
            vals = ({"ACKED": "ACKNOWLEDGED", "CLEARED": "RETURNED"}.get(state, state),
                    self.app.source_label(r.device), r.describe() + ("" if r.enabled else "  (disabled)")
                    + ("  [preset]" if r.origin == "preset" else ""), r.message,
                    hm(st.t_raised) if state in ("ALARM", "ACKED", "CLEARED") else "",
                    hm(st.t_acked) if state == "ACKED" else "")
            iid = str(r.id)
            if self.rules.exists(iid):
                if tuple(self.rules.item(iid, "values")) != tuple(str(v) for v in vals):
                    self.rules.item(iid, values=vals, tags=(state,))
            else:
                self.rules.insert("", "end", iid=iid, values=vals, tags=(state,))
        if sel and self.rules.exists(sel[0]):
            self.rules.selection_set(sel)
        act, un = eng.active(), eng.unacked()
        self.l_sum.configure(text=f"{len(act)} active, {len(un)} not acknowledged, {len(eng.rules)} rules",
                             style="SBad.TLabel" if un else ("SWarn.TLabel" if act else "SOk.TLabel"))

    def add_events(self, events):
        for e in events:
            v = e["value"]
            vt = "" if v is None else (mon.fmt_num(v) if isinstance(v, float) else str(v))
            self.ev.insert("", "end", values=(stamp(e["t"]), self.app.source_label(e["device"]), e["rule"], vt,
                                              e["state"], e["message"]), tags=(e["state"],))
        kids = self.ev.get_children()
        if len(kids) > 5000:
            self.ev.delete(*kids[:len(kids) - 5000])
            kids = self.ev.get_children()
        if events and kids:
            self.ev.see(kids[-1])

    def clear_log(self):
        self.app.alarms.events.clear()
        self.ev.delete(*self.ev.get_children())

    def ack_selected(self):
        sel = self.rules.selection()
        if not sel:
            self.l_msg.configure(text="Select a rule to acknowledge (or use Acknowledge all)", style="SWarn.TLabel")
            return
        ev = self.app.alarms.ack(int(sel[0]), now=time.time())
        if ev:
            self.app._alarm_events(ev)
        self.app._alarms_changed()

    def export(self, path=None):
        path = path or filedialog.asksaveasfilename(parent=self.w, title="Export alarm events", defaultextension=".csv",
                                                    initialfile=time.strftime("mbtool_alarms_%Y%m%d_%H%M%S.csv"),
                                                    filetypes=[("CSV files", "*.csv"), ("Text log", "*.log")])
        if not path:
            return None
        try:
            if path.lower().endswith((".log", ".txt")):
                with open(path, "w", encoding="utf-8") as f:
                    for e in self.app.alarms.events:
                        f.write(f"{stamp(e['t'])}  {e['state']:<8} {self.app.source_label(e['device'])}: {e['rule']}"
                                f"  value={'' if e['value'] is None else e['value']}  {e['message']}\n")
                n = len(self.app.alarms.events)
            else:
                n = self.app.alarms.export_events(path)
        except OSError as e:
            self.l_msg.configure(text=f"Export failed: {e}", style="SBad.TLabel")
            return None
        self.l_msg.configure(text=f"Exported {n} events to {path}", style="SInfo.TLabel")
        return path

    # ----- editor -----
    def refresh_sources(self):
        self.cb_dev.configure(values=[self.app.source_label(n) for n in self.app.source_names()])
        self._points_for_device()

    def _points_for_device(self):
        src = self.app.source_from_label(self.v["device"].get())
        self.cb_pt.configure(values=[k for k, _l, _u in self.app.source_points(src)] + ["*"])

    def new_rule(self, device=""):
        self.cur = None
        self.rules.selection_set(())
        for k in self.v:
            self.v[k].set("")
        self.v["device"].set(self.app.source_label(device))
        self.v["when"].set(COND_LABELS["above"])
        self.enabled.set(True)
        self.b_save.configure(text="Add rule")
        self.b_del.state(["disabled"])
        self.refresh_sources()
        self.validate()

    def _select(self):
        sel = self.rules.selection()
        if not sel:
            return
        r = self.app.alarms.rule(int(sel[0]))
        if r is None:
            return
        self.cur = r.id
        d = r.to_dict()
        self.v["device"].set(self.app.source_label(r.device))
        self.v["point"].set(r.point)
        self.v["when"].set(COND_LABELS[r.when])
        for k in ("value", "high", "for_s", "hysteresis"):
            x = d.get(k)
            self.v[k].set("" if x is None else (mon.fmt_num(x) if isinstance(x, float) else str(x)))
        self.v["message"].set(r.message)
        self.enabled.set(r.enabled)
        self.b_save.configure(text="Update rule")
        self.b_del.state(["!disabled"])
        self.validate()

    def collect(self):
        when = next((k for k, lab in COND_LABELS.items() if lab == self.v["when"].get()), "")
        d = {"device": self.app.source_from_label(self.v["device"].get()), "point": self.v["point"].get().strip(),
             "when": when, "message": self.v["message"].get().strip(), "enabled": self.enabled.get()}
        for k in ("value", "high", "for_s", "hysteresis"):
            if self.v[k].get().strip():
                d[k] = self.v[k].get().strip()
        if self.cur is not None:
            old = self.app.alarms.rule(self.cur)
            if old is not None and old.origin == "preset":
                d["origin"] = "preset"
        return d

    def validate(self):
        d = self.collect()
        when = d["when"]
        self.e_high.configure(state="normal" if when == "outside" else "disabled")
        self.e_val.configure(state="normal" if when in ("above", "below", "outside", "equals") else "disabled")
        self.e_hyst.configure(state="normal" if when in ("above", "below", "outside", "stuck") else "disabled")
        for k in ("high", "value", "hysteresis"):
            w = {"high": self.e_high, "value": self.e_val, "hysteresis": self.e_hyst}[k]
            if str(w.cget("state")) == "disabled" and k in d:
                d.pop(k)
        known = [k for k, _l, _u in self.app.source_points(d["device"])]
        r, errs = mon.validate_rule(d, known or None)
        info = {}
        if known and d["point"] and d["point"] not in known and d["point"] != "*":
            errs["point"] = "unknown point / address"
        if not known and d["point"] not in ("", "*"):
            info["point"] = "not checked (no points known yet)"
        set_hints(self.hints, errs, info)
        self.b_save.state(["disabled"] if errs else ["!disabled"])
        self.l_ed.configure(text=("→ " + r.describe()) if r else "")
        return r if not errs else None

    def save_rule(self):
        r = self.validate()
        if r is None:
            self.l_msg.configure(text="Fix the rule fields marked in red", style="SBad.TLabel")
            return None
        eng = self.app.alarms
        if self.cur is None:
            eng.add_rule(r)
            self.l_msg.configure(text=f"Rule added: {r.describe()}", style="SOk.TLabel")
        else:
            eng.replace_rule(self.cur, r)
            self.l_msg.configure(text=f"Rule updated: {r.describe()}", style="SOk.TLabel")
        self.cur = r.id
        self.app._alarms_changed()
        self.refresh_rules()
        self.rules.selection_set(str(r.id))
        self.b_save.configure(text="Update rule")
        self.b_del.state(["!disabled"])
        return r

    def delete_rule(self):
        if self.cur is None:
            return
        self.app.alarms.remove_rule(self.cur)
        self.app._alarms_changed()
        self.refresh_rules()
        self.l_msg.configure(text="Rule deleted", style="SInfo.TLabel")
        self.new_rule()


STATE_LIGHT = {"ok": ("● OK", "ok"), "error": ("● ERROR", "error"), "stale": ("● STALE", "stale"), "off": ("○ off", "off")}
DOT_COLORS = {"ok": "#1e9e3e", "error": "#d93025", "stale": "#e69500", "off": "#9aa0a6"}


def dot_image(master, color, size):
    """Small filled circle as a PhotoImage: the status dot on a device tab."""
    img = tk.PhotoImage(master=master, width=size, height=size)
    r = (size - 1) / 2.0
    for y in range(size):
        dy = y - r
        half = (max(r * r - dy * dy, 0.0)) ** 0.5
        x0, x1 = int(round(r - half)), int(round(r + half))
        if x1 >= x0:
            img.put(color, to=(x0, y, x1 + 1, y + 1))
    return img


def help_btn(app, parent, key, **pack):
    """'?' button for a part of a window (does not change the window's own F1 key)."""
    b = ttk.Button(parent, text="?", width=3, command=lambda: app.open_help(key=key))
    Tooltip(b, "Help for this part   (F1)")
    b.pack(**(pack or {"side": "right"}))
    return b


def same_target(a, b):
    """True when two connections reach the same bus / TCP target (host:port or COM port)."""
    if a["mode"] == "TCP" or b["mode"] == "TCP":
        return a["mode"] == b["mode"] == "TCP" and a["host"].strip().lower() == b["host"].strip().lower() \
            and a["port"].strip() == b["port"].strip()
    return a["com"].strip().lower() == b["com"].strip().lower()


class DevicesWindow:
    """F11: several devices polled at once. One worker per RS-485 bus (shared master, sequential with a gap),
    one per TCP device; the overview shows state, counters and the first values. Tabs: Overview, Cycle (PLC
    cycle statistics), Calculator (bus timing estimate) and one tab per device with its full latest data."""
    COLS = (("state", "State", 80), ("name", "Device", 120), ("conn", "Connection", 180), ("unit", "ID", 36),
            ("what", "Preset / range", 150), ("scan", "Scan", 56), ("last", "Last update", 84), ("ok", "OK", 54),
            ("err", "Errors", 54), ("kinds", "Error types", 92), ("resp", "Resp. ms avg/max", 110),
            ("values", "Values", 300))
    FIXED_TABS = 4  # Overview, Cycle, Calculator, Health; the device tabs follow

    def __init__(self, app):
        self.app, s = app, app.s
        self.s = s
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Devices")
        help_key(w, self._help)
        w.geometry(f"{int(1140 * s)}x{int(600 * s)}")
        w.minsize(int(780 * s), int(380 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.dots = {k: dot_image(w, c, max(8, int(10 * s))) for k, c in DOT_COLORS.items()}
        self.tabs = {}  # device name -> DeviceTab
        self.scan_dlg = None
        sb = ttk.Frame(w, padding=(8, 2, 8, 6))
        sb.pack(fill="x", side="bottom")
        self.l_msg = ttk.Label(sb, text="Double-click a device to show it in the Trend, Alerts and Traffic windows.",
                               style="SInfo.TLabel")
        self.l_msg.pack(side="left")
        self.nb = ttk.Notebook(w)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        ov = self.overview = ttk.Frame(self.nb)
        self.nb.add(ov, text=" Overview ")
        tb = ttk.Frame(ov, padding=(8, 6))
        tb.pack(fill="x")
        ttk.Button(tb, text="Start all", command=self.start_all).pack(side="left")
        ttk.Button(tb, text="Stop all", command=self.stop_all).pack(side="left", padx=(4, 0))
        self.b_toggle = ttk.Button(tb, text="Start / stop selected", command=self.toggle_selected)
        self.b_toggle.pack(side="left", padx=(4, 0))
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(tb, text="Add...", command=self.add).pack(side="left")
        ttk.Button(tb, text="Edit...", command=self.edit).pack(side="left", padx=(4, 0))
        ttk.Button(tb, text="Remove", command=self.remove).pack(side="left", padx=(4, 0))
        self.b_scan = ttk.Button(tb, text="Scan & add...", command=self.scan_add)
        self.b_scan.pack(side="left", padx=(4, 0))
        Tooltip(self.b_scan, "Look for devices (Slave IDs) on a connection, identify them and add the ones you tick")
        self.b_demo = ttk.Button(tb, text="Demo devices", command=self.toggle_demo)
        self.b_demo.pack(side="left", padx=(4, 0))
        Tooltip(self.b_demo, "No hardware? Start simulated devices (meter, drive, level, flow, pressure) with moving\n"
                             "values: each gets its own tab. Press again to stop and remove them.")
        self.b_busmap = ttk.Button(tb, text="Bus map...", command=app.open_busmap)
        self.b_busmap.pack(side="left", padx=(4, 0))
        self.b_report = ttk.Button(tb, text="Report...", command=app.open_report)
        self.b_report.pack(side="left", padx=(4, 0))
        Tooltip(self.b_report, "Commissioning report (HTML / PDF): devices, error types, response times, values,\n"
                               "PLC cycle, the last health test and the bus map, with sign-off boxes")
        if app.demo is not None:
            self.b_demo.configure(text="Stop demo")
        Tooltip(self.b_busmap, "Draw where the devices sit on the RS-485 line (cable lengths, terminators) and\n"
                               "watch how a request travels to a slave and back   (Ctrl+M)")
        ttk.Separator(tb, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(tb, text="Bus gap (ms):").pack(side="left", padx=(0, 4))
        self.gap = tk.StringVar(value=app.cfg["devices"].get("gap_ms", "50"))
        eg = ttk.Entry(tb, textvariable=self.gap, width=6)
        eg.pack(side="left")
        Tooltip(eg, "Silence between two requests on the same RS-485 bus (0-5000 ms).\n"
                    "Devices on one COM port are polled one after the other.\n"
                    "PLC cycle mode: kept before every request of the cycle.")
        self.l_gap = ttk.Label(tb, text="", style="Hint.TLabel")
        self.l_gap.pack(side="left", padx=(4, 0))
        self.gap.trace_add("write", lambda *_: self._gap())
        self.b_help = add_help_button(app, w, self._help, parent=tb, side="right", padx=(8, 0))
        self.l_sum = ttk.Label(tb, text="", style="Info.TLabel")
        self.l_sum.pack(side="right")
        # scan mode row
        mf = ttk.Frame(ov, padding=(8, 0, 8, 4))
        mf.pack(fill="x")
        ttk.Label(mf, text="Scan mode:").pack(side="left", padx=(0, 4))
        self.mode_var = tk.StringVar(value=mon.SCAN_MODES[mon.norm_scan_mode(app.cfg["devices"].get("scan_mode"))])
        self.cb_mode = ttk.Combobox(mf, textvariable=self.mode_var, values=list(mon.SCAN_MODES.values()),
                                    state="readonly", width=34)
        self.cb_mode.pack(side="left")
        self.cb_mode.bind("<<ComboboxSelected>>", lambda e: self._mode_changed())
        Tooltip(self.cb_mode, "Independent: every device is read at its own scan rate (default).\n"
                              "PLC cycle: each bus (COM port / TCP target) reads all its devices one after the\n"
                              "other, then starts again - measured in the Cycle tab.")
        help_key(self.cb_mode, "devices.cycle")
        ttk.Label(mf, text="Cycle time target (ms):").pack(side="left", padx=(14, 4))
        self.target = tk.StringVar(value=app.cfg["devices"].get("cycle_target_ms", ""))
        self.e_target = ttk.Entry(mf, textvariable=self.target, width=8)
        self.e_target.pack(side="left")
        Tooltip(self.e_target, "PLC cycle mode only. Empty = start the next cycle at once.\n"
                               "A number: fixed period like a PLC task - when a cycle finishes early the rest is\n"
                               "waited; a cycle that takes longer counts as an overrun.")
        help_key(self.e_target, "devices.cycle")
        self.l_target = ttk.Label(mf, text="", style="Hint.TLabel")
        self.l_target.pack(side="left", padx=(4, 0))
        self.target.trace_add("write", lambda *_: self._target_changed())
        lf = ttk.Frame(ov, padding=(8, 0, 8, 4))
        lf.pack(fill="x")
        self.log_on = tk.BooleanVar(value=bool(app.cfg["devices"].get("log_on")))
        cbl = ttk.Checkbutton(lf, text="Log each device to its own file in folder:", variable=self.log_on,
                              command=self._log_toggled)
        cbl.pack(side="left")
        Tooltip(cbl, "One CSV per device (device name in the file name), started and stopped with polling.\n"
                     "Failed polls are written as ERROR rows. Format / rotation: File > Log options...")
        self.folder = tk.StringVar(value=app.cfg["devices"].get("log_folder", ""))
        ttk.Entry(lf, textvariable=self.folder, width=50).pack(side="left", padx=(4, 4), fill="x", expand=True)
        ttk.Button(lf, text="Browse...", command=self.browse).pack(side="left")
        ttk.Button(lf, text="Log options...", command=app.open_log_options).pack(side="left", padx=(4, 0))
        self.folder.trace_add("write", lambda *_: self._folder_changed())
        ttk.Separator(ov).pack(fill="x")
        tf = ttk.Frame(ov)
        tf.pack(fill="both", expand=True, padx=8, pady=(6, 4))
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in self.COLS], show="headings", selectmode="browse",
                                 style=tree_style(app.root, s))
        for cid, lab, wd in self.COLS:
            self.tree.heading(cid, text=lab, anchor="w")
            self.tree.column(cid, width=int(wd * s), stretch=cid == "values", minwidth=int(30 * s))
        self.tree.tag_configure("ok", foreground="#1e7b34")
        self.tree.tag_configure("error", foreground=COL["bad"], background="#fdecea")
        self.tree.tag_configure("stale", foreground="#a15c00", background="#fff4dc")
        self.tree.tag_configure("off", foreground=COL["stale"])
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", self._dbl)
        self.tree.bind("<Return>", lambda e: self.activate())
        self.tree.bind("<Delete>", lambda e: self.remove())
        self.cycle_tab = CycleTab(self)
        self.nb.add(self.cycle_tab.frame, text=" Cycle ")
        self.calc_tab = CalcTab(self)
        self.nb.add(self.calc_tab.frame, text=" Calculator ")
        self.health_tab = HealthTab(self)
        self.nb.add(self.health_tab.frame, text=" Health test ")
        Tooltip(self.tree, "Error types: T = timeout, C = CRC/LRC, E = Modbus exception, F = bad frame,\n"
                           "W = answer from a wrong Slave ID, L = no connection, O = other.\n"
                           "Resp. ms: round trip of one request (average / slowest) since start or reset.")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        w.bind("<F11>", lambda e: self.close())
        self.reload()

    def _help(self):
        try:
            cur = self.nb.select()
        except tk.TclError:
            return "devices"
        if cur in (str(self.cycle_tab.frame), str(self.calc_tab.frame)):
            return "devices.cycle"
        if cur == str(self.health_tab.frame):
            return "devices.health"
        if cur and cur != str(self.overview):
            return "devices.tabs"
        return "devices"

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        for t in list(self.tabs.values()):
            t.destroy()
        self.tabs.clear()
        if self.scan_dlg is not None and self.scan_dlg.alive():
            self.scan_dlg.close()
        self.calc_tab.destroy()
        self.health_tab.destroy()
        if self.alive():
            self.w.destroy()

    def _status(self, text, kind="info"):
        self.l_msg.configure(text=text, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel",
                                               "warn": "SWarn.TLabel"}.get(kind, "SInfo.TLabel"))

    # ----- table -----
    def reload(self):
        dc = self.app.cfg["devices"]
        self.gap.set(dc.get("gap_ms", "50"))
        self.folder.set(dc.get("log_folder", ""))
        self.log_on.set(bool(dc.get("log_on")))
        self.mode_var.set(mon.SCAN_MODES[mon.norm_scan_mode(dc.get("scan_mode"))])
        if self.target.get() != dc.get("cycle_target_ms", ""):
            self.target.set(dc.get("cycle_target_ms", ""))
        sel = self.selected()
        self.tree.delete(*self.tree.get_children())
        for d in self.app.devices:
            self.tree.insert("", "end", iid=d["name"], values=self._row(d))
        if sel and self.tree.exists(sel):
            self.tree.selection_set(sel)
        self.sync_tabs()
        self.calc_tab.reload()
        self.health_tab.reload()
        self.refresh_states()

    def _row(self, d):
        app = self.app
        st = app.dev_status.get(d["name"])
        state = st.state(time.time()) if st else "off"
        light, _tag = STATE_LIGHT[state]
        if d["preset"]:
            what = os.path.basename(d["preset"])
        else:
            fc, addr, qty = int(d["fc"]), int(d["addr"]), int(d["qty"])
            what = f"FC{fc:02d} {addr_label(fc, addr, app.addr_base())} x{qty} {d['fmt'] if fc in (3, 4) else ''}"
        last = time.strftime("%H:%M:%S", time.localtime(st.last_t)) if st and st.last_t else ""
        if st and state == "error" and st.last_error:
            values = "ERROR: " + st.last_error
        elif st and state == "off" and st.last_error and not st.running:
            values = st.last_error
        else:
            values = (st.summary if st else "") or ""
            if st and st.last_error and state == "ok":
                values += f"   (some points failed: {st.last_error})"
        conn = mon.conn_text(d["conn"])
        if not d["enabled"]:
            light = "○ disabled"
        scan = "cycle" if app.devpoll.mode == "cycle" else d["scan"]
        kinds = resp = ""
        if st:
            q = st.quality.summary()
            kinds = mon.kinds_text(q["kinds"])
            if q["avg"] is not None:
                resp = f"{q['avg']:.0f} / {q['max']:.0f}"
        return (light, d["name"], conn, d["unit"], what, scan, last, st.ok if st else 0,
                st.errors if st else 0, kinds, resp, values)

    def update_row(self, name):
        d = self.app.dev_by_name(name)
        if d is None or not self.tree.exists(name):
            return
        st = self.app.dev_status.get(name)
        state = st.state(time.time()) if st else "off"
        self.tree.item(name, values=self._row(d), tags=(STATE_LIGHT[state][1],))
        t = self.tabs.get(name)
        if t is not None:
            t.refresh()

    def refresh_states(self):
        now = time.time()
        counts = collections.Counter()
        for d in self.app.devices:
            st = self.app.dev_status.get(d["name"])
            counts[st.state(now) if st else "off"] += 1
            self.update_row(d["name"])
        mode = " (PLC cycle)" if self.app.devpoll.mode == "cycle" else ""
        self.l_sum.configure(text=f"{len(self.app.devices)} devices{mode}:  {counts['ok']} ok,  "
                                  f"{counts['error']} error,  {counts['stale']} stale,  {counts['off']} off"
                                  + ("   ● logging" if self.app.devlogs is not None else ""))
        if self._shown(self.cycle_tab.frame):
            self.cycle_tab.refresh()

    def _shown(self, frame):
        try:
            return self.nb.select() == str(frame)
        except tk.TclError:
            return False

    def _tab_changed(self):
        if self._shown(self.cycle_tab.frame):
            self.cycle_tab.refresh(force=True)
        elif self._shown(self.calc_tab.frame):
            self.calc_tab.recalc()
        elif self._shown(self.health_tab.frame):
            self.health_tab.reload()

    # ----- per-device tabs -----
    def sync_tabs(self, renamed=None):
        """One tab per configured device, in device order (created / removed / renamed as the list changes)."""
        if renamed:
            old, new = renamed
            t = self.tabs.pop(old, None)
            if t is not None and new not in self.tabs:
                t.name = new
                self.tabs[new] = t
        names = [d["name"] for d in self.app.devices]
        for n in list(self.tabs):
            if n not in names:
                self.tabs.pop(n).destroy()
        for i, n in enumerate(names):
            t = self.tabs.get(n)
            if t is None:
                t = self.tabs[n] = DeviceTab(self, n)
            want = self.FIXED_TABS + i
            try:
                if self.nb.index(t.frame) != want:
                    self.nb.insert(want, t.frame)
            except tk.TclError:
                pass
            t.refresh(full=True)

    def show_tab(self, name):
        t = self.tabs.get(name)
        if t is not None:
            self.nb.select(t.frame)
        return t

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _dbl(self, e):
        if self.tree.identify_row(e.y):
            self.activate()

    def activate(self, name=None):
        name = name or self.selected()
        if not name:
            return
        self.app.activate_source(name)
        self._status(f"'{name}' is now shown in the Trend window (and selected in Traffic / Alerts)", "ok")

    # ----- actions -----
    def _gap(self):
        try:
            int_in(self.gap.get(), 0, 5000)
        except ValueError as e:
            self.l_gap.configure(text=str(e))
            return
        self.l_gap.configure(text="")
        self.app.cfg["devices"]["gap_ms"] = self.gap.get().strip()
        self.app.devpoll.gap_s = self.app._gap_s()

    def _mode_changed(self):
        label = self.mode_var.get()
        mode = next((k for k, v in mon.SCAN_MODES.items() if v == label), "independent")
        errors = self.app.set_scan_mode(mode)
        self.refresh_states()
        if errors:
            n, e = next(iter(errors.items()))
            self._status(f"Scan mode {label}: {len(errors)} device(s) not restarted - {n}: {e}", "bad")
        elif mode == "cycle":
            self._status("PLC cycle: every bus reads all its devices in turn - see the Cycle tab", "ok")
        else:
            self._status("Independent: every device is read at its own scan rate", "ok")

    def _target_changed(self):
        text = self.target.get().strip()
        try:
            ms = mon.parse_target_ms(text)
        except ValueError as e:
            self.l_target.configure(text=str(e))
            return
        self.l_target.configure(text="")
        self.app.cfg["devices"]["cycle_target_ms"] = text
        self.app.devpoll.target_s = ms / 1000.0 if ms else None

    def _folder_changed(self):
        self.app.cfg["devices"]["log_folder"] = self.folder.get().strip()

    def browse(self):
        p = filedialog.askdirectory(parent=self.w, title="Folder for the device log files",
                                    initialdir=self.folder.get() or None)
        if p:
            self.folder.set(p)

    def _log_toggled(self):
        on = self.log_on.get()
        app = self.app
        app.cfg["devices"]["log_on"] = on
        if on and not self.folder.get().strip():
            self.log_on.set(False)
            app.cfg["devices"]["log_on"] = False
            self._status("Choose a folder for the device logs first", "bad")
            return
        if on and app.devices_running() and app.devlogs is None:
            if app._open_devlogs():
                self._status(f"Logging every device to {self.folder.get()}", "ok")
        elif not on:
            app._close_devlogs()
            self._status("Device logging stopped", "info")
        save_settings(app.cfg)

    def log_stopped(self, err):
        self.log_on.set(False)
        self._status(f"Device log write failed, logging stopped: {err}", "bad")

    def start_all(self):
        errors = self.app.start_all_devices()
        self.refresh_states()
        if errors:
            n, e = next(iter(errors.items()))
            self._status(f"{len(errors)} device(s) not started - {n}: {e}", "bad")
        else:
            self._status("Polling all enabled devices", "ok")
        return errors

    def stop_all(self):
        self.app.stop_all_devices()
        self.refresh_states()
        self._status("All devices stopped", "info")

    def toggle_device(self, name):
        if self.app.devpoll.running(name):
            self.app.stop_device(name)
            self._status(f"'{name}' stopped", "info")
            err = None
        else:
            if self.app.cfg["devices"].get("log_on") and self.app.devlogs is None:
                self.app._open_devlogs()
            err = self.app.start_device(name)
            self._status(f"'{name}' not started: {err}" if err else f"'{name}' started", "bad" if err else "ok")
        self.refresh_states()
        return err

    def toggle_selected(self):
        name = self.selected()
        if not name:
            self._status("Select a device first", "warn")
            return
        self.toggle_device(name)

    def add(self):
        return DeviceDialog(self.app, None, self._saved)

    def edit(self, name=None):
        name = name or self.selected()
        if not name:
            self._status("Select a device first", "warn")
            return None
        return DeviceDialog(self.app, self.app.dev_by_name(name), self._saved)

    def scan_add(self):
        if self.scan_dlg is not None and self.scan_dlg.alive():
            self.scan_dlg.w.deiconify()
            self.scan_dlg.w.lift()
            return self.scan_dlg
        self.scan_dlg = ScanAddDialog(self)
        return self.scan_dlg

    def toggle_demo(self):
        app = self.app
        if app.demo is not None:
            app.stop_demo()
            self.b_demo.configure(text="Demo devices")
            self.reload()
            self._status("Demo devices stopped and removed", "info")
            return
        from tkinter import messagebox
        ans = messagebox.askyesnocancel(
            "Demo devices",
            "Start 5 simulated devices with moving values?\n\n"
            "Yes:  one emulated RS-485 line at 19200 baud (shows realistic cycle time, good for the Bus map)\n"
            "No:   a separate TCP simulator per device\n\n"
            "They are only for trying the program: never saved, removed when you press the button again "
            "or close mbtool.", parent=self.w)
        if ans is None:
            return
        try:
            names = app.start_demo("rtu" if ans else "tcp")
        except Exception as e:
            self._status(f"Could not start the demo devices: {e}", "bad")
            return
        self.b_demo.configure(text="Stop demo")
        self.reload()
        self._status(f"{len(names)} demo devices started - one tab each; values move by themselves", "ok")

    def devices_added(self, names):
        """Called by Scan & add after it appended devices: tabs + table now, nothing is started."""
        self.app.save_devices()
        self.reload()
        if names and self.tree.exists(names[-1]):
            self.tree.selection_set(names[-1])
        self._status(f"{len(names)} device(s) added: " + ", ".join(names), "ok")

    def _saved(self, old_name, dv):
        app = self.app
        was_running = old_name is not None and app.devpoll.running(old_name)
        renamed = None
        if old_name is not None:
            app.stop_device(old_name)
            i = next(i for i, d in enumerate(app.devices) if d["name"] == old_name)
            app.devices[i] = dv
            if old_name != dv["name"]:
                renamed = (old_name, dv["name"])
                app.dev_status.pop(old_name, None)
                for r in app.alarms.rules:
                    if r.device == old_name:
                        r.device = dv["name"]
                calc = app.cfg["devices"].get("calc", {}).get("dev", {})
                if old_name in calc:
                    calc[dv["name"]] = calc.pop(old_name)
        else:
            app.devices.append(dv)
        if renamed:
            app.sync_poll_tabs(renamed)  # a poll tab keeps its page, only the title changes
        app.save_devices()
        if renamed:
            self.sync_tabs(renamed)
        self.reload()
        self.tree.selection_set(dv["name"])
        if was_running and dv["enabled"]:
            err = app.start_device(dv["name"])
            if err:
                self._status(f"'{dv['name']}' not restarted: {err}", "bad")
                return
        self._status(f"Device '{dv['name']}' saved", "ok")

    def remove(self, name=None):
        name = name or self.selected()
        if not name:
            return
        self.app.stop_device(name)
        self.app.devices = [d for d in self.app.devices if d["name"] != name]
        self.app.dev_status.pop(name, None)
        self.app.save_devices()
        self.reload()
        self._status(f"Device '{name}' removed (its alarm rules stay; delete them in Alerts)", "info")


POINT_COLS = (("point", "Point", 220), ("value", "Value", 140), ("unit", "Unit", 70), ("raw", "Raw", 110),
              ("status", "Status", 260))


def point_table(app, parent, points):
    """Named points table of a device with a preset (Devices tab, poll tab) -> (frame, Treeview)."""
    s = app.s
    tf = ttk.Frame(parent)
    t = ttk.Treeview(tf, columns=[c[0] for c in POINT_COLS], show="headings", selectmode="browse",
                     style=tree_style(app.root, s))
    for cid, lab, wd in POINT_COLS:
        t.heading(cid, text=lab, anchor="w")
        t.column(cid, width=int(wd * s), stretch=cid in ("point", "status"), minwidth=int(40 * s),
                 anchor="e" if cid in ("value", "raw") else "w")
    t.tag_configure("error", foreground=COL["bad"], background="#fdecea")
    t.tag_configure("stale", foreground=COL["stale"])
    t.tag_configure("flash", background=COL["flash"])
    vsb = ttk.Scrollbar(tf, orient="vertical", command=t.yview)
    t.configure(yscrollcommand=vsb.set)
    t.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    tf.rowconfigure(0, weight=1)
    tf.columnconfigure(0, weight=1)
    for p in points:
        t.insert("", "end", iid=p.name, values=(p.name, "", p.unit, "", "no data yet"), tags=("stale",))
    return tf, t


def point_raw_text(raw):
    if isinstance(raw, bool):
        return "1" if raw else "0"
    if isinstance(raw, int):
        return f"{raw}" if raw < 0 or raw > 0xFFFF else f"{raw}  (0x{raw:04X})"
    return "" if raw is None else str(raw)


def fill_point_table(tree, points, st, state, flash=None):
    """Latest DeviceStatus -> the points table. flash: {name: time} of rows whose value just changed (yellow)."""
    have = st is not None and st.last_ok is not None
    for p in points:
        if not have:
            vals, tag = (p.name, "", p.unit, "", "no data yet"), "stale"
        elif p.name in st.point_errors:
            vals, tag = (p.name, "", p.unit, "", "ERROR: " + st.point_errors[p.name]), "error"
        elif p.name in st.texts:
            stale = state != "ok"
            txt = st.texts[p.name]
            if p.unit and txt.endswith(" " + p.unit):  # the unit has its own column
                txt = txt[:-len(p.unit) - 1]
            vals = (p.name, txt, p.unit, point_raw_text(st.raws.get(p.name)),
                    "OK" if not stale else ("old value - " + ("stopped" if state == "off" else state)))
            tag = "stale" if stale else ("flash" if flash and p.name in flash else "")
        else:
            vals, tag = (p.name, "", p.unit, "", "not read"), "stale"
        tree.item(p.name, values=vals, tags=(tag,) if tag else ())


class DeviceTab:
    """One device's tab in the Devices window: header (connection, ID, last update, counters, last error, buttons)
    and its full latest data - the preset points (scaled values / enum names from the poll's decode) or the
    configured register block in the multi-type grid. Fed from DeviceStatus; never polls by itself."""
    PCOLS = POINT_COLS

    def __init__(self, win, name):
        self.win, self.app, self.name = win, win.app, name
        s = self.app.s
        self.frame = f = ttk.Frame(win.nb, padding=(8, 6, 8, 4))
        help_key(f, "devices.tabs")
        hdr = ttk.Frame(f)
        hdr.pack(fill="x")
        info = ttk.Frame(hdr)
        info.pack(side="left", fill="x", expand=True)
        self.l = {}
        for r, c, key, lab in ((0, 0, "conn", "Connection"), (0, 2, "unit", "Slave ID"), (0, 4, "what", "Reads"),
                               (1, 0, "state", "State"), (1, 2, "last", "Last update"),
                               (1, 4, "counts", "OK / errors"), (2, 0, "quality", "Line quality"),
                               (2, 2, "resp", "Response ms"), (2, 4, "kinds", "Errors by type")):
            ttk.Label(info, text=lab + ":", style="Info.TLabel").grid(row=r, column=c, sticky="e",
                                                                    padx=(0 if c == 0 else int(14 * s), 4))
            self.l[key] = ttk.Label(info, text="")
            self.l[key].grid(row=r, column=c + 1, sticky="w")
        bf = ttk.Frame(hdr)
        bf.pack(side="right", anchor="n")
        self.b_run = ttk.Button(bf, text="Start", width=8, command=self.toggle)
        self.b_run.pack(side="left")
        Tooltip(self.b_run, "Start / stop polling this device")
        ttk.Button(bf, text="Edit...", command=lambda: self.win.edit(self.name)).pack(side="left", padx=(4, 0))
        b_show = ttk.Button(bf, text="Show in main window", command=lambda: self.win.activate(self.name))
        b_show.pack(side="left", padx=(4, 0))
        Tooltip(b_show, "Show this device in the Trend window (and select it in Traffic / Alerts)")
        b_reset = ttk.Button(bf, text="Reset counters", command=self.reset_counters)
        b_reset.pack(side="left", padx=(4, 0))
        Tooltip(b_reset, "Set OK / error counters, error types and response times of this device back to zero")
        help_btn(self.app, bf, "devices.tabs", side="left", padx=(6, 0))
        self.l_err = ttk.Label(f, text="", style="Hint.TLabel", wraplength=int(1000 * s), justify="left")
        self.l_err.pack(fill="x", pady=(2, 4))
        self.body = ttk.Frame(f)
        self.body.pack(fill="both", expand=True)
        self.sig = self.kind = None
        self.grid = self.ptree = None
        self.points, self.dev = [], None
        self.filled_t = None
        self.state = None
        win.nb.add(f, text=" " + name, image=win.dots["off"], compound="left")

    def destroy(self):
        self._drop_grid()
        try:
            self.frame.destroy()
        except tk.TclError:
            pass

    def _drop_grid(self):
        g, self.grid = self.grid, None
        if g is not None and g.flash_job:
            try:
                g.cv.after_cancel(g.flash_job)
            except tk.TclError:
                pass
            g.flash_job = None

    def toggle(self):
        return self.win.toggle_device(self.name)

    def reset_counters(self):
        st = self.app.dev_status.get(self.name)
        if st is not None:
            st.reset_counters()
        self.refresh()
        self.win.update_row(self.name)
        self.win._status(f"Counters of '{self.name}' reset", "info")

    def _signature(self, dv):
        return (dv["preset"], dv["fc"], dv["addr"], dv["qty"], dv["fmt"], str(self.app.addr_base()),
                tuple(self.app.multi_cols), self.app.multi_align)

    def _build_body(self, dv):
        app, s = self.app, self.app.s
        self._drop_grid()
        for c in self.body.winfo_children():
            c.destroy()
        self.ptree, self.points, self.filled_t = None, [], None
        if dv["preset"]:
            try:
                pr, _data = app.device_preset(dv)
            except (OSError, ValueError, PresetError) as e:
                ttk.Label(self.body, text=f"Preset not usable: {e}", style="Hint.TLabel").pack(anchor="w")
                self.kind = "error"
                return
            self.kind = "preset"
            self.points = [p for p in pr.points if p.access != "w"]
            tf, self.ptree = point_table(app, self.body, self.points)
            tf.pack(fill="both", expand=True)
            return
        self.kind = "grid"
        fc, addr, qty = int(dv["fc"]), int(dv["addr"]), int(dv["qty"])
        cols = ["Value"] if fc in (1, 2) else list(app.multi_cols)
        base = app.addr_base()
        g = self.grid = MultiGrid(self.body, s)
        g.set_structure([addr_label(fc, addr + i, base) for i in range(qty)], cols)
        g.frame.pack(fill="both", expand=True)
        help_key(g.frame, "devices.tabs")

    _raw_text = staticmethod(point_raw_text)

    def fill(self, st, state):
        dv = self.dev
        if self.kind == "preset" and self.ptree is not None:
            fill_point_table(self.ptree, self.points, st, state)
        elif self.kind == "grid" and self.grid is not None and dv is not None:
            if st is not None and st.data is not None and st.last_ok is not None:
                fc, addr = int(dv["fc"]), int(dv["addr"])
                rows = mon.multi_rows(fc, addr, st.data, self.grid.cols, self.app.addr_base(),
                                      align=self.app.multi_align)
                self.grid.set_values([r[1:] for r in rows], raw=[int(v) for v in st.data])
                if state != "ok":
                    self.grid.set_stale()

    def refresh(self, full=False):
        app = self.app
        dv = app.dev_by_name(self.name)
        if dv is None:
            return
        self.dev = dv
        if full or self.sig is None:
            sig = self._signature(dv)
            if sig != self.sig:
                self.sig = sig
                self._build_body(dv)
        st = app.dev_status.get(self.name)
        now = time.time()
        state = st.state(now) if st else "off"
        if state != self.state or full:
            self.state = state
            try:
                self.win.nb.tab(self.frame, text=" " + self.name, image=self.win.dots[state])
            except tk.TclError:
                pass
        base = app.addr_base()
        if dv["preset"]:
            what = os.path.basename(dv["preset"]) + (f"  ({len(self.points)} points)" if self.points else "")
        else:
            fc, addr, qty = int(dv["fc"]), int(dv["addr"]), int(dv["qty"])
            what = f"FC{fc:02d} {addr_label(fc, addr, base)} x{qty}"
        running = app.devpoll.running(self.name)
        txt = {"ok": "● OK", "error": "● ERROR", "stale": "● no answer for a while", "off": "○ stopped"}[state]
        if not dv["enabled"]:
            txt += "  (disabled in 'Start all')"
        cfg = {"conn": mon.conn_text(dv["conn"]), "unit": dv["unit"], "what": what, "state": txt,
               "last": (time.strftime("%H:%M:%S", time.localtime(st.last_t))
                        + (f"  ({now - st.last_t:.0f} s ago)" if now - st.last_t >= 2 else "")) if st and st.last_t else "-",
               "counts": f"{st.ok if st else 0} / {st.errors if st else 0}"}
        cfg.update(quality_texts(st))
        for k, v in cfg.items():
            if self.l[k].cget("text") != v:
                self.l[k].configure(text=v)
        self.l["state"].configure(foreground={"ok": "#1e7b34", "error": COL["bad"], "stale": "#a15c00"}.get(
            state, COL["stale"]))
        err = st.last_error if st and st.last_error else ""
        self.l_err.configure(text=("Last error: " + err) if err else "")
        self.b_run.configure(text="Stop" if running else "Start")
        stamp_key = (st.last_t if st else None, state)
        if stamp_key != self.filled_t:
            self.filled_t = stamp_key
            self.fill(st, state)


def quality_texts(st):
    """DeviceStatus -> texts for the 'Line quality', 'Response ms' and 'Errors by type' fields."""
    if st is None or not st.quality.requests:
        return {"quality": "-", "resp": "-", "kinds": "-"}
    q = st.quality.summary()
    pct = q["error_pct"] or 0.0
    grade = "good" if pct == 0 else ("fair" if pct < 1 else ("poor" if pct < 5 else "bad"))
    resp = "-" if q["avg"] is None else (f"avg {q['avg']:.1f}  p95 {q['p95']:.1f}  max {q['max']:.1f}")
    return {"quality": f"{grade} - {q['errors']} error(s) in {q['requests']} requests ({pct:.2f} %)", "resp": resp,
            "kinds": mon.kinds_text(q["kinds"], short=False) or "none"}


TAB_LINE = "#c3c8cf"
TAB_IDLE = "#dfe3e9"
TAB_HOVER = "#e9ecf1"
TAB_HELP = ("Poll tabs: click = switch,  double-click = rename,  right-click = menu (rename, duplicate, close,\n"
            "show in Devices window),  middle-click = close.\n"
            "Ctrl+T new tab,  Ctrl+W close tab,  Ctrl+PgUp / Ctrl+PgDn or Ctrl+Tab switch.\n"
            "The dot: green = OK, red = error, amber = no answer for a while, grey = stopped.")


def elide(font, text, width):
    """text shortened with '…' to fit width pixels in font."""
    if font.measure(text) <= width:
        return text
    while text and font.measure(text + "…") > width:
        text = text[:-1]
    return text + "…"


class TabStrip:
    """Tab bar of the main window: one tab per poll (status dot + title). Click = select, double-click = rename
    in place, right-click = menu, middle-click = close. When the tabs do not fit, ◀ ▶ (and the mouse wheel)
    scroll them and the shown tab is always scrolled into view; ▾ lists every tab; + adds one (Ctrl+T)."""
    MISS = object()  # hit() result: no tab there (None is the first tab's key)

    def __init__(self, parent, app):
        self.app, s = app, app.s
        self.s = s
        self.frame = ttk.Frame(parent)
        self.bg = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self.font = tkfont.nametofont("TkDefaultFont")
        self.h = self.font.metrics("linespace") + int(12 * s)
        self.cv = tk.Canvas(self.frame, height=self.h, width=int(100 * s), highlightthickness=0, bg=self.bg)
        self.b_list = ttk.Button(self.frame, text="▾", width=2, command=self.list_menu)
        self.b_add = ttk.Button(self.frame, text="+", width=3, command=app.new_poll_tab)
        self.b_left = ttk.Button(self.frame, text="◀", width=2, command=lambda: self.scroll(-1))
        self.b_right = ttk.Button(self.frame, text="▶", width=2, command=lambda: self.scroll(1))
        self.b_list.pack(side="right", padx=(2, 0), pady=(0, 2))
        self.b_add.pack(side="right", padx=(4, 0), pady=(0, 2))
        self.cv.pack(side="left", fill="x", expand=True)
        Tooltip(self.b_add, "New poll tab   (Ctrl+T)\nIt starts with the poll definition of the tab that is shown")
        Tooltip(self.b_list, "List of all poll tabs\n\n" + TAB_HELP)
        for b in (self.b_left, self.b_right):
            Tooltip(b, "Scroll the tabs (or turn the mouse wheel over them)")
        self.keys, self.states, self.sel = [None], {}, None
        self.off = 0
        self.follow = True  # scroll the shown tab into view on every redraw (off after a manual scroll)
        self.total = 0
        self.rects = []  # (key, x0, x1, shown text) in strip coordinates
        self.hover = self.MISS
        self.editor = self.ren_key = None
        self.menu = None
        self.arrows = False
        cv = self.cv
        cv.bind("<Configure>", lambda e: self.redraw())
        cv.bind("<Button-1>", self._click)
        cv.bind("<Double-1>", self._dbl)
        cv.bind("<Button-3>", self._ctx)
        cv.bind("<Button-2>", self._middle)
        cv.bind("<MouseWheel>", lambda e: self.scroll(-1 if e.delta > 0 else 1))
        cv.bind("<Motion>", self._motion)
        cv.bind("<Leave>", lambda e: self._set_hover(self.MISS), add="+")

    # ----- model -----
    def set_tabs(self, keys):
        self.keys = list(keys)
        if self.sel not in self.keys:
            self.sel = None
        if self.editor is not None and self.ren_key not in self.keys:
            self.end_rename()
        self.redraw()

    def set_state(self, key, state):
        if self.states.get(key) != state:
            self.states[key] = state
            self.redraw()

    def select(self, key):
        self.sel = key if key in self.keys else None
        self.see(self.sel)

    # ----- geometry -----
    def _layout(self):
        s = self.s
        pad, dot, gap = int(10 * s), int(8 * s), int(6 * s)
        minw, maxw = int(72 * s), int(190 * s)
        x, self.rects = 0, []
        for key in self.keys:
            text = elide(self.font, self.app.tab_title(key), maxw - 2 * pad - dot - gap)
            w = max(minw, 2 * pad + dot + gap + self.font.measure(text))
            self.rects.append((key, x, x + w, text))
            x += w + int(2 * s)
        self.total = x

    def _arrows(self):
        """Show ◀ ▶ only when the tabs do not fit (decided on the width without them: no flip-flop)."""
        w = self.cv.winfo_width()
        if w <= 1:
            return
        room = w + (self.b_left.winfo_width() + self.b_right.winfo_width() if self.arrows else 0)
        need = self.total > room
        if need and not self.arrows:
            self.arrows = True
            self.b_right.pack(side="right", padx=(2, 0), pady=(0, 2), after=self.b_add)
            self.b_left.pack(side="right", padx=(4, 0), pady=(0, 2), after=self.b_right)
        elif not need and self.arrows:
            self.arrows = False
            self.b_left.pack_forget()
            self.b_right.pack_forget()

    def _clamp(self):
        w = max(self.cv.winfo_width(), 1)
        self.off = max(0, min(self.off, max(self.total - w, 0)))

    def redraw(self):
        cv, s, h = self.cv, self.s, self.h
        self._layout()
        self._arrows()
        if self.follow:  # keep the shown tab in view, also after a resize / the arrows appearing
            r = self._rect(self.sel)
            w = cv.winfo_width()
            if r is not None and w > 1:
                if r[1] < self.off:
                    self.off = r[1]
                elif r[2] > self.off + w:
                    self.off = r[2] - w
        self._clamp()
        cv.delete("all")
        w = max(cv.winfo_width(), 1)
        cv.create_line(0, h - 1, w, h - 1, fill=TAB_LINE)
        pad, dot, gap = int(10 * s), int(8 * s), int(6 * s)
        for key, x0, x1, text in self.rects:
            X0, X1 = x0 - self.off, x1 - self.off
            if X1 < 0 or X0 > w:
                continue
            sel = key == self.sel
            top = 1 if sel else int(4 * s)
            fill = self.bg if sel else (TAB_HOVER if key == self.hover else TAB_IDLE)
            cv.create_rectangle(X0, top, X1, h - 1, fill=fill, outline=TAB_LINE)
            if sel:
                cv.create_line(X0 + 1, h - 1, X1, h - 1, fill=self.bg)  # the shown tab is open to its page
                cv.create_line(X0, top, X1 + 1, top, fill=COL["sel"], width=max(2, int(2 * s)))
            cy = (top + h) / 2
            dx = X0 + pad
            cv.create_oval(dx, cy - dot / 2, dx + dot, cy + dot / 2,
                           fill=DOT_COLORS.get(self.states.get(key, "off"), DOT_COLORS["off"]), outline="")
            cv.create_text(dx + dot + gap, cy, text=text, anchor="w", font=self.font,
                           fill="#111111" if sel else "#3c4048")
        if self.arrows:
            self.b_left.state(["!disabled"] if self.off > 0 else ["disabled"])
            self.b_right.state(["!disabled"] if self.off < self.total - w else ["disabled"])
        if self.editor is not None:
            self._place_editor()

    def _rect(self, key):
        return next((r for r in self.rects if r[0] == key), None)

    def see(self, key):
        """Scroll so that tab `key` is fully visible."""
        self._layout()
        r = self._rect(key)
        w = self.cv.winfo_width()
        if r is not None and w > 1:
            if r[1] < self.off:
                self.off = r[1]
            elif r[2] > self.off + w:
                self.off = r[2] - w
        self.follow = key == self.sel
        self.redraw()

    def scroll(self, step):
        self.follow = False  # scrolled by hand: the shown tab may leave the view
        self.off += int(step * 140 * self.s)
        self.redraw()

    def hit(self, x):
        x += self.off
        for key, x0, x1, _t in self.rects:
            if x0 <= x < x1:
                return key
        return self.MISS

    # ----- mouse -----
    def _click(self, e):
        k = self.hit(e.x)
        if k is not self.MISS:
            self.app.select_poll_tab(k)

    def _dbl(self, e):
        k = self.hit(e.x)
        if k is not self.MISS:
            self.start_rename(k)

    def _middle(self, e):
        k = self.hit(e.x)
        if k is not self.MISS and k is not None:
            self.app.close_poll_tab(k)

    def _motion(self, e):
        self._set_hover(self.hit(e.x))

    def _set_hover(self, k):
        if k is not self.hover:
            self.hover = k
            self.redraw()

    def build_menu(self, key):
        app = self.app
        m = tk.Menu(self.cv, tearoff=0)
        m.add_command(label="Rename...", command=lambda: self.start_rename(key))
        m.add_command(label="Duplicate tab", command=lambda: app.duplicate_poll_tab(key))
        m.add_command(label="New poll tab", accelerator="Ctrl+T", command=app.new_poll_tab)
        if key is not None and key in app.poll_tabs:
            m.add_separator()
            m.add_command(label="Stop polling" if app.devpoll.running(key) else "Start polling",
                          command=app.poll_tabs[key].toggle)
        m.add_separator()
        m.add_command(label="Show in Devices window", command=lambda: app.show_tab_in_devices(key))
        m.add_separator()
        m.add_command(label="Close tab", accelerator="Ctrl+W", command=lambda: app.close_poll_tab(key),
                      state="disabled" if key is None else "normal")
        self.menu = m
        return m

    def _ctx(self, e):
        k = self.hit(e.x)
        if k is self.MISS:
            return
        self.app.select_poll_tab(k)
        m = self.build_menu(k)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def list_menu(self):
        m = tk.Menu(self.cv, tearoff=0)
        mark = {"ok": "●", "error": "●", "stale": "●", "off": "○"}
        for k in self.keys:
            st = self.states.get(k, "off")
            m.add_command(label=f"{mark.get(st, '○')}  {self.app.tab_title(k)}   ({st})",
                          command=lambda k=k: self.app.select_poll_tab(k))
        m.add_separator()
        m.add_command(label="New poll tab", accelerator="Ctrl+T", command=self.app.new_poll_tab)
        self.menu = m
        try:
            m.tk_popup(self.b_list.winfo_rootx(), self.b_list.winfo_rooty() + self.b_list.winfo_height())
        finally:
            m.grab_release()
        return m

    # ----- rename in place -----
    def start_rename(self, key):
        if key not in self.keys:
            return None
        self.end_rename()
        self.see(key)
        e = tk.Entry(self.cv, font=self.font, relief="solid", bd=1, highlightthickness=0)
        e.insert(0, self.app.tab_title(key))
        e.select_range(0, "end")
        self.editor, self.ren_key = e, key

        def ok(_e=None):
            err = self.app.rename_poll_tab(key, e.get())
            if err:
                e.configure(bg="#ffd6d6")
                self.app._status(f"Tab name: {err}", "bad", main=False)
                tab = self.app.poll_tabs.get(key)
                if tab is not None:
                    tab._status(f"Tab name: {err}", "bad")
            else:
                self.end_rename()
            return "break"
        e.bind("<Return>", ok)
        e.bind("<KP_Enter>", ok)
        e.bind("<Escape>", lambda _e: (self.end_rename(), "break")[1])
        e.bind("<FocusOut>", lambda _e: self.cv.after_idle(lambda: self.editor is e and self.end_rename()))
        e.bind("<Key>", lambda _e: e.configure(bg="white"), add="+")
        e.mb_commit = ok
        self._place_editor()
        e.focus_set()
        return e

    def _place_editor(self):
        r = self._rect(self.ren_key)
        if r is None:
            return
        self.cv.delete("ren")
        x0 = r[1] - self.off
        self.cv.create_window(x0 + 2, 2, window=self.editor, anchor="nw", height=self.h - 4,
                              width=max(r[2] - r[1] - 4, int(130 * self.s)), tags="ren")

    def end_rename(self):
        e, self.editor, self.ren_key = self.editor, None, None
        if e is not None:
            self.cv.delete("ren")
            e.destroy()


def poll_spec():
    """The fields of a Poll definition bar: (key, label, width, choices, tooltip)."""
    return [("unit", "Slave ID", 6, None, "Slave address 1-247 (0 = broadcast, 255 often used for TCP)"),
            ("func", "Function", 30, list(FUNCS.values()), "Read function code"),
            ("addr", "Address", 9, None, "Start address, typed in the chosen Address base.\n"
                                         "The hint below shows the protocol address that is sent."),
            ("qty", "Quantity", 7, None, "Registers (1-125) or coils/inputs (1-2000) to read"),
            ("scan", "Scan Rate (ms)", 9, None, "Time between polls in milliseconds (20 or more)"),
            ("fmt", "Display", 15, FORMATS, "How registers are shown.\nLong/Float use 2 registers, Double uses 4.\n"
                                            "'Inverse' = word-swapped")]


class PollTab:
    """An extra tab of the main window ('Poll 2', ...): own Poll definition, connection ('same as Poll 1' or its
    own), Start / Stop, the register grid (or multi-type table, or a preset's named points) with in-place writing,
    and a status line. Backed by the device of the same name in app.devices (tab=True): the Devices engine polls it,
    so scheduling, status, device logging, trend and alerts work as for any device. Never polls by itself."""

    def __init__(self, app, name):
        self.app, self.name, s = app, name, app.s
        self.s = s
        self.frame = f = ttk.Frame(app.pages)
        app.pages.add(f)
        help_key(f, "poll.tabs")
        self.loading = False
        self.apply_job = self.unflash_job = None
        self.state = self.filled = self.sig = self.kind = None
        self.p = None  # like MasterApp.p: the valid poll definition shown (protocol address)
        self.cell_addrs, self.points, self.last_data = [], [], None
        self.layout_key = self.mlayout_key = None
        self.multi_shown = False
        self.ptree = self.ptf = None
        self.prev_texts = {}
        self.snap = None
        self.setup_dlg = self.picker = None
        # ----- poll definition (same fields and checks as the first tab) -----
        lf = self.poll_lf = ttk.LabelFrame(f, text="Poll definition", padding=(10, 4, 10, 0))
        lf.pack(fill="x", padx=8, pady=(6, 2))
        self.pv = {k: tk.StringVar() for k in ("unit", "func", "addr", "qty", "scan", "fmt")}
        self.base_var = tk.StringVar(value=BASE_CHOICES[0])
        self.multi_var = tk.BooleanVar(value=False)
        self.rows_var = tk.StringVar(value="Auto")
        self.pw, self.hints = {}, {}
        for c, (k, lab, wd, vals, tip) in enumerate(poll_spec()):
            w = (ttk.Combobox(lf, textvariable=self.pv[k], values=vals, width=wd, state="readonly") if vals
                 else ttk.Entry(lf, textvariable=self.pv[k], width=wd))
            self.pw[k] = w
            self.hints[k] = vfield(lf, c, lab, w, tip)
        cb = ttk.Combobox(lf, textvariable=self.base_var, values=list(BASE_CHOICES.values()), width=27,
                          state="readonly", style="Wide.TCombobox")
        self.pw["base"] = cb
        self.hints["base"] = vfield(lf, 6, "Address base", cb, BASE_TIP)
        self.hints["fmt"].grid_remove()
        self.cb_multi = ttk.Checkbutton(lf, text="Multi-type view", variable=self.multi_var,
                                        command=self._changed)
        self.cb_multi.grid(row=2, column=5, sticky="nw", padx=(0, 12))
        Tooltip(self.cb_multi, MULTI_TIP)
        for k, key in (("unit", "poll.unit"), ("func", "poll.function"), ("addr", "poll.address"),
                       ("qty", "poll.quantity"), ("scan", "poll.scan"), ("fmt", "poll.display"), ("base", "poll.base")):
            help_key(self.pw[k], key)
        help_key(self.cb_multi, "poll.multi")
        # ----- run / connection / preset row -----
        cr = ttk.Frame(f)
        cr.pack(fill="x", padx=8, pady=(2, 4))
        self.b_run = ttk.Button(cr, text="Start", width=8, command=self.toggle)
        self.b_run.pack(side="left")
        Tooltip(self.b_run, "Start / stop polling this tab\n(F5 and the toolbar's Connect belong to the first tab)")
        ttk.Label(cr, text="Connection:").pack(side="left", padx=(14, 4))
        self.conn_var = tk.StringVar()
        self.cb_conn = ttk.Combobox(cr, textvariable=self.conn_var, state="readonly", width=26)
        self.cb_conn.pack(side="left")
        self.cb_conn.bind("<<ComboboxSelected>>", lambda e: self._conn_picked())
        Tooltip(self.cb_conn, "Same as the first tab: uses (and follows) the first tab's connection settings,\n"
                              "with its own link to the device.\nCustom: this tab's own TCP / RTU / ASCII settings.")
        help_key(self.cb_conn, "poll.tabs")
        self.b_setup = ttk.Button(cr, text="Setup...", command=self.custom_conn)
        self.b_setup.pack(side="left", padx=(4, 0))
        Tooltip(self.b_setup, "Own connection settings for this tab (makes it 'Custom')")
        self.l_conn = ttk.Label(cr, style="Info.TLabel")
        self.l_conn.pack(side="left", padx=(8, 0))
        help_btn(app, cr, "poll.tabs", side="right", padx=(6, 0))
        self.b_nopreset = ttk.Button(cr, text="Register grid", command=self.clear_preset)
        Tooltip(self.b_nopreset, "Leave the preset and show the address / value grid again")
        self.b_preset = ttk.Button(cr, text="Preset...", command=self.pick_preset)
        self.b_preset.pack(side="right")
        Tooltip(self.b_preset, "Show named, scaled values from a device preset in this tab")
        # ----- status line (bottom) -----
        sb = ttk.Frame(f, padding=(8, 2, 8, 4))
        sb.pack(fill="x", side="bottom")
        ttk.Separator(f).pack(fill="x", side="bottom")
        sb.columnconfigure(1, weight=1)
        self.lamp = Lamp(sb, s)
        self.lamp.c.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 6), pady=(2, 0))
        self.l_msg = ttk.Label(sb, style="SInfo.TLabel")
        self.l_msg.grid(row=0, column=1, sticky="w")
        self.l_cnt = ttk.Label(sb)
        self.l_cnt.grid(row=0, column=2, sticky="e")
        self.l_last = ttk.Label(sb, style="Info.TLabel")
        self.l_last.grid(row=1, column=1, columnspan=2, sticky="w")
        # ----- caption + view selectors -----
        cf = ttk.Frame(f)
        cf.pack(fill="x", padx=10, pady=(2, 2))
        self.caption = ttk.Label(cf, style="Info.TLabel")
        self.caption.pack(side="left", fill="x", expand=True)
        self.cb_rows = ttk.Combobox(cf, textvariable=self.rows_var, values=ROW_CHOICES, width=6, state="readonly")
        self.l_rows = ttk.Label(cf, text="Rows per column:")
        self.b_mcols = ttk.Button(cf, text="Columns...", command=app.open_multi_cols)
        Tooltip(self.b_mcols, "Data types shown as columns in the multi-type view (shared by all tabs)")
        help_key(self.cb_rows, "poll.rows")
        help_key(self.b_mcols, "poll.multi")
        # ----- data views -----
        self.grid = RegGrid(f, s, on_edit=self.edit_cell)
        self.grid.on_context = lambda i, e: self.context_menu(e)
        self.mgrid = MultiGrid(f, s, on_edit=self.edit_mcell)
        self.mgrid.on_context = lambda i, e: self.context_menu(e)
        help_key(self.mgrid.frame, "poll.multi")
        self.shown = None  # the packed view: self.grid / self.mgrid / self.ptf
        for var in list(self.pv.values()) + [self.base_var, self.rows_var]:
            var.trace_add("write", lambda *_: self._changed())
        self.load_fields()
        self.show_conn()
        self.refresh(full=True)

    # ----- device <-> fields -----
    def dev(self):
        return self.app.dev_by_name(self.name)

    def running(self):
        return self.app.devpoll.running(self.name)

    def alive(self):
        try:
            return bool(self.frame.winfo_exists())
        except tk.TclError:
            return False

    @staticmethod
    def _snapshot(dv):
        return json.dumps(dv, sort_keys=True)

    def load_fields(self):
        """Device dict -> the fields (no restart)."""
        dv = self.dev()
        if dv is None:
            return
        self.loading = True
        try:
            fc = int(dv["fc"]) if str(dv["fc"]).isdigit() and int(dv["fc"]) in FUNCS else 3
            base = mon.norm_base(dv["tab_base"])
            try:
                addr = addr_label(fc, int(dv["addr"]), base)
            except ValueError:
                addr = dv["addr"]
            for k, v in (("unit", dv["unit"]), ("func", FUNCS[fc]), ("addr", addr), ("qty", dv["qty"]),
                         ("scan", dv["scan"]), ("fmt", dv["fmt"] if dv["fmt"] in FORMATS else "Unsigned")):
                self.pv[k].set(v)
            self.base_var.set(BASE_CHOICES[base])
            self.multi_var.set(bool(dv["tab_multi"]))
            self.rows_var.set(dv["tab_rows"] if dv["tab_rows"] in ROW_CHOICES else "Auto")
        finally:
            self.loading = False
        self.grid.rows_setting = self.rows_var.get()
        self._validate()
        self.snap = self._snapshot(dv)

    def reload(self):
        """The device was edited elsewhere (Devices window, workspace): show it."""
        dv = self.dev()
        if dv is not None and self._snapshot(dv) != self.snap:
            self.load_fields()
            self.show_conn()
            self.refresh(full=True)

    def addr_base(self):
        return next((k for k, v in BASE_CHOICES.items() if v == self.base_var.get()), 0)

    def _changed(self):
        if self.loading:
            return
        if self.grid.rows_setting != self.rows_var.get():
            self.grid.rows_setting = self.rows_var.get()
            self.grid._layout(force=True)
        self._validate()
        self._schedule_apply()

    def _schedule_apply(self):
        if self.apply_job is not None:
            self.frame.after_cancel(self.apply_job)
        # a running tab is restarted with the new definition - wait until the typing stops
        self.apply_job = self.frame.after(400 if self.running() else 1, self.apply)

    def flush(self):
        if self.apply_job is not None:
            self.frame.after_cancel(self.apply_job)
            self.apply()

    def apply(self):
        """Valid fields -> the device (restarted when it polls and what it reads changed)."""
        self.apply_job = None
        dv, p = self.dev(), self.p
        if dv is None:
            return
        dv.update(tab_base=str(self.addr_base()), tab_multi=bool(self.multi_var.get()), tab_rows=self.rows_var.get())
        if p is not None:
            new = {"unit": str(p["unit"]), "scan": str(p["scan"])}
            if not p.get("preset"):
                new.update(fc=str(p["fc"]), addr=str(p["addr"]), qty=str(p["qty"]), fmt=self.pv["fmt"].get())
            if any(dv[k] != v for k, v in new.items()):
                dv.update(new)
                if self.running():
                    self._restart()
        self.snap = self._snapshot(dv)

    def _restart(self):
        st = self.app.dev_status.get(self.name)
        if st is not None:
            st.data = None  # the old block does not belong to the new definition
        self.last_data = None
        err = self.app.start_device(self.name, load_alarms=False)
        if err:
            self._status(f"Not restarted: {err}", "bad")
        self.filled = None

    def _validate(self):
        dv = self.dev()
        preset = bool(dv and dv["preset"])
        pv = self.pv
        fc = int(pv["func"].get()[:2]) if pv["func"].get()[:2].isdigit() else 3
        base = self.addr_base()
        bits = fc in (1, 2)
        fmt = "Unsigned" if bits else pv["fmt"].get()
        errs, vals, info = {}, {}, {}

        def chk(k, fn):
            try:
                vals[k] = fn(pv[k].get())
            except ValueError as e:
                errs[k] = str(e)
        chk("unit", lambda t: int_in(t, 0, 255))
        chk("scan", lambda t: int_in(t, 20, 3600000))
        if not preset:
            chk("addr", lambda t: parse_addr(t, fc, base))
            chk("qty", lambda t: int_in(t, 1, 2000 if bits else 125))
            if "addr" in vals and "qty" in vals and vals["addr"] + vals["qty"] > 65536:
                errs["qty"] = f"max {65536 - vals['addr']} here"
            if not bits and "qty" in vals and "qty" not in errs:
                w = fmt_width(fmt)
                if vals["qty"] < w:
                    errs["qty"] = f"{fmt} needs {w}+"
                elif vals["qty"] % w:
                    info["qty"] = f"not a multiple of {w}"
            if "addr" in vals:
                info["addr"] = f"= protocol {vals['addr']}"
        for k in ("func", "addr", "qty"):
            self.pw[k].configure(state="disabled" if preset else ("readonly" if k == "func" else "normal"))
        self.pw["fmt"].configure(state="disabled" if bits or preset else "readonly")
        self.cb_multi.configure(state="disabled" if bits or preset else "normal")
        self.poll_lf.configure(text="Poll definition  -  preset mode: Slave ID and Scan Rate apply; "
                                    "the register range comes from the preset" if preset else "Poll definition")
        set_hints(self.hints, errs, info)
        if errs:
            self.p = None
            self._status("Poll definition has errors - see the red hints", "bad")
            self._show_view()
            return
        if self.l_msg.cget("text").startswith("Poll definition has errors"):
            self._status("Poll definition OK", "info")
        if preset:
            self.p = dict(unit=vals["unit"], fc=0, scan=vals["scan"], plc=base, preset=True)
            self._show_view()
            return
        p = dict(unit=vals["unit"], fc=fc, addr=vals["addr"], qty=vals["qty"], scan=vals["scan"], fmt=fmt, plc=base)
        key = (fc, p["addr"], p["qty"], fmt, base)
        if key != self.layout_key:
            self.layout_key = key
            if bits:
                offs, chars = range(p["qty"]), 1
            else:
                w = fmt_width(fmt)
                offs, chars = range(0, p["qty"] - w + 1, w), fmt_chars(fmt)
            self.cell_addrs = [p["addr"] + o for o in offs]
            self.grid.set_cells([addr_label(fc, a, base) for a in self.cell_addrs], chars)
            self.filled = None
        self.p = p
        self._show_view()
        self._multi_layout()
        if self.last_data is not None and self._data_matches():
            self._fill_grid(self.last_data, flash=False)
            if self.state != "ok":
                self.grid.set_stale()
                self.mgrid.set_stale()

    def _data_matches(self):
        """The device polls exactly the shown block (not a definition still being typed)."""
        dv, p = self.dev(), self.p
        if dv is None or not p or p.get("preset"):
            return False
        return (dv["fc"], dv["addr"], dv["qty"], dv["unit"]) == (str(p["fc"]), str(p["addr"]), str(p["qty"]),
                                                                    str(p["unit"]))

    # ----- views -----
    def _show_view(self):
        dv = self.dev()
        preset = bool(dv and dv["preset"])
        fc = int(self.pv["func"].get()[:2]) if self.pv["func"].get()[:2].isdigit() else 3
        multi = bool(self.multi_var.get()) and fc not in (1, 2) and not preset
        want = self.ptf if preset else (self.mgrid.frame if multi else self.grid.frame)
        if want is None:
            return
        self.multi_shown = multi
        if self.shown is not want:
            for g in (self.grid, self.mgrid):
                g.end_edit()
            for fr in (self.grid.frame, self.mgrid.frame, self.ptf):
                if fr is not None and fr is not want:
                    fr.pack_forget()
            want.pack(fill="both", expand=True, padx=8, pady=(0, 4))
            self.shown = want
            if multi:
                self.mlayout_key = None
        for wd in (self.b_mcols, self.cb_rows, self.l_rows):
            wd.pack_forget()
        if multi:
            self.b_mcols.pack(side="right")
        elif not preset:
            self.cb_rows.pack(side="right")
            self.l_rows.pack(side="right", padx=(12, 4))
        if preset:
            self.caption.configure(text="Preset view: named, scaled values from the preset (read-only here - "
                                        "write registers with F6).  Changed values flash yellow.")
        elif multi:
            self.caption.configure(text=(
                "Multi-type view: each register in several data types ('-' = not enough registers left).  "
                + ("Read-only data (FC 04)." if fc == 4 else
                   "Double-click a cell (or select + Enter) to write it in that column's type.")
                + "  Right-click / F4: inspect."))
        else:
            self.caption.configure(text=(
                "Read-only data (FC 02 / 04).  Changed values flash yellow." if fc in (2, 4) else
                "Double-click a value (or select it and press Enter) to write it.  Changed values flash yellow."
                "  Right-click / F4: inspect."))

    def _multi_layout(self):
        p = self.p
        if not self.multi_shown or not p or p.get("preset"):
            return
        key = (p["fc"], p["addr"], p["qty"], p["plc"], tuple(self.app.multi_cols), self.app.multi_align)
        if key == self.mlayout_key:
            return
        self.mlayout_key = key
        self.mgrid.set_structure([addr_label(p["fc"], p["addr"] + i, p["plc"]) for i in range(p["qty"])],
                                 self.app.multi_cols)

    def _build_preset(self, dv):
        """(Re)build the named points table for the device's preset."""
        if self.ptf is not None:
            if self.shown is self.ptf:
                self.shown = None
            self.ptf.destroy()
        self.ptf = self.ptree = None
        self.points, self.prev_texts = [], {}
        if not dv["preset"]:
            self.kind = "grid"
            return
        try:
            pr, _data = self.app.device_preset(dv)
        except (OSError, ValueError, PresetError) as e:
            self.kind = "error"
            self.ptf = ttk.Frame(self.frame)
            ttk.Label(self.ptf, text=f"Preset not usable: {e}", style="Hint.TLabel").pack(anchor="w")
            return
        self.kind = "preset"
        self.points = [p for p in pr.points if p.access != "w"]
        self.ptf, self.ptree = point_table(self.app, self.frame, self.points)
        help_key(self.ptf, "poll.tabs")

    def _fill_grid(self, data, flash=True):
        p = self.p
        self.grid.set_values([t for _i, t in render(data, p["fc"], p["fmt"])], flash=flash)
        if self.multi_shown:
            self._multi_layout()
            rows = mon.multi_rows(p["fc"], p["addr"], data, self.mgrid.cols, p["plc"], align=self.app.multi_align)
            self.mgrid.set_values([r[1:] for r in rows], raw=[int(v) for v in data], flash=flash)

    def fill(self, st, state):
        if self.kind == "preset" and self.ptree is not None:
            now = time.monotonic()
            texts = dict(st.texts) if st is not None and st.last_ok is not None else {}
            flash = {n: now for n, t in texts.items() if n in self.prev_texts and self.prev_texts[n] != t}
            if texts:
                self.prev_texts = texts
            fill_point_table(self.ptree, self.points, st, state, flash)
            if flash:
                if self.unflash_job is not None:
                    self.frame.after_cancel(self.unflash_job)
                self.unflash_job = self.frame.after(1200, self._unflash)
            return
        if self.kind != "grid" or not self.p:
            return
        if st is not None and st.data is not None and st.last_ok is not None and self._data_matches():
            if st.last_ok == st.last_t:  # this very poll succeeded
                self.last_data = list(st.data)
                self._fill_grid(self.last_data)
        if state != "ok":
            self.grid.set_stale()
            self.mgrid.set_stale()

    def _unflash(self):
        self.unflash_job = None
        if self.ptree is not None:
            st = self.app.dev_status.get(self.name)
            fill_point_table(self.ptree, self.points, st, self.state or "off")

    def refresh(self, full=False):
        app = self.app
        dv = self.dev()
        if dv is None or not self.alive():
            return
        sig = (dv["preset"],)
        if full or sig != self.sig:
            if sig != self.sig:
                self.sig = sig
                self._build_preset(dv)
                self.layout_key = None
                self._validate()
                self.b_nopreset.pack_forget()
                if dv["preset"]:
                    self.b_nopreset.pack(side="right", padx=(0, 4))
            if full:
                self.mlayout_key = None
                self._multi_layout()
                if self.last_data is not None and self._data_matches():
                    self._fill_grid(self.last_data, flash=False)
            self._show_view()
            self.filled = None
        st = app.dev_status.get(self.name)
        now = time.time()
        state = st.state(now) if st else "off"
        if state != self.state:
            self.state = state
            app.tabstrip.set_state(self.name, state)
        running = self.running()
        self.b_run.configure(text="Stop" if running else "Start")
        self.lamp.set(DOT_COLORS.get(state, COL["off"]))
        self.l_cnt.configure(text=f"Polls OK: {st.ok if st else 0}     Errors: {st.errors if st else 0}")
        parts = []
        if st and st.last_t:
            parts.append("Last update " + time.strftime("%H:%M:%S", time.localtime(st.last_t))
                         + (f" ({now - st.last_t:.0f} s ago)" if now - st.last_t >= 2 else ""))
        if st and st.last_error:
            parts.append(f"Last error: {st.last_error}")
        self.l_last.configure(text="     ".join(parts))
        stamp = (st.last_t if st else None, state)
        if stamp != self.filled:
            self.filled = stamp
            self.fill(st, state)
            if running and st is not None and st.last_t:
                if state == "ok":
                    what = (f"{len(self.points)} points" if self.kind == "preset" else
                            f"{len(st.data or [])} {({1: 'coils', 2: 'inputs'}).get(self.p['fc'] if self.p else 3, 'registers')}")
                    self._status(f"OK  {time.strftime('%H:%M:%S', time.localtime(st.last_t))}   {what} read", "ok",
                                 keep_write=True)
                elif state == "error":
                    self._status(f"Error: {st.last_error}", "bad", keep_write=True)
                elif state == "stale":
                    self._status("No answer for a while - check the connection", "warn", keep_write=True)

    # ----- status -----
    def _status(self, text, kind="info", keep_write=False):
        """keep_write: a poll result does not overwrite a write result for 3 s."""
        if keep_write and time.monotonic() < getattr(self, "msg_hold", 0):
            return
        if not keep_write:
            self.msg_hold = time.monotonic() + 3.0 if text.startswith(("Written", "Write failed", "Writing")) else 0
        self.l_msg.configure(text=text, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel",
                                               "warn": "SWarn.TLabel"}.get(kind, "SInfo.TLabel"))

    # ----- connection -----
    def show_conn(self):
        dv = self.dev()
        if dv is None or not self.alive():
            return
        same = f"Same as {self.app.main_tab_name()} connection"
        self.cb_conn.configure(values=[same, "Custom connection"])
        self.conn_var.set(same if dv["tab_link"] else "Custom connection")
        self.l_conn.configure(text=mon.conn_text(dv["conn"] if not dv["tab_link"] else
                                                 mon.norm_conn(self.app.cfg["conn"])))

    def _conn_picked(self):
        dv = self.dev()
        if dv is None:
            return
        if self.cb_conn.current() == 0:
            if not dv["tab_link"]:
                dv["tab_link"] = True
                new = mon.norm_conn(self.app.cfg["conn"])
                if new != dv["conn"]:
                    dv["conn"] = new
                    if self.running():
                        self._restart()
                self._status(f"Connection: same as {self.app.main_tab_name()} ({mon.conn_text(new)})", "info")
            self.show_conn()
        else:
            self.custom_conn()

    def custom_conn(self):
        dv = self.dev()
        if dv is None:
            return None
        dlg = SetupDialog(self.app, conn=dict(dv["conn"]), on_done=self._custom_done,
                          title=f"Connection - {self.name}")
        dlg.w.bind("<Destroy>", lambda e: e.widget is dlg.w and self.show_conn(), add="+")
        self.setup_dlg = dlg
        return dlg

    def _custom_done(self, conn):
        dv = self.dev()
        if dv is None:
            return
        new = mon.norm_conn(conn)
        changed = new != dv["conn"] or dv["tab_link"]
        dv["conn"], dv["tab_link"] = new, False
        if changed and self.running():
            self._restart()
        self.show_conn()
        self._status(f"Own connection: {mon.conn_text(new)}", "info")

    # ----- run -----
    def toggle(self):
        if self.running():
            self.app.stop_device(self.name)
            self._status("Stopped", "info")
            return None
        return self.start()

    def start(self):
        app = self.app
        self.flush()
        dv = self.dev()
        if dv is None:
            return "unknown tab"
        conn = mon.norm_conn(app.cfg["conn"]) if dv["tab_link"] else dv["conn"]
        clash = conn["mode"] != "TCP" and app.state != "off" and same_target(conn, mon.norm_conn(app.cfg["conn"]))
        if app.cfg["devices"].get("log_on") and app.devlogs is None:
            app._open_devlogs()
        st = app.dev_status.get(self.name)
        if st is not None:
            st.data = None
        err = app.start_device(self.name, load_alarms=False)
        if err:
            self._status(f"Not started: {err}", "bad")
        elif clash:  # the first tab holds the COM port: this tab's open will fail with "busy"
            self._status(f"{conn['com']} is open in {app.main_tab_name()} - a serial port can be used by one "
                         f"connection only: disconnect {app.main_tab_name()} (F5) or give this tab another port",
                         "warn")
        else:
            self._status(f"Polling {mon.conn_text(conn)} ...", "info")
        self.refresh()
        return err

    # ----- preset -----
    def pick_preset(self):
        dv = self.dev()
        self.picker = SimPresetPicker(self.app, self.app.root, self.set_preset, current=dv["preset"] if dv else "",
                                      title=f"Preset for {self.name}",
                                      fail_text="Not a valid preset - see the tab's status line")
        return self.picker

    def set_preset(self, path):
        dv = self.dev()
        if dv is None:
            return False
        if path:
            try:
                _d, pr = read_preset_file(path)
            except (OSError, ValueError, PresetError) as e:
                self._status(f"Preset not usable: {e}", "bad")
                return False
        dv["preset"] = os.path.normpath(path) if path else ""
        self.refresh(full=True)
        if self.running():
            self._restart()
        self.snap = self._snapshot(dv)
        self.app.trend_sources_changed()
        self._status(f"Preset '{pr.name}': {len(self.points)} points" if path else "Register grid", "info")
        return True

    def clear_preset(self):
        return self.set_preset("")

    # ----- writing (same paths and checks as the first tab) -----
    def edit_cell(self, i):
        p = self.p
        if not p or p.get("preset") or i >= len(self.cell_addrs):
            return
        if p["fc"] not in (1, 3):
            self._status(f"FC {p['fc']:02d} data is read-only - switch Function to 01 or 03 to edit", "warn")
            return
        if not self.running():
            self._status("Not polling - press Start in this tab, then edit", "warn")
            return
        self.grid.start_edit(i, lambda i, text: MasterApp._commit_cell(self, p, i, text))

    def edit_mcell(self, row, col):
        p = self.p
        if not p or p.get("preset") or not self.multi_shown or row >= len(self.mgrid.labels):
            return
        if p["fc"] != 3:
            self._status(f"FC {p['fc']:02d} data is read-only - switch Function to 03 to edit", "warn")
            return
        if not self.running():
            self._status("Not polling - press Start in this tab, then edit", "warn")
            return
        self.mgrid.start_edit(row, col, lambda r, c, text: MasterApp._commit_mcell(self, p, r, c, text))

    def run_bg(self, fn, done):
        """Run fn(master) with this tab's link (the Devices worker's master) on a thread; done on the Tk thread."""
        app = self.app
        if not self.running():
            done(None, "Not polling - press Start in this tab")
            return
        m = app.devpoll.master_for(self.name)
        if m is None:
            done(None, "not connected yet - wait for the first successful poll")
            return

        def work():
            try:
                r = fn(m)
                app.q.put(("call", None, done, r, None))
            except Exception as e:
                app.q.put(("call", None, done, None, str(e) or repr(e)))
        threading.Thread(target=work, daemon=True).start()

    def write_done(self, desc, err):
        if err:
            self._status(f"Write failed ({desc}): {err}", "bad")
        else:
            self._status(f"Written: {desc}", "ok")
            self.app.devpoll.poke(self.name)  # read again right away to show the new value

    # ----- inspector / context menu -----
    def context_menu(self, e):
        m = tk.Menu(self.frame, tearoff=0)
        m.add_command(label="Inspect value...", accelerator="F4", command=self.app.open_inspector)
        m.add_command(label="Write...", accelerator="F6", command=self.app.open_write)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def inspect_source(self):
        p = self.p
        if not p:
            return None, "Fix the poll definition of this tab first."
        if p.get("preset"):
            return None, "This tab shows a preset - the inspector works on a register grid."
        if self.multi_shown:
            i = self.mgrid.sel
            if i is None or i >= len(self.mgrid.labels):
                return None, "Select a value in the multi-type table (click it)."
            addr, default_n = p["addr"] + i, mon.multi_width(self.mgrid.sel_format() or "Unsigned")
        else:
            i = self.grid.sel
            if i is None or i >= len(self.cell_addrs):
                return None, "Select a value in the register grid (click it)."
            addr, default_n = self.cell_addrs[i], fmt_width(p["fmt"])
        if p["fc"] in (1, 2):
            return None, "Coils / discrete inputs are single bits - choose FC 03 or 04 to inspect registers."
        words = []
        if self.last_data is not None and self._data_matches():
            off = addr - p["addr"]
            words = list(self.last_data[off:off + 4])
        lab = addr_label(p["fc"], addr, p["plc"])
        title = (f"{self.name}:  {('Holding' if p['fc'] == 3 else 'Input')} register {lab} "
                 f"(FC{p['fc']:02d}, address {addr})")
        key = ("tab", self.name, p["fc"], addr) + ((self.mgrid.sel_format(),) if self.multi_shown else ())
        return dict(key=key, title=title, fc=p["fc"], addr=addr, words=words, default_n=default_n, point=None), None

    # ----- teardown -----
    def destroy(self):
        for job in (self.apply_job, self.unflash_job):
            if job is not None:
                try:
                    self.frame.after_cancel(job)
                except tk.TclError:
                    pass
        self.apply_job = self.unflash_job = None
        for g in (self.grid, self.mgrid):
            g.end_edit()
            if g.flash_job:
                try:
                    g.cv.after_cancel(g.flash_job)
                except tk.TclError:
                    pass
                g.flash_job = None
        app = self.app
        if app.write_dlg is not None and getattr(app.write_dlg, "t", None) is self and app.write_dlg.alive():
            app.write_dlg.close()
        for dlg in (self.setup_dlg, self.picker):
            if dlg is not None and dlg.alive():
                dlg.close()
        try:
            app.pages.forget(self.frame)
        except tk.TclError:
            pass
        try:
            self.frame.destroy()
        except tk.TclError:
            pass


class CycleTab:
    """Devices > Cycle: measured PLC cycle statistics per bus and per device + a rolling chart of the cycle time."""
    BUS_COLS = (("bus", "Bus", 150), ("cycles", "Cycles", 58), ("last", "Last ms", 66), ("avg", "Avg ms", 66),
                ("min", "Min ms", 66), ("max", "Max ms", 66), ("jitter", "Jitter ms", 70), ("cps", "Cycles/s", 66),
                ("target", "Target ms", 70), ("over", "Overruns", 66), ("tout", "Timeouts", 66),
                ("err", "Errors", 56), ("busy", "Line busy", 70))
    DEV_COLS = (("bus", "Bus", 150), ("dev", "Device", 160), ("req", "Requests / cycle", 110),
                ("ms", "ms / cycle", 80), ("ravg", "Avg response ms", 112), ("rmax", "Worst response ms", 122),
                ("err", "Errors (timeouts)", 116))

    def __init__(self, win):
        self.win, self.app = win, win.app
        s = self.app.s
        self.frame = f = ttk.Frame(win.nb, padding=(8, 6, 8, 4))
        help_key(f, "devices.cycle")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.l_mode = ttk.Label(top, text="", style="Info.TLabel")
        self.l_mode.pack(side="left")
        help_btn(self.app, top, "devices.cycle", side="right", padx=(6, 0))
        ttk.Button(top, text="Copy report", command=self.copy_report).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="Reset statistics", command=self.reset).pack(side="right")
        self.bus = self._table(f, self.BUS_COLS, 3)
        self.dev = self._table(f, self.DEV_COLS, 5)
        self.cv = tk.Canvas(f, bg=COL["bg"], highlightthickness=1, highlightbackground=COL["line"],
                            height=int(170 * s))
        self.cv.pack(fill="both", expand=True, pady=(6, 0))
        self.cv.bind("<Configure>", lambda e: self.draw())
        self.n_seen = None

    def _table(self, parent, cols, height):
        s = self.app.s
        tf = ttk.Frame(parent)
        tf.pack(fill="x", pady=(6, 0))
        t = ttk.Treeview(tf, columns=[c[0] for c in cols], show="headings", height=height,
                         style=tree_style(self.app.root, s))
        for cid, lab, wd in cols:
            t.heading(cid, text=lab, anchor="w" if cid in ("bus", "dev") else "e")
            t.column(cid, width=int(wd * s), stretch=cid in ("bus", "dev"), minwidth=int(40 * s),
                     anchor="w" if cid in ("bus", "dev") else "e")
        t.tag_configure("bad", foreground=COL["bad"])
        vsb = ttk.Scrollbar(tf, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vsb.set)
        t.pack(side="left", fill="x", expand=True)
        vsb.pack(side="left", fill="y")
        return t

    def reset(self):
        self.app.cycle_stats.reset()
        self.refresh(force=True)
        self.win._status("Cycle statistics reset", "info")

    def copy_report(self):
        text = self.app.cycle_stats.report(self.win.calc_tab.estimates_by_bus())
        try:
            self.win.w.clipboard_clear()
            self.win.w.clipboard_append(text)
        except tk.TclError:
            pass
        self.last_report = text
        self.win._status("Cycle report copied to the clipboard", "ok")
        return text

    def refresh(self, force=False):
        app = self.app
        cs = app.cycle_stats
        n = sum(b.n + b.link_errors for b in cs.buses.values())
        mode = app.devpoll.mode
        tgt = app.devpoll.target_s
        if mode == "cycle":
            self.l_mode.configure(text="Scan mode: PLC cycle" + (f"   target {tgt * 1000:g} ms" if tgt else
                                                                 "   (no target: next cycle starts at once)")
                                       + ("   - running" if app.devices_running() else "   - Start all to measure"))
        else:
            self.l_mode.configure(text="Scan mode: Independent - switch the Overview's Scan mode to 'PLC cycle' "
                                       "and press Start all to measure cycles")
        if not force and n == self.n_seen:
            return
        self.n_seen = n
        bus_rows, dev_rows = [], []
        for key, b in cs.buses.items():
            sm = b.summary()
            busy = "n/a" if sm["busy_pct"] is None else f"{sm['busy_pct']:.0f} %"
            bus_rows.append(((sm["label"], sm["cycles"], mon.fmt_ms(sm["last"]), mon.fmt_ms(sm["avg"]),
                              mon.fmt_ms(sm["min"]), mon.fmt_ms(sm["max"]), mon.fmt_ms(sm["jitter"]),
                              mon.fmt_ms(sm["cps"], 2), mon.fmt_ms(sm["target_ms"], 0), sm["overruns"],
                              sm["timeouts"], sm["errors"] + sm["link_errors"], busy),
                             sm["overruns"] or sm["timeouts"] or sm["errors"] or sm["link_errors"]))
            for ds in b.devices.values():
                r = ds.row()
                dev_rows.append(((sm["label"], r["name"], r["requests"], mon.fmt_ms(r["ms"]),
                                  mon.fmt_ms(r["resp_avg"]), mon.fmt_ms(r["resp_max"]),
                                  f"{r['errors']} ({r['timeouts']})"), r["errors"]))
        for t, rows in ((self.bus, bus_rows), (self.dev, dev_rows)):
            t.delete(*t.get_children())
            for vals, bad in rows:
                t.insert("", "end", values=vals, tags=("bad",) if bad else ())
        self.draw()

    def draw(self):
        cv, s = self.cv, self.app.s
        cv.delete("all")
        W, H = max(cv.winfo_width(), 50), max(cv.winfo_height(), 50)
        left, right, top, bot = int(52 * s), int(12 * s), int(22 * s), int(22 * s)
        buses = [b for b in self.app.cycle_stats.buses.values() if b.history]
        font = ("Segoe UI", 8)
        if not buses:
            cv.create_text(W / 2, H / 2, text="Cycle time chart: no cycles measured yet", fill=COL["stale"])
            return
        hi = max(max(b.history) for b in buses)
        tgts = [b.target_ms for b in buses if b.target_ms]
        if tgts:
            hi = max(hi, max(tgts))
        lo, hi = 0.0, hi * 1.15 if hi > 0 else 1.0
        ticks = mon.nice_ticks(lo, hi, 4)
        if ticks:
            hi = max(hi, ticks[-1])
        n = max(len(b.history) for b in buses)
        cap = max(n, 2)

        def X(i):
            return left + (W - left - right) * i / (cap - 1)

        def Y(v):
            return H - bot - (H - top - bot) * (v - lo) / (hi - lo)
        for tv in ticks:
            y = Y(tv)
            cv.create_line(left, y, W - right, y, fill="#e6e9ee")
            cv.create_text(left - 4, y, text=mon.fmt_num(tv), anchor="e", font=font, fill=COL["addrfg"])
        cv.create_line(left, top, left, H - bot, fill=COL["line"])
        cv.create_line(left, H - bot, W - right, H - bot, fill=COL["line"])
        cv.create_text(left, H - bot + 3, text=f"last {n} cycles (newest right)", anchor="nw", font=font,
                       fill=COL["addrfg"])
        cv.create_text(left - 4, top - 4, text="ms", anchor="se", font=font, fill=COL["addrfg"])
        x_leg = left + 6
        for i, b in enumerate(buses):
            col = mon.PALETTE[i % len(mon.PALETTE)]
            hist = list(b.history)
            off = cap - len(hist)
            pts = []
            for k, v in enumerate(hist):
                pts += [X(off + k), Y(v)]
            if len(pts) >= 4:
                cv.create_line(*pts, fill=col, width=max(1, int(1.5 * s)))
            elif pts:
                cv.create_oval(pts[0] - 2, pts[1] - 2, pts[0] + 2, pts[1] + 2, fill=col, outline=col)
            if b.target_ms:
                y = Y(b.target_ms)
                cv.create_line(left, y, W - right, y, fill=col, dash=(6, 4))
                cv.create_text(W - right - 2, y - 2, text=f"target {b.target_ms:g} ms", anchor="se", font=font,
                               fill=col)
            leg = f"■ {b.label}  avg {mon.fmt_ms(b.avg)} ms"
            tid = cv.create_text(x_leg, top - 4, text=leg, anchor="sw", font=font, fill=col)
            bb = cv.bbox(tid)
            x_leg = (bb[2] if bb else x_leg + 150) + int(14 * s)


class HealthTab:
    """Devices > Health test: bushealth.HealthTest on one bus - real response times, errors by kind, a gap sweep,
    recommended timeout / gap (Apply), and measured device delays for the Calculator. Polling of the devices on
    that bus is paused while the test runs and resumed afterwards."""
    COLS = (("dev", "Device", 130), ("unit", "ID", 36), ("grade", "Grade", 78), ("ok", "OK", 40),
            ("err", "Errors", 50), ("kinds", "Errors by type", 150), ("avg", "Avg ms", 60), ("p95", "p95 ms", 60),
            ("max", "Max ms", 60), ("turn", "Device delay ms", 100), ("to", "Timeout needed", 96),
            ("why", "Notes", 280))

    def __init__(self, win):
        self.win, self.app = win, win.app
        s = self.app.s
        self.frame = f = ttk.Frame(win.nb, padding=(8, 6, 8, 4))
        help_key(f, "devices.health")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Bus:").pack(side="left", padx=(0, 4))
        self.bus = tk.StringVar()
        self.cb_bus = ttk.Combobox(top, textvariable=self.bus, state="readonly", width=28)
        self.cb_bus.pack(side="left")
        ttk.Label(top, text="Requests per device:").pack(side="left", padx=(12, 4))
        self.samples = tk.StringVar(value="20")
        ttk.Entry(top, textvariable=self.samples, width=5).pack(side="left")
        ttk.Label(top, text="Probe timeout (ms):").pack(side="left", padx=(12, 4))
        self.probe = tk.StringVar(value="2000")
        e = ttk.Entry(top, textvariable=self.probe, width=6)
        e.pack(side="left")
        Tooltip(e, "Long timeout used while measuring, so slow answers are timed instead of cut off")
        self.sweep = tk.BooleanVar(value=True)
        cb = ttk.Checkbutton(top, text="Gap sweep", variable=self.sweep)
        cb.pack(side="left", padx=(12, 0))
        Tooltip(cb, "Serial lines: read all devices with shrinking gaps (50 .. 0 ms) to find how tightly\n"
                    "the line can be polled without errors")
        self.b_run = ttk.Button(top, text="Run test", command=self.run)
        self.b_run.pack(side="left", padx=(12, 0))
        self.b_stop = ttk.Button(top, text="Stop", command=self.stop, state="disabled")
        self.b_stop.pack(side="left", padx=(4, 0))
        help_btn(self.app, top, "devices.health", side="right", padx=(6, 0))
        pf = ttk.Frame(f)
        pf.pack(fill="x", pady=(6, 0))
        self.pb = ttk.Progressbar(pf, maximum=1.0, length=int(220 * s))
        self.pb.pack(side="left")
        self.l_prog = ttk.Label(pf, text="Polling of the tested bus is paused during the test and resumed after it.",
                                style="Info.TLabel")
        self.l_prog.pack(side="left", padx=(8, 0))
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True, pady=(6, 0))
        t = self.tree = ttk.Treeview(tf, columns=[c[0] for c in self.COLS], show="headings", height=6,
                                     style=tree_style(self.app.root, s))
        for cid, lab, wd in self.COLS:
            left = cid in ("dev", "grade", "kinds", "why")
            t.heading(cid, text=lab, anchor="w" if left else "e")
            t.column(cid, width=int(wd * s), stretch=cid == "why", minwidth=int(30 * s), anchor="w" if left else "e")
        t.tag_configure("good", foreground="#1e7b34")
        t.tag_configure("warn", foreground="#a15c00", background="#fff4dc")
        t.tag_configure("bad", foreground=COL["bad"], background="#fdecea")
        vsb = ttk.Scrollbar(tf, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vsb.set)
        t.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.l_rec = ttk.Label(f, text="", style="Conn.TLabel")
        self.l_rec.pack(fill="x", pady=(6, 0))
        self.txt = tk.Text(f, height=7, wrap="word", relief="flat", bg=COL["bg"], font=("Segoe UI", 9))
        self.txt.pack(fill="x", pady=(4, 0))
        self.txt.configure(state="disabled")
        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=(6, 0))
        self.b_apply = ttk.Button(bf, text="Apply recommended timeout + gap", command=self.apply, state="disabled")
        self.b_apply.pack(side="left")
        Tooltip(self.b_apply, "Set the timeout of every device on this bus and the bus gap to the recommended values")
        self.b_calc = ttk.Button(bf, text="Use measured delays in Calculator", command=self.use_delays,
                                 state="disabled")
        self.b_calc.pack(side="left", padx=(4, 0))
        Tooltip(self.b_calc, "Write each device's measured response delay (and 'no answer' = offline) into the\n"
                             "Calculator tab, so its estimate matches this line")
        self.b_copy = ttk.Button(bf, text="Copy report", command=self.copy_report, state="disabled")
        self.b_copy.pack(side="left", padx=(4, 0))
        self.report = None
        self.thread = None
        self.halt = False
        self.q = queue.Queue()
        self.paused = []
        self.bus_key = None
        self.job = None

    # ----- data -----
    def buses(self):
        return self.win.calc_tab.buses()

    def reload(self):
        labels = [mon.bus_label(k) for k in self.buses()]
        self.cb_bus.configure(values=labels)
        if self.bus.get() not in labels:
            self.bus.set(labels[0] if labels else "")

    def targets(self, devs):
        """Devices -> health test targets (the first read block of a preset device). -> (targets, skipped)"""
        out, skipped = [], []
        for d in devs:
            try:
                if d["preset"]:
                    pr, _data = self.app.device_preset(d)
                    blocks = [b for b in pr.blocks() if b["fc"] in (1, 2, 3, 4)]
                    if not blocks:
                        skipped.append(d["name"])
                        continue
                    b = blocks[0]
                    fc, addr, qty = b["fc"], b["start"], b["end"] - b["start"]
                else:
                    fc, addr, qty = int(d["fc"]), int(d["addr"]), int(d["qty"])
                if fc not in (1, 2, 3, 4) or qty < 1:
                    skipped.append(d["name"])
                    continue
                out.append({"name": d["name"], "unit": int(d["unit"]), "fc": fc, "addr": addr,
                            "qty": min(qty, 125 if fc in (3, 4) else 2000)})
            except (OSError, ValueError, PresetError, KeyError):
                skipped.append(d["name"])
        return out, skipped

    def _bus_devices(self):
        for k, devs in self.buses().items():
            if mon.bus_label(k) == self.bus.get():
                return k, devs
        return None, []

    # ----- run -----
    def run(self):
        import bushealth
        if self.thread is not None:
            return None
        key, devs = self._bus_devices()
        if not devs:
            self.win._status("No devices on this bus - add devices first", "warn")
            return None
        targets, skipped = self.targets(devs)
        if not targets:
            self.win._status("None of these devices has a readable block to test", "bad")
            return None
        try:
            samples = int_in(self.samples.get(), 1, 1000)
            probe = int_in(self.probe.get(), 100, 30000)
            conn = devs[0]["conn"]
            factory, _summary = master_factory(conn)
            cur_to = int_in(conn.get("timeout", "1000"), 50, 60000)
        except ValueError as e:
            self.win._status(f"Health test: {e}", "bad")
            return None
        kind = mon._kind(conn)
        self.paused = [d["name"] for d in devs if self.app.devpoll.running(d["name"])]
        for n in self.paused:
            self.app.stop_device(n)
        self.bus_key = key
        self.halt = False
        gaps = bushealth.DEFAULT_GAPS_MS if self.sweep.get() else ()
        base_gap = max(self.app._gap_s() * 1000.0, 20.0) if kind != "tcp" else 0.0
        ht = bushealth.HealthTest(factory, targets, kind=kind, baud=mon._baud(conn), bits=mon.bits_per_char(conn),
                                  samples=samples, probe_timeout_s=probe / 1000.0, base_gap_ms=base_gap, gaps_ms=gaps,
                                  current_timeout_ms=cur_to,
                                  progress=lambda text, frac: self.q.put(("prog", text, frac)),
                                  stop=lambda: self.halt)

        def work():
            try:
                self.q.put(("done", ht.run()))
            except Exception as e:  # noqa: BLE001 - shown in the tab
                self.q.put(("error", str(e) or repr(e)))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        self.b_run.configure(state="disabled")
        self.b_stop.configure(state="normal")
        self.pb.configure(value=0)
        self.l_prog.configure(text="Starting..." + (f"  (skipped: {', '.join(skipped)})" if skipped else ""))
        if self.paused:
            self.win._status(f"Health test running - paused {len(self.paused)} device(s) on this bus", "info")
        self.job = self.frame.after(100, self._pump)
        return self.thread

    def stop(self):
        self.halt = True

    def _pump(self):
        self.job = None
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "prog":
                    self.pb.configure(value=msg[2])
                    self.l_prog.configure(text=msg[1])
                elif msg[0] == "done":
                    self._finished(msg[1], None)
                    return
                else:
                    self._finished(None, msg[1])
                    return
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self.job = self.frame.after(100, self._pump)
        except tk.TclError:
            pass

    def _resume(self):
        errs = []
        for n in self.paused:
            if self.app.dev_by_name(n) is not None:
                e = self.app.start_device(n)
                if e:
                    errs.append(f"{n}: {e}")
        self.paused = []
        return errs

    def _finished(self, rep, err):
        self.thread = None
        self.b_run.configure(state="normal")
        self.b_stop.configure(state="disabled")
        errs = self._resume()
        self.win.refresh_states()
        if err:
            self.l_prog.configure(text=f"Test failed: {err}")
            self.win._status(f"Health test failed: {err}", "bad")
            return
        self.report = rep
        self.app.last_health = rep  # for the commissioning report
        self.pb.configure(value=1.0)
        self.l_prog.configure(text=("Stopped early" if rep["aborted"] else "Done") + f" in {rep['seconds']:.1f} s")
        self.show(rep)
        msg = "Health test finished"
        if errs:
            msg += " - not restarted: " + "; ".join(errs)
        self.win._status(msg, "bad" if errs else "ok")

    def show(self, rep):
        t = self.tree
        t.delete(*t.get_children())
        f = mon.fmt_ms
        for d in rep["devices"]:
            r = d["rtt"]
            tag = {"good": "good", "warn": "warn"}.get(d["grade"], "bad")
            t.insert("", "end", values=(d["name"], d["unit"], d["grade"], d["ok"], d["errors"],
                                        mon.kinds_text(d["kinds"], short=False), f(r["avg"]), f(r["p95"]),
                                        f(r["max"]), f(d["turnaround_ms"]),
                                        "-" if d["timeout_ms"] is None else d["timeout_ms"], d["why"]), tags=(tag,))
        g = rep["recommended_gap_ms"]
        parts = [f"Recommended timeout: {rep['recommended_timeout_ms'] or '-'} ms"]
        if rep["kind"] != "tcp":
            parts.append("gap: " + ("- (no clean gap found)" if g is None else f"{g:g} ms"))
        if rep["expected_cycle_ms"]:
            parts.append(f"expected PLC cycle: {rep['expected_cycle_ms']:.0f} ms")
        if rep["sweep"]:
            parts.append("sweep " + "  ".join(f"{x['gap_ms']:g}ms:{x['errors']}" for x in rep["sweep"]))
        self.l_rec.configure(text="     ".join(parts))
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("end", "\n".join("• " + a for a in rep["advice"]))
        self.txt.configure(state="disabled")
        ok = not rep["aborted"]
        self.b_apply.configure(state="normal" if ok and rep["recommended_timeout_ms"] else "disabled")
        self.b_calc.configure(state="normal" if rep["devices"] else "disabled")
        self.b_copy.configure(state="normal")

    def apply(self):
        rep = self.report
        if not rep or not rep["recommended_timeout_ms"]:
            return None
        app = self.app
        names = [d["name"] for d in rep["devices"]]
        to = str(int(rep["recommended_timeout_ms"]))
        running = [n for n in names if app.devpoll.running(n)]
        for n in running:
            app.stop_device(n)
        for n in names:
            dv = app.dev_by_name(n)
            if dv is not None:
                dv["conn"]["timeout"] = to
        g = rep["recommended_gap_ms"]
        if rep["kind"] != "tcp" and g is not None:
            self.win.gap.set(str(int(round(g))))
        app.save_devices()
        save_settings(app.cfg)
        for n in running:
            app.start_device(n)
        self.win.reload()
        text = f"Applied: timeout {to} ms on {len(names)} device(s)"
        if rep["kind"] != "tcp" and g is not None:
            text += f", bus gap {int(round(g))} ms"
        self.win._status(text, "ok")
        return text

    def use_delays(self):
        rep = self.report
        if not rep:
            return
        calc = self.app.cfg["devices"]["calc"]
        calc.setdefault("dev", {})
        for d in rep["devices"]:
            if d["turnaround_ms"] is None:
                calc["dev"][d["name"]] = {"delay_ms": calc["dev"].get(d["name"], {}).get("delay_ms", ""),
                                          "online": False}
            else:
                calc["dev"][d["name"]] = {"delay_ms": f"{d['turnaround_ms']:.1f}", "online": True}
        save_settings(self.app.cfg)
        self.win.calc_tab.reload()
        self.win._status("Measured device delays written to the Calculator tab", "ok")

    def copy_report(self):
        import bushealth
        if not self.report:
            return None
        text = bushealth.format_report(self.report)
        try:
            self.win.w.clipboard_clear()
            self.win.w.clipboard_append(text)
        except tk.TclError:
            pass
        self.win._status("Health report copied to the clipboard", "ok")
        return text

    def destroy(self):
        self.halt = True
        if self.job is not None:
            try:
                self.frame.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None
            self._resume()


class CalcTab:
    """Devices > Calculator: bus timing estimate with buscalc (works without polling) from the configured devices,
    the baud what-if table, advice and a comparison with the Cycle tab's measured averages."""
    INPUTS = (("kind", "Link", ("RTU", "ASCII", "TCP")), ("baud", "Baud", BAUDS), ("parity", "Parity", ("N", "E", "O")),
              ("stop", "Stop bits", ("1", "2")), ("gap_ms", "Master gap (ms)", None),
              ("timeout_ms", "Timeout (ms)", None), ("usb_ms", "USB adapter latency (ms)", None),
              ("delay_ms", "Device response delay (ms)", None), ("retries", "Retries", None),
              ("target_ms", "Cycle target (ms)", None))
    DCOLS = (("dev", "Device", 150), ("unit", "ID", 40), ("bus", "Bus", 140), ("req", "Requests per cycle", 190),
             ("delay", "Response delay (ms)", 120), ("online", "Online", 60))
    ALL = "All configured devices"

    def __init__(self, win):
        self.win, self.app = win, win.app
        s = self.app.s
        self.frame = f = ttk.Frame(win.nb, padding=(8, 6, 8, 4))
        help_key(f, "devices.cycle")
        self.app.cfg["devices"]["calc"] = mon.norm_calc(self.app.cfg["devices"].get("calc"))
        self.v, self.hints = {}, {}
        self.estimate = None
        self.compare_rows = []
        top = ttk.Frame(f)
        top.pack(fill="x")
        lf = ttk.LabelFrame(top, text="Link and timing", padding=(8, 2, 8, 4))
        lf.pack(side="left", fill="y")
        for i, (k, lab, values) in enumerate(self.INPUTS):
            r, c = i % 5, (i // 5) * 3
            self.v[k] = tk.StringVar(value=self.cfg[k])
            if values is not None:
                wdg = ttk.Combobox(lf, textvariable=self.v[k], values=list(values), width=8,
                                   state="readonly" if k != "baud" else "normal")
            else:
                wdg = ttk.Entry(lf, textvariable=self.v[k], width=9)
            ttk.Label(lf, text=lab).grid(row=r, column=c, sticky="e", padx=(0 if c == 0 else int(10 * s), 4), pady=1)
            wdg.grid(row=r, column=c + 1, sticky="w", pady=1)
            self.hints[k] = ttk.Label(lf, text="", style="Hint.TLabel")
            self.hints[k].grid(row=r, column=c + 2, sticky="w")
            self.v[k].trace_add("write", lambda *_a, key=k: self._input(key))
        Tooltip(lf, "The timing model of buscalc: per request t3.5 + request + device delay + response + gap\n"
                    "(+ USB adapter latency). Offline devices cost the timeout x (1 + retries).")
        rf = ttk.Frame(top)
        rf.pack(side="left", fill="both", expand=True, padx=(10, 0))
        bf = ttk.Frame(rf)
        bf.pack(fill="x")
        ttk.Label(bf, text="Devices:").pack(side="left")
        self.scope = tk.StringVar(value=self.ALL)
        self.cb_scope = ttk.Combobox(bf, textvariable=self.scope, state="readonly", width=30)
        self.cb_scope.pack(side="left", padx=(4, 4))
        self.cb_scope.bind("<<ComboboxSelected>>", lambda e: self.recalc())
        Tooltip(self.cb_scope, "Which configured devices to put on the calculated line")
        b_take = ttk.Button(bf, text="Use this bus's settings", command=self.take_bus)
        b_take.pack(side="left")
        Tooltip(b_take, "Copy link, baud, parity, stop bits and timeout of the chosen bus (and the Bus gap)")
        help_btn(self.app, bf, "devices.cycle", side="right")
        ttk.Button(bf, text="Compare with measured", command=self.compare).pack(side="right", padx=(0, 6))
        tf = ttk.Frame(rf)
        tf.pack(fill="both", expand=True, pady=(4, 0))
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in self.DCOLS], show="headings", height=4,
                                 selectmode="browse", style=tree_style(self.app.root, s))
        for cid, lab, wd in self.DCOLS:
            self.tree.heading(cid, text=lab, anchor="w")
            self.tree.column(cid, width=int(wd * s), stretch=cid in ("dev", "req"), minwidth=int(36 * s))
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._load_row())
        self.tree.bind("<Button-1>", self._click)
        ef = ttk.Frame(rf)
        ef.pack(fill="x", pady=(4, 0))
        ttk.Label(ef, text="Selected device - response delay (ms):").pack(side="left")
        self.d_delay = tk.StringVar()
        self.e_delay = ttk.Entry(ef, textvariable=self.d_delay, width=8)
        self.e_delay.pack(side="left", padx=(4, 8))
        Tooltip(self.e_delay, "Time the device needs before it answers (empty = the default above).\n"
                              "Measure it: Cycle tab, 'Avg response ms' minus the frame times.")
        self.d_online = tk.BooleanVar(value=True)
        ttk.Checkbutton(ef, text="Online (answers)", variable=self.d_online).pack(side="left")
        ttk.Button(ef, text="Apply", command=self.apply_row).pack(side="left", padx=(8, 0))
        self.l_dev = ttk.Label(ef, text="", style="Hint.TLabel")
        self.l_dev.pack(side="left", padx=(8, 0))
        of = ttk.Frame(f)
        of.pack(fill="both", expand=True, pady=(6, 0))
        self.out = tk.Text(of, height=12, wrap="none", font=("Consolas", 9), relief="flat", borderwidth=1,
                           highlightthickness=1, highlightbackground=COL["line"], padx=6, pady=4)
        osb = ttk.Scrollbar(of, orient="vertical", command=self.out.yview)
        self.out.configure(yscrollcommand=osb.set)
        self.out.pack(side="left", fill="both", expand=True)
        osb.pack(side="left", fill="y")
        self.out.tag_configure("h", font=("Consolas", 9, "bold"))
        self.out.tag_configure("bad", foreground=COL["bad"])
        self.out.configure(state="disabled")
        self._loading = False

    def destroy(self):
        pass  # no after() jobs; widgets go with the window

    @property
    def cfg(self):
        """The calculator inputs live in app.cfg['devices']['calc'] (saved settings / workspace)."""
        return self.app.cfg["devices"]["calc"]

    # ----- data -----
    def buses(self):
        """{cycle bus key: [devices in order]} of the configured devices (as the PLC cycle groups them)."""
        out = collections.OrderedDict()
        for d in self.app.devices:
            out.setdefault(mon.bus_key(d, "cycle"), []).append(d)
        return out

    def requests(self, dv):
        """[(fc, qty)] one per read request of a device per cycle; ([], error) when its preset is not usable."""
        import buscalc
        if dv["preset"]:
            try:
                pr, _d = self.app.device_preset(dv)
            except (OSError, ValueError, PresetError) as e:
                return [], str(e)
            return buscalc.requests_for_preset(pr), None
        try:
            return [(int(dv["fc"]), int(dv["qty"]))], None
        except ValueError as e:
            return [], str(e)

    def dev_spec(self, dv):
        c = self.cfg["dev"].get(dv["name"], {})
        try:
            default = float(str(self.cfg["delay_ms"]).replace(",", "."))
        except ValueError:
            default = 10.0
        try:
            delay = float(str(c.get("delay_ms", "")).replace(",", ".")) if str(c.get("delay_ms", "")).strip() \
                else default
        except ValueError:
            delay = default
        reqs, _err = self.requests(dv)
        return {"name": dv["name"], "unit": dv["unit"], "requests": reqs, "delay_ms": delay,
                "online": bool(c.get("online", True))}

    def scope_devices(self):
        if self.scope.get() == self.ALL:
            return list(self.app.devices)
        for key, devs in self.buses().items():
            if mon.bus_label(key) == self.scope.get():
                return devs
        return list(self.app.devices)

    def reload(self):
        self._loading = True
        try:
            self.app.cfg["devices"]["calc"] = mon.norm_calc(self.app.cfg["devices"].get("calc"))
            for k, var in self.v.items():
                if var.get() != self.cfg[k]:
                    var.set(self.cfg[k])
        finally:
            self._loading = False
        labels = [self.ALL] + [mon.bus_label(k) for k in self.buses()]
        self.cb_scope.configure(values=labels)
        if self.scope.get() not in labels:
            self.scope.set(self.ALL)
        self.recalc()

    def _input(self, key):
        if self._loading:
            return
        self.cfg[key] = self.v[key].get().strip()
        self.recalc()

    def _click(self, e):
        if self.tree.identify_region(e.x, e.y) != "cell":
            return None
        iid = self.tree.identify_row(e.y)
        if iid and self.tree.identify_column(e.x) == f"#{len(self.DCOLS)}":
            c = self.cfg["dev"].setdefault(iid, {"delay_ms": "", "online": True})
            c["online"] = not c.get("online", True)
            self.tree.selection_set(iid)
            self.recalc()
            return "break"
        return None

    def _load_row(self):
        sel = self.tree.selection()
        if not sel:
            return
        c = self.cfg["dev"].get(sel[0], {})
        self.d_delay.set(c.get("delay_ms", ""))
        self.d_online.set(bool(c.get("online", True)))
        self.l_dev.configure(text="")

    def apply_row(self):
        sel = self.tree.selection()
        if not sel:
            self.l_dev.configure(text="select a device in the list first")
            return False
        text = self.d_delay.get().strip()
        if text:
            try:
                mon._fnum(text, "delay", 0, 60000)
            except ValueError as e:
                self.l_dev.configure(text=str(e))
                return False
        self.cfg["dev"][sel[0]] = {"delay_ms": text, "online": bool(self.d_online.get())}
        self.l_dev.configure(text="")
        self.recalc()
        return True

    def take_bus(self):
        devs = self.scope_devices()
        if self.scope.get() == self.ALL or not devs:
            self.l_dev.configure(text="choose one bus in 'Devices:' first")
            return
        c = devs[0]["conn"]
        self.v["kind"].set(c["mode"])
        if c["mode"] != "TCP":
            self.v["baud"].set(c["baud"])
            self.v["parity"].set(c["parity"])
            self.v["stop"].set(c["stop"] if c["stop"] in ("1", "2") else "1")
        self.v["timeout_ms"].set(c["timeout"])
        self.v["gap_ms"].set(str(int(self.app._gap_s() * 1000)))

    # ----- estimate -----
    def link(self):
        link, errs = mon.calc_link(self.cfg)
        for k, h in self.hints.items():
            h.configure(text=errs.get(k, ""))
        return link

    def recalc(self):
        import buscalc
        devs = self.scope_devices()
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        specs = []
        for d in devs:
            sp = self.dev_spec(d)
            specs.append(sp)
            reqs, err = self.requests(d)
            if not reqs:
                rq = f"(preset not usable: {err})"
            elif len(reqs) <= 2:
                rq = ", ".join(f"FC{fc:02d} x{q}" for fc, q in reqs)
            else:  # a long block list does not fit the column: summarise it
                fcs = "/".join(f"FC{fc:02d}" for fc in sorted({fc for fc, _q in reqs}))
                rq = f"{len(reqs)} requests: {fcs}, {sum(q for _fc, q in reqs)} in total"
            c = self.cfg["dev"].get(d["name"], {})
            delay = c.get("delay_ms", "") or f"{sp['delay_ms']:g} (default)"
            self.tree.insert("", "end", iid=d["name"], values=(d["name"], d["unit"], mon.bus_label(mon.bus_key(d, "cycle")),
                                                                rq, delay, "☑" if sp["online"] else "☐"))
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        try:
            target = mon.parse_target_ms(self.cfg["target_ms"])
            self.hints["target_ms"].configure(text="")
        except ValueError as e:
            target = None
            self.hints["target_ms"].configure(text=str(e))
        link = self.link()
        lines = []
        if link is None:
            self.estimate = None
            lines.append(("Fix the inputs marked in red.", "bad"))
        elif not specs:
            self.estimate = None
            lines.append(("No devices configured: add some on the Overview tab (Add... or Scan & add...).", ""))
        else:
            est = self.estimate = buscalc.estimate_cycle(link, specs)
            busy = f"{est['utilisation_pct']:.0f} %" if link.kind != "tcp" else "n/a"
            lines.append((f"Estimate for {est['link']}  ({len(specs)} device(s), {est['requests']} requests)", "h"))
            lines.append((f"  Cycle {est['total_ms']:.1f} ms   =  {est['cycles_per_s']:.2f} cycles/s   "
                          f"line busy {busy}   bytes per cycle {est['bytes']}", ""))
            lines.append(("", ""))
            lines.append(("Per device:", "h"))
            for d in est["devices"]:
                lines.append((f"  {d['name']:<22} ID {str(d['unit']):<4} {d['ms']:8.1f} ms  in {len(d['requests'])} "
                               f"request(s)   delay {d['delay_ms']:g} ms" + ("" if d["online"] else
                                                                            "   OFFLINE: costs the timeout"),
                               "" if d["online"] else "bad"))
            if link.kind != "tcp":
                lines.append(("", ""))
                lines.append(("Baud what-if (everything else equal):", "h"))
                lines.append(("  " + "".join(f"{b:>9}" for b, _t, _u in buscalc.what_if(link, specs)), ""))
                lines.append(("  " + "".join(f"{t:>6.0f} ms" for _b, t, _u in buscalc.what_if(link, specs)), ""))
                lines.append(("  " + "".join(f"{u:>7.0f} %" for _b, _t, u in buscalc.what_if(link, specs))
                              + "   line busy", ""))
            lines.append(("", ""))
            lines.append(("Advice:", "h"))
            for tip in buscalc.advice(link, est, target):
                lines.append(("  - " + tip, ""))
        if self.compare_rows:
            lines.append(("", ""))
            lines += self._compare_lines()
        self._show(lines)
        return self.estimate

    def _show(self, lines):
        t = self.out
        t.configure(state="normal")
        t.delete("1.0", "end")
        for text, tag in lines:
            t.insert("end", text + "\n", (tag,) if tag else ())
        t.configure(state="disabled")

    def estimates_by_bus(self):
        """{cycle bus key: estimated cycle ms} with each bus's real connection and the app's bus gap, the
        calculator's delays / USB latency / retries (for 'Compare with measured' and the Cycle report)."""
        import buscalc
        out = {}
        gap_ms = self.app._gap_s() * 1000.0
        for key, devs in self.buses().items():
            on = [d for d in devs if d["enabled"]]
            if not on:
                continue
            link, errs = mon.calc_link(self.cfg, conn=on[0]["conn"], gap_ms=gap_ms)
            if link is None:
                continue
            out[key] = buscalc.estimate_cycle(link, [self.dev_spec(d) for d in on])["total_ms"]
        return out

    def compare(self):
        est = self.estimates_by_bus()
        rows = []
        for key, b in self.app.cycle_stats.buses.items():
            rows.append({"bus": b.label, "estimate": est.get(key), "measured": b.avg,
                         "diff": mon.pct_diff(est.get(key), b.avg), "cycles": b.n})
        for key, e in est.items():
            if key not in self.app.cycle_stats.buses:
                rows.append({"bus": mon.bus_label(key), "estimate": e, "measured": None, "diff": None, "cycles": 0})
        self.compare_rows = rows or [{"bus": "-", "estimate": None, "measured": None, "diff": None, "cycles": 0}]
        self.recalc()
        self.out.see("end")
        return rows

    def _compare_lines(self):
        out = [("Estimated vs measured (each bus with its own connection and the Bus gap):", "h")]
        for r in self.compare_rows:
            if r["measured"] is None:
                out.append((f"  {r['bus']:<26} estimate {mon.fmt_ms(r['estimate'])} ms   measured: none yet "
                            "(Scan mode = PLC cycle, Start all)", ""))
                continue
            d = "-" if r["diff"] is None else f"{r['diff']:+.0f} %"
            out.append((f"  {r['bus']:<26} estimate {mon.fmt_ms(r['estimate'])} ms   measured avg "
                        f"{mon.fmt_ms(r['measured'])} ms over {r['cycles']} cycles   difference {d}",
                        "bad" if r["diff"] is not None and abs(r["diff"]) > 30 else ""))
        out.append(("  Much slower than estimated: larger device delay, USB latency or retries than entered; "
                    "much faster: the device answers quicker than the delay entered.", ""))
        return out


class DeviceDialog(Dialog):
    """Add / edit one device: name, connection (the Setup dialog), Slave ID, scan rate, preset or register range."""

    def __init__(self, app, dv, on_saved):
        super().__init__(app, "Device" if dv else "Add device", modal=True)
        self.b_help = add_help_button(app, self.w, "devices")
        s = app.s
        self.old = dv["name"] if dv else None
        d = json.loads(json.dumps(dv)) if dv else mon.norm_device({"name": self._new_name(),
                                                                  "conn": dict(app.cfg["conn"])})
        self.conn = d["conn"]
        # a poll tab's view settings are not edited here but must survive the edit
        self.tab_keys = {k: d[k] for k in ("tab", "tab_link", "tab_multi", "tab_base", "tab_rows") if k in d}
        self.on_saved = on_saved
        b = self.body
        base = app.addr_base()
        self.v = {"name": tk.StringVar(value=d["name"]), "unit": tk.StringVar(value=d["unit"]),
                  "scan": tk.StringVar(value=d["scan"]), "preset": tk.StringVar(value=d["preset"]),
                  "fc": tk.StringVar(value=FUNCS.get(int(d["fc"]) if d["fc"].isdigit() else 3, FUNCS[3])),
                  "addr": tk.StringVar(value=addr_label(int(d["fc"]), int(d["addr"]), base)),
                  "qty": tk.StringVar(value=d["qty"]), "fmt": tk.StringVar(value=d["fmt"])}
        self.enabled = tk.BooleanVar(value=d["enabled"])
        self.use_preset = tk.StringVar(value="preset" if d["preset"] else "range")
        self.hints = {}
        _l, self.hints["name"] = form_row(b, 0, "Name", ttk.Entry(b, textvariable=self.v["name"], width=26),
                                          "Unique name; also used in the log file name")
        cf = ttk.Frame(b)
        self.l_conn = ttk.Label(cf, text=mon.conn_text(self.conn), width=34)
        self.l_conn.pack(side="left")
        ttk.Button(cf, text="Connection...", command=self.setup).pack(side="left", padx=(6, 0))
        _l, self.hints["conn"] = form_row(b, 1, "Connection", cf,
                                          "TCP host / port, or RTU / ASCII on a COM port.\n"
                                          "Devices on the same COM port share one RS-485 bus.")
        _l, self.hints["unit"] = form_row(b, 2, "Slave ID", ttk.Entry(b, textvariable=self.v["unit"], width=8))
        _l, self.hints["scan"] = form_row(b, 3, "Scan rate (ms)", ttk.Entry(b, textvariable=self.v["scan"], width=8))
        ttk.Checkbutton(b, text="Enabled (polled by 'Start all')", variable=self.enabled).grid(
            row=4, column=1, sticky="w", pady=3)
        sf = ttk.LabelFrame(b, text="What to read", padding=(10, 4, 10, 6))
        sf.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Radiobutton(sf, text="Preset file", value="preset", variable=self.use_preset,
                        command=self.validate).grid(row=0, column=0, sticky="w")
        pf = ttk.Frame(sf)
        pf.grid(row=0, column=1, columnspan=3, sticky="w")
        self.e_preset = ttk.Entry(pf, textvariable=self.v["preset"], width=44)
        self.e_preset.pack(side="left")
        self.b_browse = ttk.Button(pf, text="Browse...", command=self.browse)
        self.b_browse.pack(side="left", padx=(4, 0))
        self.hints["preset"] = ttk.Label(sf, text="", style="Hint.TLabel")
        self.hints["preset"].grid(row=1, column=1, columnspan=3, sticky="w")
        ttk.Radiobutton(sf, text="Register range", value="range", variable=self.use_preset,
                        command=self.validate).grid(row=2, column=0, sticky="nw", pady=(6, 0))
        rf = ttk.Frame(sf)
        rf.grid(row=2, column=1, columnspan=3, sticky="w", pady=(6, 0))
        self.range_w = [ttk.Combobox(rf, textvariable=self.v["fc"], values=list(FUNCS.values()), width=28,
                                     state="readonly"),
                        ttk.Entry(rf, textvariable=self.v["addr"], width=8),
                        ttk.Entry(rf, textvariable=self.v["qty"], width=6),
                        ttk.Combobox(rf, textvariable=self.v["fmt"], values=FORMATS, width=13, state="readonly")]
        for c, (k, lab, wd) in enumerate((("fc", "Function", self.range_w[0]),
                                          ("addr", f"Address ({'base 0' if base == 0 else 'base 1' if base == 1 else 'PLC'})",
                                           self.range_w[1]),
                                          ("qty", "Quantity", self.range_w[2]), ("fmt", "Display", self.range_w[3]))):
            self.hints[k] = vfield(rf, c, lab, wd)
        bf = ttk.Frame(b)
        bf.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        self.b_ok = ttk.Button(bf, text="OK", command=self.ok, default="active")
        self.b_ok.pack(side="right", padx=(0, 6))
        self.l_res = ttk.Label(b, text="", style="Hint.TLabel", wraplength=int(520 * s))
        self.l_res.grid(row=7, column=0, columnspan=3, sticky="w")
        b.columnconfigure(2, minsize=int(160 * s))
        self.w.bind("<Return>", lambda e: self.ok())
        for var in self.v.values():
            var.trace_add("write", lambda *_: self.validate())
        self.validate()
        self.place()

    def _new_name(self):
        n = len(self.app.devices) + 1
        names = {d["name"] for d in self.app.devices}
        while f"Device {n}" in names:
            n += 1
        return f"Device {n}"

    def setup(self):
        def done(conn):
            self.conn = mon.norm_conn(conn)
            if self.tab_keys.get("tab"):
                self.tab_keys["tab_link"] = False  # own connection now, no longer "same as Poll 1"
            self.l_conn.configure(text=mon.conn_text(self.conn))
            self.w.grab_set()
            self.validate()
        SetupDialog(self.app, conn=dict(self.conn), on_done=done, title="Device connection", parent=self.w)

    def browse(self):
        p = filedialog.askopenfilename(parent=self.w, title="Preset for this device", initialdir=preset_dir(),
                                       filetypes=[("Preset JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.v["preset"].set(os.path.normpath(p))

    def collect(self):
        """-> (normalised device, {field: error})"""
        v = self.v
        errs = {}
        pre = self.use_preset.get() == "preset"
        fc = int(v["fc"].get()[:2])
        addr = "0"
        if not pre:
            try:
                addr = str(parse_addr(v["addr"].get(), fc, self.app.addr_base()))
            except ValueError as e:
                errs["addr"] = str(e)
        dv = mon.norm_device({"name": v["name"].get(), "conn": self.conn, "unit": v["unit"].get().strip(),
                              "scan": v["scan"].get().strip(), "enabled": self.enabled.get(),
                              "preset": v["preset"].get().strip() if pre else "", "fc": str(fc), "addr": addr,
                              "qty": v["qty"].get().strip(), "fmt": v["fmt"].get(), **self.tab_keys})
        others = [d for d in self.app.devices if d["name"] != self.old]
        for k, e in mon.device_errors(dv, others).items():
            errs.setdefault(k, e)
        if pre:
            p = dv["preset"]
            if not p:
                errs["preset"] = "choose a preset file"
            elif not os.path.isfile(p):
                errs["preset"] = "file not found"
            else:
                try:
                    read_preset_file(p)
                except (OSError, ValueError, PresetError) as e:
                    errs["preset"] = f"not a valid preset: {e}"
        elif fc in (3, 4) and "qty" not in errs:
            try:
                if int(dv["qty"]) < fmt_width(dv["fmt"]):
                    errs["qty"] = f"{dv['fmt']} needs {fmt_width(dv['fmt'])}+"
            except ValueError:
                pass
        return dv, errs

    def validate(self):
        pre = self.use_preset.get() == "preset"
        self.e_preset.configure(state="normal" if pre else "disabled")
        self.b_browse.state(["!disabled"] if pre else ["disabled"])
        for wd in self.range_w:
            wd.configure(state="disabled" if pre else ("readonly" if isinstance(wd, ttk.Combobox) else "normal"))
        dv, errs = self.collect()
        for k, h in self.hints.items():
            h.configure(text=errs.get(k, ""), style="Hint.TLabel")
        if "addr" not in errs and not pre:
            self.hints["addr"].configure(text=f"= protocol {dv['addr']}", style="Info.TLabel")
        self.b_ok.state(["disabled"] if errs else ["!disabled"])
        return dv, errs

    def ok(self):
        dv, errs = self.validate()
        if errs:
            self.l_res.configure(text="Fix the fields marked in red")
            return None
        self.close()
        self.on_saved(self.old, dv)
        return dv


def ident_text(info):
    """Identification dict (scanner.read_device_id) -> 'Vendor Product' / 'FC17: ...' / '-'."""
    if not info:
        return "-"
    vend = str(info.get("VendorName", "")).strip()
    prod = str(info.get("ProductCode") or info.get("ModelName") or info.get("ProductName") or "").strip()
    t = " ".join(x for x in (vend, prod) if x)
    if not t and isinstance(info.get("ReportSlaveId"), dict):
        t = "FC17: " + info["ReportSlaveId"].get("ascii", "").strip(". ")
    return t or "-"


def ident_model(info):
    """Short model word for a default device name ('' when the device did not identify itself)."""
    if not info:
        return ""
    return str(info.get("ProductCode") or info.get("ModelName") or info.get("ProductName")
               or info.get("VendorName") or "").strip()[:24]


class ScanAddDialog(Dialog):
    """Devices > Scan & add: find the Slave IDs that answer on a connection (scanner.scan_unit_ids on a worker
    thread), identify them (FC43 / FC17) and suggest a library preset; the ticked rows become devices."""
    COLS = (("chk", "Add", 46), ("unit", "Slave ID", 66), ("model", "Identified model", 200), ("preset", "Preset", 230),
            ("name", "Name", 170), ("note", "Note", 130))
    NONE = "(none - register range 0..9 holding)"
    ON, OFF, NA = "☑", "☐", "—"

    def __init__(self, win):
        app = win.app
        super().__init__(app, "Scan & add devices")
        self.win, s = win, app.s
        help_key(self.w, "devices.scan")
        self.w.resizable(True, True)
        self.w.minsize(int(720 * s), int(460 * s))
        self.conn = mon.norm_conn(dict(app.cfg["conn"]))
        self.rows = collections.OrderedDict()  # iid -> {unit, info, model, preset, name, tick, exists, added}
        self.stop_event = None
        self.scanning = False
        self.result = None
        self.lib = self._library()
        self._editing = False
        b = self.body
        sf = ttk.LabelFrame(b, text="Where to look", padding=(10, 4, 10, 6))
        sf.pack(fill="x")
        cf = ttk.Frame(sf)
        cf.grid(row=0, column=0, columnspan=8, sticky="w", pady=(0, 4))
        ttk.Label(cf, text="Connection:").pack(side="left")
        self.l_conn = ttk.Label(cf, text=mon.conn_text(self.conn), width=40)
        self.l_conn.pack(side="left", padx=(6, 6))
        self.b_conn = ttk.Button(cf, text="Connection...", command=self.setup)
        self.b_conn.pack(side="left")
        Tooltip(self.b_conn, "Default: the main window's connection. The devices you add get this connection.")
        self.v = {"from": tk.StringVar(value="1"), "to": tk.StringVar(value="32"),
                  "timeout": tk.StringVar(value="300"), "fc": tk.StringVar(value=FUNCS[3]),
                  "addr": tk.StringVar(value="0")}
        self.hints = {}
        spec = (("from", "Slave ID from", ttk.Entry(sf, textvariable=self.v["from"], width=6)),
                ("to", "to", ttk.Entry(sf, textvariable=self.v["to"], width=6)),
                ("timeout", "Timeout (ms)", ttk.Entry(sf, textvariable=self.v["timeout"], width=7)),
                ("fc", "Probe with", ttk.Combobox(sf, textvariable=self.v["fc"], values=list(FUNCS.values()),
                                                   width=28, state="readonly")),
                ("addr", "Address", ttk.Entry(sf, textvariable=self.v["addr"], width=7)))
        for c, (k, lab, wd) in enumerate(spec):
            self.hints[k] = vfield(sf, c, lab, wd, row=1)
        Tooltip(spec[2][2], "Per Slave ID that does not answer the scan waits this long.\n"
                            "32 IDs x 300 ms = about 10 s worst case.")
        Tooltip(spec[3][2], "One read of 1 register / bit at the address. Any valid reply - also an exception "
                            "reply - means a device answers.")
        self.auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(sf, text="Tick everything found automatically", variable=self.auto).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(2, 0))
        bar = ttk.Frame(b)
        bar.pack(fill="x", pady=(8, 4))
        self.b_scan = ttk.Button(bar, text="Scan", width=10, command=self.scan, default="active")
        self.b_scan.pack(side="left")
        self.b_cancel = ttk.Button(bar, text="Cancel scan", command=self.cancel)
        self.b_cancel.pack(side="left", padx=(4, 0))
        self.b_cancel.state(["disabled"])
        self.pb = ttk.Progressbar(bar, mode="determinate", length=int(200 * s))
        self.pb.pack(side="left", padx=(10, 0))
        self.l_status = ttk.Label(bar, text="Choose the connection and the ID range, then Scan", style="Info.TLabel")
        self.l_status.pack(side="left", padx=(10, 0))
        help_btn(app, bar, "devices.scan", side="right")
        tf = ttk.Frame(b)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in self.COLS], show="headings", selectmode="extended",
                                 height=9, style=tree_style(app.root, s))
        for cid, lab, wd in self.COLS:
            self.tree.heading(cid, text=lab, anchor="center" if cid == "chk" else "w")
            self.tree.column(cid, width=int(wd * s), stretch=cid in ("model", "preset", "name"), minwidth=int(36 * s),
                             anchor="center" if cid in ("chk", "unit") else "w")
        self.tree.tag_configure("exists", foreground=COL["stale"])
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", lambda e: (self.toggle(self.tree.selection()), "break")[1])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._load_edit())
        self.tree.bind("<Double-1>", self._dbl)
        ef = ttk.Frame(b)
        ef.pack(fill="x", pady=(6, 0))
        ttk.Button(ef, text="Select all", command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(ef, text="Select none", command=lambda: self.set_all(False)).pack(side="left", padx=(4, 12))
        ttk.Label(ef, text="Preset:").pack(side="left")
        self.e_preset_var = tk.StringVar(value=self.NONE)
        self.cb_preset = ttk.Combobox(ef, textvariable=self.e_preset_var, values=[self.NONE] + [lab for lab, _p in self.lib],
                                      state="readonly", width=34)
        self.cb_preset.pack(side="left", padx=(4, 4))
        self.cb_preset.bind("<<ComboboxSelected>>", lambda e: self._preset_picked())
        Tooltip(self.cb_preset, "Preset for the selected row(s). Library presets are UNVERIFIED unless marked:\n"
                                "compare the values with the device display after adding.")
        ttk.Button(ef, text="Browse...", command=self.browse).pack(side="left")
        ttk.Label(ef, text="Name:").pack(side="left", padx=(12, 4))
        self.e_name_var = tk.StringVar()
        self.e_name = ttk.Entry(ef, textvariable=self.e_name_var, width=22)
        self.e_name.pack(side="left")
        self.e_name_var.trace_add("write", lambda *_: self._name_typed())
        self.l_edit = ttk.Label(ef, text="", style="Hint.TLabel")
        self.l_edit.pack(side="left", padx=(6, 0))
        af = ttk.Frame(b)
        af.pack(fill="x", pady=(10, 0))
        ttk.Button(af, text="Close", command=self.close).pack(side="right")
        self.b_add = ttk.Button(af, text="Add selected", command=self.add_selected)
        self.b_add.pack(side="right", padx=(0, 6))
        Tooltip(self.b_add, "Create a device (and its tab) for every ticked row. Nothing is started.")
        self.l_count = ttk.Label(af, text="", style="Info.TLabel")
        self.l_count.pack(side="left")
        self.w.bind("<Return>", lambda e: None if self.scanning else self.scan())
        self._update_buttons()
        self.place()

    # ----- helpers -----
    def _library(self):
        out = []
        root = preset_dir()
        for dirpath, _d, files in os.walk(root):
            for fn in sorted(files):
                if fn.lower().endswith(".json"):
                    p = os.path.join(dirpath, fn)
                    out.append((os.path.relpath(p, root).replace("\\", "/"), os.path.normpath(p)))
        out.sort()
        return out

    def _preset_label(self, path):
        if not path:
            return self.NONE
        np = os.path.normcase(os.path.normpath(path))
        for lab, p in self.lib:
            if os.path.normcase(p) == np:
                return lab
        return path

    def _exists(self, unit):
        return any(d["unit"].strip() == str(unit) and same_target(d["conn"], self.conn) for d in self.app.devices)

    def _unique(self, name, skip=None):
        taken = {d["name"].lower() for d in self.app.devices}
        taken |= {r["name"].lower() for iid, r in self.rows.items() if iid != skip and not r["added"]}
        base, n = name.strip() or "Device", 2
        cand = base
        while cand.lower() in taken:
            cand = f"{base} ({n})"
            n += 1
        return cand

    def _status(self, text, kind="info"):
        self.l_status.configure(text=text, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel",
                                                  "warn": "SWarn.TLabel"}.get(kind, "SInfo.TLabel"))

    def setup(self):
        def done(conn):
            self.conn = mon.norm_conn(conn)
            self.l_conn.configure(text=mon.conn_text(self.conn))
            for iid, r in self.rows.items():
                r["exists"] = r["added"] or self._exists(r["unit"])
                if r["exists"]:
                    r["tick"] = False
                self._draw_row(iid)
            self._update_buttons()
        SetupDialog(self.app, conn=dict(self.conn), on_done=done, title="Scan connection", parent=self.w)

    def close(self):
        if self.stop_event is not None:
            self.stop_event.set()
        super().close()

    # ----- scan (worker thread) -----
    def _params(self):
        errs, out = {}, {}
        tcp = self.conn["mode"] == "TCP"
        hi = 255 if tcp else 247
        for k, lo, top in (("from", 0 if tcp else 1, hi), ("to", 0 if tcp else 1, hi), ("timeout", 50, 60000),
                           ("addr", 0, 65535)):
            try:
                out[k] = int_in(self.v[k].get(), lo, top)
            except ValueError as e:
                errs[k] = str(e)
        if "from" in out and "to" in out and out["from"] > out["to"]:
            errs["to"] = "must be >= from"
        out["fc"] = int(self.v["fc"].get()[:2])
        for k, h in self.hints.items():
            h.configure(text=errs.get(k, ""))
        return out, errs

    def _post(self, fn, payload=None, err=None):
        def cb(p, e):
            if self.alive():
                fn(p, e)
        self.app.q.put(("call", None, cb, payload, err))

    def scan(self):
        if self.scanning:
            return False
        prm, errs = self._params()
        if errs:
            self._status("Fix the fields marked in red", "bad")
            return False
        conn = dict(self.conn, timeout=str(prm["timeout"]))
        try:
            factory, summary = master_factory(conn)
        except ValueError as e:
            self._status(f"Connection settings: {e}", "bad")
            return False
        units = list(range(prm["from"], prm["to"] + 1))
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.result = None
        self.scanning = True
        ev = self.stop_event = threading.Event()
        self.pb.configure(maximum=len(units), value=0)
        self._status(f"Scanning {summary}, Slave ID {units[0]}-{units[-1]} ...", "info")
        self._update_buttons()
        fc, addr = prm["fc"], prm["addr"]

        def work():
            import scanner
            try:
                m = factory()
            except Exception as e:  # port busy / host down: shown in the status line
                self._post(self._done, None, f"cannot open {summary}: {e}")
                return
            try:
                res = scanner.scan_unit_ids(m, units, fc, addr, stop_event=ev,
                                            progress=lambda n, t, txt: self._post(self._progress, (n, t, txt)))
                found = []
                for u in res["found"]:
                    if ev.is_set():
                        break
                    self._post(self._progress, (None, None, f"identifying Slave ID {u} ..."))
                    try:
                        info = scanner.read_device_id(m, u, max_requests=4)
                    except Exception:  # noqa: BLE001 - identification is optional
                        info = {}
                    try:
                        sugg = scanner.suggest_presets(info, folder=preset_dir()) if info else []
                    except Exception:  # noqa: BLE001
                        sugg = []
                    found.append((u, info, sugg))
                self._post(self._done, {"found": found, "noise": res["noise"], "scanned": res["scanned"],
                                        "aborted": ev.is_set(), "range": (units[0], units[-1])})
            except Exception as e:  # noqa: BLE001
                self._post(self._done, None, str(e) or repr(e))
            finally:
                try:
                    m.close()
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=work, daemon=True).start()
        return True

    def cancel(self):
        if self.stop_event is not None:
            self.stop_event.set()
        self._status("Cancelling ...", "warn")

    def _progress(self, p, _err):
        n, total, text = p
        if n is not None:
            self.pb.configure(value=n)
            self._status(f"Scanning ... {n}/{total}  ({text})", "info")
        else:
            self._status(text, "info")

    def _done(self, res, err):
        self.scanning = False
        self.result = res if err is None else {"error": err}
        self._update_buttons()
        if err:
            self.pb.configure(value=0)
            self._status(f"Scan failed: {err}", "bad")
            return
        for u, info, sugg in res["found"]:
            path = ""
            if sugg:
                cand = os.path.normpath(os.path.join(preset_dir(), sugg[0]["preset"]))
                path = cand if os.path.isfile(cand) else ""
            exists = self._exists(u)
            model = ident_model(info)
            iid = str(u)
            self.rows[iid] = {"unit": u, "info": info, "model": ident_text(info), "preset": path, "name": "",
                              "tick": bool(self.auto.get()) and not exists, "exists": exists, "added": False}
            self.rows[iid]["name"] = self._unique(f"{model or 'Device'} {u}", skip=iid)
            self.tree.insert("", "end", iid=iid)
            self._draw_row(iid)
        lo, hi = res["range"]
        n = len(res["found"])
        old = sum(1 for r in self.rows.values() if r["exists"])
        if res["aborted"]:
            self._status(f"Cancelled - {n} device(s) found so far", "warn")
        elif n:
            self._status(f"Found {n} device(s) in Slave ID {lo}-{hi}" + (f", {old} already added" if old else ""),
                         "ok")
        else:
            extra = f"; garbled replies at ID {', '.join(map(str, res['noise'][:5]))} - check baud / parity" \
                if res["noise"] else " - check the connection, baud / parity, wiring and the ID range"
            self._status(f"No device answered in Slave ID {lo}-{hi}{extra}  (no hardware? use Devices > Demo devices)", "bad")
        self.pb.configure(value=self.pb.cget("maximum"))
        self._update_buttons()

    # ----- table -----
    def _draw_row(self, iid):
        r = self.rows[iid]
        chk = self.NA if r["exists"] else (self.ON if r["tick"] else self.OFF)
        note = "added" if r["added"] else ("already added" if r["exists"] else "")
        self.tree.item(iid, values=(chk, r["unit"], r["model"], self._preset_label(r["preset"]), r["name"], note),
                       tags=("exists",) if r["exists"] else ())
        self._update_buttons()

    def toggle(self, iids, value=None):
        for iid in iids:
            r = self.rows.get(iid)
            if r is None or r["exists"]:
                continue
            r["tick"] = (not r["tick"]) if value is None else value
            self._draw_row(iid)

    def set_all(self, value):
        self.toggle(list(self.rows), value)

    def ticked(self):
        return [iid for iid, r in self.rows.items() if r["tick"] and not r["exists"]]

    def _click(self, e):
        if self.tree.identify_region(e.x, e.y) != "cell":
            return None
        iid = self.tree.identify_row(e.y)
        if iid and self.tree.identify_column(e.x) == "#1":
            self.toggle([iid])
            self.tree.selection_set(iid)
            return "break"
        return None

    def _dbl(self, e):
        col = self.tree.identify_column(e.x)
        if col == "#4":
            self.cb_preset.focus_set()
        elif col == "#5":
            self.e_name.focus_set()
            self.e_name.select_range(0, "end")

    def _load_edit(self):
        sel = [i for i in self.tree.selection() if i in self.rows]
        self._editing = True
        try:
            if sel:
                r = self.rows[sel[0]]
                self.e_preset_var.set(self._preset_label(r["preset"]))
                self.e_name_var.set(r["name"])
            self.l_edit.configure(text="")
        finally:
            self._editing = False

    def set_preset(self, iids, path):
        for iid in iids:
            if iid in self.rows and not self.rows[iid]["added"]:
                self.rows[iid]["preset"] = path or ""
                self._draw_row(iid)

    def _preset_picked(self):
        lab = self.e_preset_var.get()
        path = "" if lab == self.NONE else dict(self.lib).get(lab, lab)
        self.set_preset([i for i in self.tree.selection() if i in self.rows], path)

    def browse(self):
        sel = [i for i in self.tree.selection() if i in self.rows]
        if not sel:
            self.l_edit.configure(text="select a row first")
            return
        p = filedialog.askopenfilename(parent=self.w, title="Preset for the selected device(s)",
                                       initialdir=preset_dir(),
                                       filetypes=[("Preset JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.set_preset(sel, os.path.normpath(p))
            self.e_preset_var.set(self._preset_label(os.path.normpath(p)))

    def set_name(self, iid, name):
        r = self.rows.get(iid)
        if r is None or r["added"]:
            return False
        name = name.strip()
        if not name:
            self.l_edit.configure(text="name required")
            return False
        if name.lower() in {d["name"].lower() for d in self.app.devices} | {
                x["name"].lower() for k, x in self.rows.items() if k != iid and not x["added"]}:
            self.l_edit.configure(text="name already used")
            return False
        self.l_edit.configure(text="")
        r["name"] = name
        self._draw_row(iid)
        return True

    def _name_typed(self):
        if self._editing:
            return
        sel = [i for i in self.tree.selection() if i in self.rows]
        if len(sel) == 1:
            self.set_name(sel[0], self.e_name_var.get())

    def _update_buttons(self):
        try:
            self.b_scan.state(["disabled"] if self.scanning else ["!disabled"])
            self.b_cancel.state(["!disabled"] if self.scanning else ["disabled"])
            n = len(self.ticked())
            self.b_add.state(["!disabled"] if n and not self.scanning else ["disabled"])
            self.l_count.configure(text=f"{len(self.rows)} found, {n} ticked" if self.rows else "")
        except tk.TclError:
            pass

    # ----- add -----
    def add_selected(self):
        if self.scanning:
            return []
        app = self.app
        added, problems = [], []
        for iid in self.ticked():
            r = self.rows[iid]
            if self._exists(r["unit"]):  # added meanwhile (e.g. a second scan)
                r["exists"], r["tick"] = True, False
                self._draw_row(iid)
                continue
            name = r["name"].strip()
            if name.lower() in {d["name"].lower() for d in app.devices}:
                name = self._unique(name, skip=iid)
            dv = mon.norm_device({"name": name, "conn": dict(self.conn), "unit": str(r["unit"]), "scan": "1000",
                                  "enabled": True, "preset": r["preset"] or "", "fc": "3", "addr": "0",
                                  "qty": "10", "fmt": "Unsigned"})
            errs = mon.device_errors(dv, app.devices)
            if errs:
                problems.append(f"ID {r['unit']}: " + "; ".join(f"{k} {v}" for k, v in errs.items()))
                continue
            app.devices.append(dv)
            r.update(name=name, added=True, exists=True, tick=False)
            self._draw_row(iid)
            added.append(name)
        if added:
            self.win.devices_added(added)
        if problems:
            self._status(f"{len(added)} added, {len(problems)} not added - {problems[0]}", "bad")
        elif added:
            self._status(f"{len(added)} device(s) added - each has its own tab in the Devices window", "ok")
        else:
            self._status("Nothing ticked to add", "warn")
        self._update_buttons()
        return added


# ---------------- AI Assist ----------------

# ---------------- bus map (Tools > Bus map, Ctrl+M) ----------------

BM_STATE = {"ok": ("#dcf3e1", "#1e9e3e", "polling: ok"), "error": ("#fde3e1", "#d93025", "polling: error"),
            "stale": ("#fff1d6", "#e69500", "polling: stale"), "off": ("#eceef1", "#9aa0a6", "stopped"),
            None: ("#ffffff", "#7a8594", "not in Devices")}
BM_LEVEL = {"error": ("● ERROR ", "#d93025"), "warn": ("▲ WARN ", "#b06a00"), "info": ("● info ", "#7a8190")}
BM_PHASE = {"idle": ("t3.5", "#c3c9d2"), "request": ("request", "#1a64d6"), "device": ("device delay", "#e69500"),
            "response": ("response", "#1e9e3e"), "gap": ("gap/usb", "#dfe3e8")}
BM_ALL = "All in turn"
BM_FCS = ("1", "2", "3", "4", "5", "6", "15", "16", "23")


def bm_fit(text, font, width):
    """Text shortened with an ellipsis so it fits width pixels."""
    text = str(text)
    if width <= 0 or font.measure(text) <= width:
        return text
    while text and font.measure(text + "…") > width:
        text = text[:-1]
    return text + "…" if text else ""


class BusMapWindow:
    """Tools > Bus map (Ctrl+M): the user draws where each device sits on the RS-485 line (order, cable lengths,
    stubs, terminators); busmap.analyze computes lengths, delays, limits and warnings; an animation shows how a
    request travels from the master to a slave and the answer comes back (schematic time scale, see
    monitor.ANIM_BUS_MS_PER_S), optionally following the real traffic of the running masters."""
    COLS = (("n", "#", 30, "center"), ("name", "Name", 130, "w"), ("unit", "Slave ID", 62, "center"),
            ("segment_m", "Segment (m)", 84, "e"), ("stub_m", "Stub (m)", 64, "e"),
            ("unit_load", "Unit load", 66, "e"), ("supply_ma", "Supply (mA)", 80, "e"),
            ("terminated", "Terminator", 74, "center"))
    EDIT_LABEL = {"name": "Name", "unit": "Slave ID", "segment_m": "Segment", "stub_m": "Stub",
                  "unit_load": "Unit load", "supply_ma": "Supply"}
    CUSTOM = (("vf", "vf", "Velocity factor (0.3-1.0): signal speed / speed of light"),
              ("ohm_per_km", "Ω/km", "Loop resistance of ONE conductor, ohm per km"),
              ("pf_per_m", "pF/m", "Capacitance between the two wires, pF per metre"),
              ("db100_1mhz", "dB/100m", "Attenuation in dB per 100 m at 1 MHz"))
    TICK_MS = 33        # ~30 fps while something moves
    IDLE_MS = 1000      # only the device state colours change: once a second
    CALC_DELAY_MS = 150
    LIVE_POLL_S = 0.1
    LIVE_ITEM_S = 0.35  # one real request / answer in live mode, at speed 1x
    HEIGHT = 262        # drawing height (px at 96 dpi)

    def __init__(self, app):
        import busmap
        self.busmap = busmap
        self.app, s = app, app.s
        self.s = s
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Bus Map")
        help_key(w, "bus.map")
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"{min(int(1140 * s), sw - 40)}x{min(int(820 * s), sh - 90)}")
        w.minsize(int(780 * s), int(560 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.map = json.loads(json.dumps(app.cfg["busmap"]))
        self.ui = app.cfg["busmap_ui"] = mon.norm_busmap_ui(app.cfg.get("busmap_ui"))
        c = app.cfg["conn"]
        if not self.map["nodes"] and c.get("mode") in ("RTU", "ASCII"):  # a new map starts at the line's settings
            try:
                self.map["baud"] = int(c["baud"])
            except (KeyError, ValueError):
                pass
            self.ui["parity"] = c.get("parity", "N") if c.get("parity") in ("N", "E", "O") else "N"
            self.ui["stop"] = c.get("stop", "1") if c.get("stop") in ("1", "2") else "1"
        self.an, self.tl, self.sel, self.editor = None, None, None, None
        self._job = self._calc_job = None
        self._loading = False
        self.anim, self.playing, self.turn = None, False, 0
        self.completed, self.last_seen = 0, []
        self.flash, self.anim_flashed, self.master_flash = {}, set(), 0.0
        self.live_q, self.live_cur, self.live_seen, self.live_next = [], None, {}, 0.0
        self.geo = None
        self._last_tick = time.perf_counter()
        self._states = None
        self.font = tkfont.nametofont("TkDefaultFont")
        fonts = getattr(app.root, "_mb_fonts", None) or [self.font, self.font]
        self.small, self.bold = fonts[0], fonts[1]
        self._build_menu()
        sb = ttk.Frame(w, padding=(8, 2, 8, 6))
        sb.pack(fill="x", side="bottom")
        self.l_msg = ttk.Label(sb, text="The program cannot see the wiring: put the devices in the order they sit on "
                                        "the cable and enter the lengths. Double-click a cell to edit it.",
                               style="SInfo.TLabel")
        self.l_msg.pack(side="left", fill="x", expand=True)
        self._build_settings()
        pw = self.pw = ttk.Panedwindow(w, orient="vertical")
        pw.pack(fill="both", expand=True, padx=6, pady=(0, 2))
        top = ttk.Frame(pw)
        pw.add(top, weight=1)
        self._build_drawing(top)
        bottom = ttk.Panedwindow(pw, orient="horizontal")
        pw.add(bottom, weight=1)
        left = ttk.Frame(bottom)
        bottom.add(left, weight=3)
        self._build_table(left)
        right = ttk.Frame(bottom)
        bottom.add(right, weight=2)
        self._build_results(right)
        self._speed_changed()
        self.load_settings_vars()
        self.recalc()
        self._ensure_tick(self.IDLE_MS)

    # ----- layout -----
    def _build_menu(self):
        mb = tk.Menu(self.w)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="Save map as JSON...", command=self.save_json)
        fm.add_command(label="Load map from JSON...", command=self.load_json)
        fm.add_separator()
        fm.add_command(label="Import from Devices", command=self.import_devices)
        fm.add_command(label="Copy report", command=self.copy_report)
        fm.add_separator()
        fm.add_command(label="Close", command=self.close)
        mb.add_cascade(label="Map", menu=fm)
        hm = tk.Menu(mb, tearoff=0)
        hm.add_command(label="Bus map help", accelerator="F1", command=lambda: self.app.open_help(key="bus.map"))
        hm.add_command(label="RS-485 wiring", command=lambda: self.app.open_help(topic="rs485-wiring"))
        hm.add_command(label="PLC cycle and bus calculator", command=lambda: self.app.open_help(key="devices.cycle"))
        mb.add_cascade(label="Help", menu=hm)
        self.w.configure(menu=mb)

    def _build_settings(self):
        import busmap
        s = self.s
        f = ttk.Frame(self.w, padding=(8, 6, 8, 2))
        f.pack(fill="x")
        self.v, self.bv, self.e_custom = {}, {}, {}
        self.cable_labels = {k: c["label"] for k, c in busmap.CABLES.items()}
        r1 = ttk.Frame(f)
        r1.pack(fill="x")
        ttk.Label(r1, text="Cable:").pack(side="left", padx=(0, 4))
        self.v["cable"] = tk.StringVar()
        self.cb_cable = ttk.Combobox(r1, textvariable=self.v["cable"], values=list(self.cable_labels.values()),
                                     state="readonly", width=44)
        self.cb_cable.pack(side="left")
        Tooltip(self.cb_cable, "Typical cable values (velocity factor, resistance, capacitance, attenuation).\n"
                               "Custom: enter the values from your cable's datasheet.")
        for k, lab, tip in self.CUSTOM:
            ttk.Label(r1, text=lab).pack(side="left", padx=(int(10 * s), 3))
            self.v[k] = tk.StringVar()
            e = self.e_custom[k] = ttk.Entry(r1, textvariable=self.v[k], width=6)
            e.pack(side="left")
            Tooltip(e, tip + "\nEditable when Cable = Custom.")
        self.b_help = add_help_button(self.app, self.w, "bus.map", parent=r1, side="right")
        r2 = ttk.Frame(f)
        r2.pack(fill="x", pady=(4, 0))
        ttk.Label(r2, text="Baud:").pack(side="left", padx=(0, 4))
        self.v["baud"] = tk.StringVar()
        self.cb_baud = ttk.Combobox(r2, textvariable=self.v["baud"], values=BAUDS, width=8)
        self.cb_baud.pack(side="left")
        help_key(self.cb_baud, "setup.baud")
        for k, lab, tip in (("master_at_end", "Master at one end", "Daisy chain with the master (this PC's adapter) at "
                                                                   "one end of the trunk - the recommended wiring.\n"
                                                                   "Off: the master is tapped into the line."),
                            ("terminate_master_end", "120 Ω at master end", "A 120 ohm terminator at the master's end "
                                                                            "of the trunk"),
                            ("terminate_far_end", "120 Ω at far end", "A 120 ohm terminator at the far end (after the "
                                                                      "last device)"),
                            ("bias", "Bias", "Fail-safe bias resistors (usually in the master's adapter) keep an idle "
                                             "line in a defined state")):
            self.bv[k] = tk.BooleanVar()
            cb = ttk.Checkbutton(r2, text=lab, variable=self.bv[k])
            cb.pack(side="left", padx=(int(12 * s), 0))
            Tooltip(cb, tip)
            self.bv[k].trace_add("write", lambda *_a: self._settings_changed())
        for k, lab, tip, wd in (("supply_v", "Supply V", "Supply voltage fed at the master end (for devices powered "
                                                         "over the bus cable: Supply (mA) column)", 5),
                                ("rise_ns", "Rise time (ns)", "Transceiver rise time from its datasheet: decides the "
                                                             "critical length (when terminators are needed)", 6)):
            ttk.Label(r2, text=lab).pack(side="left", padx=(int(12 * s), 3))
            self.v[k] = tk.StringVar()
            e = ttk.Entry(r2, textvariable=self.v[k], width=wd)
            e.pack(side="left")
            Tooltip(e, tip)
        self.fit = tk.BooleanVar(value=bool(self.ui["fit"]))
        cb = ttk.Checkbutton(r2, text="Fit to width", variable=self.fit, command=self._fit_changed)
        cb.pack(side="right")
        Tooltip(cb, "Squeeze all devices into the window instead of scrolling sideways")
        for k in ("cable", "baud", "supply_v", "rise_ns") + tuple(c[0] for c in self.CUSTOM):
            self.v[k].trace_add("write", lambda *_a: self._settings_changed())

    def _build_drawing(self, top):
        s = self.s
        cf = ttk.Frame(top)
        cf.pack(fill="both", expand=True)
        self.cv = tk.Canvas(cf, height=int(self.HEIGHT * s), bg="#fbfcfd", highlightthickness=1,
                            highlightbackground=COL["line"])
        self.hsb = ttk.Scrollbar(cf, orient="horizontal", command=self.cv.xview)
        self.cv.configure(xscrollcommand=self.hsb.set)
        self.cv.pack(fill="both", expand=True)
        self.hsb.pack(fill="x")
        self.cv.bind("<Configure>", lambda e: self.redraw())
        self.cv.bind("<Button-1>", self._canvas_click)
        help_key(self.cv, "bus.map")
        ar = ttk.Frame(top, padding=(0, 4, 0, 0))
        ar.pack(fill="x")
        self.b_play = ttk.Button(ar, text="▶ Play", width=9, command=self.toggle_play)
        self.b_play.pack(side="left")
        Tooltip(self.b_play, "Animate one request / response after the other (schematic time scale)")
        ttk.Label(ar, text="Speed").pack(side="left", padx=(int(10 * s), 3))
        try:
            sp = float(self.ui["speed"])
        except ValueError:
            sp = 1.0
        self.speed_v = tk.DoubleVar(value=math.log2(min(max(sp, 0.25), 4.0)))
        self.sc_speed = ttk.Scale(ar, from_=-2, to=2, variable=self.speed_v, length=int(90 * s),
                                  command=lambda _v: self._speed_changed())
        self.sc_speed.pack(side="left")
        self.l_speed = ttk.Label(ar, text="1x", width=5)
        self.l_speed.pack(side="left", padx=(3, 0))
        ttk.Label(ar, text="Poll").pack(side="left", padx=(int(8 * s), 3))
        self.dev_v = tk.StringVar(value=BM_ALL)
        self.cb_dev = ttk.Combobox(ar, textvariable=self.dev_v, state="readonly", width=20)
        self.cb_dev.pack(side="left")
        self.cb_dev.bind("<<ComboboxSelected>>", lambda e: self._anim_inputs_changed())
        Tooltip(self.cb_dev, "Which device the master asks (All in turn = like a scan cycle)")
        self.av = {}
        for k, lab, wdg, wd, tip in (("fc", "FC", "fc", 4, "Function code of the request"),
                                     ("qty", "x", "e", 5, "Quantity (registers or bits)"),
                                     ("delay_ms", "Delay ms", "e", 5, "Device response delay: how long the device "
                                                                      "'thinks' before it answers"),
                                     ("parity", "Parity", "par", 3, "Parity of the line (bits per character)"),
                                     ("stop", "Stop", "stop", 3, "Stop bits")):
            ttk.Label(ar, text=lab).pack(side="left", padx=(int(8 * s), 3))
            self.av[k] = tk.StringVar(value=self.ui[k])
            if wdg == "e":
                wg = ttk.Entry(ar, textvariable=self.av[k], width=wd)
            else:
                vals = {"fc": BM_FCS, "par": ("N", "E", "O"), "stop": ("1", "2")}[wdg]
                wg = ttk.Combobox(ar, textvariable=self.av[k], values=list(vals), state="readonly", width=wd)
            wg.pack(side="left")
            Tooltip(wg, tip)
            self.av[k].trace_add("write", lambda *_a: self._anim_inputs_changed())
        self.live = tk.BooleanVar(value=bool(self.ui["live"]))
        cb = ttk.Checkbutton(ar, text="Follow real traffic", variable=self.live, command=self._live_changed)
        cb.pack(side="right")
        Tooltip(cb, "While the main connection or the Devices polling runs, show the real requests and answers\n"
                    "of the devices on this map (read from the traffic records; protocol timing is not changed).\n"
                    "Events older than 1 s are dropped so the picture never lags behind.")
        self.tl_cv = tk.Canvas(top, height=int(30 * s), bg="#ffffff", highlightthickness=0)
        self.tl_cv.pack(fill="x", pady=(5, 0))
        self.tl_cv.bind("<Configure>", lambda e: self.draw_timeline())
        self.l_tl = ttk.Label(top, text="", style="Info.TLabel")
        self.l_tl.pack(fill="x")
        self.l_cycle = ttk.Label(top, text="", font=self.bold)
        self.l_cycle.pack(fill="x", pady=(2, 0))
        self.l_note = ttk.Label(top, text="", style="Info.TLabel", justify="left")
        self.l_note.pack(fill="x")
        top.bind("<Configure>", lambda e: self.l_note.configure(wraplength=max(e.width - 10, 200)), add="+")

    def _build_table(self, left):
        s = self.s
        bf = ttk.Frame(left, padding=(0, 4, 0, 2))
        bf.pack(fill="x")
        for text, cmd, tip in (("Add device", self.add_node, "Add a device by hand (name and Slave ID: double-click "
                                                              "the cells to change them)"),
                               ("Remove", self.remove_node, "Remove the selected device from the map   (Del)"),
                               ("Move up", lambda: self.move_node(-1), "Move it one place towards the master"),
                               ("Move down", lambda: self.move_node(1), "Move it one place towards the far end"),
                               ("Import from Devices", self.import_devices,
                                "One node per configured device (RS-485 ones), in Slave ID order.\n"
                                "Lengths already entered for the same names are kept.")):
            b = ttk.Button(bf, text=text, command=cmd)
            b.pack(side="left", padx=(0, 4))
            Tooltip(b, tip)
        body = ttk.Frame(left)
        body.pack(fill="both", expand=True)
        tf = ttk.Frame(body)
        tf.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in self.COLS], show="headings", selectmode="browse",
                                 height=6, style=tree_style(self.app.root, s))
        for cid, lab, wd, anc in self.COLS:
            self.tree.heading(cid, text=lab, anchor="w" if anc == "w" else "center")
            self.tree.column(cid, width=int(wd * s), anchor=anc, stretch=cid == "name", minwidth=int(28 * s))
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._tree_select())
        self.tree.bind("<Double-1>", self._tree_double)
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<Delete>", lambda e: self.remove_node())
        self.tree.bind("<F2>", lambda e: self.sel is not None and self.start_edit(self.sel, "name"))
        Tooltip(self.tree, "One row per device, in the order they sit on the cable (row 1 = nearest the master).\n"
                           "Segment = cable from the previous device (row 1: from the master).\n"
                           "Stub = branch from the trunk to the device (0 = daisy chain).\n"
                           "Double-click a cell to edit (Enter = keep, Esc = cancel); click Terminator to toggle.")
        uf = ttk.Frame(body, padding=(8, 0, 0, 0))
        uf.pack(side="left", fill="y")
        ttk.Label(uf, text="Not on the map yet:").pack(anchor="w")
        self.lb_un = tk.Listbox(uf, height=5, width=18, exportselection=False, relief="solid", borderwidth=1)
        self.lb_un.pack(fill="both", expand=True, pady=(2, 2))
        self.lb_un.bind("<Double-1>", lambda e: self.add_unmapped())
        Tooltip(self.lb_un, "Devices configured in Devices (F11) that are not on this map")
        ttk.Button(uf, text="Add to map", command=self.add_unmapped).pack(fill="x")

    def _build_results(self, right):
        hf = ttk.Frame(right, padding=(4, 4, 0, 2))
        hf.pack(fill="x")
        ttk.Label(hf, text="Results (estimates from typical cable values)", font=self.bold).pack(side="left")
        b = ttk.Button(hf, text="Copy report", command=self.copy_report)
        b.pack(side="right")
        Tooltip(b, "Copy the full analysis as text (busmap report)")
        of = ttk.Frame(right, padding=(4, 0, 0, 0))
        of.pack(fill="both", expand=True)
        self.out = tk.Text(of, height=8, width=46, wrap="word", font=self.small, relief="flat", borderwidth=1,
                           highlightthickness=1, highlightbackground=COL["line"], padx=6, pady=4)
        osb = ttk.Scrollbar(of, orient="vertical", command=self.out.yview)
        self.out.configure(yscrollcommand=osb.set)
        self.out.pack(side="left", fill="both", expand=True)
        osb.pack(side="left", fill="y")
        self.out.tag_configure("h", font=self.bold, spacing1=int(4 * self.s))
        self.out.tag_configure("bad", foreground=COL["bad"])
        self.out.tag_configure("warn", foreground="#a15c00")
        self.out.tag_configure("ok", foreground="#1e7b34")
        ind = int(14 * self.s)
        self.out.tag_configure("w", lmargin1=ind, lmargin2=ind * 3)
        for lv, (_t, col) in BM_LEVEL.items():
            self.out.tag_configure("lv_" + lv, foreground=col, font=self.bold)
        self.out.configure(state="disabled")

    # ----- window -----
    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        for attr in ("_job", "_calc_job"):
            j = getattr(self, attr)
            if j is not None:
                try:
                    self.w.after_cancel(j)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        self.playing = False
        self.end_edit()
        self._store(save=True)
        if self.alive():
            self.w.destroy()

    def _status(self, text, kind="info"):
        self.l_msg.configure(text=text, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel",
                                               "warn": "SWarn.TLabel"}.get(kind, "SInfo.TLabel"))

    def _store(self, save=False):
        """Keep the (valid) map and the animation inputs in app.cfg."""
        try:
            self.busmap.validate(self.map)
            self.app.cfg["busmap"] = json.loads(json.dumps(self.map))
        except (ValueError, AttributeError):
            pass
        if save:
            save_settings(self.app.cfg)

    def reload(self):
        """The map in app.cfg changed (workspace opened): show it."""
        self.end_edit()
        self.map = json.loads(json.dumps(self.app.cfg["busmap"]))
        self.anim, self.sel = None, None
        self.load_settings_vars()
        self.recalc()

    # ----- global settings -----
    def load_settings_vars(self):
        m = self.map
        self._loading = True
        try:
            self.v["cable"].set(self.cable_labels.get(m["cable"], self.cable_labels["rs485"]))
            self.v["baud"].set(str(int(m["baud"])))
            self.v["supply_v"].set(f"{m['supply_v']:g}")
            self.v["rise_ns"].set(f"{m['rise_ns']:g}")
            for k in mon.BUSMAP_BOOLS:
                self.bv[k].set(bool(m[k]))
            self._show_cable_values()
        finally:
            self._loading = False

    def _cable_key(self):
        lab = self.v["cable"].get()
        return next((k for k, v in self.cable_labels.items() if v == lab), "rs485")

    def _show_cable_values(self):
        """Custom: the user's values (editable); otherwise the chosen cable's typical values (read-only)."""
        import busmap
        key = self._cable_key()
        vals = dict(busmap.CABLES[key])
        if key == "custom":
            vals.update(self.map.get("cable_custom") or {})
        was = self._loading
        self._loading = True
        try:
            for k, _l, _t in self.CUSTOM:
                self.v[k].set(f"{vals[k]:g}")
                self.e_custom[k].configure(state="normal" if key == "custom" else "disabled")
        finally:
            self._loading = was

    def _settings_changed(self):
        if self._loading:
            return
        if self._cable_key() != self.map.get("cable"):
            self.map["cable"] = self._cable_key()
            self._show_cable_values()
        cand = json.loads(json.dumps(self.map))
        try:
            cand["cable"] = self._cable_key()
            cand["baud"] = mon._num(self.v["baud"].get(), "Baud", whole=True)
            if not 50 <= cand["baud"] <= 4000000:
                raise ValueError("Baud: 50 - 4000000")
            cand["supply_v"] = mon._num(self.v["supply_v"].get(), "Supply V")
            cand["rise_ns"] = mon._num(self.v["rise_ns"].get(), "Rise time")
            for k in mon.BUSMAP_BOOLS:
                cand[k] = bool(self.bv[k].get())
            if cand["cable"] == "custom":
                cand["cable_custom"] = {k: mon._num(self.v[k].get(), lab) for k, lab, _t in self.CUSTOM}
            self.busmap.validate(cand)
            self.busmap.analyze(cand)
        except ValueError as e:
            self._status(f"Not applied: {e}", "bad")
            return
        self.map = cand
        self._status("Map updated", "info")
        self._schedule()

    def _fit_changed(self):
        self.ui["fit"] = bool(self.fit.get())
        self.redraw()

    def _schedule(self):
        if self._calc_job is not None:
            self.w.after_cancel(self._calc_job)
        self._calc_job = self.w.after(self.CALC_DELAY_MS, self.recalc)

    def recalc(self):
        """Analyse the map and refresh everything (debounced from the inputs; direct after table edits)."""
        if self._calc_job is not None:
            try:
                self.w.after_cancel(self._calc_job)
            except tk.TclError:
                pass
        self._calc_job = None
        try:
            self.an = self.busmap.analyze(self.map)
        except ValueError as e:
            self.an = None
            self._status(f"Map not valid: {e}", "bad")
        if self.an is not None:
            self._store()
        self.refresh_table()
        self.refresh_unmapped()
        self.refresh_devices()
        self.redraw()
        self.show_results()
        self.update_timeline()
        return self.an

    # ----- table -----
    def refresh_table(self):
        sel = self.sel
        self.tree.delete(*self.tree.get_children())
        for i, n in enumerate(self.map["nodes"]):
            self.tree.insert("", "end", iid=str(i), values=(
                i + 1, n["name"], "" if n.get("unit") is None else n["unit"], f"{n['segment_m']:g}",
                f"{n['stub_m']:g}", f"{n['unit_load']:g}", f"{n['supply_ma']:g}", "☑" if n["terminated"] else "☐"))
        if sel is not None and sel < len(self.map["nodes"]):
            self.tree.selection_set(str(sel))
            self.tree.see(str(sel))
        else:
            self.sel = None

    def _tree_select(self):
        s = self.tree.selection()
        new = int(s[0]) if s else None
        if new != self.sel:
            self.sel = new
            self.update_dynamic()

    def select(self, idx):
        self.sel = idx
        if idx is not None and self.tree.exists(str(idx)):
            self.tree.selection_set(str(idx))
            self.tree.see(str(idx))
        self.update_dynamic()

    def _col_key(self, x):
        col = self.tree.identify_column(x)
        try:
            return self.COLS[int(col.lstrip("#")) - 1][0]
        except (ValueError, IndexError):
            return None

    def _tree_click(self, e):
        if self.tree.identify_region(e.x, e.y) != "cell":
            return None
        iid = self.tree.identify_row(e.y)
        if iid and self._col_key(e.x) == "terminated":
            self.toggle_terminator(int(iid))
            return "break"
        return None

    def _tree_double(self, e):
        if self.tree.identify_region(e.x, e.y) != "cell":
            return None
        iid, key = self.tree.identify_row(e.y), self._col_key(e.x)
        if iid and key in self.EDIT_LABEL:
            self.start_edit(int(iid), key)
            return "break"
        return None

    def toggle_terminator(self, idx):
        cand = json.loads(json.dumps(self.map))
        cand["nodes"][idx]["terminated"] = not cand["nodes"][idx]["terminated"]
        self._apply(cand, idx)

    def _apply(self, cand, sel=None, msg=None):
        """Use a changed map when it is valid -> None, else the error text (map unchanged)."""
        try:
            self.busmap.validate(cand)
            self.busmap.analyze(cand)
        except ValueError as e:
            self._status(f"Not applied: {e}", "bad")
            return str(e)
        self.map = cand
        self.sel = sel
        self.recalc()
        self._status(msg or "Map updated", "ok" if msg else "info")
        return None

    def start_edit(self, idx, key):
        """In-place editor over a table cell. Enter keeps the value (when valid), Esc cancels."""
        self.end_edit()
        iid = str(idx)
        if not self.tree.exists(iid) or key not in self.EDIT_LABEL:
            return None
        self.select(idx)
        self.tree.see(iid)
        self.tree.update_idletasks()
        bbox = self.tree.bbox(iid, key)
        if not bbox:
            return None
        x, y, wd, ht = bbox
        n = self.map["nodes"][idx]
        cur = n[key]
        e = tk.Entry(self.tree, relief="solid", bd=1, font=self.font, highlightthickness=0,
                     justify="left" if key == "name" else "right")
        e.insert(0, "" if cur is None else (f"{cur:g}" if isinstance(cur, float) else str(cur)))
        e.select_range(0, "end")
        e.place(x=x, y=y, width=max(wd, int(60 * self.s)), height=ht)
        self.editor = e

        def ok(_e=None):
            err = self.commit_edit(idx, key, e.get())
            if err:
                e.configure(bg="#ffd6d6")
            else:
                self.end_edit()
                self.tree.focus_set()
            return "break"

        def cancel(_e=None):
            self.end_edit()
            self.tree.focus_set()
            self._status("Edit cancelled", "info")
            return "break"
        e.bind("<Return>", ok)
        e.bind("<KP_Enter>", ok)
        e.bind("<Escape>", cancel)
        e.bind("<FocusOut>", lambda _e: self.end_edit())
        e.bind("<Key>", lambda _e: e.configure(bg="white"), add="+")
        e.mb_commit, e.mb_cancel = ok, cancel  # Enter / Esc without key events (tests)
        e.focus_set()
        return e

    def end_edit(self):
        if self.editor is not None:
            ed, self.editor = self.editor, None
            try:
                ed.destroy()
            except tk.TclError:
                pass

    def commit_edit(self, idx, key, text):
        """New text for one cell -> None (applied) or the error text (shown in the status line)."""
        cand = json.loads(json.dumps(self.map))
        n = cand["nodes"][idx]
        lab = self.EDIT_LABEL[key]
        text = str(text).strip()
        try:
            if key == "name":
                n["name"] = text
            elif key == "unit":
                n["unit"] = None if not text else int_in(text, 0, 255)
            else:
                n[key] = mon._num(text, lab)
        except ValueError as e:
            msg = str(e) if str(e).startswith(lab) else f"{lab}: {e}"
            self._status(f"Not applied: {msg}   (Esc = cancel)", "bad")
            return msg
        err = self._apply(cand, idx, f"{lab} of row {idx + 1} set to {text or '(empty)'}")
        if err:
            self._status(f"Not applied: {err}   (Esc = cancel)", "bad")
        return err

    def _free_unit(self, start=1):
        used = {n.get("unit") for n in self.map["nodes"]}
        return next((u for u in range(start, 248) if u not in used), None)

    def _new_node(self, name, unit):
        return {"name": name, "unit": unit, "segment_m": 10.0, "stub_m": 0.0, "unit_load": 1.0, "supply_ma": 0.0,
                "terminated": False}

    def add_node(self, name=None, unit=None):
        cand = json.loads(json.dumps(self.map))
        names = {n["name"] for n in cand["nodes"]}
        if name is None:
            k = len(cand["nodes"]) + 1
            while f"Device {k}" in names:
                k += 1
            name = f"Device {k}"
        if unit is None:
            unit = self._free_unit()
        pos = (self.sel + 1) if self.sel is not None else len(cand["nodes"])
        cand["nodes"].insert(pos, self._new_node(name, unit))
        return self._apply(cand, pos, f"'{name}' (ID {unit}) added at position {pos + 1}: "
                                      "double-click its cells to set the lengths")

    def remove_node(self):
        if self.sel is None:
            self._status("Select a device in the table first", "warn")
            return
        cand = json.loads(json.dumps(self.map))
        n = cand["nodes"].pop(self.sel)
        self.anim = None
        self._apply(cand, min(self.sel, len(cand["nodes"]) - 1) if cand["nodes"] else None,
                    f"'{n['name']}' removed from the map")

    def move_node(self, d):
        i = self.sel
        if i is None or not 0 <= i + d < len(self.map["nodes"]):
            return
        cand = json.loads(json.dumps(self.map))
        cand["nodes"][i], cand["nodes"][i + d] = cand["nodes"][i + d], cand["nodes"][i]
        self._apply(cand, i + d, f"'{cand['nodes'][i + d]['name']}' is now at position {i + d + 1}")

    def unmapped(self):
        names = {n["name"] for n in self.map["nodes"]}
        return [d for d in self.app.devices if d["name"] not in names]

    def refresh_unmapped(self):
        self.lb_un.delete(0, "end")
        for d in self.unmapped():
            self.lb_un.insert("end", f"{d['name']}  (ID {d['unit']})")

    def add_unmapped(self, name=None):
        un = self.unmapped()
        if name is None:
            sel = self.lb_un.curselection()
            if not sel:
                if not un:
                    self._status("Every configured device is on the map", "info")
                    return None
                sel = (0,)
            name = un[sel[0]]["name"]
        dv = next((d for d in un if d["name"] == name), None)
        if dv is None:
            return None
        self.sel = len(self.map["nodes"]) - 1 if self.map["nodes"] else None
        return self.add_node(dv["name"], int(dv["unit"]))

    def import_devices(self):
        """Nodes for the configured devices (the RS-485 ones; all of them when none is serial) in Slave ID order.
        Lengths already on the map for the same names are kept; hand-made nodes stay at the end."""
        devs = [d for d in self.app.devices if d["conn"]["mode"] != "TCP"] or list(self.app.devices)
        if not devs:
            self._status("No devices configured: add some in Devices (F11) first, or use Add device", "warn")
            return None
        old = {n["name"]: n for n in self.map["nodes"]}
        nodes, units, skipped = [], set(), []
        for d in sorted(devs, key=lambda d: (int(d["unit"]), d["name"])):
            u = int(d["unit"])
            if u in units:
                skipped.append(d["name"])
                continue
            n = dict(old.get(d["name"]) or self._new_node(d["name"], u))
            n["name"], n["unit"] = d["name"], u
            nodes.append(n)
            units.add(u)
        names = {n["name"] for n in nodes}
        for n in self.map["nodes"]:
            if n["name"] not in names and n.get("unit") not in units:
                nodes.append(n)
                if n.get("unit") is not None:
                    units.add(n["unit"])
        cand = json.loads(json.dumps(self.map))
        cand["nodes"] = nodes
        msg = f"Imported {len(names)} device(s) from Devices in Slave ID order"
        if skipped:
            msg += f"; skipped (same Slave ID as another): {', '.join(skipped)}"
        self.anim = None
        err = self._apply(cand, None, msg + " - now put them in cable order and enter the lengths")
        return err is None

    # ----- JSON files -----
    def save_json(self, path=None):
        path = path or filedialog.asksaveasfilename(parent=self.w, title="Save bus map", defaultextension=".json",
                                                    filetypes=[("Bus map JSON", "*.json"), ("All files", "*.*")],
                                                    initialfile="busmap.json")
        if not path:
            return None
        try:
            self.busmap.validate(self.map)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.map, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except (OSError, ValueError) as e:
            self._status(f"Map not saved: {e}", "bad")
            return None
        self._status(f"Map saved: {path}   (also: python mbtool.py busmap \"{os.path.basename(path)}\")", "ok")
        return path

    def load_json(self, path=None):
        path = path or filedialog.askopenfilename(parent=self.w, title="Load bus map",
                                                  filetypes=[("Bus map JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self._status(f"Cannot read {os.path.basename(path)}: {e}", "bad")
            return None
        m, err = mon.norm_busmap(data)
        if err:
            self._status(f"Not a valid bus map ({os.path.basename(path)}): {err} - the current map is kept", "bad")
            return None
        self.map, self.anim, self.sel = m, None, None
        self.load_settings_vars()
        self.recalc()
        self._store(save=True)
        self._status(f"Map loaded: {path}", "ok")
        return m

    def copy_report(self):
        if self.an is None:
            self._status("The map is not valid: nothing to copy", "bad")
            return None
        text = self.busmap.format_report(self.an)
        self.w.clipboard_clear()
        self.w.clipboard_append(text)
        self._status("Report copied to the clipboard", "ok")
        return text

    # ----- results -----
    def show_results(self):
        t = self.out
        t.configure(state="normal")
        t.delete("1.0", "end")
        an = self.an
        if an is None:
            t.insert("end", "The map is not valid - see the status line.", ("bad",))
            t.configure(state="disabled")
            self.l_note.configure(text="")
            return

        def line(*segs):
            for text, tag in segs:
                t.insert("end", text, (tag,) if tag else ())
            t.insert("end", "\n")
        nerr = sum(1 for lv, _x in an["warnings"] if lv == "error")
        nwarn = sum(1 for lv, _x in an["warnings"] if lv == "warn")
        line((f"Checks  ({nerr} error, {nwarn} warning)" if an["warnings"] else "Checks", "h"))
        if not an["warnings"]:
            line(("  No problems found.", "ok"))
        for lv, text in an["warnings"]:
            mark, _c = BM_LEVEL.get(lv, BM_LEVEL["info"])
            t.insert("end", "  ", ("w",))
            t.insert("end", mark, ("w", "lv_" + (lv if lv in BM_LEVEL else "info")))
            t.insert("end", text + "\n", ("w",))
        line(("Lengths", "h"))
        line((f"  Trunk {an['trunk_m']:.1f} m   stubs {an['stub_total_m']:.1f} m   farthest device "
              f"{an['farthest_m']:.1f} m", ""))
        if an["nodes"]:
            line(("Devices (cable distance from the master, one-way delay)", "h"))
            for r in an["nodes"]:
                line((f"  {bm_fit(r['name'] or 'device', self.small, int(130 * self.s))}  ID {r['unit']}:  "
                      f"{r['distance_m']:.1f} m,  {r['one_way_us']:.2f} us", ""))
        line(("Speed", "h"))
        rec, baud = an["recommended_baud"], an["baud"]
        ok = rec is not None and baud <= rec
        line((f"  {baud:.0f} baud chosen; usual maximum for a {an['trunk_m']:.0f} m trunk: "
              + (f"{rec} baud" if rec else "none (longer than the standard: repeater)"), ""),
             ("   OK" if ok else "   TOO HIGH", "ok" if ok else "bad"))
        line((f"  One bit lasts {an['bit_time_us']:.1f} us = {an['bit_length_m'] / 1000:.1f} km of cable; "
              f"farthest device {an['max_one_way_us']:.2f} us one way", ""))
        line((f"  Critical length {an['critical_length_m']:.0f} m ({self.map['rise_ns']:g} ns rise time): ", ""),
             ("electrically LONG - terminate both ends" if an["electrically_long"] else "electrically short",
              "warn" if an["electrically_long"] else ""))
        line((f"  Longest stub usually allowed at this baud: {an['max_stub_m']:g} m", ""))
        line(("Cable and load", "h"))
        line((f"  {an['cable']['label']}", ""))
        line((f"  Capacitance {an['capacitance_nf']:.2f} nF,  loop resistance {an['loop_resistance_ohm']:.1f} ohm,  "
              f"attenuation {an['attenuation_db']:.2f} dB at {an['signal_frequency_hz'] / 1000:.1f} kHz", ""))
        line((f"  Unit loads {an['unit_loads']:.2f} of {an['unit_load_limit']:.0f} (incl. the master)",
              "bad" if an["unit_loads"] > an["unit_load_limit"] else ""))
        if "min_voltage" in an:
            low = an["min_voltage"] < self.map["supply_v"] * 0.9
            line((f"  Lowest supply voltage at a device: {an['min_voltage']:.1f} V of {self.map['supply_v']:g} V",
                  "bad" if low else ""))
        t.configure(state="disabled")
        km = an["bit_length_m"] / 1000.0
        self.l_note.configure(text=(
            f"Schematic, not to scale: at {baud:.0f} baud one bit is ~{km:.1f} km of cable long, so the signal reaches "
            "every device almost at once; propagation delay does not change Modbus timing.   Animation time scale: "
            f"1 s on screen = {mon.ANIM_BUS_MS_PER_S:g} ms on the bus at 1x (very short phases are stretched to "
            f"{mon.ANIM_MIN_PHASE_S:g} s); the bar above is to scale."))

    # ----- drawing -----
    def node_state(self, name):
        """Polling state of the configured device with this name ('ok' / 'error' / 'stale' / 'off'), None if the
        map node is not a configured device."""
        if self.app.dev_by_name(name) is None:
            return None
        st = self.app.dev_status.get(name)
        return st.state(time.time()) if st else "off"

    def node_label(self, name, word):
        """State word under a device box + its error rate and error types when it had errors ('ok · 2.1% C3 T1')."""
        st = self.app.dev_status.get(name) if self.app.dev_by_name(name) is not None else None
        if st is None or not st.quality.errors:
            return word
        q = st.quality.summary()
        return f"{word} · {q['error_pct']:.1f}% {mon.kinds_text(q['kinds'])}"

    def _geometry(self):
        s = self.s
        n = len(self.map["nodes"])
        avail = max(self.cv.winfo_width() - 2, int(300 * s))
        end = bool(self.map["master_at_end"])
        mw, mh = int(104 * s), int(50 * s)
        oy = max(0, int((self.cv.winfo_height() - self.HEIGHT * s) / 2))  # centred when the pane is taller
        ty = oy + int(110 * s)
        mx0 = int(14 * s)
        if end:
            mbox = (mx0, ty - mh // 2, mx0 + mw, ty + mh // 2)
            x_start = mbox[2]
            trunk_x0 = x_start
            left = x_start + int(26 * s)
        else:
            mbox = (mx0 + int(30 * s), oy + int(10 * s), mx0 + int(30 * s) + mw, oy + int(10 * s) + mh)
            x_start = (mbox[0] + mbox[2]) // 2
            trunk_x0 = mx0
            left = mbox[2] + int(18 * s)
        right = int(70 * s)
        fit = bool(self.ui["fit"])
        sp, total = mon.busmap_spacing(n, avail, left, right, (70 if fit else 128) * s, 230 * s, fit)
        xs = [left + sp * (i + 0.5) for i in range(n)]
        bw = max(min(sp - 14 * s, 156 * s), 30 * s)
        x_far = (xs[-1] + bw / 2 + 24 * s) if xs else left + 38 * s  # far-end terminator right of the last box
        btop, bh = ty + int(44 * s), int(58 * s)
        return {"n": n, "ty": ty, "mbox": mbox, "x_start": x_start, "trunk_x0": trunk_x0, "xs": xs, "x_far": x_far,
                "sp": sp, "bw": bw, "btop": btop, "bh": bh, "total": total, "end": end}

    def _resistor(self, x, y0, y1, color="#5b6472", label="120 Ω", side="below"):
        """Vertical zigzag (a 120 ohm terminator) from (x, y0) to (x, y1)."""
        s, cv = self.s, self.cv
        n, amp = 6, 4 * s
        pts = [x, y0, x, y0 + (y1 - y0) * 0.2]
        h = (y1 - y0) * 0.6 / n
        for i in range(n):
            pts += [x + (amp if i % 2 == 0 else -amp), y0 + (y1 - y0) * 0.2 + h * (i + 0.5)]
        pts += [x, y0 + (y1 - y0) * 0.8, x, y1]
        cv.create_line(*pts, fill=color, width=max(1, int(1.5 * s)))
        cv.create_line(x - 5 * s, y1, x + 5 * s, y1, fill=color, width=max(1, int(1.5 * s)))
        if side == "below":
            cv.create_text(x, y1 + 2 * s, text=label, anchor="n", font=self.small, fill=color)
        else:
            cv.create_text(x - 8 * s, (y0 + y1) / 2, text=label, anchor="e", font=self.small, fill=color)

    def redraw(self):
        """Static picture: master, trunk, devices, lengths, terminators, bias. Packets and highlights are drawn on
        top by update_dynamic()."""
        cv, s = self.cv, self.s
        cv.delete("all")
        g = self.geo = self._geometry()
        m, nodes = self.map, self.map["nodes"]
        ty, mbox = g["ty"], g["mbox"]
        line_col, text_col, len_col = "#3b4351", "#1f2328", "#1a4fa8"
        # trunk: a thick pair
        cv.create_line(g["trunk_x0"], ty, g["x_far"], ty, fill=line_col, width=max(3, int(5 * s)), capstyle="round")
        cv.create_line(g["trunk_x0"], ty, g["x_far"], ty, fill="#c9d1dc", width=1)
        # master
        if not g["end"]:
            cv.create_line(g["x_start"], mbox[3], g["x_start"], ty, fill=line_col, width=max(2, int(3 * s)))
            cv.create_text(g["x_start"] + 5 * s, (mbox[3] + ty) / 2, text="master stub", anchor="w", font=self.small,
                           fill="#8a5a00")
            cv.create_oval(g["x_start"] - 4 * s, ty - 4 * s, g["x_start"] + 4 * s, ty + 4 * s, fill="#ffffff",
                           outline=line_col)
        cv.create_rectangle(*mbox, fill="#e8f0fe", outline="#1a64d6", width=max(2, int(2 * s)), tags=("master",))
        cx = (mbox[0] + mbox[2]) / 2
        cv.create_text(cx, mbox[1] + 17 * s, text="MASTER", font=self.bold, fill="#123a7a", tags=("master_t",))
        cv.create_text(cx, mbox[1] + 35 * s, text="mbtool (this PC)", font=self.small, fill="#3c4a63")
        if m["bias"]:
            if g["end"]:
                bx, by = mbox[0], mbox[1] - 20 * s
            else:
                bx, by = mbox[0] - 4 * s, mbox[3] + 4 * s
            cv.create_rectangle(bx, by, bx + 40 * s, by + 15 * s, fill="#fff1d6", outline="#e69500")
            cv.create_text(bx + 20 * s, by + 7.5 * s, text="bias", font=self.small, fill="#8a5a00")
        # terminators at the ends of the trunk
        tx = g["x_start"] + 12 * s if g["end"] else g["trunk_x0"] + 4 * s
        if m["terminate_master_end"]:
            self._resistor(tx, ty, ty + 32 * s)
        else:
            cv.create_text(tx, ty + 6 * s, text="no 120 Ω", anchor="n", font=self.small, fill="#a0a5ad")
        if m["terminate_far_end"]:
            self._resistor(g["x_far"], ty, ty + 32 * s)
        else:
            cv.create_text(g["x_far"], ty + 6 * s, text="open end", anchor="n", font=self.small, fill="#a0a5ad")
        if not nodes:
            cv.create_text(max(g["x_far"] + 30 * s, 330 * s), ty - 30 * s, anchor="w", font=self.font, fill="#59606b",
                           text="No devices on the map yet:\nImport from Devices, or Add device")
        dist, prev_x = 0.0, g["x_start"]
        bw, btop, bh = g["bw"], g["btop"], g["bh"]
        show_stub_text = g["sp"] >= 96 * s
        for i, n in enumerate(nodes):
            x = g["xs"][i]
            dist += n["segment_m"]
            cv.create_text((prev_x + x) / 2, ty - 7 * s, text=bm_fit(f"{n['segment_m']:g} m", self.bold,
                                                                     max(x - prev_x - 6 * s, 20 * s)),
                           anchor="s", font=self.bold, fill=len_col)
            prev_x = x
            cv.create_line(x, ty, x, btop, fill=line_col, width=max(2, int(2 * s)))
            if show_stub_text:
                cv.create_text(x + 5 * s, ty + 21 * s, text=f"{n['stub_m']:g} m", anchor="w", font=self.small,
                               fill="#59606b")
            if n["terminated"]:
                self._resistor(x - 14 * s, ty + 6 * s, ty + 34 * s, color="#8a5a00", label="120Ω", side="left")
            cv.create_oval(x - 4 * s, ty - 4 * s, x + 4 * s, ty + 4 * s, fill="#ffffff", outline=line_col,
                           tags=(f"port{i}",))
            x0, x1 = x - bw / 2, x + bw / 2
            fill, outline, _w = BM_STATE[self.node_state(n["name"])]
            tags = ("node", f"n{i}")
            cv.create_rectangle(x0, btop, x1, btop + bh, fill=fill, outline=outline, width=max(1, int(2 * s)),
                                tags=tags + (f"box{i}",))
            inner = bw - 10 * s
            cv.create_text(x, btop + 13 * s, text=bm_fit(n["name"] or "device", self.bold, inner), font=self.bold,
                           fill=text_col, tags=tags)
            unit = "-" if n.get("unit") is None else n["unit"]
            cv.create_text(x, btop + 30 * s, text=bm_fit(f"ID {unit} · {dist + n['stub_m']:g} m", self.font, inner),
                           font=self.font, fill=text_col, tags=tags)
            cv.create_text(x, btop + 46 * s, text=bm_fit(self.node_label(n["name"], _w), self.small, inner),
                           font=self.small, fill="#59606b", tags=tags + (f"st{i}",))
        if nodes:
            yb = btop + bh + 14 * s
            xa, xb = g["x_start"], g["xs"][-1]
            cv.create_line(xa, yb, xb, yb, fill="#7a8594")
            for xx in (xa, xb):
                cv.create_line(xx, yb - 5 * s, xx, yb + 5 * s, fill="#7a8594")
            trunk = sum(n["segment_m"] for n in nodes)
            far = max((sum(nn["segment_m"] for nn in nodes[:k + 1]) + nodes[k]["stub_m"]) for k in range(len(nodes)))
            lab = f"Trunk {trunk:g} m  ·  farthest device {far:g} m  ·  {len(nodes)} device(s)"
            cv.create_text(max((xa + xb) / 2, xa + self.font.measure(lab) / 2), yb + 4 * s, text=lab, anchor="n",
                           font=self.font, fill="#2b2f36")
        cv.configure(scrollregion=(0, 0, max(g["total"], g["x_far"] + 40 * s), max(int(self.HEIGHT * s),
                                                                                   cv.winfo_height())))
        self._states = None
        self.update_dynamic()

    def _canvas_click(self, e):
        x = self.cv.canvasx(e.x)
        for it in self.cv.find_overlapping(x - 1, self.cv.canvasy(e.y) - 1, x + 1, self.cv.canvasy(e.y) + 1):
            for t in self.cv.gettags(it):
                if t.startswith("n") and t[1:].isdigit():
                    self.select(int(t[1:]))
                    return

    def _idx_of_unit(self, unit):
        return next((i for i, n in enumerate(self.map["nodes"]) if n.get("unit") == unit), None)

    def update_dynamic(self):
        """Packets, flashes and highlights (tag 'dyn') + device state colours. Cheap: called every frame."""
        cv, s, g = self.cv, self.s, self.geo
        if g is None or not self.alive():
            return
        cv.delete("dyn")
        now = time.perf_counter()
        nodes = self.map["nodes"]
        if len(g["xs"]) != len(nodes):
            return
        states = [self.node_state(n["name"]) for n in nodes]
        labels = [self.node_label(n["name"], BM_STATE[stt][2]) for n, stt in zip(nodes, states)]
        if (states, labels) != self._states:  # Devices polling changed a state or the error counts
            self._states = (states, labels)
            for i, lab in enumerate(labels):
                cv.itemconfigure(f"st{i}", text=bm_fit(lab, self.small, g["bw"] - 10 * s))
        phase, frac, unit, live_kind = None, 0.0, None, None
        if self.live.get() and self.live_cur is not None:
            lc = self.live_cur
            frac = min((now - lc["t0"]) / lc["dur"], 1.0)
            unit, live_kind = lc["ev"]["unit"], lc["ev"]["kind"]
            phase = {"req": "request", "rsp": "response", "err": "error"}[live_kind]
        elif self.anim is not None and not self.anim.done:
            phase, frac = self.anim.current()
            unit = self.anim.unit
        target = self._idx_of_unit(unit) if unit is not None else None
        ty = g["ty"]
        for i, stt in enumerate(states):
            fill, outline, _w = BM_STATE[stt]
            width = max(1, int(2 * s))
            if i == target and phase in ("request", "device", "response", "error"):
                fill, outline, width = "#fff3b0", "#e0a800", max(2, int(3 * s))
                if phase == "device":
                    width = max(2, int((3 + 2 * math.sin(now * 12)) * s))
                if phase == "error":
                    fill, outline = "#fde3e1", COL["bad"]
            if i == self.sel:
                outline, width = "#1a64d6", max(width, int(3 * s))
            cv.itemconfigure(f"box{i}", fill=fill, outline=outline, width=width)
            on = self.flash.get(i, 0) > now
            r = (6 if on else 4) * s
            x = g["xs"][i]
            cv.coords(f"port{i}", x - r, ty - r, x + r, ty + r)
            cv.itemconfigure(f"port{i}", fill="#ffd23f" if on else "#ffffff")
        master_on = self.master_flash > now
        cv.itemconfigure("master", fill="#cdeccf" if master_on else "#e8f0fe")
        if phase is None:
            return
        x_start, x_far = g["x_start"], g["x_far"]
        tx = g["xs"][target] if target is not None else x_far
        if phase == "idle":
            cv.create_text(x_start + 8 * s, ty + 10 * s, text="bus idle (t3.5)", anchor="nw", font=self.small,
                           fill="#59606b", tags=("dyn",))
        elif phase == "request":
            x = x_start + (x_far - x_start) * frac
            for i, xi in enumerate(g["xs"]):  # every port sees the request pass
                if xi <= x and (i, id(self.anim), id(self.live_cur)) not in self.anim_flashed:
                    self.anim_flashed.add((i, id(self.anim), id(self.live_cur)))
                    self.flash[i] = now + 0.25
            fc = self.av["fc"].get() if live_kind is None else ""
            self._packet(x, "#1a64d6", f"REQ → ID {unit}" + (f" FC{int(fc):02d}" if fc.isdigit() else ""))
        elif phase == "device":
            if target is not None:
                ms = self.anim.phases[[p[0] for p in self.anim.phases].index("device")][1] if self.anim else 0
                cv.create_text(tx, g["btop"] - 4 * s, text=f"thinking {ms:g} ms", anchor="s", font=self.small,
                               fill="#8a5a00", tags=("dyn",))
        elif phase == "response":
            x = tx + (x_start - tx) * frac
            self._packet(x, "#1e9e3e", f"ID {unit} → RSP")
        elif phase == "gap":
            self.master_flash = max(self.master_flash, now + 0.05)
        elif phase == "error" and target is not None:
            cv.create_text(tx, g["btop"] - 4 * s, text="no answer / error", anchor="s", font=self.small,
                           fill=COL["bad"], tags=("dyn",))
        if self.anim is not None and live_kind is None:
            self._timeline_marker(self.anim.bus_ms())

    def _packet(self, x, color, text):
        cv, s, ty = self.cv, self.s, self.geo["ty"]
        w = self.small.measure(text) + 12 * s
        h = 17 * s
        cv.create_rectangle(x - w / 2, ty - h / 2, x + w / 2, ty + h / 2, fill=color, outline="#ffffff",
                            tags=("dyn", "packet"))
        cv.create_text(x, ty, text=text, font=self.small, fill="#ffffff", tags=("dyn", "packet"))

    # ----- animation -----
    def speed(self):
        return 2.0 ** float(self.speed_v.get())

    def _speed_changed(self):
        sp = self.speed()
        self.l_speed.configure(text=f"{sp:.2g}x")
        self.ui["speed"] = f"{sp:.3g}"

    def refresh_devices(self):
        """Device choices for the animation: the map nodes that have a Slave ID."""
        labels = [BM_ALL] + [f"{n['name'] or 'device'} (ID {n['unit']})" for n in self.map["nodes"]
                             if n.get("unit") is not None]
        self.cb_dev.configure(values=labels)
        if self.dev_v.get() not in labels:
            self.dev_v.set(BM_ALL)
        want = self.ui.get("device", "")
        if want and want in labels and self.dev_v.get() == BM_ALL and want != BM_ALL:
            self.dev_v.set(want)

    def chosen_unit(self):
        lab = self.dev_v.get()
        m = re.search(r"\(ID (\d+)\)$", lab)
        return int(m.group(1)) if m and lab != BM_ALL else None

    def anim_inputs(self):
        """(fc, qty, delay ms) from the entries; raises ValueError with the field name."""
        fc = int(self.av["fc"].get())
        qty = int_in(self.av["qty"].get(), 1, 2000) if self.av["qty"].get().strip() else 1
        delay = mon._fnum(self.av["delay_ms"].get(), "Delay", 0, 60000)
        return fc, qty, delay

    def _anim_inputs_changed(self):
        for k in ("fc", "qty", "delay_ms", "parity", "stop"):
            self.ui[k] = self.av[k].get().strip()
        self.ui["device"] = self.dev_v.get()
        self.update_timeline()

    def link(self):
        import buscalc
        try:
            usb = float(str(self.app.cfg["devices"]["calc"].get("usb_ms", "0") or 0).replace(",", "."))
        except ValueError:
            usb = 0.0
        return buscalc.Link("rtu", int(self.map["baud"]), 8, self.ui["parity"], int(self.ui["stop"]),
                            self.app._gap_s() * 1000.0, 1000.0, usb)

    def units(self):
        return [n["unit"] for n in self.map["nodes"] if n.get("unit") is not None]

    def timeline_for(self, unit):
        fc, qty, delay = self.anim_inputs()
        return self.busmap.exchange_timeline(self.an, self.link(), unit, fc, qty, delay)

    def update_timeline(self):
        """Timeline bar for the exchange being shown (or the one that would be shown) + the cycle estimate."""
        self.tl = None
        units = self.units()
        unit = self.anim.unit if self.anim is not None else (self.chosen_unit() or (units[0] if units else None))
        if self.an is None or unit is None:
            self.l_tl.configure(text="Add devices with a Slave ID to see the request / response timeline.")
            self.l_cycle.configure(text="")
            self.draw_timeline()
            return None
        try:
            self.tl = self.timeline_for(unit)
        except ValueError as e:
            self.l_tl.configure(text=f"Timeline not available: {e}")
            self.l_cycle.configure(text="")
            self.draw_timeline()
            return None
        p = self.tl["parts"]
        fc, qty, _d = self.anim_inputs()
        name = next((n["name"] for n in self.map["nodes"] if n.get("unit") == unit), "")
        self.l_tl.configure(text=(
            f"{name} (ID {unit}), FC{fc:02d} x{qty} at {self.link().describe()}:   t3.5 {p['wait']:.1f} ms  |  request "
            f"{p['request']:.1f} ms  |  device delay {p['device']:.1f} ms  |  response {p['response']:.1f} ms  |  "
            f"gap/usb {p.get('usb', 0) + p['gap']:.1f} ms   =  {self.tl['total_ms']:.1f} ms   "
            f"(+ propagation {self.tl['propagation_ms'] * 1000:.2f} us)"))
        self._cycle_text()
        self.draw_timeline()
        return self.tl

    def cycle_estimate(self):
        """buscalc.estimate_cycle for the devices on the map: configured devices use their preset blocks / range
        and their calculator delay, others the FC / quantity / delay entered here."""
        import buscalc
        fc, qty, delay = self.anim_inputs()
        calc = self.app.cfg["devices"]["calc"]
        devs, from_cfg = [], 0
        for n in self.map["nodes"]:
            if n.get("unit") is None:
                continue
            reqs, d_ms = None, delay
            dv = self.app.dev_by_name(n["name"])
            if dv is not None:
                try:
                    if dv["preset"]:
                        reqs = buscalc.requests_for_preset(self.app.device_preset(dv)[0])
                    else:
                        reqs = [(int(dv["fc"]), int(dv["qty"]))]
                    from_cfg += 1
                except (OSError, ValueError, PresetError):
                    reqs = None
                cd = str((calc.get("dev") or {}).get(n["name"], {}).get("delay_ms", "")).strip()
                if cd:
                    try:
                        d_ms = float(cd.replace(",", "."))
                    except ValueError:
                        pass
            devs.append({"name": n["name"], "unit": n["unit"], "requests": reqs or [(fc, qty)], "delay_ms": d_ms})
        return buscalc.estimate_cycle(self.link(), devs), from_cfg

    def _cycle_text(self):
        try:
            est, from_cfg = self.cycle_estimate()
        except ValueError as e:
            self.l_cycle.configure(text=f"Cycle time: {e}")
            return None
        if not est["devices"]:
            self.l_cycle.configure(text="")
            return est
        src = (f"; {from_cfg} device(s) use their configured preset / range" if from_cfg else
               "; FC / quantity / delay as entered")
        self.l_cycle.configure(text=(
            f"Cycle time for all devices on the map ({len(est['devices'])}): {est['total_ms']:.1f} ms  "
            f"({est['cycles_per_s']:.1f} "
            f"cycles/s, {est['requests']} requests, line busy {est['utilisation_pct']:.0f} %)  at {est['link']}, gap "
            f"{self.app._gap_s() * 1000:g} ms{src}"))
        return est

    def draw_timeline(self):
        cv, s = self.tl_cv, self.s
        cv.delete("all")
        if not self.tl:
            return
        p = self.tl["parts"]
        segs = [("idle", p["wait"]), ("request", p["request"]), ("device", p["device"]),
                ("response", p["response"]), ("gap", p.get("usb", 0.0) + p["gap"])]
        tot = sum(ms for _k, ms in segs) or 1.0
        W = max(cv.winfo_width(), 200)
        x0, x1, y0, y1 = 4 * s, W - 4 * s, 3 * s, 27 * s
        x = x0
        for k, ms in segs:
            if ms <= 0:
                continue
            w = (x1 - x0) * ms / tot
            lab, col = BM_PHASE[k]
            cv.create_rectangle(x, y0, x + w, y1, fill=col, outline="#ffffff")
            txt = f"{lab} {ms:.1f} ms"
            fg = "#ffffff" if k in ("request", "response", "device") else "#2b2f36"
            if self.small.measure(txt) + 6 * s < w:
                cv.create_text(x + w / 2, (y0 + y1) / 2, text=txt, font=self.small, fill=fg)
            elif self.small.measure(f"{ms:.0f}") + 4 * s < w:
                cv.create_text(x + w / 2, (y0 + y1) / 2, text=f"{ms:.0f}", font=self.small, fill=fg)
            x += w
        self._tl_scale = (x0, x1, tot)

    def _timeline_marker(self, bus_ms):
        sc = getattr(self, "_tl_scale", None)
        if not sc or not self.tl:
            return
        x0, x1, tot = sc
        x = x0 + (x1 - x0) * min(bus_ms / tot, 1.0)
        self.tl_cv.delete("mark")
        self.tl_cv.create_line(x, 0, x, 30 * self.s, fill="#d93025", width=max(2, int(2 * self.s)), tags=("mark",))

    def toggle_play(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if self.an is None or not self.units():
            self._status("Nothing to animate: the map needs devices with a Slave ID", "warn")
            return False
        try:
            self.anim_inputs()
        except ValueError as e:
            self._status(f"Animation: {e}", "bad")
            return False
        self.playing = True
        self.b_play.configure(text="⏸ Pause")
        if self.anim is None or self.anim.done:
            self.start_exchange()
        self._last_tick = time.perf_counter()
        self._ensure_tick(self.TICK_MS)
        return True

    def pause(self):
        self.playing = False
        self.b_play.configure(text="▶ Play")

    def start_exchange(self):
        """Next request / response: the chosen device, or the next one in map order ('All in turn')."""
        units = self.units()
        unit = self.chosen_unit()
        if unit is None or unit not in units:
            unit = units[self.turn % len(units)]
            self.turn += 1
        tl = self.timeline_for(unit)
        name = next((n["name"] for n in self.map["nodes"] if n.get("unit") == unit), "")
        self.anim = mon.ExchangeAnim(mon.exchange_phases(tl), unit, name)
        self.anim_flashed = set()
        self.update_timeline()
        return self.anim

    def step(self, dt):
        """Advance the animation by dt real seconds (the tick calls it; tests call it directly)."""
        if self.live.get():
            self._live_step(time.time())
        elif self.playing and self.anim is not None:
            self.anim.advance(dt, self.speed())
            if self.anim.done:
                self.completed += 1
                self.last_seen = list(self.anim.seen)
                self.master_flash = time.perf_counter() + 0.3
                try:
                    self.start_exchange()
                except ValueError as e:
                    self.pause()
                    self._status(f"Animation stopped: {e}", "bad")
        self.update_dynamic()

    def _ensure_tick(self, ms):
        """The single after() job of this window: reschedule it sooner when needed."""
        if not self.alive():
            return
        if self._job is not None:
            if ms >= self.IDLE_MS:
                return
            self.w.after_cancel(self._job)
        self._job = self.w.after(ms, self._tick)

    def _tick(self):
        self._job = None
        if not self.alive():
            return
        now = time.perf_counter()
        dt, self._last_tick = min(now - self._last_tick, 0.1), now
        try:
            self.step(dt)
        except tk.TclError:
            return
        busy = self.playing or self.live.get() or self.master_flash > now or any(v > now for v in self.flash.values())
        self._job = self.w.after(self.TICK_MS if busy else self.IDLE_MS, self._tick)

    # ----- live follow -----
    def _live_changed(self):
        self.ui["live"] = bool(self.live.get())
        self.live_q, self.live_cur, self.live_seen = [], None, {}
        if self.live.get():
            self._status("Following the real traffic of the main connection and the Devices polling (devices on "
                         "the map only)", "info")
            self._last_tick = time.perf_counter()
            self._ensure_tick(self.TICK_MS)
        else:
            self._status("Live follow off", "info")
            self.update_dynamic()

    def live_sources(self):
        out = []
        if self.app.m is not None:
            out.append(self.app.m)
        out += [m for m in self.app.devpoll.masters().values() if m is not None]
        return [m for m in out if getattr(m, "traffic", None) is not None]

    def poll_live(self, now):
        """Read new entries of the traffic records (no change to the masters) and queue the ones for mapped
        devices; the queue is trimmed so the picture is never more than ~1 s behind."""
        units = set(self.units())
        for m in self.live_sources():
            dq = m.traffic
            snap = TrafficView._snap(dq)
            key = id(dq)
            since = self.live_seen.get(key)
            if since is None:  # first look at this master: start from now, old history is not replayed
                self.live_seen[key] = snap[-1]["t"] if snap else now
                continue
            self.live_q += mon.live_events(snap, since, units)
            if snap:
                self.live_seen[key] = max(since, snap[-1]["t"])
        self.live_q.sort(key=lambda e: e["t"])
        self.live_q = mon.trim_live_queue(self.live_q, now)

    def _live_step(self, now_wall):
        if now_wall >= self.live_next:
            self.live_next = now_wall + self.LIVE_POLL_S
            self.poll_live(now_wall)
        pc = time.perf_counter()
        if self.live_cur is not None and pc - self.live_cur["t0"] >= self.live_cur["dur"]:
            if self.live_cur["ev"]["kind"] == "rsp":
                self.master_flash = pc + 0.2
            self.live_cur = None
        if self.live_cur is None:
            self.live_q = mon.trim_live_queue(self.live_q, now_wall)
            if self.live_q:
                ev = self.live_q.pop(0)
                self.anim_flashed = set()
                self.live_cur = {"ev": ev, "t0": pc, "dur": self.LIVE_ITEM_S / self.speed()}


class AiShare:
    """Controller of the in-process MCP server (mbtool_mcp.BackgroundServer) that shares the main window's master.
    Used on the Tk thread only; starting / stopping the server runs on worker threads that report back through
    app.q ("call" messages). A generation token retires results of a start that was overtaken (stop, app close).
    The bearer token exists only here, in memory: it is never saved, logged or shown in the status bar."""
    TAIL_BYTES = 64 * 1024

    def __init__(self, app):
        self.app = app
        self.token = secrets.token_urlsafe(32)
        self.copy_ok = None      # the token value the owner already agreed to copy (warned once per token)
        self.lan_ok = False      # the owner confirmed network access in this session
        self.srv, self.state, self.gen = None, "off", 0   # state: off | starting | on | stopping
        self.error = None        # (field or None, text) of the last failed start
        self.opts, self.url = None, ""
        self.audit_path = AI_AUDIT_LOG
        self.pos, self.partial = None, b""
        self.events = collections.deque(maxlen=1000)  # (seq, parsed line, from an earlier session)
        self.seq = self.requests = self.failed = 0
        self.local_ips = None    # None = not detected yet
        self._ips_busy = False
        self._shown = False
        app.root.after(1000, self._tick)

    # ----- server -----
    def detect_ips(self, then=None):
        """Find this PC's IPv4 addresses on a worker thread (name resolution may block)."""
        if self.local_ips is not None or self._ips_busy:
            if then and self.local_ips is not None:
                then()
            return
        self._ips_busy = True

        def work():
            ips = mon.local_ipv4s()
            self.app.q.put(("call", None, lambda r, e: self._ips_found(r, then), ips, None))
        threading.Thread(target=work, daemon=True).start()

    def _ips_found(self, ips, then):
        self._ips_busy = False
        self.local_ips = list(ips or [])
        if then:
            then()
        self._refresh()

    def start(self, opts):
        if self.state != "off":
            return
        self.opts, self.error = mon.norm_ai_settings(opts), None
        self.state, self.gen = "starting", self.gen + 1
        gen, token, audit, opts = self.gen, self.token, self.audit_path, self.opts
        host, port, lan = mon.ai_bind_host(opts), int(opts["port"]), opts["bind"] == "lan"
        known = self.local_ips

        def work():
            res = err = None
            try:
                ips = known if known is not None else (mon.local_ipv4s() if lan else None)
                try:
                    size = os.path.getsize(audit)
                except OSError:
                    size = 0
                import mbtool_mcp
                srv = mbtool_mcp.BackgroundServer(host, port, token, audit, allow_connect=bool(opts["allow_connect"]),
                                                  allow_write=False, allowed_hosts=mon.ai_allowed_hosts(opts, ips or []))
                if self.gen != gen:  # stopped or app closed while starting: do not leave it listening
                    srv.stop()
                    return
                res = (srv, ips, size)
            except ImportError as e:
                err = (None, f"the AI server needs the 'mcp' and 'uvicorn' packages (pip install -r requirements.txt): {e}")
            except OSError as e:
                err = ("port", f"Port {port} is busy or not allowed - choose another port or close the program "
                               f"that uses it ({e})")
            except Exception as e:
                err = (None, str(e) or repr(e))
            self.app.q.put(("call", None, lambda r, e: self._started(gen, r, e), res, err))
        threading.Thread(target=work, daemon=True).start()
        self._refresh()

    def _started(self, gen, res, err):
        if gen != self.gen:
            if res:
                threading.Thread(target=res[0].stop, daemon=True).start()
            return
        if err:
            self.state, self.error = "off", err
            self.app._status(f"AI sharing not started: {err[1]}", "bad")
            self._win_status(f"Not started: {err[1]}", "bad")
            self._refresh()
            return
        srv, ips, size = res
        if ips is not None:
            self.local_ips = list(ips)
        self.srv, self.state = srv, "on"
        self.url = mon.ai_client_url(self.opts, self.local_ips or [])
        self.requests = self.failed = 0
        self.pos, self.partial = size, b""
        self.sync()
        self.app._status(f"AI sharing started: {self.url}  (read-only)", "ok")
        self._win_status(f"Sharing started - give the URL and token to your AI client ('Connect an AI')", "ok")
        self._refresh()

    def stop(self, then=None):
        if self.state not in ("on", "starting"):
            if then:
                then()
            return
        self.gen += 1
        srv, self.srv = self.srv, None
        own = self._release()
        if srv is None:  # still starting: the start worker stops its server when it sees the new generation
            self.state = "off"
            self._refresh()
            if then:
                then()
            return
        self.state = "stopping"

        def work():
            try:
                srv.stop()
            finally:
                if own is not None:
                    try:
                        own.close()
                    except Exception:
                        pass
                self.app.q.put(("call", None, lambda r, e: self._stopped(then), None, None))
        threading.Thread(target=work, daemon=True).start()
        self._refresh()

    def _stopped(self, then):
        self.state = "off"
        self._read_audit()  # the last lines written before the stop
        self.app._status("AI sharing stopped", "info")
        self._win_status("Sharing stopped - no AI client can connect now", "info")
        self._refresh()
        if then:
            then()

    def _win_status(self, text, kind):
        win = self.app.ai_win
        if win is not None and win.alive():
            win._status(text, kind)

    def restart(self):
        if self.state == "on":
            opts = self.opts
            self.stop(then=lambda: self.start(opts))

    def regenerate(self):
        """New token; while sharing, the server restarts so the old token stops working at once."""
        self.token = secrets.token_urlsafe(32)
        self.copy_ok = None
        if self.state == "on":
            self.restart()
        self._refresh()

    def shutdown(self):
        """App closing: stop the server now (bounded wait) so the port is free when mbtool has closed."""
        self.gen += 1
        srv, self.srv = self.srv, None
        own = self._release()
        self.state = "off"
        if srv is not None:
            srv.stop()
        if own is not None:
            try:
                own.close()
            except Exception:
                pass

    # ----- sharing the GUI's master -----
    def _release(self):
        """Detach whatever the MCP tools use. Returns a master the AI opened itself (to be closed), else None."""
        mm = sys.modules.get("mbtool_mcp")
        if mm is None:
            return None
        cur = mm._state.get("m")
        mm.detach_master()
        return None if cur is None or isinstance(cur, mon.AiSharedMaster) else cur

    def sync(self):
        """Point the MCP tools at the main window's current master (read-only wrapper), or at nothing.
        A device the AI connected to itself (only possible with 'Let the AI choose the device') is left alone."""
        mm = sys.modules.get("mbtool_mcp")
        if mm is None or self.state != "on":
            return
        cur, m = mm._state.get("m"), self.app.m
        ours = isinstance(cur, mon.AiSharedMaster)
        if cur is not None and not ours:
            return
        if m is None:
            if cur is not None:
                mm.detach_master()
                self._refresh()
        elif not ours or cur.target is not m or mm._state.get("url") != self.app.conn_url:
            mm.attach_master(mon.AiSharedMaster(m), self.app.conn_url)
            self._refresh()

    def device_text(self):
        """(text, style kind) describing what the AI can read right now."""
        if self.state != "on":
            return "", "info"
        mm = sys.modules.get("mbtool_mcp")
        cur = mm._state.get("m") if mm else None
        if cur is None:
            return "No device - connect in the main window (F5); the AI sees 'not connected' until then", "warn"
        if not isinstance(cur, mon.AiSharedMaster):
            return f"The AI connected to its own device: {mm._state.get('url')}", "warn"
        app = self.app
        if app.state == "on" and app.link_bad:
            return f"Device: {app.summary} - link lost, mbtool is reconnecting", "warn"
        return f"Device: {app.summary}  (the main window's connection, read-only)", "ok"

    # ----- audit log -----
    def _feed(self, data, old):
        buf = self.partial + data
        lines = buf.split(b"\n")
        self.partial = lines.pop()
        for raw in lines:
            e = mon.parse_audit_line(raw.decode("utf-8", "replace"))
            if e is None:
                continue
            self.seq += 1
            self.events.append((self.seq, e, old))
            if not old:
                if e["failed"]:
                    self.failed += 1
                elif e["event"] == "tool":
                    self.requests += 1

    def _read_audit(self):
        """New audit lines since the server started (a small local file, read every second)."""
        if self.pos is None:
            return
        try:
            size = os.path.getsize(self.audit_path)
        except OSError:
            return
        if size < self.pos:  # file replaced / truncated
            self.pos, self.partial = 0, b""
        if size == self.pos:
            return
        try:
            with open(self.audit_path, "rb") as f:
                f.seek(self.pos)
                data = f.read(min(size - self.pos, 1 << 20))
        except OSError:
            return
        self.pos += len(data)
        self._feed(data, old=False)

    def load_tail(self):
        """Earlier lines of the audit log (shown grey) when nothing was read yet in this session."""
        if self.seq:
            return
        try:
            end = self.pos if self.pos is not None else os.path.getsize(self.audit_path)
            with open(self.audit_path, "rb") as f:
                start = max(0, end - self.TAIL_BYTES)
                f.seek(start)
                data = f.read(end - start)
        except OSError:
            return
        if start:
            data = data.split(b"\n", 1)[-1]
        keep, self.partial = self.partial, b""
        self._feed(data if data.endswith(b"\n") else data + b"\n", old=True)
        self.partial = keep

    # ----- indicator -----
    def indicator_text(self):
        if self.state != "on":
            return ""
        n = self.requests
        return f"AI: sharing {mon.ai_share_label(self.opts)}, {n} request{'' if n == 1 else 's'}"

    def _indicator(self):
        lab = self.app.l_ai
        text = self.indicator_text()
        if text:
            lab.configure(text=text)
            if not self._shown:
                lab.grid()
                self._shown = True
        elif self._shown:
            lab.grid_remove()
            self._shown = False

    def _refresh(self):
        self._indicator()
        win = self.app.ai_win
        if win is not None and win.alive():
            win.refresh()

    def _tick(self):
        try:
            if self.state == "on":
                self._read_audit()
                self.sync()
            self._indicator()
        except Exception:
            import traceback
            traceback.print_exc()
        try:
            self.app.root.after(1000, self._tick)
        except tk.TclError:
            pass


class AiAssistWindow:
    """F12: let an AI analyse the device - tab 1 shares the live connection with an MCP client (read-only, bearer
    token), tab 2 builds a diagnostic report to paste into any AI chat."""
    ACT_COLS = (("time", "Time", 70), ("ip", "Client", 110), ("what", "Tool / event", 170), ("args", "Arguments", 300))
    SNIPPETS = (("claude", "Claude Code"), ("json", "JSON config"), ("text", "Other AI clients"))

    def __init__(self, app):
        self.app, s = app, app.s
        self.s, self.ai = s, app.ai
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - AI Assist")
        help_key(w, "ai")
        w.geometry(f"{int(1000 * s)}x{int(700 * s)}")
        w.minsize(int(780 * s), int(540 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.mono = tkfont.Font(family="Consolas", size=10)
        sb = ttk.Frame(w, padding=(8, 2, 8, 6))
        sb.pack(fill="x", side="bottom")
        self.l_msg = ttk.Label(sb, style="SInfo.TLabel")
        self.l_msg.pack(side="left")
        self.nb = ttk.Notebook(w)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 2))
        self.t1 = ttk.Frame(self.nb, padding=(8, 6, 8, 4))
        self.t2 = ttk.Frame(self.nb, padding=(8, 6, 8, 4))
        self.nb.add(self.t1, text="Share live connection with an AI")
        self.nb.add(self.t2, text="Ask any AI (copy a report)")
        self.last_seq, self.snip_key, self.shown_token = 0, None, False
        self.report_md, self.report_setup, self.rep_gen = None, {}, 0
        self._build_share(self.t1)
        self._build_report(self.t2)
        w.bind("<F12>", lambda e: self.close())
        self.b_help = add_help_button(app, w, "ai")
        self.reload()
        self.ai.detect_ips(then=self._fill_addrs)
        self.ai.load_tail()
        self.refresh()
        self._status("Sharing lets an MCP-capable AI read this device; the report tab works with any AI chat.")
        self._tick()

    # ----- tab 1: share -----
    def _build_share(self, f):
        s = self.s
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.b_run = ttk.Button(top, text="Start sharing", width=15, command=self.toggle)
        self.b_run.pack(side="left")
        Tooltip(self.b_run, "Start / stop the MCP server inside mbtool.\nIt stops by itself when mbtool closes.")
        self.lamp = Lamp(top, s)
        self.lamp.c.pack(side="left", padx=(10, 6))
        self.l_state = ttk.Label(top, text="Stopped", style="Conn.TLabel")
        self.l_state.pack(side="left")
        self.l_req = ttk.Label(top, text="", style="Info.TLabel")
        self.l_req.pack(side="right")
        self.l_dev = ttk.Label(f, text="", style="Info.TLabel")
        self.l_dev.pack(fill="x", pady=(4, 0))
        body = ttk.Frame(f)
        body.pack(fill="x", pady=(6, 0))
        body.columnconfigure(0, weight=1, uniform="ai")
        body.columnconfigure(1, weight=1, uniform="ai")
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        wrap = int(430 * s)
        self.hints = {}
        # who may connect
        lf = ttk.LabelFrame(left, text="Who may connect", padding=(10, 4, 10, 6))
        lf.pack(fill="x")
        self.v_bind = tk.StringVar(value="local")
        self.r_local = ttk.Radiobutton(lf, text="Only this PC (127.0.0.1)", value="local", variable=self.v_bind,
                                       command=self._bind_clicked)
        self.r_local.grid(row=0, column=0, columnspan=3, sticky="w")
        self.r_lan = ttk.Radiobutton(lf, text="Other computers on my network...", value="lan", variable=self.v_bind,
                                     command=self._bind_clicked)
        self.r_lan.grid(row=1, column=0, columnspan=3, sticky="w")
        Tooltip(self.r_lan, "Plain HTTP: use it on a trusted network or through a VPN such as Tailscale.\n"
                            "You are asked to confirm.")
        self.v_addr, self.v_host, self.v_port = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.cb_addr = ttk.Combobox(lf, textvariable=self.v_addr, width=30, state="readonly")
        self.e_host = ttk.Entry(lf, textvariable=self.v_host, width=32)
        self.e_port = ttk.Entry(lf, textvariable=self.v_port, width=8)
        for r, (k, lab, wdg, tip) in enumerate((
                ("lan_addr", "Bind address:", self.cb_addr,
                 "0.0.0.0 listens on every network adapter of this PC;\nan address listens on that adapter only."),
                ("host_name", "Host name clients use:", self.e_host,
                 "Optional. The name other computers type in the URL, e.g. mypc.tailnet.ts.net (Tailscale).\n"
                 "Requests that use an unknown host name are refused (DNS-rebinding protection)."),
                ("port", "Port:", self.e_port, "TCP port of the MCP server (default 8765)."))):
            ttk.Label(lf, text=lab).grid(row=2 + 2 * r, column=0, sticky="w", padx=(18 if k != "port" else 0, 8),
                                         pady=(4, 0))
            wdg.grid(row=2 + 2 * r, column=1, sticky="w", pady=(4, 0))
            Tooltip(wdg, tip)
            h = ttk.Label(lf, text=" ", style="Hint.TLabel", wraplength=int(300 * s))
            h.grid(row=3 + 2 * r, column=1, sticky="w")
            self.hints[k] = h
        self.l_addr_info = ttk.Label(lf, text="", style="Info.TLabel", wraplength=wrap)
        self.l_addr_info.grid(row=8, column=0, columnspan=3, sticky="w", pady=(2, 0))
        for v in (self.v_addr, self.v_host, self.v_port):
            v.trace_add("write", lambda *_: self.validate())
        # access
        af = ttk.LabelFrame(left, text="Access", padding=(10, 4, 10, 6))
        af.pack(fill="x", pady=(6, 0))
        tr = ttk.Frame(af)
        tr.pack(fill="x")
        ttk.Label(tr, text="Bearer token:").pack(side="left", padx=(0, 6))
        self.v_tok = tk.StringVar()
        self.e_tok = ttk.Entry(tr, textvariable=self.v_tok, show="•", width=26, state="readonly")
        self.e_tok.pack(side="left", fill="x", expand=True)
        br = ttk.Frame(af)
        br.pack(fill="x", pady=(4, 0))
        self.b_show = ttk.Button(br, text="Show", width=7, command=self.toggle_show)
        self.b_show.pack(side="left")
        self.b_tcopy = ttk.Button(br, text="Copy", width=7, command=self.copy_token)
        self.b_tcopy.pack(side="left", padx=(4, 0))
        self.b_regen = ttk.Button(br, text="Regenerate", command=self.regenerate)
        self.b_regen.pack(side="left", padx=(4, 0))
        Tooltip(self.b_regen, "Make a new token. Everyone using the old one is locked out\n"
                              "(sharing restarts when it runs).")
        ttk.Label(af, text="Made for this session and kept only in memory - never saved to settings, workspace "
                           "or log files.", style="Info.TLabel", wraplength=wrap).pack(fill="x", pady=(2, 0))
        self.v_choose = tk.BooleanVar(value=False)
        self.cb_choose = ttk.Checkbutton(af, text="Let the AI choose the device", variable=self.v_choose,
                                         command=self.validate)
        self.cb_choose.pack(anchor="w", pady=(6, 0))
        ttk.Label(af, text="Off (recommended): the AI reads only the device this window is connected to; it cannot "
                           "connect elsewhere, disconnect, or open and scan serial ports. On: it may connect to any TCP "
                           "address or COM port this PC can reach.", style="Info.TLabel",
                  wraplength=wrap).pack(fill="x")
        ttk.Label(af, text="Writes: disabled for AI (read-only)", style="Conn.TLabel").pack(anchor="w", pady=(6, 0))
        self.setting_widgets = [self.r_local, self.r_lan, self.cb_addr, self.e_host, self.e_port, self.cb_choose]
        # connect an AI
        cf = ttk.LabelFrame(right, text="Connect an AI", padding=(10, 4, 10, 6))
        cf.pack(fill="both", expand=True)
        ttk.Label(cf, text="⚠ These snippets contain the secret token: anyone who has it can read your device "
                           "while sharing is on. Give it only to people and AI clients you trust.",
                  style="Warn.TLabel", wraplength=wrap).pack(fill="x")
        sr = ttk.Frame(cf)
        sr.pack(fill="x", pady=(6, 2))
        self.v_snip = tk.StringVar(value="claude")
        for key, lab in self.SNIPPETS:
            ttk.Radiobutton(sr, text=lab, value=key, variable=self.v_snip,
                            command=self.refresh_snippet).pack(side="left", padx=(0, 10))
        tf = ttk.Frame(cf)
        tf.pack(fill="both", expand=True)
        self.t_snip = tk.Text(tf, wrap="word", font=self.mono, height=8, width=40, relief="flat", bg=COL["alt"],
                              padx=int(6 * s), pady=int(4 * s), highlightthickness=1,
                              highlightbackground=COL["line"])
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.t_snip.yview)
        self.t_snip.configure(yscrollcommand=vs.set, state="disabled")
        self.t_snip.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        cr = ttk.Frame(cf)
        cr.pack(fill="x", pady=(4, 0))
        self.b_scopy = ttk.Button(cr, text="Copy (contains token)", command=self.copy_snippet)
        self.b_scopy.pack(side="left")
        self.l_snip = ttk.Label(cr, text="", style="Info.TLabel", wraplength=int(260 * s))
        self.l_snip.pack(side="left", padx=(8, 0))
        # activity
        act = ttk.LabelFrame(f, text="Activity (audit log): tool calls and failed logins", padding=(8, 4, 8, 4))
        act.pack(fill="both", expand=True, pady=(6, 0))
        tf2 = ttk.Frame(act)
        tf2.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf2, columns=[c[0] for c in self.ACT_COLS], show="headings", height=5,
                                 style=tree_style(self.app.root, s))
        for cid, lab, wd in self.ACT_COLS:
            self.tree.heading(cid, text=lab, anchor="w")
            self.tree.column(cid, width=int(wd * s), stretch=cid == "args", minwidth=int(40 * s))
        self.tree.tag_configure("failed", foreground="#c5221f", background="#fdecea")
        self.tree.tag_configure("old", foreground=COL["stale"])
        vs2 = ttk.Scrollbar(tf2, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs2.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs2.grid(row=0, column=1, sticky="ns")
        tf2.rowconfigure(0, weight=1)
        tf2.columnconfigure(0, weight=1)
        self.l_audit = ttk.Label(act, text=f"Audit log file: {self.ai.audit_path}   (grey = before this session)",
                                 style="Info.TLabel")
        self.l_audit.pack(fill="x", pady=(2, 0))

    def _fill_addrs(self):
        if not self.alive():
            return
        ips = self.ai.local_ips or []
        vals = ["0.0.0.0  (all network adapters)"] + ips
        cur = self.v_addr.get().split(" ")[0]
        if cur and cur != "0.0.0.0" and cur not in ips:
            vals.append(cur)
        self.cb_addr.configure(values=vals)
        self.l_addr_info.configure(text=("Addresses of this PC: " + ", ".join(ips)) if ips else
                                   "No network address found (only this PC can connect).")
        self.refresh_snippet()

    def reload(self):
        """Fields from app.cfg['ai'] (used at the next start)."""
        c = mon.norm_ai_settings(self.app.cfg.get("ai"))
        self.v_bind.set(c["bind"])
        self.v_addr.set("0.0.0.0  (all network adapters)" if c["lan_addr"] == "0.0.0.0" else c["lan_addr"])
        self.v_host.set(c["host_name"])
        self.v_port.set(c["port"])
        self.v_choose.set(c["allow_connect"])
        self.validate()

    def collect(self):
        return {"bind": self.v_bind.get(), "lan_addr": (self.v_addr.get().split(" ")[0] or "0.0.0.0"),
                "port": self.v_port.get().strip(), "host_name": self.v_host.get().strip(),
                "allow_connect": bool(self.v_choose.get())}

    def validate(self):
        opts = self.collect()
        errs = mon.ai_errors(opts)
        err = self.ai.error
        if err and err[0] == "port" and "port" not in errs and self.ai.state == "off" \
                and self.ai.opts and opts["port"] == self.ai.opts["port"]:
            errs["port"] = err[1]  # inline: busy port of the last start
        set_hints(self.hints, errs)
        pending = opts["bind"] == "lan" and not self.ai.lan_ok and self.app.cfg["ai"].get("bind") != "lan"
        if not mon.ai_errors(opts) and not pending:  # network access is stored only after the confirmation
            self.app.cfg["ai"] = mon.norm_ai_settings(opts)
        self.refresh_snippet()
        return opts, errs

    def _bind_clicked(self):
        if self.v_bind.get() == "lan" and not self.ai.lan_ok:
            self.confirm_lan(on_yes=None)
        self.validate()
        self.refresh()

    def confirm_lan(self, on_yes=None):
        ips = self.ai.local_ips
        port = self.v_port.get().strip() or "8765"
        where = (", ".join(ips) if ips else "no network address detected") if ips is not None else "being detected"
        text = ("Let other computers connect to the AI server?\n\n"
                f"The server will listen on a network address, so any computer that can reach this PC on port {port} "
                "can try to connect. Only clients with the bearer token are let in, but the traffic is plain HTTP: "
                "the token and your device data cross the network unencrypted and can be read by anyone on it - "
                "unless you use a VPN such as Tailscale.\n\n"
                "Bind address choices: 0.0.0.0 (every network adapter of this PC) or one address of this PC "
                f"({where}). If clients use a host name (e.g. mypc.tailnet.ts.net), type it in 'Host name clients "
                "use'.\n\nWrites stay disabled for the AI.")

        def yes():
            self.ai.lan_ok = True
            self.v_bind.set("lan")
            self.validate()
            self.refresh()
            self._status("Network access allowed for this session - choose the bind address and start sharing", "warn")
            if on_yes:
                on_yes()

        def no():
            self.v_bind.set("local")
            self.validate()
            self.refresh()
            self._status("Only this PC (127.0.0.1) may connect", "info")
        self.lan_dlg = ConfirmDialog(self.app, "Share with other computers?", text, "Allow network access", yes, no,
                                     parent=self.w)
        return self.lan_dlg

    def toggle(self):
        st = self.ai.state
        if st == "on":
            self.ai.stop()
            self._status("Stopping AI sharing...", "info")
            return
        if st != "off":
            return
        opts, errs = self.validate()
        errs = mon.ai_errors(opts)
        if errs:
            self._status("Fix the fields marked in red", "bad")
            return
        if opts["bind"] == "lan" and not self.ai.lan_ok:
            self.confirm_lan(on_yes=self.toggle)
            return
        self.app.cfg["ai"] = mon.norm_ai_settings(opts)
        save_settings(self.app.cfg)
        self.ai.start(opts)
        self._status(f"Starting the AI server on {mon.ai_share_label(opts)}...", "info")
        self.refresh()

    # ----- token / snippets -----
    def toggle_show(self):
        self.shown_token = not self.shown_token
        self.e_tok.configure(show="" if self.shown_token else "•")
        self.b_show.configure(text="Hide" if self.shown_token else "Show")
        self.refresh_snippet(force=True)

    def regenerate(self):
        was = self.ai.state == "on"
        self.ai.regenerate()
        self.v_tok.set(self.ai.token)
        self.refresh_snippet(force=True)
        self._status("New token made - the old one no longer works" + ("; sharing restarts" if was else ""), "ok")

    def _copy_secret(self, text, what):
        def do():
            self.ai.copy_ok = self.ai.token
            self.w.clipboard_clear()
            self.w.clipboard_append(text)
            self._status(f"{what} copied to the clipboard - it contains the secret token", "warn")
        if self.ai.copy_ok == self.ai.token:
            do()
            return None
        self.copy_dlg = ConfirmDialog(
            self.app, "Copy the secret token?",
            f"The {what.lower()} contains the bearer token. Anyone who gets it can read your Modbus device while "
            "sharing is on (writes stay disabled).\n\nPaste it only into your own AI client or send it to people "
            "you trust, over a private channel. Regenerate the token to lock them out again.\n\n"
            "You are asked once per token.", "Copy", do, parent=self.w)
        return self.copy_dlg

    def copy_token(self):
        return self._copy_secret(self.ai.token, "Token")

    def snippet_url(self):
        if self.ai.state == "on":
            return self.ai.url
        return mon.ai_client_url(self.collect(), self.ai.local_ips or [])

    def copy_snippet(self):
        key = self.v_snip.get()
        text = mon.ai_snippets(self.snippet_url(), self.ai.token)[key]
        return self._copy_secret(text, dict(self.SNIPPETS)[key] + " snippet")

    def refresh_snippet(self, force=False):
        if not hasattr(self, "t_snip"):
            return
        url, key = self.snippet_url(), self.v_snip.get()
        k = (url, key, self.ai.token, self.shown_token, self.ai.state)
        if k == self.snip_key and not force:
            return
        self.snip_key = k
        tok = self.ai.token if self.shown_token else mon.mask_token(self.ai.token)
        self.t_snip.configure(state="normal")
        self.t_snip.delete("1.0", "end")
        self.t_snip.insert("end", mon.ai_snippets(url, tok)[key])
        self.t_snip.configure(state="disabled")
        self.l_snip.configure(text=("Token hidden above (Show to see it); Copy puts the real token on the clipboard."
                                    if not self.shown_token else "Token visible - mind who sees your screen.")
                              + ("" if self.ai.state == "on" else "  The URL works once sharing is started."))

    # ----- periodic refresh -----
    def refresh(self):
        ai, app = self.ai, self.app
        st = ai.state
        self.lamp.set({"off": COL["bad"], "starting": COL["warn"], "stopping": COL["warn"], "on": COL["ok"]}[st])
        self.l_state.configure(text={"off": "Stopped", "starting": "Starting...", "stopping": "Stopping...",
                                     "on": f"Sharing on {ai.url}"}[st])
        self.b_run.configure(text="Stop sharing" if st in ("on", "stopping") else "Start sharing",
                             state="normal" if st in ("on", "off") else "disabled")
        self.b_regen.configure(state="normal" if st in ("on", "off") else "disabled")
        text, kind = ai.device_text()
        if st == "off":
            text, kind = ("Not sharing. Start to let an AI client read the device this window is connected to "
                          f"(currently: {app.summary if app.state == 'on' else 'not connected'})."), "info"
        self.l_dev.configure(text=text, style={"ok": "SOk.TLabel", "warn": "SWarn.TLabel"}.get(kind, "Info.TLabel"))
        self.l_req.configure(text=f"Requests: {ai.requests}    Failed logins: {ai.failed}" if st == "on" else "")
        off = st == "off"
        for wdg in self.setting_widgets:
            wdg.configure(state=("readonly" if wdg is self.cb_addr else "normal") if off else "disabled")
        if off and self.v_bind.get() != "lan":
            for wdg in (self.cb_addr, self.e_host):
                wdg.configure(state="disabled")
        if self.v_tok.get() != ai.token:
            self.v_tok.set(ai.token)
        self.refresh_snippet()
        new = [ev for ev in ai.events if ev[0] > self.last_seq]
        for seq, e, old in new:
            what = "LOGIN FAILED" if e["failed"] else (e["name"] if e["event"] == "tool" else e["event"])
            args = e["args"] if e["event"] == "tool" else e["name"]
            self.tree.insert("", "end", values=(e["time"], e["ip"], what, args),
                             tags=("failed",) if e["failed"] else (("old",) if old else ()))
            self.last_seq = seq
        if new:
            kids = self.tree.get_children()
            if len(kids) > 1000:
                self.tree.delete(*kids[:len(kids) - 1000])
                kids = self.tree.get_children()
            self.tree.see(kids[-1])
        if ai.error and st == "off":
            self.validate()
        self._report_state()

    def _tick(self):
        if not self.alive():
            return
        try:
            self.refresh()
        finally:
            self.w.after(1000, self._tick)

    # ----- tab 2: report -----
    def _build_report(self, f):
        s = self.s
        ttk.Label(f, text="For AI chat apps without MCP: create a report from the live device (a few samples of the "
                          "poll range, identification, preset values, recent traffic, error checklist) and paste it "
                          "into the chat.", style="Info.TLabel", wraplength=int(900 * s)).pack(fill="x")
        ttk.Label(f, text="⚠ Privacy: the report contains register values and the request / reply traffic of your "
                          "device. Review it before pasting it into a cloud AI service.", style="Warn.TLabel",
                  wraplength=int(900 * s)).pack(fill="x", pady=(4, 0))
        r1 = ttk.Frame(f)
        r1.pack(fill="x", pady=(8, 0))
        self.b_rep = ttk.Button(r1, text="Create diagnostic report", command=self.make_report)
        self.b_rep.pack(side="left")
        self.pb = ttk.Progressbar(r1, mode="indeterminate", length=int(120 * s))
        self.l_rep = ttk.Label(r1, text="", style="Info.TLabel")
        self.l_rep.pack(side="left", padx=(10, 0))
        r2 = ttk.Frame(f)
        r2.pack(fill="x", pady=(8, 0))
        ttk.Label(r2, text="My question (optional):").pack(side="left", padx=(0, 6))
        self.v_q = tk.StringVar()
        eq = ttk.Entry(r2, textvariable=self.v_q)
        eq.pack(side="left", fill="x", expand=True)
        Tooltip(eq, "Added to 'Copy report with question prompt', e.g.\n"
                    "\"Why is the voltage 10 times too high?\"")
        bf = ttk.Frame(f)
        bf.pack(fill="x", side="bottom", pady=(6, 0))
        self.b_copy = ttk.Button(bf, text="Copy report", command=self.copy_report)
        self.b_copy.pack(side="left")
        self.b_copyq = ttk.Button(bf, text="Copy report with question prompt", command=self.copy_prompt)
        self.b_copyq.pack(side="left", padx=(4, 0))
        Tooltip(self.b_copyq, "Report + your setup + a request to answer as\n"
                              "observed / inferred / unknown / next check (+ your question)")
        self.b_save = ttk.Button(bf, text="Save as .md...", command=self.save_report)
        self.b_save.pack(side="left", padx=(4, 0))
        self.l_rmsg = ttk.Label(bf, text="", style="SInfo.TLabel")
        self.l_rmsg.pack(side="left", padx=(10, 0))
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True, pady=(6, 0))
        self.t_rep = tk.Text(tf, wrap="word", font=self.mono, relief="flat", bg=COL["bg"], padx=int(8 * s),
                             pady=int(6 * s), highlightthickness=1, highlightbackground=COL["line"], height=10)
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.t_rep.yview)
        self.t_rep.configure(yscrollcommand=vs.set)
        self.t_rep.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.t_rep.tag_configure("err", foreground=COL["bad"])
        self.t_rep.bind("<Control-a>", lambda e: (self.t_rep.tag_add("sel", "1.0", "end"), "break")[1])
        self._set_report_text("No report yet. Connect in the main window, then press 'Create diagnostic report'.")
        self.busy = False

    def _set_report_text(self, text, tag=()):
        self.t_rep.configure(state="normal")
        self.t_rep.delete("1.0", "end")
        self.t_rep.insert("end", text, tag)
        self.t_rep.configure(state="disabled")

    def _report_plan(self):
        """(request tuple, setup dict) for the report from the main window, or (None, reason)."""
        app = self.app
        if app.m is None or app.state != "on":
            return None, "Connect first (F5 in the main window) - the report is read from the live device."
        p = app.p
        if p is None:
            return None, "Fix the poll definition in the main window first."
        pr = app.preset["obj"] if app.preset else None
        unit, fc, addr, qty = mon.report_request(p, pr)
        setup = {"Connection": app.summary, "Slave ID": unit,
                 "Request sampled": f"{FUNCS.get(fc, f'FC{fc:02d}')}, protocol address {addr}, quantity {qty}",
                 "Address base used in mbtool": BASE_CHOICES[mon.norm_base(p.get('plc', 0))],
                 "Display format": None if pr or fc in (1, 2) else p.get("fmt"),
                 "Preset": (f"{pr.name} ({'verified' if pr.verified else 'UNVERIFIED - written from documents'})"
                            if pr else "none (plain register poll)")}
        return (unit, fc, addr, qty, pr), setup

    def _report_state(self):
        if self.busy:
            return
        plan, why = self._report_plan()
        self.b_rep.configure(state="normal" if plan else "disabled")
        if plan:
            unit, fc, addr, qty, pr = plan
            self.l_rep.configure(text=f"Will read Slave ID {unit}, FC{fc:02d} address {addr} x{qty}"
                                      + (f", preset '{pr.name}'" if pr else ""), style="Info.TLabel")
        else:
            self.l_rep.configure(text=why, style="Warn.TLabel")
        has = self.report_md is not None
        for b in (self.b_copy, self.b_copyq, self.b_save):
            b.configure(state="normal" if has else "disabled")

    def make_report(self):
        plan, setup = self._report_plan()
        if plan is None:
            self.l_rep.configure(text=setup, style="Warn.TLabel")
            return False
        unit, fc, addr, qty, pr = plan
        master, url = self.app.m, self.app.conn_url
        self.rep_gen += 1
        gen = self.rep_gen
        self.busy = True
        self.b_rep.configure(state="disabled")
        self.pb.pack(side="left", padx=(10, 0), before=self.l_rep)
        self.pb.start(12)
        self.l_rep.configure(text="Sampling the device (3 reads), identification, preset, traffic...",
                             style="Info.TLabel")
        self._set_report_text("Creating the report...")

        def work():
            try:
                import ai_report
                rep = ai_report.build_report(master, unit, fc, addr, qty, samples=3, preset=pr, url=url)
                res, err = ai_report.to_markdown(rep), None
            except Exception as e:
                res, err = None, str(e) or repr(e)
            self.app.q.put(("call", None, lambda r, e: self._report_done(gen, setup, r, e), res, err))
        threading.Thread(target=work, daemon=True).start()
        return True

    def _report_done(self, gen, setup, md, err):
        if gen != self.rep_gen or not self.alive():
            return
        self.busy = False
        self.pb.stop()
        self.pb.pack_forget()
        if err:
            self._set_report_text(f"The report could not be created: {err}", ("err",))
            self.l_rmsg.configure(text="Report failed", style="SBad.TLabel")
        else:
            self.report_md, self.report_setup = md, setup
            self._set_report_text(md)
            self.l_rmsg.configure(text=f"Report created {time.strftime('%H:%M:%S')} - review it before sharing",
                                  style="SOk.TLabel")
        self._report_state()

    def prompt_text(self):
        return mon.ai_prompt(self.report_setup, self.report_md or "", self.v_q.get())

    def _clip(self, text, what):
        self.w.clipboard_clear()
        self.w.clipboard_append(text)
        self.l_rmsg.configure(text=f"{what} copied ({len(text)} characters)", style="SOk.TLabel")

    def copy_report(self):
        if self.report_md:
            self._clip(self.report_md, "Report")

    def copy_prompt(self):
        if self.report_md:
            self._clip(self.prompt_text(), "Report with question prompt")

    def save_report(self, path=None):
        if not self.report_md:
            return None
        path = path or filedialog.asksaveasfilename(
            parent=self.w, title="Save diagnostic report", defaultextension=".md",
            initialfile=time.strftime("mbtool_report_%Y%m%d_%H%M%S.md"),
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")])
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.report_md + "\n")
        except OSError as e:
            self.l_rmsg.configure(text=f"Save failed: {e}", style="SBad.TLabel")
            return None
        self.l_rmsg.configure(text=f"Saved {path}", style="SOk.TLabel")
        return path

    # ----- window -----
    def _status(self, text, kind="info"):
        self.l_msg.configure(text=text, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel",
                                               "warn": "SWarn.TLabel"}.get(kind, "SInfo.TLabel"))

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        """Closing the window does not stop sharing (the amber indicator in the main window stays)."""
        if self.alive():
            self.validate()  # valid, confirmed options go to app.cfg['ai']
            save_settings(self.app.cfg)
            self.w.destroy()


# ---------------- built-in manual (F1) ----------------

def help_key(widget, key):
    """Context key (helpdocs.CONTEXT_TOPICS) of a widget: F1 on it or on any child opens that topic.
    key may be a callable returning the key (e.g. TCP vs serial in the setup dialog)."""
    widget._help_key = key
    return widget


def add_help_button(app, toplevel, key, parent=None, **pack):
    """Small '?' button that opens the matching manual topic. Packed into parent when given, else placed in the
    top-right corner of the window. The window also gets the key for F1."""
    help_key(toplevel, key)
    b = ttk.Button(parent or toplevel, text="?", width=3,
                   command=lambda: app.open_help(key=key() if callable(key) else key))
    Tooltip(b, "Help for this window   (F1)")
    if parent is not None:
        b.pack(**(pack or {"side": "right"}))
    else:
        b.place(relx=1.0, x=-int(6 * app.s), y=int(5 * app.s), anchor="ne")
    return b


def widget_help_key(widget):
    """Walk from the focused widget up to its window and return the first context key found."""
    w = widget
    while w is not None:
        k = getattr(w, "_help_key", None)
        if k is not None:
            return k() if callable(k) else k
        w = getattr(w, "master", None)
    return None


class HelpWindow:
    """F1: the built-in manual (helpdocs + help/*.md). Search, topic tree (by category or A-Z), native rendering of
    the Markdown subset in a Text widget with clickable links, back / forward history, related topics."""
    SEARCH_DELAY_MS = 60

    def __init__(self, app):
        import helpdocs
        self.app, s = app, app.s
        self.s, self.hd = s, helpdocs
        self.topics = helpdocs.load()
        self.by_id = {t.id: t for t in self.topics}
        cfg = app.cfg["help"]
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Manual")
        try:
            w.geometry(cfg.get("geometry") or f"{int(1060 * s)}x{int(700 * s)}")
        except tk.TclError:
            w.geometry(f"{int(1060 * s)}x{int(700 * s)}")
        w.minsize(int(640 * s), int(380 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        self._fonts()
        self.hist = mon.HelpHistory()
        self.cur, self.query_toks, self._sel_guard, self._job, self._wjob = None, [], False, None, None
        self.last_width, self.search_ms, self.cells = 0, [], []
        self.pw = ttk.PanedWindow(w, orient="horizontal")
        self.pw.pack(fill="both", expand=True, padx=8, pady=8)
        self._build_left()
        self._build_right()
        self.pw.add(self.left, weight=0)
        self.pw.add(self.right, weight=1)
        w.bind("<Control-f>", lambda e: (self.focus_search(), "break")[1])
        w.bind("<Alt-Left>", lambda e: (self.back(), "break")[1])
        w.bind("<Alt-Right>", lambda e: (self.forward(), "break")[1])
        self.fill_list()
        self._sash = int(cfg.get("sash") or 0) or int(320 * s)
        self.w.after(60, self._set_sash)  # after the window is mapped, else the sash position is lost
        first = cfg.get("topic") if cfg.get("topic") in self.by_id else "quickstart"
        self.open(first if first in self.by_id else self.topics[0].id)

    def _set_sash(self):
        try:
            if not self.pw.winfo_ismapped():
                self.w.after(60, self._set_sash)
                return
            pos = max(int(180 * self.s), min(self._sash, self.pw.winfo_width() - int(300 * self.s)))
            self.pw.sashpos(0, pos)
        except tk.TclError:
            pass

    def _fonts(self):
        base = tkfont.nametofont("TkDefaultFont")
        fam = base.cget("family")
        if "Leelawadee UI" in tkfont.families(self.w):  # Thai and Latin from one font: no fallback gaps
            fam = "Leelawadee UI"
        size = abs(base.cget("size")) + 1
        self.f_body = tkfont.Font(family=fam, size=size)
        self.f_bold = tkfont.Font(family=fam, size=size, weight="bold")
        self.f_h1 = tkfont.Font(family=fam, size=size + 5, weight="bold")
        self.f_small = tkfont.Font(family=fam, size=max(size - 1, 7))
        self.f_code = tkfont.Font(family="Consolas", size=size)

    # ----- left: search + list -----
    def _build_left(self):
        s = self.s
        self.left = f = ttk.Frame(self.pw, width=int(300 * s))
        lf = ttk.Frame(f)
        lf.pack(fill="x", pady=(0, 6))
        ttk.Label(lf, text="Language:").pack(side="left")
        self.v_lang = tk.StringVar(value=self.hd.current_lang())
        for code, name in (("th", "ไทย"), ("en", "English")):
            ttk.Radiobutton(lf, text=name, value=code, variable=self.v_lang,
                            command=lambda: self.app.set_help_lang(self.v_lang.get())).pack(side="left", padx=(6, 0))
        ttk.Label(f, text="Search (Ctrl+F):").pack(anchor="w")
        self.v_q = tk.StringVar()
        self.e_q = ttk.Entry(f, textvariable=self.v_q, font=self.f_body)
        self.e_q.pack(fill="x", pady=(2, 6))
        self.e_q.bind("<Escape>", lambda e: (self.v_q.set(""), "break")[1])
        self.e_q.bind("<Return>", lambda e: (self._open_first(), "break")[1])
        self.e_q.bind("<Down>", lambda e: (self.tree.focus_set(), self._first_row(), "break")[2])
        self.v_q.trace_add("write", lambda *_: self._schedule_search())
        mr = ttk.Frame(f)
        mr.pack(fill="x")
        mode = self.app.cfg["help"].get("mode")
        self.v_mode = tk.StringVar(value=mode if mode in ("category", "az") else "category")
        ttk.Radiobutton(mr, text="By category", value="category", variable=self.v_mode,
                        command=self.fill_list).pack(side="left")
        ttk.Radiobutton(mr, text="All topics (A-Z)", value="az", variable=self.v_mode,
                        command=self.fill_list).pack(side="left", padx=(10, 0))
        self.l_none = ttk.Label(f, text="", style="Warn.TLabel", wraplength=int(270 * s), justify="left")
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True, pady=(6, 0))
        self.tf = tf
        st = ttk.Style(self.w)
        st.configure("Help.Treeview", font=self.f_body, rowheight=self.f_body.metrics("linespace") + int(8 * s))
        self.tree = ttk.Treeview(tf, show="tree", selectmode="browse", style="Help.Treeview")
        self.tree.column("#0", width=int(280 * s), stretch=True)
        self.tree.tag_configure("cat", font=self.f_bold)
        self.tree.tag_configure("snip", foreground="#6b7280", font=self.f_small)
        self.tree.tag_configure("res", font=self.f_bold)
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._tree_selected())
        self.l_count = ttk.Label(f, text="", style="Info.TLabel")
        self.l_count.pack(anchor="w", pady=(4, 0))

    def reload_lang(self):
        """Language changed: reload the topics, keep the search text and the open topic."""
        self.topics = self.hd.load()
        self.by_id = {t.id: t for t in self.topics}
        if self.v_lang.get() != self.hd.current_lang():
            self.v_lang.set(self.hd.current_lang())
        cur = self.cur if isinstance(self.cur, str) else getattr(self.cur, "id", None)
        self.fill_list()
        tid = cur if cur in self.by_id else ("quickstart" if "quickstart" in self.by_id else self.topics[0].id)
        self.open(tid)

    def focus_search(self):
        self.e_q.focus_set()
        self.e_q.select_range(0, "end")

    def _schedule_search(self):
        """Typing restarts a short timer, so a burst of keys costs one search (never blocks the UI)."""
        if self._job:
            self.w.after_cancel(self._job)
        self._job = self.w.after(self.SEARCH_DELAY_MS, self.fill_list)

    def fill_list(self):
        """Rebuild the list for the current query / mode (helpdocs.search over ~60 topics takes about 1 ms)."""
        self._job = None
        t0 = time.perf_counter()
        q = self.v_q.get().strip()
        self.app.cfg["help"]["mode"] = self.v_mode.get()
        tr = self.tree
        self._sel_guard = True
        tr.delete(*tr.get_children())
        self.query_toks = [x for x in q.lower().split() if x]
        if q:
            res = self.hd.search(q, limit=60)
            for _score, t, sn in res:
                tr.insert("", "end", iid="t:" + t.id, text=f"{t.title}   ·  {t.category}", tags=("res",),
                          open=True)
                tr.insert("t:" + t.id, "end", iid="s:" + t.id, text=sn, tags=("snip",))
            self.l_count.configure(text=f"{len(res)} topic(s) match - best first")
            if res:
                self.l_none.pack_forget()
            else:
                self.l_none.configure(text=f"No topic matches \"{q}\".\nEvery word must appear in a topic: try fewer "
                                           "or other words, English or Thai (e.g. timeout, exception 02, เห"
                                           "ลื่อม), or press Esc to see all topics.")
                self.l_none.pack(fill="x", pady=(6, 0), before=self.tf)
        else:
            self.l_none.pack_forget()
            if self.v_mode.get() == "az":
                for t in sorted(self.topics, key=lambda t: t.title.casefold()):
                    tr.insert("", "end", iid="t:" + t.id, text=t.title)
            else:
                for cat, ts in self.hd.categories():
                    cid = "c:" + cat
                    tr.insert("", "end", iid=cid, text=cat, open=True, tags=("cat",))
                    for t in ts:
                        tr.insert(cid, "end", iid="t:" + t.id, text=t.title)
            self.l_count.configure(text=f"{len(self.topics)} topics")
        if self.cur and tr.exists("t:" + self.cur.id):
            tr.selection_set("t:" + self.cur.id)
            tr.see("t:" + self.cur.id)
        self._sel_guard = False
        if self.cur is not None:
            self._highlight()
        self.search_ms.append((time.perf_counter() - t0) * 1000)

    def _first_row(self):
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])

    def _open_first(self):
        for k in self.tree.get_children():
            if k.startswith("t:"):
                return self.open(k[2:])
            for c in self.tree.get_children(k):
                if c.startswith("t:"):
                    return self.open(c[2:])
        return False

    def _tree_selected(self):
        if self._sel_guard:
            return
        sel = self.tree.selection()
        if sel and sel[0][:2] in ("t:", "s:"):
            tid = sel[0][2:]
            if not self.cur or tid != self.cur.id:
                self.open(tid)

    # ----- right: topic -----
    def _build_right(self):
        s = self.s
        self.right = f = ttk.Frame(self.pw)
        nav = ttk.Frame(f)
        nav.pack(fill="x", padx=(8, 0))
        self.b_back = ttk.Button(nav, text="◀ Back", command=self.back, width=8)
        self.b_back.pack(side="left")
        Tooltip(self.b_back, "Previous topic   (Alt+Left)")
        self.b_fwd = ttk.Button(nav, text="Forward ▶", command=self.forward, width=10)
        self.b_fwd.pack(side="left", padx=(4, 0))
        Tooltip(self.b_fwd, "Next topic   (Alt+Right)")
        self.b_copy = ttk.Button(nav, text="Copy topic text", command=self.copy_topic)
        self.b_copy.pack(side="right")
        self.l_msg = ttk.Label(nav, text="", style="Info.TLabel")
        self.l_msg.pack(side="right", padx=(0, 10))
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True, padx=(8, 0), pady=(6, 0))
        self.txt = t = tk.Text(tf, wrap="word", font=self.f_body, relief="flat", bg=COL["bg"], padx=int(14 * s),
                               pady=int(10 * s), highlightthickness=1, highlightbackground=COL["line"], cursor="",
                               spacing1=int(2 * s), spacing3=int(2 * s), width=60, height=20)
        vs = ttk.Scrollbar(tf, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vs.set)
        t.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        ind = int(22 * s)
        t.tag_configure("h1", font=self.f_h1, spacing3=int(2 * s))
        t.tag_configure("cat", font=self.f_small, foreground="#6b7280", spacing3=int(8 * s))
        t.tag_configure("bold", font=self.f_bold)
        t.tag_configure("code", font=self.f_code, background="#eef1f5", foreground="#9a3412")
        t.tag_configure("p", spacing3=int(8 * s))
        t.tag_configure("li", lmargin1=int(8 * s), lmargin2=ind, tabs=(ind,), spacing3=int(3 * s))
        t.tag_configure("lilast", spacing3=int(10 * s))
        t.tag_configure("pre", font=self.f_code, background="#f4f6f9", lmargin1=int(10 * s), lmargin2=int(10 * s),
                        spacing1=0, spacing3=0, wrap="none")
        t.tag_configure("link", foreground=COL["sel"], underline=True)
        t.tag_configure("hit", background="#ffe37a")
        t.tag_bind("link", "<Enter>", lambda e: t.configure(cursor="hand2"))
        t.tag_bind("link", "<Leave>", lambda e: t.configure(cursor=""))
        t.bind("<Control-a>", lambda e: (t.tag_add("sel", "1.0", "end"), "break")[1])
        t.bind("<Configure>", self._resized)
        rel = ttk.Frame(f)
        rel.pack(fill="x", padx=(8, 0), pady=(6, 0))
        ttk.Label(rel, text="Related:", style="Conn.TLabel").pack(side="left", anchor="n", padx=(0, 6))
        self.t_rel = tk.Text(rel, wrap="word", font=self.f_body, height=2, relief="flat", cursor="",
                             bg=ttk.Style().lookup("TFrame", "background") or "#f0f0f0", highlightthickness=0)
        self.t_rel.pack(side="left", fill="x", expand=True)
        self.t_rel.tag_configure("link", foreground=COL["sel"], underline=True)
        self.t_rel.tag_configure("none", foreground="#6b7280")
        self.t_rel.tag_bind("link", "<Enter>", lambda e: self.t_rel.configure(cursor="hand2"))
        self.t_rel.tag_bind("link", "<Leave>", lambda e: self.t_rel.configure(cursor=""))

    def title_of(self, tid):
        t = self.by_id.get(tid)
        return t.title if t else tid

    def _link(self, widget, tid, text, tags=()):
        tag = f"to:{tid}"
        widget.insert("end", text, ("link", tag) + tuple(tags))
        widget.tag_bind(tag, "<Button-1>", lambda e, tid=tid: self.open(tid))

    def _inline(self, text, tags=()):
        for kind, seg, target in self.hd.inline(text):
            if kind == "link":
                self._link(self.txt, target, self.title_of(target), tags)
            else:
                self.txt.insert("end", seg, tuple(tags) + ((kind,) if kind in ("bold", "code") else ()))

    def open(self, tid, push=True):
        t = self.by_id.get(tid)
        if t is None:
            self.l_msg.configure(text=f"Unknown topic '{tid}'", style="Warn.TLabel")
            return False
        self.cur = t
        if push:
            self.hist.visit(tid)
        self.app.cfg["help"]["topic"] = tid
        self.render()
        self._sel_guard = True
        if self.tree.exists("t:" + tid):
            self.tree.selection_set("t:" + tid)
            self.tree.see("t:" + tid)
        elif self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())
        self._sel_guard = False
        self.b_back.configure(state="normal" if self.hist.can_back() else "disabled")
        self.b_fwd.configure(state="normal" if self.hist.can_forward() else "disabled")
        self.l_msg.configure(text="")
        return True

    def render(self):
        t, txt = self.cur, self.txt
        self.cells = []
        txt.configure(state="normal")
        for name in txt.window_names():
            try:
                self.w.nametowidget(name).destroy()
            except (KeyError, tk.TclError):
                pass
        txt.delete("1.0", "end")
        for tag in txt.tag_names():
            if tag.startswith("to:"):
                txt.tag_delete(tag)
        txt.insert("end", t.title + "\n", ("h1",))
        txt.insert("end", t.category + "\n", ("cat",))
        for b in self.hd.blocks(t.body):
            kind = b[0]
            if kind == "p":
                self._inline(b[1], ("p",))
                txt.insert("end", "\n", ("p",))
            elif kind in ("ul", "ol"):
                for i, item in enumerate(b[1], 1):
                    last = ("lilast",) if i == len(b[1]) else ()
                    txt.insert("end", ("•" if kind == "ul" else f"{i}.") + "\t", ("li",) + last)
                    self._inline(item, ("li",) + last)
                    txt.insert("end", "\n", ("li",) + last)
            elif kind == "code":
                txt.insert("end", b[1] + "\n", ("pre",))
                txt.insert("end", "\n", ("p",))
            elif kind == "table":
                self._table(b[1], b[2])
        txt.configure(state="disabled")
        txt.yview_moveto(0)
        self.last_width = txt.winfo_width()
        self._related()
        self._highlight()

    def _table(self, header, rows):
        """Embedded grid of labels: real borders, Thai shaped by the font engine, long cells wrapped."""
        s, txt = self.s, self.txt
        src = [header] + rows
        ncol = max(len(r) for r in src)
        plain = [[mon.help_inline_plain(c, self.title_of) for c in r] + [""] * (ncol - len(r)) for r in src]
        pad = int(8 * s)
        natural = [max(max((self.f_bold if r == 0 else self.f_body).measure(plain[r][c]) for r in range(len(plain)))
                       + 2 * pad + 4, int(40 * s)) for c in range(ncol)]
        avail = max(txt.winfo_width(), int(500 * s)) - int(14 * s) * 2 - int(24 * s)
        widths = mon.table_widths(natural, avail, min_w=int(70 * s))
        frame = tk.Frame(txt, bg="#b8bfc9")
        for r, row in enumerate(src):
            for c in range(ncol):
                raw = row[c] if c < len(row) else ""
                bg = "#e2e6ec" if r == 0 else ("#ffffff" if r % 2 else "#f6f8fa")
                cell = tk.Frame(frame, bg=bg)
                cell.grid(row=r, column=c, sticky="nsew", padx=(0 if c else 1, 1), pady=(0 if r else 1, 1))
                segs = self.hd.inline(raw)
                text = "".join(seg for k, seg, _t in segs if k != "link").strip()
                code_only = bool(segs) and all(k == "code" for k, _s, _t in segs)
                if text or not any(k == "link" for k, _s, _t in segs):
                    lab = tk.Label(cell, text=text, bg=bg, anchor="nw", justify="left",
                                   font=self.f_code if code_only else (self.f_bold if r == 0 else self.f_body),
                                   wraplength=max(widths[c] - 2 * pad, 20), padx=pad, pady=int(3 * s))
                    lab.pack(fill="x", anchor="nw")
                    self.cells.append((lab, text))
                for k, _seg, target in segs:
                    if k == "link":
                        ln = tk.Label(cell, text="→ " + self.title_of(target), bg=bg, fg=COL["sel"],
                                      font=self.f_small, cursor="hand2", anchor="w", justify="left",
                                      wraplength=max(widths[c] - 2 * pad, 20), padx=pad)
                        ln.pack(fill="x", anchor="w", pady=(0, int(2 * s)))
                        ln.bind("<Button-1>", lambda e, tid=target: self.open(tid))
        for c in range(ncol):
            frame.columnconfigure(c, minsize=widths[c])
        wheel = lambda e: (txt.yview_scroll(int(-e.delta / 120) * 3, "units"), "break")[1]
        for wdg in [frame] + frame.winfo_children() + [g for ch in frame.winfo_children() for g in ch.winfo_children()]:
            wdg.bind("<MouseWheel>", wheel)
        txt.window_create("end", window=frame, padx=int(2 * s), pady=int(4 * s))
        txt.insert("end", "\n\n", ("p",))

    def _related(self):
        r = self.t_rel
        r.configure(state="normal")
        r.delete("1.0", "end")
        for tag in r.tag_names():
            if tag.startswith("to:"):
                r.tag_delete(tag)
        links = [x for x in dict.fromkeys(self.cur.links()) if x != self.cur.id]
        self.related_ids = links
        if not links:
            r.insert("end", "(this topic links to no other topic)", ("none",))
        for i, tid in enumerate(links):
            if i:
                r.insert("end", "   ·   ")
            self._link(r, tid, self.title_of(tid))
        r.update_idletasks()
        try:
            lines = int(r.count("1.0", "end", "displaylines")[0])
        except (tk.TclError, TypeError, IndexError):
            lines = 2
        r.configure(height=max(1, min(lines, 3)), state="disabled")

    def _highlight(self):
        txt = self.txt
        txt.tag_remove("hit", "1.0", "end")
        for lab, _text in self.cells:
            try:
                lab.configure(bg=lab.master.cget("bg"))
            except tk.TclError:
                pass
        toks = self.query_toks if self.v_q.get().strip() else []
        first = None
        for tok in toks:
            idx = "1.0"
            while True:
                cnt = tk.IntVar(self.w)
                pos = txt.search(tok, idx, "end", nocase=True, count=cnt)
                if not pos or not cnt.get():
                    break
                end = f"{pos}+{cnt.get()}c"
                txt.tag_add("hit", pos, end)
                first = first or pos
                idx = end
            for lab, text in self.cells:
                if tok in text.lower():
                    lab.configure(bg="#ffe37a")
        txt.tag_raise("hit")
        if first:
            txt.see(first)
        self.hits = len(txt.tag_ranges("hit")) // 2

    def _resized(self, e):
        if self.cur is not None and self.txt.window_names() and abs(e.width - self.last_width) > 24:
            if self._wjob:
                self.w.after_cancel(self._wjob)
            self._wjob = self.w.after(200, self._rerender)

    def _rerender(self):
        self._wjob = None
        y = self.txt.yview()[0]
        self.render()
        self.txt.yview_moveto(y)

    # ----- navigation / actions -----
    def back(self):
        if self.hist.can_back():
            self.open(self.hist.back(), push=False)

    def forward(self):
        if self.hist.can_forward():
            self.open(self.hist.forward(), push=False)

    def copy_topic(self):
        text = mon.help_plain_text(self.cur, self.title_of)
        self.w.clipboard_clear()
        self.w.clipboard_append(text)
        self.l_msg.configure(text="Topic text copied", style="Info.TLabel")
        return text

    def show(self, tid=None):
        try:
            self.w.deiconify()
            self.w.lift()
        except tk.TclError:
            pass
        if tid:
            if self.v_q.get():
                self.v_q.set("")
                self.fill_list()
            self.open(tid)

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def save_state(self):
        if self.alive():
            c = self.app.cfg["help"]
            try:
                c["geometry"] = self.w.geometry()
                c["sash"] = int(self.pw.sashpos(0))
            except tk.TclError:
                pass
            c["mode"] = self.v_mode.get()
            if self.cur:
                c["topic"] = self.cur.id

    def close(self):
        if self.alive():
            self.save_state()
            save_settings(self.app.cfg)
            self.w.destroy()


def cli_busmap(a):
    import busmap
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        with open(a.file, encoding="utf-8") as f:
            m = json.load(f)
        print(busmap.format_report(busmap.analyze(m)))
    except (OSError, ValueError) as e:  # busmap.MapError is a ValueError
        print(f"error: {e}")
        sys.exit(1)


def gui(workspace=None):
    if sys.platform == "win32":  # crisp text on high-DPI screens
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    appinfo.install_crash_handlers(root)
    appinfo.seed_presets()
    try:
        ico = os.path.join(appinfo.resource_dir(), "mbtool.ico")
        if os.path.isfile(ico):
            root.iconbitmap(ico)
    except tk.TclError:
        pass
    app = MasterApp(root)
    recent = app.cfg["workspace"].get("recent") or []
    path = workspace or (recent[0] if app.cfg["workspace"].get("autoload") and recent else None)
    if path:
        def load():
            if os.path.isfile(path):
                app.open_workspace(path)
            else:
                app._status(f"Workspace not found: {path}", "bad")
        root.after(150, load)
    root.mainloop()


def main():
    p = argparse.ArgumentParser(prog="mbtool")
    sub = p.add_subparsers(dest="cmd")
    for name, fn in (("read", cli_read), ("write", cli_write)):
        s = sub.add_parser(name); s.set_defaults(fn=fn)
        s.add_argument("url"); s.add_argument("-u", "--unit", type=int, default=1)
        s.add_argument("-f", "--func", type=int, default=3); s.add_argument("-a", "--addr", type=int, default=0)
        s.add_argument("--fmt", default="Unsigned", choices=FORMATS)
        if name == "read":
            s.add_argument("-n", type=int, default=10); s.add_argument("--scan", type=int, default=0)
            s.add_argument("--log")
        else:
            s.add_argument("values", nargs="+")
    s = sub.add_parser("vcom", help="virtual null-modem cable (socket://) for RTU / ASCII tests without a driver")
    s.set_defaults(fn=cli_vcom)
    s.add_argument("--port", type=int, default=7000)
    s.add_argument("--baud", type=int, default=0,
                   help="emulate this line speed (every byte takes bits/baud seconds) so cycle times look like a "
                        "real RS-485 bus; 0 = as fast as possible (default)")
    s.add_argument("--bits", type=int, default=11,
                   help="bits per character for --baud: 10 = 8N1, 11 = 8E1 / 8N2 (default 11)")
    s = sub.add_parser("preset-list"); s.set_defaults(fn=cli_preset_list)
    s = sub.add_parser("preset-read"); s.set_defaults(fn=cli_preset_read)
    s.add_argument("url"); s.add_argument("preset"); s.add_argument("-u", "--unit", type=int)
    s.add_argument("-g", "--group"); s.add_argument("--scan", type=int, default=0)
    s = sub.add_parser("preset-write"); s.set_defaults(fn=cli_preset_write)
    s.add_argument("url"); s.add_argument("preset"); s.add_argument("point"); s.add_argument("value")
    s.add_argument("-u", "--unit", type=int)
    s = sub.add_parser("sim"); s.set_defaults(fn=cli_sim)
    s.add_argument("--port", type=int, default=5020); s.add_argument("--public", action="store_true")
    s.add_argument("--preset", help="fill the simulator from a preset file")
    s.add_argument("--signals", choices=["static", "random", "loop", "sine", "profile", "preset"],
                   help="move the preset values: random walk, ramp loop, sine, device-type behaviour (profile)")
    s.add_argument("--device-type", choices=["generic", "meter", "drive", "level", "pressure", "flow", "temperature", "io"],
                   help="behaviour for --signals profile (default: guessed from the preset)")
    s.add_argument("--speed", type=float, default=1.0, help="time scale for --signals (2 = twice as fast)")
    s.add_argument("--serial", help="COM port: run an RTU slave instead of TCP")
    s.add_argument("--mode", default="rtu", choices=["rtu", "ascii"], help="framing for --serial")
    s.add_argument("-u", "--unit", type=int, default=1); s.add_argument("--baud", type=int, default=9600)
    s.add_argument("--parity", default="N", choices=["N", "E", "O"])
    s.add_argument("--delay", type=float, default=0.0,
                   help="response delay in ms: the simulator waits this long before every answer, like a real "
                        "device thinking (for cycle-time tests, default 0)")
    s = sub.add_parser("busmap", help="print the bus map analysis of a map JSON file (Tools > Bus map > Save map)")
    s.add_argument("file")
    s.set_defaults(fn=cli_busmap)
    s = sub.add_parser("mcp", help="run the MCP server (stdio, or --http); same options as mbtool_mcp.py")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=lambda a: __import__("mbtool_mcp").main(a.rest))
    s = sub.add_parser("health", help="bus health test: real response times, errors by kind, recommended timeout "
                                      "and gap (same options as bushealth.py, e.g. --com COM3 --baud 19200 --units 1-5)")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=lambda a: sys.exit(__import__("bushealth").main(a.rest)))
    s = sub.add_parser("selftest", help="check the program on this PC without hardware (prints PASS / FAIL)")
    s.set_defaults(fn=lambda a: sys.exit(__import__("selftest").main()))
    p.add_argument("--version", action="version", version=f"mbtool {appinfo.VERSION}")
    p.add_argument("--workspace", help="GUI: open this workspace (.mbw) at start")
    if sys.argv[1:2] == ["health"]:  # its own options start with "--": hand them over untouched
        sys.exit(__import__("bushealth").main(sys.argv[2:]))
    if "--uninstall" in sys.argv[1:]:  # Apps & features calls: mbtool.exe --uninstall
        import setup_app
        sys.exit(setup_app.uninstall_gui())
    a = p.parse_args()
    appinfo.seed_presets()
    appinfo.install_crash_handlers()
    (a.fn(a) if a.cmd else gui(a.workspace))


if __name__ == "__main__":
    main()
