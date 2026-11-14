"""Poll tabs of the main window (tab strip and one PollTab per tab)."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


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


# Names from sibling modules, imported last so that the modules can refer to each other.
from .app import MasterApp  # noqa: E402
from .dialogs import SetupDialog  # noqa: E402
from .grids import MultiGrid, RegGrid  # noqa: E402
from .simwin import SimPresetPicker  # noqa: E402
