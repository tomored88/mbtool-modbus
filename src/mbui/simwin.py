"""Slave simulator window (F9) with its Signals and Faults tabs."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


SIM_AREAS = {"Coils (0x)": "coils", "Discrete Inputs (1x)": "discrete",
             "Input Registers (3x)": "input", "Holding Registers (4x)": "holding"}


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


# Names from sibling modules, imported last so that the modules can refer to each other.
from .grids import MultiGrid, RegGrid  # noqa: E402
from .traffic import TrafficView  # noqa: E402
