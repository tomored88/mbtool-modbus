"""Connection setup (F3), write dialog (F6), multi-type columns and CSV log options."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


WRITE_FUNCS = {5: "05 Write Single Coil", 6: "06 Write Single Register",
               15: "15 Write Multiple Coils", 16: "16 Write Multiple Registers",
               22: "22 Mask Write Register", 23: "23 Read/Write Multiple Registers"}


def split_values(text):
    return [t for t in re.split(r"[\s,;]+", str(text).strip()) if t]


def encode_many(text, fmt):
    words = []
    for tok in split_values(text):
        try:
            words += encode_value(tok, fmt)
        except ValueError:
            raise ValueError(f"'{tok}' is not a valid {fmt} value")
    if not words:
        raise ValueError("enter at least one value")
    return words


class MultiColumnsDialog(Dialog):
    """Checklist of the data types shown as columns in the multi-type view."""

    def __init__(self, app, current, apply, align=0, apply_align=None):
        super().__init__(app, "Multi-type view - columns")
        self.apply_fn = apply
        self.apply_align = apply_align
        self.align0 = mon.norm_multi_align(align)
        self.order = list(current)
        add_help_button(app, self.w, "poll.multi")
        ttk.Label(self.body, text="Show these data types as columns:").pack(anchor="w", pady=(0, 6))
        gf = ttk.Frame(self.body)
        gf.pack(fill="x")
        self.vars = {}
        for k, f in enumerate(mon.MULTI_FORMATS):
            v = tk.BooleanVar(value=f in current)
            self.vars[f] = v
            n = mon.multi_width(f)
            note = {1: "", 2: "  (2 registers)", 4: "  (4 registers)"}[n] + ("  (2 characters)" if f == "ASCII" else "")
            ttk.Checkbutton(gf, text=f + note, variable=v).grid(row=k % 6, column=k // 6, sticky="w", padx=(0, 18), pady=1)
        af = ttk.Frame(self.body)
        af.pack(fill="x", pady=(8, 0))
        ttk.Label(af, text="Pair alignment:").pack(side="left")
        self.align = tk.StringVar(value=str(self.align0))
        cb = ttk.Combobox(af, textvariable=self.align, values=["0", "1", "2", "3"], width=3, state="readonly")
        cb.pack(side="left", padx=(6, 8))
        ttk.Label(af, text="Long / Float / Double show their value on the row where they start. 0 = the first\n"
                           "polled register starts a pair; 1 = the second one does (32-bit values at odd registers).",
                  style="Hint.TLabel", justify="left").pack(side="left")
        Tooltip(cb, "Which register starts a 2- or 4-register value, counting from the first polled register")
        self.l_err = ttk.Label(self.body, text=" ", style="Hint.TLabel")
        self.l_err.pack(anchor="w", pady=(6, 0))
        bf = ttk.Frame(self.body)
        bf.pack(fill="x", pady=(6, 0))
        ttk.Button(bf, text="Default", command=self.default).pack(side="left")
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        self.b_ok = ttk.Button(bf, text="OK", command=self.ok, default="active")
        self.b_ok.pack(side="right", padx=(0, 6))
        self.w.bind("<Return>", lambda e: self.ok())
        self.place()

    def default(self):
        self.order = list(mon.MULTI_DEFAULT)
        for f, v in self.vars.items():
            v.set(f in mon.MULTI_DEFAULT)
        self.align.set("0")

    def picked(self):
        """Checked types: the current columns keep their order, newly checked ones follow in list order."""
        on = [f for f in mon.MULTI_FORMATS if self.vars[f].get()]
        return [f for f in self.order if f in on] + [f for f in on if f not in self.order]

    def ok(self):
        cols = self.picked()
        if not cols:
            self.l_err.configure(text="Pick at least one data type")
            return
        self.apply_fn(cols)
        if self.apply_align:
            self.apply_align(self.align.get())
        self.close()


class SetupDialog(Dialog):
    MODES = [("TCP", "Modbus TCP/IP"), ("RTU", "Modbus RTU (serial)"), ("ASCII", "Modbus ASCII (serial)")]

    def __init__(self, app, conn=None, on_done=None, title="Connection Setup", parent=None):
        super().__init__(app, title, modal=True)
        if parent is not None:
            self.w.transient(parent)
        self.on_done = on_done
        c = conn or app.cfg["conn"]
        b = self.body
        self.v = {k: tk.StringVar(value=c[k]) for k in c}
        self.v["parity"].set(PARITY_NAMES.get(c["parity"], "None"))
        mf = ttk.Frame(b)
        mf.pack(fill="x", pady=(0, 8))
        ttk.Label(mf, text="Connection:").pack(side="left", padx=(0, 8))
        for val, text in self.MODES:
            ttk.Radiobutton(mf, text=text, value=val, variable=self.v["mode"],
                            command=self._mode).pack(side="left", padx=(0, 10))
        self.hints = {}
        # TCP
        self.tcp = ttk.LabelFrame(b, text="TCP/IP", padding=10)
        _, self.hints["host"] = form_row(self.tcp, 0, "IP address / host", ttk.Entry(self.tcp, textvariable=self.v["host"], width=24))
        _, self.hints["port"] = form_row(self.tcp, 1, "Port", ttk.Entry(self.tcp, textvariable=self.v["port"], width=8),
                                         "TCP port of the slave (502 is the Modbus standard)")
        # serial
        self.ser = ttk.LabelFrame(b, text="Serial port", padding=10)
        pf = ttk.Frame(self.ser)
        self.com = ttk.Combobox(pf, textvariable=self.v["com"], width=34)
        self.com.pack(side="left")
        ttk.Button(pf, text="Refresh", command=self.refresh_ports, width=8).pack(side="left", padx=(6, 0))
        _, self.hints["com"] = form_row(self.ser, 0, "COM port", pf)
        Tooltip(self.com, "Serial port. The list shows the ports Windows reports; you may also type a name.")
        cb_baud = ttk.Combobox(self.ser, textvariable=self.v["baud"], values=BAUDS, width=10)
        _, self.hints["baud"] = form_row(self.ser, 1, "Baud rate", cb_baud)
        _, self.hints["data"] = form_row(self.ser, 2, "Data bits", ttk.Combobox(self.ser, textvariable=self.v["data"], values=["7", "8"], width=10, state="readonly"),
                                         "RTU needs 8 data bits; ASCII commonly uses 7")
        cb_par = ttk.Combobox(self.ser, textvariable=self.v["parity"], values=list(PARITY_NAMES.values()), width=10,
                              state="readonly")
        _, self.hints["parity"] = form_row(self.ser, 3, "Parity", cb_par)
        cb_stop = ttk.Combobox(self.ser, textvariable=self.v["stop"], values=["1", "2"], width=10, state="readonly")
        _, self.hints["stop"] = form_row(self.ser, 4, "Stop bits", cb_stop)
        for wdg, key in ((mf, "setup.mode"), (self.tcp, "setup.tcp"), (self.ser, "setup.serial"),
                         (cb_baud, "setup.baud"), (cb_par, "setup.parity"), (cb_stop, "setup.stop")):
            help_key(wdg, key)
        self.help_fields = {"mode": mf, "tcp": self.tcp, "serial": self.ser, "baud": cb_baud, "parity": cb_par,
                            "stop": cb_stop}
        self._help = lambda: "setup.tcp" if self.v["mode"].get() == "TCP" else "setup.serial"
        self.ser.columnconfigure(2, minsize=int(170 * app.s))
        self.tcp.columnconfigure(2, minsize=int(170 * app.s))
        # common
        self.common = ttk.Frame(b, padding=(10, 8, 10, 0))
        _, self.hints["timeout"] = form_row(self.common, 0, "Response timeout (ms)",
                                            ttk.Entry(self.common, textvariable=self.v["timeout"], width=8),
                                            "How long to wait for each reply (50-60000 ms)")
        bf = ttk.Frame(b)
        bf.pack(side="bottom", fill="x", pady=(12, 0))
        self.b_help = add_help_button(app, self.w, self._help, parent=bf, side="left")
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        ttk.Button(bf, text="Save" if on_done is None else "OK", command=lambda: self.ok(False)).pack(side="right", padx=6)
        self.b_ok = ttk.Button(bf, text="Connect", command=lambda: self.ok(True), default="active")
        if on_done is None:  # the device editor only collects settings
            self.b_ok.pack(side="right")
        Tooltip(self.b_ok, "Save these settings and connect   (Enter)")
        self.w.bind("<Return>", lambda e: self.ok(True))
        self.refresh_ports(initial=True)
        self._mode()
        self.place()

    def refresh_ports(self, initial=False):
        ports = list_com_ports()
        self.com.configure(values=[f"{d}  -  {desc}" if desc else d for d, desc in ports])
        if not ports:
            self.hints["com"].configure(text="No serial ports found - plug in the adapter, then Refresh",
                                        style="Hint.TLabel")
        else:
            self.hints["com"].configure(text=f"{len(ports)} port(s) found", style="Info.TLabel")
            cur = self.v["com"].get().split(" ")[0]
            if not initial or cur not in [d for d, _ in ports]:
                if cur not in [d for d, _ in ports]:
                    self.v["com"].set(ports[0][0])
        return ports

    def _mode(self):
        serial_mode = self.v["mode"].get() != "TCP"
        self.tcp.pack_forget()
        self.ser.pack_forget()
        self.common.pack_forget()
        (self.ser if serial_mode else self.tcp).pack(fill="x")
        self.common.pack(fill="x")
        if self.v["mode"].get() == "ASCII" and self.v["data"].get() == "8":
            pass  # leave the user's choice; hint only
        self.w.update_idletasks()

    def collect(self):
        """Validate; returns (conn dict, errors)."""
        v = self.v
        conn = {k: var.get().strip() for k, var in v.items()}
        conn["com"] = conn["com"].split(" ")[0]
        conn["parity"] = {n: k for k, n in PARITY_NAMES.items()}.get(conn["parity"], "N")
        errs = {}
        try:
            int_in(conn["timeout"], 50, 60000)
        except ValueError as e:
            errs["timeout"] = str(e)
        if conn["mode"] == "TCP":
            if not conn["host"]:
                errs["host"] = "required"
            try:
                int_in(conn["port"], 1, 65535)
            except ValueError as e:
                errs["port"] = str(e)
        else:
            if not conn["com"]:
                errs["com"] = "choose a COM port"
            try:
                int_in(conn["baud"], 50, 4000000)
            except ValueError as e:
                errs["baud"] = str(e)
            if conn["mode"] == "RTU" and conn["data"] != "8":
                errs["data"] = "RTU requires 8 data bits"
        return conn, errs

    def ok(self, connect):
        conn, errs = self.collect()
        keep = {"com": self.hints["com"].cget("text")} if "com" not in errs else {}
        for k, h in self.hints.items():
            if k in errs:
                h.configure(text=errs[k], style="Hint.TLabel")
            elif k not in keep:
                h.configure(text="")
        if errs:
            return
        self.close()
        if self.on_done:
            self.on_done(conn)
            return
        self.app.apply_conn(conn, connect)


class WriteDialog(Dialog):
    SINGLE = ["Unsigned", "Signed", "Hex", "Binary"]

    def __init__(self, app, target=None):
        # target: what the dialog writes through - the main window (default) or a PollTab (same attributes:
        # p, grid, mgrid, multi_shown, cell_addrs, addr_base(), run_bg(), write_done())
        self.t = target or app
        super().__init__(app, "Write" if target is None else f"Write - {target.name}")
        self.b_help = add_help_button(app, self.w, "write")
        b = self.body
        s = app.s
        self.v = {k: tk.StringVar(value=d) for k, d in dict(
            func=WRITE_FUNCS[6], unit="1", addr="0", coil="1", value="0", values="", fmt="Unsigned",
            and_="0xFFFF", or_="0x0000", raddr="0", rqty="1").items()}
        self.hints, self.rows = {}, []
        fcb = ttk.Combobox(b, textvariable=self.v["func"], values=list(WRITE_FUNCS.values()), width=34, state="readonly")
        form_row(b, 0, "Function", fcb, hint=False)
        fcb.bind("<<ComboboxSelected>>", lambda e: self._func())
        r = 1

        def row(key, label, widget, fcs, tip=None):
            nonlocal r
            lab, h = form_row(b, r, label, widget, tip)
            self.hints[key] = h
            self.rows.append((fcs, (lab, widget, h)))
            r += 1
            return lab
        ALL = (5, 6, 15, 16, 22, 23)
        row("unit", "Slave ID", ttk.Entry(b, textvariable=self.v["unit"], width=8), ALL)
        self.l_addr = row("addr", "Address", ttk.Entry(b, textvariable=self.v["addr"], width=10), ALL,
                          "Start address (follows the 'PLC addresses' setting of the main window)")
        cf = ttk.Frame(b)
        ttk.Radiobutton(cf, text="ON  (1)", value="1", variable=self.v["coil"]).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(cf, text="OFF  (0)", value="0", variable=self.v["coil"]).pack(side="left")
        row("coil", "Value", cf, (5,))
        row("value", "Value", ttk.Entry(b, textvariable=self.v["value"], width=16), (6,))
        row("raddr", "Read address", ttk.Entry(b, textvariable=self.v["raddr"], width=10), (23,))
        row("rqty", "Read quantity", ttk.Entry(b, textvariable=self.v["rqty"], width=8), (23,), "1-125 registers")
        self.l_vals = row("values", "Values", ttk.Entry(b, textvariable=self.v["values"], width=36), (15, 16, 23),
                          "Several values separated by spaces or commas")
        self.fmt_cb = ttk.Combobox(b, textvariable=self.v["fmt"], values=FORMATS, width=16, state="readonly")
        row("fmt", "Format", self.fmt_cb, (6, 16, 23), "How the typed values are converted to registers")
        row("and_", "AND mask", ttk.Entry(b, textvariable=self.v["and_"], width=10), (22,), "0x0000-0xFFFF")
        row("or_", "OR mask", ttk.Entry(b, textvariable=self.v["or_"], width=10), (22,), "0x0000-0xFFFF")
        self.l_help = ttk.Label(b, style="Info.TLabel", wraplength=int(380 * s), justify="left")
        self.l_help.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.l_res = ttk.Label(b, text="", wraplength=int(440 * s), justify="left")
        self.l_res.grid(row=r + 1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        b.columnconfigure(2, minsize=int(170 * s))
        bf = ttk.Frame(b)
        bf.grid(row=r + 2, column=0, columnspan=3, sticky="e", pady=(10, 0))
        self.b_send = ttk.Button(bf, text="Send", command=self.send, default="active")
        self.b_send.pack(side="left", padx=(0, 6))
        ttk.Button(bf, text="Close", command=self.close).pack(side="left")
        Tooltip(self.b_send, "Send the request   (Enter)")
        self.w.bind("<Return>", lambda e: self.send())
        self.prefill()
        self.place()

    def prefill(self):
        """Use the main window's (or the poll tab's) Slave ID and selected cell (if any)."""
        app = self.t
        p = app.p
        if p:
            self.v["unit"].set(str(p["unit"]))
        sel = app.grid.sel
        mg = app.mgrid
        if (app.multi_shown and p and not p.get("preset") and p["fc"] not in (1, 2)
                and mg.sel is not None and mg.sel < len(mg.labels) and mg.cols):
            a, fmt = p["addr"] + mg.sel, mg.sel_format()  # multi-type view: the selected cell's row and type
            cur = mg.values[mg.sel][mg.sel_col]
            if fmt == "ASCII":
                raw = mg.raw[mg.sel]
                fmt, cur = "Hex", (f"0x{raw:04X}" if isinstance(raw, int) else "")
            if cur == mon.MULTI_NONE:
                cur = ""
            fc = 6 if fmt_width(fmt) == 1 else 16
            self.v["func"].set(WRITE_FUNCS[fc])
            self.v["addr"].set(addr_label(fc, a, p["plc"]))
            self.v["fmt"].set(fmt)
            self.v["value" if fc == 6 else "values"].set(cur)
        elif p and not p.get("preset") and sel is not None and sel < len(app.cell_addrs):
            a = app.cell_addrs[sel]
            cur = app.grid.values[sel]
            if p["fc"] in (1, 2):
                self.v["func"].set(WRITE_FUNCS[5])
                self.v["addr"].set(addr_label(5, a, p["plc"]))
                self.v["coil"].set("0" if cur == "1" else "1")
            else:
                fc = 6 if fmt_width(p["fmt"]) == 1 else 16
                self.v["func"].set(WRITE_FUNCS[fc])
                self.v["addr"].set(addr_label(fc, a, p["plc"]))
                self.v["fmt"].set(p["fmt"])
                self.v["value" if fc == 6 else "values"].set(cur)
        self._func()

    def _fc(self):
        return int(self.v["func"].get()[:2])

    def _func(self):
        fc = self._fc()
        for fcs, widgets in self.rows:
            for wd in widgets:
                if fc in fcs:
                    wd.grid()
                else:
                    wd.grid_remove()
        self.l_addr.configure(text="Write address" if fc == 23 else "Address")
        self.l_vals.configure(text="Values (0/1)" if fc == 15 else "Values")
        self.fmt_cb.configure(values=self.SINGLE if fc == 6 else FORMATS)
        if fc == 6 and self.v["fmt"].get() not in self.SINGLE:
            self.v["fmt"].set("Unsigned")
        self.l_help.configure(text={
            5: "Sets one coil ON or OFF.",
            6: "Writes one 16-bit holding register.",
            15: "Writes several coils, e.g.  1 0 1 1",
            16: "Writes several holding registers, e.g.  10 20 30   (Float/Long values use 2 registers each)",
            22: "Result = (Current AND And_Mask) OR (Or_Mask AND NOT And_Mask)",
            23: "Writes the values, then reads 'Read quantity' registers from 'Read address' in one request.",
        }[fc])
        self.l_res.configure(text="")
        for h in self.hints.values():
            h.configure(text="")

    def _result(self, text, kind):
        self.l_res.configure(text=text, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel"}.get(kind, "SInfo.TLabel"))

    def send(self):
        fc, plc, v = self._fc(), self.t.addr_base(), self.v
        errs, val = {}, {}

        def chk(k, fn):
            try:
                val[k] = fn(v[k].get())
            except ValueError as e:
                errs[k] = str(e)
        chk("unit", lambda t: int_in(t, 0, 255))
        chk("addr", lambda t: parse_addr(t, fc, plc))
        fmt = v["fmt"].get()
        if fc == 6:
            def one(t):
                try:
                    return encode_value(t.strip(), fmt)[0]
                except ValueError:
                    raise ValueError(f"not a valid {fmt} value")
            chk("value", one)
        elif fc == 15:
            def bits(t):
                toks = split_values(t)
                if not 1 <= len(toks) <= 1968:
                    raise ValueError("enter 1-1968 values (0/1)")
                return [parse_bit(x) for x in toks]
            chk("values", bits)
        elif fc in (16, 23):
            def words(t):
                w = encode_many(t, fmt)
                lim = 123 if fc == 16 else 121
                if len(w) > lim:
                    raise ValueError(f"too many registers ({len(w)} > {lim})")
                return w
            chk("values", words)
        elif fc == 22:
            chk("and_", lambda t: int_in(t, 0, 0xFFFF))
            chk("or_", lambda t: int_in(t, 0, 0xFFFF))
        if fc == 23:
            chk("raddr", lambda t: parse_addr(t, 23, plc))
            chk("rqty", lambda t: int_in(t, 1, 125))
        n = len(val.get("values", [])) or 1
        if "addr" in val and val["addr"] + n > 65536:
            errs["values"] = "runs past address 65535"
        for k, h in self.hints.items():
            h.configure(text=errs.get(k, ""))
        if errs:
            self._result("Please fix the fields marked in red.", "bad")
            return
        unit, addr = val["unit"], val["addr"]
        lab = addr_label(fc, addr, plc)
        if fc == 5:
            on = v["coil"].get() == "1"
            fn, desc = (lambda m: m.write_coil(unit, addr, on)), f"FC05 coil {lab} = {'ON' if on else 'OFF'}"
        elif fc == 6:
            fn, desc = (lambda m: m.write_register(unit, addr, val["value"])), f"FC06 register {lab} = {v['value'].get().strip()}"
        elif fc == 15:
            fn, desc = (lambda m: m.write_coils(unit, addr, val["values"])), f"FC15 {len(val['values'])} coils from {lab}"
        elif fc == 16:
            fn, desc = (lambda m: m.write_registers(unit, addr, val["values"])), f"FC16 {len(val['values'])} registers from {lab}"
        elif fc == 22:
            fn, desc = (lambda m: m.mask_write(unit, addr, val["and_"], val["or_"])), \
                f"FC22 register {lab} AND 0x{val['and_']:04X} OR 0x{val['or_']:04X}"
        else:
            fn = lambda m: m.read_write(unit, val["raddr"], val["rqty"], addr, val["values"])
            desc = f"FC23 {len(val['values'])} registers to {lab}, read {val['rqty']} from {addr_label(23, val['raddr'], plc)}"
        self._result("Sending...", "info")
        self.t.run_bg(fn, lambda r, err: self._done(fc, fmt, desc, r, err))

    def _done(self, fc, fmt, desc, r, err):
        self.t.write_done(desc, err)
        if not self.alive():
            return
        if err:
            self._result(f"Failed: {err}", "bad")
        elif fc == 23 and r is not None:
            shown = ", ".join(t for _i, t in format_registers(r, fmt if fmt in FORMATS else "Unsigned"))
            self._result(f"OK - {desc}\nRead back: {shown}", "ok")
        else:
            self._result(f"OK - {desc}", "ok")


DELIMS = {"Comma  ,": ",", "Semicolon  ;": ";", "Tab": "\t", "Pipe  |": "|"}


DECIMALS = {"Point  1.5": ".", "Comma  1,5": ","}


TIME_FMT_NAMES = {"iso": "ISO date + time", "iso_ms": "ISO date + time + ms", "unix": "Unix seconds",
                  "excel": "Excel date number"}


ENCODINGS = {"utf-8": "UTF-8", "utf-8-sig": "UTF-8 with BOM for Excel"}


class LogOptionsDialog(Dialog):
    """CSV log format and rotation (datalog.CsvLog), validated inline; stored in the settings and the workspace."""

    def __init__(self, app):
        super().__init__(app, "Log options")
        self.b_help = add_help_button(app, self.w, "log")
        s = app.s
        o = mon.norm_log_options(app.cfg["log"])
        b = self.body
        self.v = {"rotate": tk.StringVar(value=o["rotate"]), "size_mb": tk.StringVar(value=o["size_mb"]),
                  "keep": tk.StringVar(value=o["keep"]),
                  "delimiter": tk.StringVar(value=next((k for k, v in DELIMS.items() if v == o["delimiter"]), "Comma  ,")),
                  "decimal": tk.StringVar(value=next((k for k, v in DECIMALS.items() if v == o["decimal"]), "Point  1.5")),
                  "time_format": tk.StringVar(value=TIME_FMT_NAMES.get(o["time_format"], TIME_FMT_NAMES["iso_ms"])),
                  "encoding": tk.StringVar(value=o["encoding"])}
        self.keep_on = tk.BooleanVar(value=o["keep_on"])
        self.hints = {}
        rf = ttk.LabelFrame(b, text="New file", padding=(10, 4, 10, 6))
        rf.pack(fill="x")
        for i, (val, text) in enumerate((("none", "Never - append to one file"),
                                         ("daily", "Every day (name_YYYY-MM-DD.csv)"))):
            ttk.Radiobutton(rf, text=text, value=val, variable=self.v["rotate"]).grid(row=i, column=0, sticky="w")
        szf = ttk.Frame(rf)
        szf.grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(szf, text="When the file reaches", value="size", variable=self.v["rotate"]).pack(side="left")
        self.e_size = ttk.Entry(szf, textvariable=self.v["size_mb"], width=7)
        self.e_size.pack(side="left", padx=(6, 0))
        ttk.Label(szf, text="MB   (name_YYYY-MM-DD_001.csv, _002, ...)").pack(side="left", padx=(4, 0))
        self.l_rot_hint = ttk.Label(rf, text="", style="Hint.TLabel")
        self.l_rot_hint.grid(row=3, column=0, columnspan=2, sticky="w")
        kf = ttk.Frame(rf)
        kf.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.cb_keep = ttk.Checkbutton(kf, text="Keep only the last", variable=self.keep_on)
        self.cb_keep.pack(side="left")
        self.e_keep = ttk.Entry(kf, textvariable=self.v["keep"], width=6)
        self.e_keep.pack(side="left", padx=(4, 4))
        ttk.Label(kf, text="files").pack(side="left")
        ttk.Label(rf, text="⚠ When on, OLDER rotated files of this log (same name pattern in the same folder) are "
                           "DELETED. Other files are never touched.", style="Warn.TLabel",
                  wraplength=int(470 * s), justify="left").grid(row=6, column=0, columnspan=2, sticky="w")
        self.hints["keep"] = ttk.Label(rf, text="", style="Hint.TLabel")
        self.hints["keep"].grid(row=7, column=0, columnspan=2, sticky="w")
        ff = ttk.LabelFrame(b, text="Format", padding=(10, 4, 10, 6))
        ff.pack(fill="x", pady=(8, 0))
        rows = (("delimiter", "Column separator", list(DELIMS)), ("decimal", "Decimal separator", list(DECIMALS)),
                ("time_format", "Time column", list(TIME_FMT_NAMES.values())))
        for r, (k, lab, vals) in enumerate(rows):
            _l, self.hints[k] = form_row(ff, r, lab, ttk.Combobox(ff, textvariable=self.v[k], values=vals, width=24,
                                                                  state="readonly"))
        ttk.Label(ff, text="Encoding").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=3)
        ef = ttk.Frame(ff)
        ef.grid(row=3, column=1, columnspan=2, sticky="w")
        for val, text in ENCODINGS.items():
            ttk.Radiobutton(ef, text=text, value=val, variable=self.v["encoding"]).pack(side="left", padx=(0, 10))
        ttk.Label(ff, text="Example:").grid(row=4, column=0, sticky="ne", padx=(0, 8), pady=(6, 0))
        self.l_ex = ttk.Label(ff, text="", font=("Consolas", 9), justify="left")
        self.l_ex.grid(row=4, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ff.columnconfigure(2, minsize=int(150 * s))
        self.l_res = ttk.Label(b, text="", style="Hint.TLabel", wraplength=int(470 * s))
        self.l_res.pack(fill="x", pady=(6, 0))
        bf = ttk.Frame(b)
        bf.pack(fill="x", pady=(8, 0))
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        self.b_ok = ttk.Button(bf, text="OK", command=self.ok, default="active")
        self.b_ok.pack(side="right", padx=(0, 6))
        self.w.bind("<Return>", lambda e: self.ok())
        for var in list(self.v.values()) + [self.keep_on]:
            var.trace_add("write", lambda *_: self.validate())
        self.validate()
        self.place()

    def options(self):
        v = self.v
        return {"rotate": v["rotate"].get(), "size_mb": v["size_mb"].get().strip(), "keep_on": self.keep_on.get(),
                "keep": v["keep"].get().strip(), "delimiter": DELIMS.get(v["delimiter"].get(), ","),
                "decimal": DECIMALS.get(v["decimal"].get(), "."),
                "time_format": next((k for k, n in TIME_FMT_NAMES.items() if n == v["time_format"].get()), "iso_ms"),
                "encoding": v["encoding"].get()}

    def validate(self):
        o = self.options()
        self.e_size.configure(state="normal" if o["rotate"] == "size" else "disabled")
        self.e_keep.configure(state="normal" if o["keep_on"] else "disabled")
        _kw, errs = mon.log_kwargs(o)
        for k, h in self.hints.items():
            h.configure(text=errs.get(k, ""))
        self.l_rot_hint.configure(text=errs.get("size_mb") or errs.get("rotate") or "")
        t = time.time()
        d = o["delimiter"]
        num = "230,4" if o["decimal"] == "," else "230.4"
        q = '"' if d == "," and o["decimal"] == "," else ""
        self.l_ex.configure(text=f"Time{d}Voltage [V]{d}Mode\n{TIME_FORMATS[o['time_format']](t)}{d}{q}{num}{q}{d}1"
                                 .replace("\t", "  →  "))
        self.b_ok.state(["disabled"] if errs else ["!disabled"])
        self.l_res.configure(text="Fix the fields marked in red." if errs else "")
        return not errs

    def ok(self):
        if not self.validate():
            return False
        self.close()
        self.app.apply_log_options(self.options())
        return True
