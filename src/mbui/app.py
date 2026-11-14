"""MasterApp: the main window - toolbar, poll tabs, Poll 1 connection and polling, menus, settings, workspaces and the message queue that worker threads report through."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


AI_AMBER = "#ffc247"


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
        hm.add_command(label="Check for updates...", command=lambda: self.check_updates(manual=True))
        self.upd_auto_var = tk.BooleanVar(value=bool(self.cfg["updates"].get("auto", True)))
        hm.add_checkbutton(label="Check for updates when mbtool starts", variable=self.upd_auto_var,
                           command=lambda: self.set_update_auto(self.upd_auto_var.get()))
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

    # ---------- updates ----------
    def set_update_auto(self, on):
        self.cfg["updates"]["auto"] = bool(on)
        if hasattr(self, "upd_auto_var") and self.upd_auto_var.get() != bool(on):
            self.upd_auto_var.set(bool(on))
        save_settings(self.cfg)

    def auto_check_updates(self, now=None):
        """At start: check GitHub at most once a day, silently; only a newer, not skipped version shows a dialog.
        Returns True when a check was started."""
        u = self.cfg["updates"]
        now = time.time() if now is None else now
        try:
            last = float(u.get("last_check") or 0)
        except (TypeError, ValueError):
            last = 0.0
        if not u.get("auto", True) or now - last < updates_mod().CHECK_EVERY_S:
            return False
        self.check_updates(manual=False)
        return True

    def check_updates(self, manual=True):
        """Ask GitHub for the latest release on a worker thread; the result comes back through the app queue."""
        upd = updates_mod()
        if manual:
            self._status("Checking for updates...", "info")

        def work():
            try:
                r = upd.check()
                self.q.put(("call", None, lambda res, err: self._updates_done(res, err, manual), r, None))
            except Exception as e:  # noqa: BLE001 - reported (manual) or ignored (automatic)
                self.q.put(("call", None, lambda res, err: self._updates_done(res, err, manual), None,
                            str(e) or repr(e)))
        threading.Thread(target=work, daemon=True).start()

    def _updates_done(self, res, err, manual):
        from tkinter import messagebox
        u = self.cfg["updates"]
        if err:
            if manual:
                self._status(f"Could not check for updates: {err}", "bad")
                messagebox.showwarning("Check for updates", f"Could not check for updates:\n{err}\n\n"
                                       f"You can look yourself: {updates_mod().RELEASES_URL}", parent=self.root)
            return None
        u["last_check"] = time.time()
        save_settings(self.cfg)
        if res["newer"] and (manual or u.get("skip") != res["latest"]):
            self._status(f"mbtool {res['latest']} is available (you have {res['current']})", "warn")
            self.update_dlg = UpdateDialog(self, res)
            return self.update_dlg
        if manual:
            self._status(f"mbtool {res['current']} is up to date", "ok")
            messagebox.showinfo("Check for updates", f"mbtool {res['current']} is up to date.\n"
                                f"(latest release: {res['latest']})", parent=self.root)
        return None

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


class _Recorder:
    """Stands in for a master during Preset.read and keeps the raw blocks it fetched (worker thread only)."""

    def __init__(self, m):
        self.m, self.blocks = m, []

    def request(self, unit, fc, **kw):
        data = self.m.request(unit, fc, **kw)
        if data is not None:
            self.blocks.append((fc, kw.get("addr", 0), list(data)))
        return data


# Names from sibling modules, imported last so that the modules can refer to each other.
from .ai_ui import AiAssistWindow, AiShare  # noqa: E402
from .busmap_ui import BusMapWindow  # noqa: E402
from .cli import main  # noqa: E402
from .devices import DevicesWindow  # noqa: E402
from .dialogs import LogOptionsDialog, MultiColumnsDialog, SetupDialog, WriteDialog  # noqa: E402
from .export_ui import ExportWindow, ReportDialog, UpdateDialog, updates_mod  # noqa: E402
from .grids import MultiGrid, RegGrid  # noqa: E402
from .help_ui import HelpWindow  # noqa: E402
from .inspector import Inspector  # noqa: E402
from .polltabs import PollTab, TabStrip  # noqa: E402
from .presets_ui import PresetEditor, PresetView, PresetsDialog  # noqa: E402
from .simwin import SimWindow  # noqa: E402
from .traffic import TrafficWindow  # noqa: E402
from .trend import AlertsWindow, TrendWindow  # noqa: E402
