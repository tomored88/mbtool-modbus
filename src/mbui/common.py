"""Shared parts of the GUI: imports, settings (load / save), constants, small helpers and widgets (Tooltip, Lamp, Dialog ...). Every other mbui module starts with `from .common import *`."""
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
import tempfile
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


def seed(s):
    for i in range(100):
        s.holding[i] = i * 10
        s.input[i] = 1000 + i
        s.coils[i] = bool(i % 2)
        s.discrete[i] = bool(i % 3 == 0)


# ---------------- presets (CLI) ----------------

def preset_dir():
    return appinfo.preset_dir()


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
    "updates": {"auto": True, "last_check": 0.0, "skip": ""},  # Help > Check for updates (GitHub releases)
    "report": {"project": "", "site": "", "customer": "", "engineer": "", "notes": "", "lang": "en", "pdf": True,
               "folder": ""},  # File > Commissioning report: the form is remembered
    "tabs": {"main_name": "Poll 1", "active": 0},  # poll tabs of the main window: first tab's title, selected tab
}


BAUDS = ["1200", "2400", "4800", "9600", "14400", "19200", "38400", "57600", "115200"]


PARITY_NAMES = {"N": "None", "E": "Even", "O": "Odd"}


PLC_PREFIX = mon.PLC_PREFIX


ROW_CHOICES = ["Auto", "10", "20", "30", "40"]


FMT_CHARS = {"Binary": 16, "Double": 24, "Float": 14, "Long": 11}


COL = dict(bg="#ffffff", alt="#f4f6f9", addr="#eceff3", addrfg="#59606b", line="#d7dbe1",
           head="#e2e6ec", headfg="#2b2f36", fg="#111111", flash="#ffe37a", sel="#1a64d6",
           stale="#a0a5ad", ok="#2e9d44", bad="#d93025", warn="#e69500", off="#9aa0a6")


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


MULTI_TIP = ("Multi-type view: one row per register, one column per data type\n"
             "(Unsigned, Signed, Hex, Binary, Float, Long... - pick them with 'Columns...').\n"
             "Long / Float use the register of the row and the next one, Double the next three:\n"
             "'-' = not enough registers left in this poll.\n"
             "Not for FC 01 / 02 (coils and discrete inputs are single bits).")


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


DOT_COLORS = {"ok": "#1e9e3e", "error": "#d93025", "stale": "#e69500", "off": "#9aa0a6"}


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
