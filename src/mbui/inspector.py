"""Value inspector (F4)."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


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


# Names from sibling modules, imported last so that the modules can refer to each other.
from .presets_ui import PresetEditor  # noqa: E402
