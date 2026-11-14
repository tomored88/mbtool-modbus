"""Traffic monitor (F8)."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


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
