"""Preset view, preset list and preset editor (F7)."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


# ---------------- presets / traffic / inspector (GUI) ----------------

BANNER = dict(bg="#ffe066", fg="#4d3800")


BADGE = {"verified": ("VERIFIED", "#cdeccf", "#1e5e2c"), "unverified": ("UNVERIFIED", "#ffe066", "#4d3800"),
         "invalid": ("INVALID", "#f8d0d0", "#8a1c13")}


FC_CHOICES = ["1 - Coil (0x)", "2 - Discrete input (1x)", "3 - Holding register (4x)", "4 - Input register (3x)"]


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
