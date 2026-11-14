"""The register tables: RegGrid (one display format) and MultiGrid (multi-type view)."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


class RegGrid:
    """Address/value grid on a Canvas: wraps into as many column pairs as fit,
    scrolls vertically, flashes changed cells, supports selection and in-place editing."""
    ROWS_MIN = 10

    def __init__(self, parent, s, on_edit=None):
        self.s, self.on_edit = s, on_edit
        self.frame = ttk.Frame(parent)
        self.font = tkfont.Font(family="Consolas", size=10)
        self.hfont = tkfont.nametofont("TkDefaultFont").copy()
        self.hfont.configure(weight="bold")
        self.cw = self.font.measure("0")
        self.rh = self.font.metrics("linespace") + int(8 * s)
        self.aw = self.cw * 6 + int(16 * s)
        self.vw = self.cw * 8 + int(16 * s)
        self.pad = int(4 * s)
        self.head = tk.Canvas(self.frame, height=self.rh, bg=COL["head"], highlightthickness=0)
        self.cv = tk.Canvas(self.frame, bg=COL["bg"], highlightthickness=0, takefocus=1,
                            yscrollincrement=self.rh)
        self.sb = ttk.Scrollbar(self.frame, orient="vertical", command=self.cv.yview)
        self.hsb = ttk.Scrollbar(self.frame, orient="horizontal", command=self._xview)
        self.cv.configure(yscrollcommand=self.sb.set, xscrollcommand=self._xset)
        self.rows_setting = "Auto"  # or "10" / "20" / "30" / "40": fixed rows per column
        self.head.grid(row=0, column=0, sticky="ew")
        self.cv.grid(row=1, column=0, sticky="nsew")
        self.sb.grid(row=0, column=1, rowspan=2, sticky="ns")
        self.hsb.grid(row=2, column=0, sticky="ew")
        self.hsb.grid_remove()
        self.frame.rowconfigure(1, weight=1)
        self.frame.columnconfigure(0, weight=1)
        self.labels, self.values, self.items = [], [], []
        self.rows = self.ncols = 0
        self.sel = None
        self.on_context = None  # on_context(i, event): right-click on a cell
        self.cv.bind("<Button-3>", self._ctx)
        self.flash, self.flash_job = {}, None
        self.stale = False
        self.editor = self.edit_err = None
        self.cv.bind("<Configure>", lambda e: self._layout())
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Double-1>", self._dbl)
        self.cv.bind("<Key>", self._key)
        self.cv.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.cv.bind("<Shift-MouseWheel>", lambda e: self._xview("scroll", -1 if e.delta > 0 else 1, "units"))

    def _xview(self, *args):
        self.cv.xview(*args)
        self.head.xview_moveto(self.cv.xview()[0])

    def _xset(self, lo, hi):
        self.hsb.set(lo, hi)
        self.head.xview_moveto(float(lo))
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            if self.hsb.winfo_ismapped():
                self.hsb.grid_remove()
        elif not self.hsb.winfo_ismapped():
            self.hsb.grid()

    # ----- structure -----
    def set_cells(self, labels, chars=7):
        self.end_edit()
        self.labels = list(labels)
        self.values = [""] * len(self.labels)
        self.vw = self.cw * max(chars, 6) + int(16 * self.s)
        self.flash.clear()
        if self.sel is not None and self.sel >= len(self.labels):
            self.sel = None
        self._layout(force=True)

    def _layout(self, force=False):
        n = len(self.labels)
        pw = self.aw + self.vw
        fit = max(1, (max(self.cv.winfo_width(), 1) - self.pad) // pw)
        rows, ncols = mon.grid_layout(n, fit, self.rows_setting, self.ROWS_MIN)
        if not force and rows == self.rows:
            return
        self.rows, self.ncols = rows, ncols
        self._draw()

    def _xy(self, i):
        c, r = divmod(i, self.rows)
        return self.pad + c * (self.aw + self.vw), r * self.rh

    def _draw(self):
        self.end_edit()
        cv, hd, s = self.cv, self.head, self.s
        aw, vw, rh = self.aw, self.vw, self.rh
        pw = aw + vw
        cv.delete("all")
        hd.delete("all")
        self.items = []
        for c in range(self.ncols):
            x = self.pad + c * pw
            hd.create_text(x + aw - 6 * s, rh / 2, text="Address", anchor="e", font=self.hfont, fill=COL["headfg"])
            hd.create_text(x + pw - 6 * s, rh / 2, text="Value", anchor="e", font=self.hfont, fill=COL["headfg"])
            hd.create_line(x + pw, 3, x + pw, rh - 3, fill="#c3c8cf")
        fg = COL["stale"] if self.stale else COL["fg"]
        for i, lab in enumerate(self.labels):
            x, y = self._xy(i)
            band = COL["alt"] if (i % self.rows) % 2 else COL["bg"]
            cv.create_rectangle(x, y, x + aw, y + rh, fill=COL["addr"], outline=COL["line"])
            cv.create_text(x + aw - 6 * s, y + rh / 2, text=lab, anchor="e", font=self.font, fill=COL["addrfg"])
            rect = cv.create_rectangle(x + aw, y, x + pw, y + rh, outline=COL["line"],
                                       fill=COL["flash"] if i in self.flash else band)
            txt = cv.create_text(x + pw - 6 * s, y + rh / 2, text=self.values[i], anchor="e",
                                 font=self.font, fill=fg)
            self.items.append((rect, txt, band))
        width = self.pad * 2 + self.ncols * pw
        cv.configure(scrollregion=(0, 0, width, self.rows * rh + 1))
        hd.configure(scrollregion=(0, 0, max(width, cv.winfo_width()), rh))
        if width <= cv.winfo_width():
            cv.xview_moveto(0)
        self._draw_sel()

    # ----- values -----
    def set_values(self, vals, flash=True):
        was_stale, self.stale = self.stale, False
        now = time.monotonic()
        for i, v in enumerate(vals[:len(self.items)]):
            rect, txt, _band = self.items[i]
            if v != self.values[i]:
                if flash and self.values[i] != "":
                    self.flash[i] = now
                    self.cv.itemconfig(rect, fill=COL["flash"])
                self.values[i] = v
                self.cv.itemconfig(txt, text=v)
            if was_stale:
                self.cv.itemconfig(txt, fill=COL["fg"])
        if self.flash and not self.flash_job:
            self.flash_job = self.cv.after(300, self._unflash)

    def _unflash(self):
        self.flash_job = None
        now = time.monotonic()
        for i, t in list(self.flash.items()):
            if now - t >= 1.2:
                del self.flash[i]
                if i < len(self.items):
                    self.cv.itemconfig(self.items[i][0], fill=self.items[i][2])
        if self.flash:
            self.flash_job = self.cv.after(300, self._unflash)

    def set_stale(self):
        """Grey out the values (last poll failed / disconnected)."""
        self.stale = True
        for _r, txt, _b in self.items:
            self.cv.itemconfig(txt, fill=COL["stale"])

    # ----- selection & keyboard -----
    def _hit(self, e):
        x, y = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        pw = self.aw + self.vw
        if not self.rows or x < self.pad:
            return None
        c, r = int((x - self.pad) // pw), int(y // self.rh)
        if not (0 <= c < self.ncols and 0 <= r < self.rows):
            return None
        i = c * self.rows + r
        return i if i < len(self.labels) else None

    def _click(self, e):
        self.cv.focus_set()
        i = self._hit(e)
        if i is not None:
            self.select(i)

    def _ctx(self, e):
        self.cv.focus_set()
        i = self._hit(e)
        if i is not None:
            self.select(i)
            if self.on_context:
                self.on_context(i, e)

    def _dbl(self, e):
        i = self._hit(e)
        if i is not None:
            self.select(i)
            if self.on_edit:
                self.on_edit(i)

    def select(self, i):
        self.sel = i
        self._draw_sel()
        self._see(i)

    def _draw_sel(self):
        self.cv.delete("sel")
        if self.sel is None or self.sel >= len(self.labels):
            return
        x, y = self._xy(self.sel)
        self.cv.create_rectangle(x + 1, y + 1, x + self.aw + self.vw - 1, y + self.rh - 1,
                                 outline=COL["sel"], width=2, tags="sel")

    def _see(self, i):
        total = self.rows * self.rh
        if total <= 0:
            return
        x, y = self._xy(i)
        top, h = self.cv.canvasy(0), self.cv.winfo_height()
        if y < top:
            self.cv.yview_moveto(y / total)
        elif y + self.rh > top + h:
            self.cv.yview_moveto((y + self.rh - h) / total)
        width = self.pad * 2 + self.ncols * (self.aw + self.vw)
        left, w = self.cv.canvasx(0), self.cv.winfo_width()
        if width > w:
            if x < left:
                self._xview("moveto", max(x - self.pad, 0) / width)
            elif x + self.aw + self.vw > left + w:
                self._xview("moveto", (x + self.aw + self.vw + self.pad - w) / width)

    def _key(self, e):
        if not self.labels:
            return None
        i = self.sel if self.sel is not None else 0
        d = {"Up": -1, "Down": 1, "Left": -self.rows, "Right": self.rows}.get(e.keysym)
        if d is not None:
            j = i + d if self.sel is not None else 0
            if 0 <= j < len(self.labels):
                self.select(j)
            return "break"
        if e.keysym in ("Return", "KP_Enter", "F2") and self.sel is not None and self.on_edit:
            self.on_edit(self.sel)
            return "break"
        return None

    # ----- in-place editor -----
    def start_edit(self, i, commit):
        """commit(i, text) -> None on success, or an error string (editor stays open, turns red)."""
        self.end_edit()
        self._see(i)
        x, y = self._xy(i)
        e = tk.Entry(self.cv, font=self.font, justify="right", relief="solid", bd=1, highlightthickness=0)
        e.insert(0, self.values[i])
        e.select_range(0, "end")
        self.cv.create_window(x + self.aw, y, window=e, anchor="nw", width=self.vw, height=self.rh, tags="editor")
        self.editor = e

        def ok(_e=None):
            err = commit(i, e.get())
            if err:
                e.configure(bg="#ffd6d6")
                self.cv.delete("edit_err")
                if self.edit_err is None:
                    self.edit_err = tk.Label(self.cv, bg=COL["bad"], fg="white", padx=4, pady=1)
                self.edit_err.configure(text=err + "   (Esc = cancel)")
                self.cv.create_window(x + self.aw, y + self.rh, window=self.edit_err, anchor="nw",
                                      tags=("editor", "edit_err"))
            else:
                self.end_edit()
                self.cv.focus_set()
            return "break"

        def cancel(_e=None):
            self.end_edit()
            self.cv.focus_set()
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
        if self.editor:
            ed, self.editor = self.editor, None
            self.cv.delete("editor")
            ed.destroy()
            if self.edit_err is not None:
                self.edit_err.destroy()
                self.edit_err = None


MULTI_CHARS = {"Binary": 16, "Double": 24, "Float": 15, "Long": 11}


class MultiGrid:
    """Multi-type view on a Canvas: Address column + one column per data type, one row per register.
    Same look as RegGrid (flash on change, grey when stale, in-place editor). Cells are (row, column index)."""

    def __init__(self, parent, s, on_edit=None):
        self.s, self.on_edit = s, on_edit  # on_edit(row, col)
        self.frame = ttk.Frame(parent)
        self.font = tkfont.Font(family="Consolas", size=10)
        self.hfont = tkfont.nametofont("TkDefaultFont").copy()
        self.hfont.configure(weight="bold")
        self.cw = self.font.measure("0")
        self.rh = self.font.metrics("linespace") + int(8 * s)
        self.aw = self.cw * 6 + int(16 * s)
        self.pad = int(4 * s)
        self.head = tk.Canvas(self.frame, height=self.rh, bg=COL["head"], highlightthickness=0)
        self.cv = tk.Canvas(self.frame, bg=COL["bg"], highlightthickness=0, takefocus=1, yscrollincrement=self.rh)
        self.sb = ttk.Scrollbar(self.frame, orient="vertical", command=self.cv.yview)
        self.hsb = ttk.Scrollbar(self.frame, orient="horizontal", command=self._xview)
        self.cv.configure(yscrollcommand=self.sb.set, xscrollcommand=self._xset)
        self.head.grid(row=0, column=0, sticky="ew")
        self.cv.grid(row=1, column=0, sticky="nsew")
        self.sb.grid(row=0, column=1, rowspan=2, sticky="ns")
        self.hsb.grid(row=2, column=0, sticky="ew")
        self.hsb.grid_remove()
        self.frame.rowconfigure(1, weight=1)
        self.frame.columnconfigure(0, weight=1)
        self.cols, self.widths = [], []
        self.labels, self.values, self.raw, self.items = [], [], [], []
        self.sel, self.sel_col = None, 0
        self.on_context = None  # on_context(row, event)
        self.flash, self.flash_job = {}, None
        self.stale = False
        self.editor = self.edit_err = None
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Double-1>", self._dbl)
        self.cv.bind("<Button-3>", self._ctx)
        self.cv.bind("<Key>", self._key)
        self.cv.bind("<MouseWheel>", lambda e: self.cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.cv.bind("<Shift-MouseWheel>", lambda e: self._xview("scroll", -1 if e.delta > 0 else 1, "units"))
        self.cv.bind("<Configure>", lambda e: self._fit_head())

    def _xview(self, *args):
        self.cv.xview(*args)
        self.head.xview_moveto(self.cv.xview()[0])

    def _xset(self, lo, hi):
        self.hsb.set(lo, hi)
        self.head.xview_moveto(float(lo))
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            if self.hsb.winfo_ismapped():
                self.hsb.grid_remove()
        elif not self.hsb.winfo_ismapped():
            self.hsb.grid()

    def _fit_head(self):
        self.head.configure(scrollregion=(0, 0, max(self._width(), self.cv.winfo_width()), self.rh))

    # ----- structure -----
    def set_structure(self, labels, cols):
        """Row labels (one per register) and the data-type columns; clears the values."""
        self.end_edit()
        self.labels, self.cols = list(labels), list(cols)
        self.values = [[""] * len(self.cols) for _ in self.labels]
        self.raw = [None] * len(self.labels)
        m = int(16 * self.s)
        self.widths = [max(self.hfont.measure(c) + m, self.cw * next(
            (v for k, v in MULTI_CHARS.items() if c.startswith(k)), 6) + m) for c in self.cols]
        self.flash.clear()
        if self.sel is not None and self.sel >= len(self.labels):
            self.sel = None
        self.sel_col = min(self.sel_col, max(len(self.cols) - 1, 0))
        self._draw()

    def _width(self):
        return self.pad * 2 + self.aw + sum(self.widths)

    def _colx(self, c):
        return self.pad + self.aw + sum(self.widths[:c])

    def _draw(self):
        self.end_edit()
        cv, hd, s, rh, aw = self.cv, self.head, self.s, self.rh, self.aw
        cv.delete("all")
        hd.delete("all")
        self.items = []
        x0 = self.pad
        hd.create_text(x0 + aw - 6 * s, rh / 2, text="Address", anchor="e", font=self.hfont, fill=COL["headfg"])
        for c, name in enumerate(self.cols):
            x = self._colx(c)
            hd.create_text(x + self.widths[c] - 6 * s, rh / 2, text=name, anchor="e", font=self.hfont,
                           fill=COL["headfg"])
            hd.create_line(x, 3, x, rh - 3, fill="#c3c8cf")
        width = self._width()
        for i, lab in enumerate(self.labels):
            y = i * rh
            band = COL["alt"] if i % 2 else COL["bg"]
            cv.create_rectangle(x0, y, x0 + aw, y + rh, fill=COL["addr"], outline=COL["line"])
            cv.create_text(x0 + aw - 6 * s, y + rh / 2, text=lab, anchor="e", font=self.font, fill=COL["addrfg"])
            rect = cv.create_rectangle(x0 + aw, y, width - self.pad, y + rh, outline=COL["line"],
                                       fill=COL["flash"] if i in self.flash else band)
            txts = []
            for c in range(len(self.cols)):
                x = self._colx(c)
                v = self.values[i][c]
                txts.append(cv.create_text(x + self.widths[c] - 6 * s, y + rh / 2, text=v, anchor="e",
                                           font=self.font, fill=self._fg(v)))
            self.items.append((rect, txts, band))
        for c in range(1, len(self.cols)):
            x = self._colx(c)
            cv.create_line(x, 0, x, len(self.labels) * rh, fill=COL["line"])
        cv.configure(scrollregion=(0, 0, width, len(self.labels) * rh + 1))
        self._fit_head()
        if width <= cv.winfo_width():
            cv.xview_moveto(0)
        self._draw_sel()

    def _fg(self, v):
        return COL["stale"] if self.stale else ("#b4b9c0" if v == mon.MULTI_NONE else COL["fg"])

    # ----- values -----
    def set_values(self, rows, raw=None, flash=True):
        """rows: one list of texts (one per column) per register; raw: the register words (a row flashes when
        its own register changed, like RegGrid)."""
        was_stale, self.stale = self.stale, False
        now = time.monotonic()
        for i, texts in enumerate(rows[:len(self.items)]):
            rect, txts, _band = self.items[i]
            r = raw[i] if raw is not None and i < len(raw) else tuple(texts)
            if flash and self.raw[i] is not None and r != self.raw[i]:
                self.flash[i] = now
                self.cv.itemconfig(rect, fill=COL["flash"])
            self.raw[i] = r
            for c, v in enumerate(texts[:len(txts)]):
                if v != self.values[i][c] or was_stale:
                    self.values[i][c] = v
                    self.cv.itemconfig(txts[c], text=v, fill=self._fg(v))
        if self.flash and not self.flash_job:
            self.flash_job = self.cv.after(300, self._unflash)

    def _unflash(self):
        self.flash_job = None
        now = time.monotonic()
        for i, t in list(self.flash.items()):
            if now - t >= 1.2:
                del self.flash[i]
                if i < len(self.items):
                    self.cv.itemconfig(self.items[i][0], fill=self.items[i][2])
        if self.flash:
            self.flash_job = self.cv.after(300, self._unflash)

    def set_stale(self):
        """Grey out the values (last poll failed / disconnected)."""
        self.stale = True
        for _r, txts, _b in self.items:
            for t in txts:
                self.cv.itemconfig(t, fill=COL["stale"])

    # ----- selection & keyboard -----
    def _hit(self, e):
        """Event -> (row, column index or None for the Address column) or None."""
        x, y = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        r = int(y // self.rh)
        if not 0 <= r < len(self.labels) or x < self.pad:
            return None
        for c in range(len(self.cols)):
            if self._colx(c) <= x < self._colx(c) + self.widths[c]:
                return r, c
        return (r, None) if x < self.pad + self.aw else None

    def _click(self, e):
        self.cv.focus_set()
        h = self._hit(e)
        if h:
            self.select(h[0], h[1])

    def _ctx(self, e):
        self.cv.focus_set()
        h = self._hit(e)
        if h:
            self.select(h[0], h[1])
            if self.on_context:
                self.on_context(h[0], e)

    def _dbl(self, e):
        h = self._hit(e)
        if h and h[1] is not None:
            self.select(*h)
            if self.on_edit:
                self.on_edit(h[0], h[1])

    def select(self, row, col=None):
        self.sel = row
        if col is not None and self.cols:
            self.sel_col = max(0, min(col, len(self.cols) - 1))
        self._draw_sel()
        self._see(row, self.sel_col)

    def sel_format(self):
        return self.cols[self.sel_col] if self.cols and 0 <= self.sel_col < len(self.cols) else None

    def _draw_sel(self):
        self.cv.delete("sel")
        if self.sel is None or self.sel >= len(self.labels):
            return
        y, rh = self.sel * self.rh, self.rh
        self.cv.create_rectangle(self.pad + 1, y + 1, self._width() - self.pad - 1, y + rh - 1,
                                 outline="#9db8e6", width=1, tags="sel")
        if self.cols:
            x = self._colx(self.sel_col)
            self.cv.create_rectangle(x + 1, y + 1, x + self.widths[self.sel_col] - 1, y + rh - 1,
                                     outline=COL["sel"], width=2, tags="sel")

    def _see(self, row, col):
        total = len(self.labels) * self.rh
        if total <= 0:
            return
        y = row * self.rh
        top, h = self.cv.canvasy(0), self.cv.winfo_height()
        if y < top:
            self.cv.yview_moveto(y / total)
        elif y + self.rh > top + h:
            self.cv.yview_moveto((y + self.rh - h) / total)
        width = self._width()
        left, w = self.cv.canvasx(0), self.cv.winfo_width()
        if width > w and self.cols:
            x, cw = self._colx(col), self.widths[col]
            if x < left + self.aw:
                self._xview("moveto", max(x - self.aw - self.pad, 0) / width)
            elif x + cw > left + w:
                self._xview("moveto", (x + cw + self.pad - w) / width)

    def _key(self, e):
        if not self.labels:
            return None
        if e.keysym in ("Up", "Down", "Left", "Right"):
            if self.sel is None:
                self.select(0, 0)
            elif e.keysym in ("Up", "Down"):
                j = self.sel + (1 if e.keysym == "Down" else -1)
                if 0 <= j < len(self.labels):
                    self.select(j)
            else:
                self.select(self.sel, self.sel_col + (1 if e.keysym == "Right" else -1))
            return "break"
        if e.keysym in ("Return", "KP_Enter", "F2") and self.sel is not None and self.cols and self.on_edit:
            self.on_edit(self.sel, self.sel_col)
            return "break"
        return None

    # ----- in-place editor -----
    def start_edit(self, row, col, commit):
        """commit(row, col, text) -> None on success, or an error string (editor stays open, turns red)."""
        self.end_edit()
        self.select(row, col)
        cur = self.values[row][col]
        if cur == mon.MULTI_NONE and mon.multi_width(self.cols[col]) > 1:
            self.cv.bell()  # blank cell of a 2/4-register type: its value starts on another row
            return
        x, y, w = self._colx(col), row * self.rh, self.widths[col]
        e = tk.Entry(self.cv, font=self.font, justify="right", relief="solid", bd=1, highlightthickness=0)
        e.insert(0, "" if cur == mon.MULTI_NONE else cur)
        e.select_range(0, "end")
        self.cv.create_window(x, y, window=e, anchor="nw", width=w, height=self.rh, tags="editor")
        self.editor = e

        def ok(_e=None):
            err = commit(row, col, e.get())
            if err:
                e.configure(bg="#ffd6d6")
                self.cv.delete("edit_err")
                if self.edit_err is None:
                    self.edit_err = tk.Label(self.cv, bg=COL["bad"], fg="white", padx=4, pady=1)
                self.edit_err.configure(text=err + "   (Esc = cancel)")
                self.cv.create_window(x, y + self.rh, window=self.edit_err, anchor="nw", tags=("editor", "edit_err"))
            else:
                self.end_edit()
                self.cv.focus_set()
            return "break"

        def cancel(_e=None):
            self.end_edit()
            self.cv.focus_set()
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
        if self.editor:
            ed, self.editor = self.editor, None
            self.cv.delete("editor")
            ed.destroy()
            if self.edit_err is not None:
                self.edit_err.destroy()
                self.edit_err = None
