"""Trend (F10) and Alerts windows."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


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
