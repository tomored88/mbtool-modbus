"""Data export (MQTT / SQLite), the update dialog and the commissioning report dialog."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


class ExportWindow:
    """Tools > Data export: send every Devices reading to an MQTT broker and / or a SQLite file (export.py)."""

    def __init__(self, app):
        import export
        self.app, s = app, app.s
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - Data export")
        help_key(w, "export")
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self.close)
        c = export.norm_export(app.cfg.get("export"))
        b = ttk.Frame(w, padding=12)
        b.pack(fill="both", expand=True)
        ttk.Label(b, text="Every reading of the devices in the Devices window (F11) is sent while polling runs.\n"
                          "Sending happens in the background: a slow broker or disk never slows the polling.",
                  style="Info.TLabel", justify="left").pack(anchor="w")
        self.v = {}
        mf = ttk.LabelFrame(b, text="MQTT broker", padding=(10, 4, 10, 6))
        mf.pack(fill="x", pady=(8, 0))
        self.mq_on = tk.BooleanVar(value=c["mqtt"]["on"])
        ttk.Checkbutton(mf, text="Publish to MQTT", variable=self.mq_on).grid(row=0, column=0, columnspan=4, sticky="w")
        self.hints = {}
        rows = (("host", "Broker host", 26), ("port", "Port", 7), ("user", "User name", 18),
                ("client_id", "Client ID", 18), ("prefix", "Topic prefix", 26), ("min_interval_s", "Min. interval (s)", 7))
        for i, (k, lab, wd) in enumerate(rows):
            r, col = 1 + i // 2, (i % 2) * 2
            ttk.Label(mf, text=lab + ":").grid(row=r * 2 - 1, column=col, sticky="e", padx=(0 if col == 0 else 14, 4))
            self.v[k] = tk.StringVar(value=c["mqtt"][k])
            ttk.Entry(mf, textvariable=self.v[k], width=wd).grid(row=r * 2 - 1, column=col + 1, sticky="w")
            self.hints[k] = ttk.Label(mf, text="", style="Hint.TLabel")
            self.hints[k].grid(row=r * 2, column=col + 1, sticky="w")
        ttk.Label(mf, text="Password:").grid(row=7, column=0, sticky="e", padx=(0, 4))
        self.pw = tk.StringVar(value=app.export_pw)
        ttk.Entry(mf, textvariable=self.pw, width=26, show="•").grid(row=7, column=1, sticky="w")
        ttk.Label(mf, text="kept for this session only, never saved", style="Info.TLabel").grid(
            row=7, column=2, columnspan=2, sticky="w", padx=(14, 0))
        of = ttk.Frame(mf)
        of.grid(row=8, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.tls = tk.BooleanVar(value=c["mqtt"]["tls"])
        ttk.Checkbutton(of, text="TLS (usually port 8883)", variable=self.tls).pack(side="left")
        self.retain = tk.BooleanVar(value=c["mqtt"]["retain"])
        cr = ttk.Checkbutton(of, text="Retain", variable=self.retain)
        cr.pack(side="left", padx=(12, 0))
        Tooltip(cr, "Broker keeps the last message per topic for clients that subscribe later")
        ttk.Label(of, text="Payload:").pack(side="left", padx=(16, 4))
        self.style = tk.StringVar(value=c["mqtt"]["style"])
        ttk.Radiobutton(of, text="one JSON per device", value="json", variable=self.style).pack(side="left")
        ttk.Radiobutton(of, text="one topic per point", value="topics", variable=self.style).pack(side="left",
                                                                                                  padx=(8, 0))
        self.l_topic = ttk.Label(mf, text="", style="Info.TLabel", font=("Consolas", 9))
        self.l_topic.grid(row=9, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(mf, text="Test connection", command=self.test).grid(row=10, column=0, sticky="w", pady=(6, 0))
        self.l_test = ttk.Label(mf, text="", style="Info.TLabel")
        self.l_test.grid(row=10, column=1, columnspan=3, sticky="w", pady=(6, 0), padx=(6, 0))
        sf = ttk.LabelFrame(b, text="Database (SQLite file)", padding=(10, 4, 10, 6))
        sf.pack(fill="x", pady=(8, 0))
        self.sq_on = tk.BooleanVar(value=c["sqlite"]["on"])
        ttk.Checkbutton(sf, text="Write to a SQLite database", variable=self.sq_on).grid(row=0, column=0,
                                                                                       columnspan=3, sticky="w")
        self.v["path"] = tk.StringVar(value=c["sqlite"]["path"])
        ttk.Entry(sf, textvariable=self.v["path"], width=56).grid(row=1, column=0, sticky="we")
        ttk.Button(sf, text="Browse...", command=self.browse).grid(row=1, column=1, padx=(4, 0))
        ttk.Label(sf, text="Table readings(ts, time, device, point, value, text, ok) - open it with DB Browser for "
                           "SQLite, Excel (ODBC), Python, Grafana ...", style="Info.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        bf = ttk.Frame(b)
        bf.pack(fill="x", pady=(10, 0))
        self.b_run = ttk.Button(bf, text="Start export", command=self.toggle)
        self.b_run.pack(side="left")
        add_help_button(app, w, "export", parent=bf, side="right")
        self.l_state = ttk.Label(b, text="", style="Conn.TLabel")
        self.l_state.pack(anchor="w", pady=(8, 0))
        self.l_stats = ttk.Label(b, text="", style="Info.TLabel", justify="left")
        self.l_stats.pack(anchor="w")
        self.l_err = ttk.Label(b, text="", style="Hint.TLabel", wraplength=int(560 * s), justify="left")
        self.l_err.pack(anchor="w")
        for var in list(self.v.values()) + [self.style]:
            var.trace_add("write", lambda *_: self._changed())
        self._changed()
        self.job = None
        self._tick()

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        self.save_cfg()
        if self.job is not None:
            try:
                self.w.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if self.alive():
            self.w.destroy()

    def cfg(self):
        import export
        m = {k: self.v[k].get().strip() for k in ("host", "port", "user", "client_id", "prefix", "min_interval_s")}
        m.update(on=bool(self.mq_on.get()), tls=bool(self.tls.get()), retain=bool(self.retain.get()),
                 style=self.style.get())
        return export.norm_export({"mqtt": m, "sqlite": {"on": bool(self.sq_on.get()),
                                                         "path": self.v["path"].get().strip()}})

    def save_cfg(self):
        self.app.cfg["export"] = self.cfg()
        self.app.export_pw = self.pw.get()
        save_settings(self.app.cfg)

    def _changed(self):
        import export
        c = self.cfg()["mqtt"]
        errs = export.check_mqtt(c) if self.mq_on.get() else {}
        for k, h in self.hints.items():
            h.configure(text=errs.get(k, ""))
        pre = c["prefix"] or "mbtool"
        self.l_topic.configure(text=(f"Topic: {pre}/<device>   payload {{\"ts\": ..., \"ok\": true, \"values\": {{...}}}}"
                                     if c["style"] == "json" else
                                     f"Topics: {pre}/<device>/<point> = value,   {pre}/<device>/status"))

    def browse(self):
        p = filedialog.asksaveasfilename(parent=self.w, title="SQLite database file", defaultextension=".db",
                                         filetypes=[("SQLite database", "*.db *.sqlite"), ("All files", "*.*")],
                                         confirmoverwrite=False, initialfile=os.path.basename(self.v["path"].get())
                                         or "mbtool_data.db")
        if p:
            self.v["path"].set(p)

    def test(self):
        import export
        self.l_test.configure(text="Connecting...", style="Info.TLabel")
        self.w.update_idletasks()
        err = export.test_mqtt(self.cfg(), self.pw.get())
        self.l_test.configure(text=("OK - published a test message to " + self.cfg()["mqtt"]["prefix"] + "/_test")
                              if err is None else f"Failed: {err}", style="SOk.TLabel" if err is None else "SBad.TLabel")
        return err

    def toggle(self):
        import export
        if self.app.exporter is not None:
            self.app.stop_export()
            self._tick(once=True)
            return None
        self.save_cfg()
        try:
            self.app.start_export(self.cfg(), self.pw.get())
        except (export.ExportError, OSError, ValueError) as e:
            self.l_err.configure(text=f"Not started: {e}")
            return str(e)
        self.l_err.configure(text="")
        self._tick(once=True)
        return None

    def _tick(self, once=False):
        if not self.alive():
            return
        ex = self.app.exporter
        if ex is None:
            self.b_run.configure(text="Start export")
            self.l_state.configure(text="○ Export stopped", foreground=COL["stale"])
            self.l_stats.configure(text="")
        else:
            st = ex.stats
            self.b_run.configure(text="Stop export")
            parts = []
            if ex.mqtt_on:
                parts.append("MQTT " + ("connected" if st["mqtt_connected"] else "connecting..."))
            if ex.sql_on:
                parts.append("SQLite " + os.path.basename(ex.cfg["sqlite"]["path"]))
            running = self.app.devices_running()
            self.l_state.configure(text="● Exporting: " + ",  ".join(parts) + ("" if running else
                                   "   (no device is polling - Devices > Start all)"),
                                   foreground="#1e7b34" if (st["mqtt_connected"] or not ex.mqtt_on) else "#a15c00")
            last = time.strftime("%H:%M:%S", time.localtime(st["last_sent_t"])) if st["last_sent_t"] else "-"
            self.l_stats.configure(text=f"MQTT messages {st['mqtt_sent']}  (last {last}, waiting {st['mqtt_backlog']}, "
                                        f"skipped by min. interval {st['skipped']})   SQLite rows {st['sql_rows']}   "
                                        f"dropped {st['dropped']}")
            err = st["last_error"]
            self.l_err.configure(text=(f"Last problem {time.strftime('%H:%M:%S', time.localtime(st['last_error_t']))}: "
                                       f"{err}") if err else "")
        if not once:
            self.job = self.w.after(1000, self._tick)


def updates_mod():
    import updates
    return updates


class UpdateDialog(Dialog):
    """A newer release exists: what is new, download link (opened in the browser), skip this version, later."""

    def __init__(self, app, res):
        super().__init__(app, "Update available")
        self.res = res
        s = app.s
        b = self.body
        ttk.Label(b, text=f"mbtool {res['latest']} is available", style="Conn.TLabel").pack(anchor="w")
        pub = f", released {res['published']}" if res.get("published") else ""
        ttk.Label(b, text=f"You have {res['current']}{pub}. Your settings, devices and presets are kept when you "
                          "install the new version over this one.", style="Info.TLabel",
                  wraplength=int(520 * s), justify="left").pack(anchor="w", pady=(2, 6))
        notes = tk.Text(b, width=72, height=12, wrap="word", font=("Segoe UI", 9), relief="flat", bg=COL["alt"])
        notes.insert("1.0", res.get("notes") or "(no release notes)")
        notes.configure(state="disabled")
        notes.pack(fill="both", expand=True)
        self.portable = appinfo.PORTABLE
        self.url = (res.get("portable_url") if self.portable else res.get("setup_url")) or res.get("page")
        how = ("Portable: unzip the new zip and copy your data folder next to the new mbtool.exe."
               if self.portable else "Run the downloaded mbtool-setup.exe and install into the same folder.")
        ttk.Label(b, text=how, style="Info.TLabel", wraplength=int(520 * s), justify="left").pack(anchor="w", pady=(6, 0))
        self.auto = tk.BooleanVar(value=bool(app.cfg["updates"].get("auto", True)))
        ttk.Checkbutton(b, text="Check for updates when mbtool starts", variable=self.auto,
                        command=lambda: app.set_update_auto(self.auto.get())).pack(anchor="w", pady=(6, 0))
        bf = ttk.Frame(b)
        bf.pack(fill="x", pady=(10, 0))
        ttk.Button(bf, text="Later", command=self.close).pack(side="right")
        ttk.Button(bf, text="Skip this version", command=self.skip).pack(side="right", padx=(0, 6))
        ttk.Button(bf, text="Release page", command=lambda: self._open(res.get("page"))).pack(side="right", padx=(0, 6))
        self.b_dl = ttk.Button(bf, text="Download portable zip" if self.portable else "Download installer",
                               command=self.download, default="active")
        self.b_dl.pack(side="right", padx=(0, 6))
        self.w.bind("<Return>", lambda e: self.download())
        self.opened = None
        self.place()

    def _open(self, url):
        import webbrowser
        if url:
            self.opened = url
            webbrowser.open(url)
        return url

    def download(self):
        url = self._open(self.url)
        self.app._status("Download started in your browser - install it when it has finished", "info")
        self.close()
        return url

    def skip(self):
        self.app.cfg["updates"]["skip"] = self.res["latest"]
        save_settings(self.app.cfg)
        self.app._status(f"Version {self.res['latest']} skipped - Help > Check for updates still shows it", "info")
        self.close()


class ReportDialog(Dialog):
    """File > Commissioning report: project details, language, PDF; Save writes commission.save()."""
    FIELDS = (("project", "Project"), ("site", "Site"), ("customer", "Customer"), ("engineer", "Commissioned by"))
    LANGS = (("en", "English"), ("th", "ไทย (Thai)"))

    def __init__(self, app):
        super().__init__(app, "Commissioning report")
        add_help_button(app, self.w, "report")
        s = app.s
        r = dict(DEFAULTS["report"], **(app.cfg.get("report") or {}))
        b = self.body
        ttk.Label(b, text="A printable report of the Devices window: every device with state, counters, error types,\n"
                          "response times and its values now, the PLC cycle, the last health test and the bus map.",
                  style="Info.TLabel", justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.v = {}
        for i, (k, lab) in enumerate(self.FIELDS, start=1):
            ttk.Label(b, text=lab + ":").grid(row=i, column=0, sticky="e", padx=(0, 8), pady=2)
            self.v[k] = tk.StringVar(value=r.get(k, ""))
            ttk.Entry(b, textvariable=self.v[k], width=46).grid(row=i, column=1, sticky="we", pady=2)
        ttk.Label(b, text="Notes:").grid(row=5, column=0, sticky="ne", padx=(0, 8), pady=2)
        self.notes = tk.Text(b, width=46, height=5, wrap="word", font=("Segoe UI", 9))
        self.notes.grid(row=5, column=1, sticky="we", pady=2)
        self.notes.insert("1.0", r.get("notes", ""))
        lf = ttk.Frame(b)
        lf.grid(row=6, column=1, sticky="w", pady=(6, 0))
        ttk.Label(b, text="Language:").grid(row=6, column=0, sticky="e", padx=(0, 8), pady=(6, 0))
        self.lang = tk.StringVar(value=r.get("lang", "en") if r.get("lang") in ("en", "th") else "en")
        for val, text in self.LANGS:
            ttk.Radiobutton(lf, text=text, value=val, variable=self.lang).pack(side="left", padx=(0, 12))
        self.pdf = tk.BooleanVar(value=bool(r.get("pdf", True)))
        ttk.Checkbutton(b, text="Also save a PDF (printed with Microsoft Edge)", variable=self.pdf).grid(
            row=7, column=1, sticky="w", pady=(4, 0))
        n = len(app.devices)
        hl = getattr(app, "last_health", None)
        info = f"{n} device(s)" + ("" if n else " - add devices in the Devices window first (F11)")
        info += ",  health test: " + ("included" if hl else "none yet (Devices > Health test)")
        self.l_info = ttk.Label(b, text=info, style="Info.TLabel")
        self.l_info.grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.l_res = ttk.Label(b, text="", style="Hint.TLabel", wraplength=int(480 * s), justify="left")
        self.l_res.grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))
        bf = ttk.Frame(b)
        bf.grid(row=10, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(bf, text="Cancel", command=self.close).pack(side="right")
        self.b_save = ttk.Button(bf, text="Save report...", command=self.save, default="active")
        self.b_save.pack(side="right", padx=(0, 6))
        ttk.Button(bf, text="Preview", command=self.preview).pack(side="right", padx=(0, 6))
        b.columnconfigure(1, weight=1)
        self.last = None
        self.place()

    def form(self):
        f = {k: v.get().strip() for k, v in self.v.items()}
        f["notes"] = self.notes.get("1.0", "end").strip()
        f["lang"] = self.lang.get()
        f["pdf"] = bool(self.pdf.get())
        return f

    def _remember(self, f, folder=None):
        r = self.app.cfg.setdefault("report", dict(DEFAULTS["report"]))
        r.update(f)
        if folder:
            r["folder"] = folder
        save_settings(self.app.cfg)

    def default_name(self, f):
        base = re.sub(r"[^\w\-]+", "_", f.get("project") or "mbtool").strip("_") or "mbtool"
        return f"{base}_commissioning_{time.strftime('%Y-%m-%d')}.html"

    def write(self, path, f=None):
        """Write the report to path (+ PDF when ticked). -> (html_path, pdf_path, pdf_error)."""
        import commission
        f = f or self.form()
        data = self.app.commission_data(f)
        html_path, _ = commission.save(data, path, f["lang"], pdf=False)
        pdf_path = pdf_err = None
        if f["pdf"]:
            try:
                self.w.configure(cursor="watch")
                self.w.update_idletasks()
                pdf_path = commission.to_pdf(html_path, os.path.splitext(html_path)[0] + ".pdf")
            except OSError as e:
                pdf_err = str(e)
            finally:
                try:
                    self.w.configure(cursor="")
                except tk.TclError:
                    pass
        self.last = (html_path, pdf_path, pdf_err)
        return self.last

    def save(self, path=None):
        f = self.form()
        if path is None:
            folder = (self.app.cfg.get("report") or {}).get("folder") or os.path.expanduser("~")
            path = filedialog.asksaveasfilename(parent=self.w, title="Save commissioning report",
                                                initialdir=folder, initialfile=self.default_name(f),
                                                defaultextension=".html", filetypes=[("HTML report", "*.html")])
            if not path:
                return None
        try:
            html_path, pdf_path, pdf_err = self.write(path, f)
        except OSError as e:
            self.l_res.configure(text=f"Could not save the report: {e}", style="Hint.TLabel")
            return None
        self._remember(f, os.path.dirname(html_path))
        msg = f"Saved {os.path.basename(html_path)}" + (f" and {os.path.basename(pdf_path)}" if pdf_path else "")
        if pdf_err:
            msg += f"   (PDF: {pdf_err})"
        self.l_res.configure(text=msg, style="Info.TLabel" if not pdf_err else "Warn.TLabel")
        self.app._status(msg, "ok" if not pdf_err else "warn")
        try:
            os.startfile(pdf_path or html_path)  # noqa: S606 - the user's own report file
        except (OSError, AttributeError):
            pass
        return self.last

    def preview(self):
        import webbrowser
        import commission
        f = self.form()
        path = os.path.join(tempfile.gettempdir(), "mbtool_report_preview.html")
        try:
            commission.save(self.app.commission_data(f), path, f["lang"], pdf=False)
        except OSError as e:
            self.l_res.configure(text=f"Could not write the preview: {e}")
            return None
        self._remember(f)
        webbrowser.open("file:///" + path.replace("\\", "/"))
        return path
