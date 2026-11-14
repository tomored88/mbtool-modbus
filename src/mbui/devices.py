"""Devices window (F11): overview, Cycle, Calculator and Health test tabs, one tab per device, device dialog and Scan & add."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


STATE_LIGHT = {"ok": ("● OK", "ok"), "error": ("● ERROR", "error"), "stale": ("● STALE", "stale"), "off": ("○ off", "off")}


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
        # scan mode row; the device summary sits at its right end (the toolbar has no room left for it)
        mf = ttk.Frame(ov, padding=(8, 0, 8, 4))
        mf.pack(fill="x")
        self.l_sum = ttk.Label(mf, text="", style="Info.TLabel")
        self.l_sum.pack(side="right")  # packed first, so it keeps its space when the window gets narrow
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


# Names from sibling modules, imported last so that the modules can refer to each other.
from .dialogs import SetupDialog  # noqa: E402
from .grids import MultiGrid  # noqa: E402
