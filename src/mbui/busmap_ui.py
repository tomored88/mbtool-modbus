"""RS-485 bus map window (Ctrl+M)."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


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


# Names from sibling modules, imported last so that the modules can refer to each other.
from .traffic import TrafficView  # noqa: E402
