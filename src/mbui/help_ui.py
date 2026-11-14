"""The manual window (F1)."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


class HelpWindow:
    """F1: the built-in manual (helpdocs + help/*.md). Search, topic tree (by category or A-Z), native rendering of
    the Markdown subset in a Text widget with clickable links, back / forward history, related topics."""
    SEARCH_DELAY_MS = 60

    def __init__(self, app):
        import helpdocs
        self.app, s = app, app.s
        self.s, self.hd = s, helpdocs
        self.topics = helpdocs.load()
        self.by_id = {t.id: t for t in self.topics}
        cfg = app.cfg["help"]
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Manual")
        try:
            w.geometry(cfg.get("geometry") or f"{int(1060 * s)}x{int(700 * s)}")
        except tk.TclError:
            w.geometry(f"{int(1060 * s)}x{int(700 * s)}")
        w.minsize(int(640 * s), int(380 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        self._fonts()
        self.hist = mon.HelpHistory()
        self.cur, self.query_toks, self._sel_guard, self._job, self._wjob = None, [], False, None, None
        self.last_width, self.search_ms, self.cells = 0, [], []
        self.pw = ttk.PanedWindow(w, orient="horizontal")
        self.pw.pack(fill="both", expand=True, padx=8, pady=8)
        self._build_left()
        self._build_right()
        self.pw.add(self.left, weight=0)
        self.pw.add(self.right, weight=1)
        w.bind("<Control-f>", lambda e: (self.focus_search(), "break")[1])
        w.bind("<Alt-Left>", lambda e: (self.back(), "break")[1])
        w.bind("<Alt-Right>", lambda e: (self.forward(), "break")[1])
        self.fill_list()
        self._sash = int(cfg.get("sash") or 0) or int(320 * s)
        self.w.after(60, self._set_sash)  # after the window is mapped, else the sash position is lost
        first = cfg.get("topic") if cfg.get("topic") in self.by_id else "quickstart"
        self.open(first if first in self.by_id else self.topics[0].id)

    def _set_sash(self):
        try:
            if not self.pw.winfo_ismapped():
                self.w.after(60, self._set_sash)
                return
            pos = max(int(180 * self.s), min(self._sash, self.pw.winfo_width() - int(300 * self.s)))
            self.pw.sashpos(0, pos)
        except tk.TclError:
            pass

    def _fonts(self):
        base = tkfont.nametofont("TkDefaultFont")
        fam = base.cget("family")
        if "Leelawadee UI" in tkfont.families(self.w):  # Thai and Latin from one font: no fallback gaps
            fam = "Leelawadee UI"
        size = abs(base.cget("size")) + 1
        self.f_body = tkfont.Font(family=fam, size=size)
        self.f_bold = tkfont.Font(family=fam, size=size, weight="bold")
        self.f_h1 = tkfont.Font(family=fam, size=size + 5, weight="bold")
        self.f_small = tkfont.Font(family=fam, size=max(size - 1, 7))
        self.f_code = tkfont.Font(family="Consolas", size=size)

    # ----- left: search + list -----
    def _build_left(self):
        s = self.s
        self.left = f = ttk.Frame(self.pw, width=int(300 * s))
        lf = ttk.Frame(f)
        lf.pack(fill="x", pady=(0, 6))
        ttk.Label(lf, text="Language:").pack(side="left")
        self.v_lang = tk.StringVar(value=self.hd.current_lang())
        for code, name in (("th", "ไทย"), ("en", "English")):
            ttk.Radiobutton(lf, text=name, value=code, variable=self.v_lang,
                            command=lambda: self.app.set_help_lang(self.v_lang.get())).pack(side="left", padx=(6, 0))
        ttk.Label(f, text="Search (Ctrl+F):").pack(anchor="w")
        self.v_q = tk.StringVar()
        self.e_q = ttk.Entry(f, textvariable=self.v_q, font=self.f_body)
        self.e_q.pack(fill="x", pady=(2, 6))
        self.e_q.bind("<Escape>", lambda e: (self.v_q.set(""), "break")[1])
        self.e_q.bind("<Return>", lambda e: (self._open_first(), "break")[1])
        self.e_q.bind("<Down>", lambda e: (self.tree.focus_set(), self._first_row(), "break")[2])
        self.v_q.trace_add("write", lambda *_: self._schedule_search())
        mr = ttk.Frame(f)
        mr.pack(fill="x")
        mode = self.app.cfg["help"].get("mode")
        self.v_mode = tk.StringVar(value=mode if mode in ("category", "az") else "category")
        ttk.Radiobutton(mr, text="By category", value="category", variable=self.v_mode,
                        command=self.fill_list).pack(side="left")
        ttk.Radiobutton(mr, text="All topics (A-Z)", value="az", variable=self.v_mode,
                        command=self.fill_list).pack(side="left", padx=(10, 0))
        self.l_none = ttk.Label(f, text="", style="Warn.TLabel", wraplength=int(270 * s), justify="left")
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True, pady=(6, 0))
        self.tf = tf
        st = ttk.Style(self.w)
        st.configure("Help.Treeview", font=self.f_body, rowheight=self.f_body.metrics("linespace") + int(8 * s))
        self.tree = ttk.Treeview(tf, show="tree", selectmode="browse", style="Help.Treeview")
        self.tree.column("#0", width=int(280 * s), stretch=True)
        self.tree.tag_configure("cat", font=self.f_bold)
        self.tree.tag_configure("snip", foreground="#6b7280", font=self.f_small)
        self.tree.tag_configure("res", font=self.f_bold)
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._tree_selected())
        self.l_count = ttk.Label(f, text="", style="Info.TLabel")
        self.l_count.pack(anchor="w", pady=(4, 0))

    def reload_lang(self):
        """Language changed: reload the topics, keep the search text and the open topic."""
        self.topics = self.hd.load()
        self.by_id = {t.id: t for t in self.topics}
        if self.v_lang.get() != self.hd.current_lang():
            self.v_lang.set(self.hd.current_lang())
        cur = self.cur if isinstance(self.cur, str) else getattr(self.cur, "id", None)
        self.fill_list()
        tid = cur if cur in self.by_id else ("quickstart" if "quickstart" in self.by_id else self.topics[0].id)
        self.open(tid)

    def focus_search(self):
        self.e_q.focus_set()
        self.e_q.select_range(0, "end")

    def _schedule_search(self):
        """Typing restarts a short timer, so a burst of keys costs one search (never blocks the UI)."""
        if self._job:
            self.w.after_cancel(self._job)
        self._job = self.w.after(self.SEARCH_DELAY_MS, self.fill_list)

    def fill_list(self):
        """Rebuild the list for the current query / mode (helpdocs.search over ~60 topics takes about 1 ms)."""
        self._job = None
        t0 = time.perf_counter()
        q = self.v_q.get().strip()
        self.app.cfg["help"]["mode"] = self.v_mode.get()
        tr = self.tree
        self._sel_guard = True
        tr.delete(*tr.get_children())
        self.query_toks = [x for x in q.lower().split() if x]
        if q:
            res = self.hd.search(q, limit=60)
            for _score, t, sn in res:
                tr.insert("", "end", iid="t:" + t.id, text=f"{t.title}   ·  {t.category}", tags=("res",),
                          open=True)
                tr.insert("t:" + t.id, "end", iid="s:" + t.id, text=sn, tags=("snip",))
            self.l_count.configure(text=f"{len(res)} topic(s) match - best first")
            if res:
                self.l_none.pack_forget()
            else:
                self.l_none.configure(text=f"No topic matches \"{q}\".\nEvery word must appear in a topic: try fewer "
                                           "or other words, English or Thai (e.g. timeout, exception 02, เห"
                                           "ลื่อม), or press Esc to see all topics.")
                self.l_none.pack(fill="x", pady=(6, 0), before=self.tf)
        else:
            self.l_none.pack_forget()
            if self.v_mode.get() == "az":
                for t in sorted(self.topics, key=lambda t: t.title.casefold()):
                    tr.insert("", "end", iid="t:" + t.id, text=t.title)
            else:
                for cat, ts in self.hd.categories():
                    cid = "c:" + cat
                    tr.insert("", "end", iid=cid, text=cat, open=True, tags=("cat",))
                    for t in ts:
                        tr.insert(cid, "end", iid="t:" + t.id, text=t.title)
            self.l_count.configure(text=f"{len(self.topics)} topics")
        if self.cur and tr.exists("t:" + self.cur.id):
            tr.selection_set("t:" + self.cur.id)
            tr.see("t:" + self.cur.id)
        self._sel_guard = False
        if self.cur is not None:
            self._highlight()
        self.search_ms.append((time.perf_counter() - t0) * 1000)

    def _first_row(self):
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])
            self.tree.focus(kids[0])

    def _open_first(self):
        for k in self.tree.get_children():
            if k.startswith("t:"):
                return self.open(k[2:])
            for c in self.tree.get_children(k):
                if c.startswith("t:"):
                    return self.open(c[2:])
        return False

    def _tree_selected(self):
        if self._sel_guard:
            return
        sel = self.tree.selection()
        if sel and sel[0][:2] in ("t:", "s:"):
            tid = sel[0][2:]
            if not self.cur or tid != self.cur.id:
                self.open(tid)

    # ----- right: topic -----
    def _build_right(self):
        s = self.s
        self.right = f = ttk.Frame(self.pw)
        nav = ttk.Frame(f)
        nav.pack(fill="x", padx=(8, 0))
        self.b_back = ttk.Button(nav, text="◀ Back", command=self.back, width=8)
        self.b_back.pack(side="left")
        Tooltip(self.b_back, "Previous topic   (Alt+Left)")
        self.b_fwd = ttk.Button(nav, text="Forward ▶", command=self.forward, width=10)
        self.b_fwd.pack(side="left", padx=(4, 0))
        Tooltip(self.b_fwd, "Next topic   (Alt+Right)")
        self.b_copy = ttk.Button(nav, text="Copy topic text", command=self.copy_topic)
        self.b_copy.pack(side="right")
        self.l_msg = ttk.Label(nav, text="", style="Info.TLabel")
        self.l_msg.pack(side="right", padx=(0, 10))
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True, padx=(8, 0), pady=(6, 0))
        self.txt = t = tk.Text(tf, wrap="word", font=self.f_body, relief="flat", bg=COL["bg"], padx=int(14 * s),
                               pady=int(10 * s), highlightthickness=1, highlightbackground=COL["line"], cursor="",
                               spacing1=int(2 * s), spacing3=int(2 * s), width=60, height=20)
        vs = ttk.Scrollbar(tf, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=vs.set)
        t.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        ind = int(22 * s)
        t.tag_configure("h1", font=self.f_h1, spacing3=int(2 * s))
        t.tag_configure("cat", font=self.f_small, foreground="#6b7280", spacing3=int(8 * s))
        t.tag_configure("bold", font=self.f_bold)
        t.tag_configure("code", font=self.f_code, background="#eef1f5", foreground="#9a3412")
        t.tag_configure("p", spacing3=int(8 * s))
        t.tag_configure("li", lmargin1=int(8 * s), lmargin2=ind, tabs=(ind,), spacing3=int(3 * s))
        t.tag_configure("lilast", spacing3=int(10 * s))
        t.tag_configure("pre", font=self.f_code, background="#f4f6f9", lmargin1=int(10 * s), lmargin2=int(10 * s),
                        spacing1=0, spacing3=0, wrap="none")
        t.tag_configure("link", foreground=COL["sel"], underline=True)
        t.tag_configure("hit", background="#ffe37a")
        t.tag_bind("link", "<Enter>", lambda e: t.configure(cursor="hand2"))
        t.tag_bind("link", "<Leave>", lambda e: t.configure(cursor=""))
        t.bind("<Control-a>", lambda e: (t.tag_add("sel", "1.0", "end"), "break")[1])
        t.bind("<Configure>", self._resized)
        rel = ttk.Frame(f)
        rel.pack(fill="x", padx=(8, 0), pady=(6, 0))
        ttk.Label(rel, text="Related:", style="Conn.TLabel").pack(side="left", anchor="n", padx=(0, 6))
        self.t_rel = tk.Text(rel, wrap="word", font=self.f_body, height=2, relief="flat", cursor="",
                             bg=ttk.Style().lookup("TFrame", "background") or "#f0f0f0", highlightthickness=0)
        self.t_rel.pack(side="left", fill="x", expand=True)
        self.t_rel.tag_configure("link", foreground=COL["sel"], underline=True)
        self.t_rel.tag_configure("none", foreground="#6b7280")
        self.t_rel.tag_bind("link", "<Enter>", lambda e: self.t_rel.configure(cursor="hand2"))
        self.t_rel.tag_bind("link", "<Leave>", lambda e: self.t_rel.configure(cursor=""))

    def title_of(self, tid):
        t = self.by_id.get(tid)
        return t.title if t else tid

    def _link(self, widget, tid, text, tags=()):
        tag = f"to:{tid}"
        widget.insert("end", text, ("link", tag) + tuple(tags))
        widget.tag_bind(tag, "<Button-1>", lambda e, tid=tid: self.open(tid))

    def _inline(self, text, tags=()):
        for kind, seg, target in self.hd.inline(text):
            if kind == "link":
                self._link(self.txt, target, self.title_of(target), tags)
            else:
                self.txt.insert("end", seg, tuple(tags) + ((kind,) if kind in ("bold", "code") else ()))

    def open(self, tid, push=True):
        t = self.by_id.get(tid)
        if t is None:
            self.l_msg.configure(text=f"Unknown topic '{tid}'", style="Warn.TLabel")
            return False
        self.cur = t
        if push:
            self.hist.visit(tid)
        self.app.cfg["help"]["topic"] = tid
        self.render()
        self._sel_guard = True
        if self.tree.exists("t:" + tid):
            self.tree.selection_set("t:" + tid)
            self.tree.see("t:" + tid)
        elif self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())
        self._sel_guard = False
        self.b_back.configure(state="normal" if self.hist.can_back() else "disabled")
        self.b_fwd.configure(state="normal" if self.hist.can_forward() else "disabled")
        self.l_msg.configure(text="")
        return True

    def render(self):
        t, txt = self.cur, self.txt
        self.cells = []
        txt.configure(state="normal")
        for name in txt.window_names():
            try:
                self.w.nametowidget(name).destroy()
            except (KeyError, tk.TclError):
                pass
        txt.delete("1.0", "end")
        for tag in txt.tag_names():
            if tag.startswith("to:"):
                txt.tag_delete(tag)
        txt.insert("end", t.title + "\n", ("h1",))
        txt.insert("end", t.category + "\n", ("cat",))
        for b in self.hd.blocks(t.body):
            kind = b[0]
            if kind == "p":
                self._inline(b[1], ("p",))
                txt.insert("end", "\n", ("p",))
            elif kind in ("ul", "ol"):
                for i, item in enumerate(b[1], 1):
                    last = ("lilast",) if i == len(b[1]) else ()
                    txt.insert("end", ("•" if kind == "ul" else f"{i}.") + "\t", ("li",) + last)
                    self._inline(item, ("li",) + last)
                    txt.insert("end", "\n", ("li",) + last)
            elif kind == "code":
                txt.insert("end", b[1] + "\n", ("pre",))
                txt.insert("end", "\n", ("p",))
            elif kind == "table":
                self._table(b[1], b[2])
        txt.configure(state="disabled")
        txt.yview_moveto(0)
        self.last_width = txt.winfo_width()
        self._related()
        self._highlight()

    def _table(self, header, rows):
        """Embedded grid of labels: real borders, Thai shaped by the font engine, long cells wrapped."""
        s, txt = self.s, self.txt
        src = [header] + rows
        ncol = max(len(r) for r in src)
        plain = [[mon.help_inline_plain(c, self.title_of) for c in r] + [""] * (ncol - len(r)) for r in src]
        pad = int(8 * s)
        natural = [max(max((self.f_bold if r == 0 else self.f_body).measure(plain[r][c]) for r in range(len(plain)))
                       + 2 * pad + 4, int(40 * s)) for c in range(ncol)]
        avail = max(txt.winfo_width(), int(500 * s)) - int(14 * s) * 2 - int(24 * s)
        widths = mon.table_widths(natural, avail, min_w=int(70 * s))
        frame = tk.Frame(txt, bg="#b8bfc9")
        for r, row in enumerate(src):
            for c in range(ncol):
                raw = row[c] if c < len(row) else ""
                bg = "#e2e6ec" if r == 0 else ("#ffffff" if r % 2 else "#f6f8fa")
                cell = tk.Frame(frame, bg=bg)
                cell.grid(row=r, column=c, sticky="nsew", padx=(0 if c else 1, 1), pady=(0 if r else 1, 1))
                segs = self.hd.inline(raw)
                text = "".join(seg for k, seg, _t in segs if k != "link").strip()
                code_only = bool(segs) and all(k == "code" for k, _s, _t in segs)
                if text or not any(k == "link" for k, _s, _t in segs):
                    lab = tk.Label(cell, text=text, bg=bg, anchor="nw", justify="left",
                                   font=self.f_code if code_only else (self.f_bold if r == 0 else self.f_body),
                                   wraplength=max(widths[c] - 2 * pad, 20), padx=pad, pady=int(3 * s))
                    lab.pack(fill="x", anchor="nw")
                    self.cells.append((lab, text))
                for k, _seg, target in segs:
                    if k == "link":
                        ln = tk.Label(cell, text="→ " + self.title_of(target), bg=bg, fg=COL["sel"],
                                      font=self.f_small, cursor="hand2", anchor="w", justify="left",
                                      wraplength=max(widths[c] - 2 * pad, 20), padx=pad)
                        ln.pack(fill="x", anchor="w", pady=(0, int(2 * s)))
                        ln.bind("<Button-1>", lambda e, tid=target: self.open(tid))
        for c in range(ncol):
            frame.columnconfigure(c, minsize=widths[c])
        wheel = lambda e: (txt.yview_scroll(int(-e.delta / 120) * 3, "units"), "break")[1]
        for wdg in [frame] + frame.winfo_children() + [g for ch in frame.winfo_children() for g in ch.winfo_children()]:
            wdg.bind("<MouseWheel>", wheel)
        txt.window_create("end", window=frame, padx=int(2 * s), pady=int(4 * s))
        txt.insert("end", "\n\n", ("p",))

    def _related(self):
        r = self.t_rel
        r.configure(state="normal")
        r.delete("1.0", "end")
        for tag in r.tag_names():
            if tag.startswith("to:"):
                r.tag_delete(tag)
        links = [x for x in dict.fromkeys(self.cur.links()) if x != self.cur.id]
        self.related_ids = links
        if not links:
            r.insert("end", "(this topic links to no other topic)", ("none",))
        for i, tid in enumerate(links):
            if i:
                r.insert("end", "   ·   ")
            self._link(r, tid, self.title_of(tid))
        r.update_idletasks()
        try:
            lines = int(r.count("1.0", "end", "displaylines")[0])
        except (tk.TclError, TypeError, IndexError):
            lines = 2
        r.configure(height=max(1, min(lines, 3)), state="disabled")

    def _highlight(self):
        txt = self.txt
        txt.tag_remove("hit", "1.0", "end")
        for lab, _text in self.cells:
            try:
                lab.configure(bg=lab.master.cget("bg"))
            except tk.TclError:
                pass
        toks = self.query_toks if self.v_q.get().strip() else []
        first = None
        for tok in toks:
            idx = "1.0"
            while True:
                cnt = tk.IntVar(self.w)
                pos = txt.search(tok, idx, "end", nocase=True, count=cnt)
                if not pos or not cnt.get():
                    break
                end = f"{pos}+{cnt.get()}c"
                txt.tag_add("hit", pos, end)
                first = first or pos
                idx = end
            for lab, text in self.cells:
                if tok in text.lower():
                    lab.configure(bg="#ffe37a")
        txt.tag_raise("hit")
        if first:
            txt.see(first)
        self.hits = len(txt.tag_ranges("hit")) // 2

    def _resized(self, e):
        if self.cur is not None and self.txt.window_names() and abs(e.width - self.last_width) > 24:
            if self._wjob:
                self.w.after_cancel(self._wjob)
            self._wjob = self.w.after(200, self._rerender)

    def _rerender(self):
        self._wjob = None
        y = self.txt.yview()[0]
        self.render()
        self.txt.yview_moveto(y)

    # ----- navigation / actions -----
    def back(self):
        if self.hist.can_back():
            self.open(self.hist.back(), push=False)

    def forward(self):
        if self.hist.can_forward():
            self.open(self.hist.forward(), push=False)

    def copy_topic(self):
        text = mon.help_plain_text(self.cur, self.title_of)
        self.w.clipboard_clear()
        self.w.clipboard_append(text)
        self.l_msg.configure(text="Topic text copied", style="Info.TLabel")
        return text

    def show(self, tid=None):
        try:
            self.w.deiconify()
            self.w.lift()
        except tk.TclError:
            pass
        if tid:
            if self.v_q.get():
                self.v_q.set("")
                self.fill_list()
            self.open(tid)

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def save_state(self):
        if self.alive():
            c = self.app.cfg["help"]
            try:
                c["geometry"] = self.w.geometry()
                c["sash"] = int(self.pw.sashpos(0))
            except tk.TclError:
                pass
            c["mode"] = self.v_mode.get()
            if self.cur:
                c["topic"] = self.cur.id

    def close(self):
        if self.alive():
            self.save_state()
            save_settings(self.app.cfg)
            self.w.destroy()
