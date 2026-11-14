"""AI Assist (F12): sharing the live connection over MCP and the diagnostic report."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


AI_AUDIT_LOG = appinfo.audit_log()


class AiShare:
    """Controller of the in-process MCP server (mbtool_mcp.BackgroundServer) that shares the main window's master.
    Used on the Tk thread only; starting / stopping the server runs on worker threads that report back through
    app.q ("call" messages). A generation token retires results of a start that was overtaken (stop, app close).
    The bearer token exists only here, in memory: it is never saved, logged or shown in the status bar."""
    TAIL_BYTES = 64 * 1024

    def __init__(self, app):
        self.app = app
        self.token = secrets.token_urlsafe(32)
        self.copy_ok = None      # the token value the owner already agreed to copy (warned once per token)
        self.lan_ok = False      # the owner confirmed network access in this session
        self.srv, self.state, self.gen = None, "off", 0   # state: off | starting | on | stopping
        self.error = None        # (field or None, text) of the last failed start
        self.opts, self.url = None, ""
        self.audit_path = AI_AUDIT_LOG
        self.pos, self.partial = None, b""
        self.events = collections.deque(maxlen=1000)  # (seq, parsed line, from an earlier session)
        self.seq = self.requests = self.failed = 0
        self.local_ips = None    # None = not detected yet
        self._ips_busy = False
        self._shown = False
        app.root.after(1000, self._tick)

    # ----- server -----
    def detect_ips(self, then=None):
        """Find this PC's IPv4 addresses on a worker thread (name resolution may block)."""
        if self.local_ips is not None or self._ips_busy:
            if then and self.local_ips is not None:
                then()
            return
        self._ips_busy = True

        def work():
            ips = mon.local_ipv4s()
            self.app.q.put(("call", None, lambda r, e: self._ips_found(r, then), ips, None))
        threading.Thread(target=work, daemon=True).start()

    def _ips_found(self, ips, then):
        self._ips_busy = False
        self.local_ips = list(ips or [])
        if then:
            then()
        self._refresh()

    def start(self, opts):
        if self.state != "off":
            return
        self.opts, self.error = mon.norm_ai_settings(opts), None
        self.state, self.gen = "starting", self.gen + 1
        gen, token, audit, opts = self.gen, self.token, self.audit_path, self.opts
        host, port, lan = mon.ai_bind_host(opts), int(opts["port"]), opts["bind"] == "lan"
        known = self.local_ips

        def work():
            res = err = None
            try:
                ips = known if known is not None else (mon.local_ipv4s() if lan else None)
                try:
                    size = os.path.getsize(audit)
                except OSError:
                    size = 0
                import mbtool_mcp
                srv = mbtool_mcp.BackgroundServer(host, port, token, audit, allow_connect=bool(opts["allow_connect"]),
                                                  allow_write=False, allowed_hosts=mon.ai_allowed_hosts(opts, ips or []))
                if self.gen != gen:  # stopped or app closed while starting: do not leave it listening
                    srv.stop()
                    return
                res = (srv, ips, size)
            except ImportError as e:
                err = (None, f"the AI server needs the 'mcp' and 'uvicorn' packages (pip install -r requirements.txt): {e}")
            except OSError as e:
                err = ("port", f"Port {port} is busy or not allowed - choose another port or close the program "
                               f"that uses it ({e})")
            except Exception as e:
                err = (None, str(e) or repr(e))
            self.app.q.put(("call", None, lambda r, e: self._started(gen, r, e), res, err))
        threading.Thread(target=work, daemon=True).start()
        self._refresh()

    def _started(self, gen, res, err):
        if gen != self.gen:
            if res:
                threading.Thread(target=res[0].stop, daemon=True).start()
            return
        if err:
            self.state, self.error = "off", err
            self.app._status(f"AI sharing not started: {err[1]}", "bad")
            self._win_status(f"Not started: {err[1]}", "bad")
            self._refresh()
            return
        srv, ips, size = res
        if ips is not None:
            self.local_ips = list(ips)
        self.srv, self.state = srv, "on"
        self.url = mon.ai_client_url(self.opts, self.local_ips or [])
        self.requests = self.failed = 0
        self.pos, self.partial = size, b""
        self.sync()
        self.app._status(f"AI sharing started: {self.url}  (read-only)", "ok")
        self._win_status(f"Sharing started - give the URL and token to your AI client ('Connect an AI')", "ok")
        self._refresh()

    def stop(self, then=None):
        if self.state not in ("on", "starting"):
            if then:
                then()
            return
        self.gen += 1
        srv, self.srv = self.srv, None
        own = self._release()
        if srv is None:  # still starting: the start worker stops its server when it sees the new generation
            self.state = "off"
            self._refresh()
            if then:
                then()
            return
        self.state = "stopping"

        def work():
            try:
                srv.stop()
            finally:
                if own is not None:
                    try:
                        own.close()
                    except Exception:
                        pass
                self.app.q.put(("call", None, lambda r, e: self._stopped(then), None, None))
        threading.Thread(target=work, daemon=True).start()
        self._refresh()

    def _stopped(self, then):
        self.state = "off"
        self._read_audit()  # the last lines written before the stop
        self.app._status("AI sharing stopped", "info")
        self._win_status("Sharing stopped - no AI client can connect now", "info")
        self._refresh()
        if then:
            then()

    def _win_status(self, text, kind):
        win = self.app.ai_win
        if win is not None and win.alive():
            win._status(text, kind)

    def restart(self):
        if self.state == "on":
            opts = self.opts
            self.stop(then=lambda: self.start(opts))

    def regenerate(self):
        """New token; while sharing, the server restarts so the old token stops working at once."""
        self.token = secrets.token_urlsafe(32)
        self.copy_ok = None
        if self.state == "on":
            self.restart()
        self._refresh()

    def shutdown(self):
        """App closing: stop the server now (bounded wait) so the port is free when mbtool has closed."""
        self.gen += 1
        srv, self.srv = self.srv, None
        own = self._release()
        self.state = "off"
        if srv is not None:
            srv.stop()
        if own is not None:
            try:
                own.close()
            except Exception:
                pass

    # ----- sharing the GUI's master -----
    def _release(self):
        """Detach whatever the MCP tools use. Returns a master the AI opened itself (to be closed), else None."""
        mm = sys.modules.get("mbtool_mcp")
        if mm is None:
            return None
        cur = mm._state.get("m")
        mm.detach_master()
        return None if cur is None or isinstance(cur, mon.AiSharedMaster) else cur

    def sync(self):
        """Point the MCP tools at the main window's current master (read-only wrapper), or at nothing.
        A device the AI connected to itself (only possible with 'Let the AI choose the device') is left alone."""
        mm = sys.modules.get("mbtool_mcp")
        if mm is None or self.state != "on":
            return
        cur, m = mm._state.get("m"), self.app.m
        ours = isinstance(cur, mon.AiSharedMaster)
        if cur is not None and not ours:
            return
        if m is None:
            if cur is not None:
                mm.detach_master()
                self._refresh()
        elif not ours or cur.target is not m or mm._state.get("url") != self.app.conn_url:
            mm.attach_master(mon.AiSharedMaster(m), self.app.conn_url)
            self._refresh()

    def device_text(self):
        """(text, style kind) describing what the AI can read right now."""
        if self.state != "on":
            return "", "info"
        mm = sys.modules.get("mbtool_mcp")
        cur = mm._state.get("m") if mm else None
        if cur is None:
            return "No device - connect in the main window (F5); the AI sees 'not connected' until then", "warn"
        if not isinstance(cur, mon.AiSharedMaster):
            return f"The AI connected to its own device: {mm._state.get('url')}", "warn"
        app = self.app
        if app.state == "on" and app.link_bad:
            return f"Device: {app.summary} - link lost, mbtool is reconnecting", "warn"
        return f"Device: {app.summary}  (the main window's connection, read-only)", "ok"

    # ----- audit log -----
    def _feed(self, data, old):
        buf = self.partial + data
        lines = buf.split(b"\n")
        self.partial = lines.pop()
        for raw in lines:
            e = mon.parse_audit_line(raw.decode("utf-8", "replace"))
            if e is None:
                continue
            self.seq += 1
            self.events.append((self.seq, e, old))
            if not old:
                if e["failed"]:
                    self.failed += 1
                elif e["event"] == "tool":
                    self.requests += 1

    def _read_audit(self):
        """New audit lines since the server started (a small local file, read every second)."""
        if self.pos is None:
            return
        try:
            size = os.path.getsize(self.audit_path)
        except OSError:
            return
        if size < self.pos:  # file replaced / truncated
            self.pos, self.partial = 0, b""
        if size == self.pos:
            return
        try:
            with open(self.audit_path, "rb") as f:
                f.seek(self.pos)
                data = f.read(min(size - self.pos, 1 << 20))
        except OSError:
            return
        self.pos += len(data)
        self._feed(data, old=False)

    def load_tail(self):
        """Earlier lines of the audit log (shown grey) when nothing was read yet in this session."""
        if self.seq:
            return
        try:
            end = self.pos if self.pos is not None else os.path.getsize(self.audit_path)
            with open(self.audit_path, "rb") as f:
                start = max(0, end - self.TAIL_BYTES)
                f.seek(start)
                data = f.read(end - start)
        except OSError:
            return
        if start:
            data = data.split(b"\n", 1)[-1]
        keep, self.partial = self.partial, b""
        self._feed(data if data.endswith(b"\n") else data + b"\n", old=True)
        self.partial = keep

    # ----- indicator -----
    def indicator_text(self):
        if self.state != "on":
            return ""
        n = self.requests
        return f"AI: sharing {mon.ai_share_label(self.opts)}, {n} request{'' if n == 1 else 's'}"

    def _indicator(self):
        lab = self.app.l_ai
        text = self.indicator_text()
        if text:
            lab.configure(text=text)
            if not self._shown:
                lab.grid()
                self._shown = True
        elif self._shown:
            lab.grid_remove()
            self._shown = False

    def _refresh(self):
        self._indicator()
        win = self.app.ai_win
        if win is not None and win.alive():
            win.refresh()

    def _tick(self):
        try:
            if self.state == "on":
                self._read_audit()
                self.sync()
            self._indicator()
        except Exception:
            import traceback
            traceback.print_exc()
        try:
            self.app.root.after(1000, self._tick)
        except tk.TclError:
            pass


class AiAssistWindow:
    """F12: let an AI analyse the device - tab 1 shares the live connection with an MCP client (read-only, bearer
    token), tab 2 builds a diagnostic report to paste into any AI chat."""
    ACT_COLS = (("time", "Time", 70), ("ip", "Client", 110), ("what", "Tool / event", 170), ("args", "Arguments", 300))
    SNIPPETS = (("claude", "Claude Code"), ("json", "JSON config"), ("text", "Other AI clients"))

    def __init__(self, app):
        self.app, s = app, app.s
        self.s, self.ai = s, app.ai
        self.w = w = tk.Toplevel(app.root)
        w.title("mbtool - AI Assist")
        help_key(w, "ai")
        w.geometry(f"{int(1000 * s)}x{int(700 * s)}")
        w.minsize(int(780 * s), int(540 * s))
        w.protocol("WM_DELETE_WINDOW", self.close)
        self.mono = tkfont.Font(family="Consolas", size=10)
        sb = ttk.Frame(w, padding=(8, 2, 8, 6))
        sb.pack(fill="x", side="bottom")
        self.l_msg = ttk.Label(sb, style="SInfo.TLabel")
        self.l_msg.pack(side="left")
        self.nb = ttk.Notebook(w)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 2))
        self.t1 = ttk.Frame(self.nb, padding=(8, 6, 8, 4))
        self.t2 = ttk.Frame(self.nb, padding=(8, 6, 8, 4))
        self.nb.add(self.t1, text="Share live connection with an AI")
        self.nb.add(self.t2, text="Ask any AI (copy a report)")
        self.last_seq, self.snip_key, self.shown_token = 0, None, False
        self.report_md, self.report_setup, self.rep_gen = None, {}, 0
        self._build_share(self.t1)
        self._build_report(self.t2)
        w.bind("<F12>", lambda e: self.close())
        self.b_help = add_help_button(app, w, "ai")
        self.reload()
        self.ai.detect_ips(then=self._fill_addrs)
        self.ai.load_tail()
        self.refresh()
        self._status("Sharing lets an MCP-capable AI read this device; the report tab works with any AI chat.")
        self._tick()

    # ----- tab 1: share -----
    def _build_share(self, f):
        s = self.s
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.b_run = ttk.Button(top, text="Start sharing", width=15, command=self.toggle)
        self.b_run.pack(side="left")
        Tooltip(self.b_run, "Start / stop the MCP server inside mbtool.\nIt stops by itself when mbtool closes.")
        self.lamp = Lamp(top, s)
        self.lamp.c.pack(side="left", padx=(10, 6))
        self.l_state = ttk.Label(top, text="Stopped", style="Conn.TLabel")
        self.l_state.pack(side="left")
        self.l_req = ttk.Label(top, text="", style="Info.TLabel")
        self.l_req.pack(side="right")
        self.l_dev = ttk.Label(f, text="", style="Info.TLabel")
        self.l_dev.pack(fill="x", pady=(4, 0))
        body = ttk.Frame(f)
        body.pack(fill="x", pady=(6, 0))
        body.columnconfigure(0, weight=1, uniform="ai")
        body.columnconfigure(1, weight=1, uniform="ai")
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        wrap = int(430 * s)
        self.hints = {}
        # who may connect
        lf = ttk.LabelFrame(left, text="Who may connect", padding=(10, 4, 10, 6))
        lf.pack(fill="x")
        self.v_bind = tk.StringVar(value="local")
        self.r_local = ttk.Radiobutton(lf, text="Only this PC (127.0.0.1)", value="local", variable=self.v_bind,
                                       command=self._bind_clicked)
        self.r_local.grid(row=0, column=0, columnspan=3, sticky="w")
        self.r_lan = ttk.Radiobutton(lf, text="Other computers on my network...", value="lan", variable=self.v_bind,
                                     command=self._bind_clicked)
        self.r_lan.grid(row=1, column=0, columnspan=3, sticky="w")
        Tooltip(self.r_lan, "Plain HTTP: use it on a trusted network or through a VPN such as Tailscale.\n"
                            "You are asked to confirm.")
        self.v_addr, self.v_host, self.v_port = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.cb_addr = ttk.Combobox(lf, textvariable=self.v_addr, width=30, state="readonly")
        self.e_host = ttk.Entry(lf, textvariable=self.v_host, width=32)
        self.e_port = ttk.Entry(lf, textvariable=self.v_port, width=8)
        for r, (k, lab, wdg, tip) in enumerate((
                ("lan_addr", "Bind address:", self.cb_addr,
                 "0.0.0.0 listens on every network adapter of this PC;\nan address listens on that adapter only."),
                ("host_name", "Host name clients use:", self.e_host,
                 "Optional. The name other computers type in the URL, e.g. mypc.tailnet.ts.net (Tailscale).\n"
                 "Requests that use an unknown host name are refused (DNS-rebinding protection)."),
                ("port", "Port:", self.e_port, "TCP port of the MCP server (default 8765)."))):
            ttk.Label(lf, text=lab).grid(row=2 + 2 * r, column=0, sticky="w", padx=(18 if k != "port" else 0, 8),
                                         pady=(4, 0))
            wdg.grid(row=2 + 2 * r, column=1, sticky="w", pady=(4, 0))
            Tooltip(wdg, tip)
            h = ttk.Label(lf, text=" ", style="Hint.TLabel", wraplength=int(300 * s))
            h.grid(row=3 + 2 * r, column=1, sticky="w")
            self.hints[k] = h
        self.l_addr_info = ttk.Label(lf, text="", style="Info.TLabel", wraplength=wrap)
        self.l_addr_info.grid(row=8, column=0, columnspan=3, sticky="w", pady=(2, 0))
        for v in (self.v_addr, self.v_host, self.v_port):
            v.trace_add("write", lambda *_: self.validate())
        # access
        af = ttk.LabelFrame(left, text="Access", padding=(10, 4, 10, 6))
        af.pack(fill="x", pady=(6, 0))
        tr = ttk.Frame(af)
        tr.pack(fill="x")
        ttk.Label(tr, text="Bearer token:").pack(side="left", padx=(0, 6))
        self.v_tok = tk.StringVar()
        self.e_tok = ttk.Entry(tr, textvariable=self.v_tok, show="•", width=26, state="readonly")
        self.e_tok.pack(side="left", fill="x", expand=True)
        br = ttk.Frame(af)
        br.pack(fill="x", pady=(4, 0))
        self.b_show = ttk.Button(br, text="Show", width=7, command=self.toggle_show)
        self.b_show.pack(side="left")
        self.b_tcopy = ttk.Button(br, text="Copy", width=7, command=self.copy_token)
        self.b_tcopy.pack(side="left", padx=(4, 0))
        self.b_regen = ttk.Button(br, text="Regenerate", command=self.regenerate)
        self.b_regen.pack(side="left", padx=(4, 0))
        Tooltip(self.b_regen, "Make a new token. Everyone using the old one is locked out\n"
                              "(sharing restarts when it runs).")
        ttk.Label(af, text="Made for this session and kept only in memory - never saved to settings, workspace "
                           "or log files.", style="Info.TLabel", wraplength=wrap).pack(fill="x", pady=(2, 0))
        self.v_choose = tk.BooleanVar(value=False)
        self.cb_choose = ttk.Checkbutton(af, text="Let the AI choose the device", variable=self.v_choose,
                                         command=self.validate)
        self.cb_choose.pack(anchor="w", pady=(6, 0))
        ttk.Label(af, text="Off (recommended): the AI reads only the device this window is connected to; it cannot "
                           "connect elsewhere, disconnect, or open and scan serial ports. On: it may connect to any TCP "
                           "address or COM port this PC can reach.", style="Info.TLabel",
                  wraplength=wrap).pack(fill="x")
        ttk.Label(af, text="Writes: disabled for AI (read-only)", style="Conn.TLabel").pack(anchor="w", pady=(6, 0))
        self.setting_widgets = [self.r_local, self.r_lan, self.cb_addr, self.e_host, self.e_port, self.cb_choose]
        # connect an AI
        cf = ttk.LabelFrame(right, text="Connect an AI", padding=(10, 4, 10, 6))
        cf.pack(fill="both", expand=True)
        ttk.Label(cf, text="⚠ These snippets contain the secret token: anyone who has it can read your device "
                           "while sharing is on. Give it only to people and AI clients you trust.",
                  style="Warn.TLabel", wraplength=wrap).pack(fill="x")
        sr = ttk.Frame(cf)
        sr.pack(fill="x", pady=(6, 2))
        self.v_snip = tk.StringVar(value="claude")
        for key, lab in self.SNIPPETS:
            ttk.Radiobutton(sr, text=lab, value=key, variable=self.v_snip,
                            command=self.refresh_snippet).pack(side="left", padx=(0, 10))
        tf = ttk.Frame(cf)
        tf.pack(fill="both", expand=True)
        self.t_snip = tk.Text(tf, wrap="word", font=self.mono, height=8, width=40, relief="flat", bg=COL["alt"],
                              padx=int(6 * s), pady=int(4 * s), highlightthickness=1,
                              highlightbackground=COL["line"])
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.t_snip.yview)
        self.t_snip.configure(yscrollcommand=vs.set, state="disabled")
        self.t_snip.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        cr = ttk.Frame(cf)
        cr.pack(fill="x", pady=(4, 0))
        self.b_scopy = ttk.Button(cr, text="Copy (contains token)", command=self.copy_snippet)
        self.b_scopy.pack(side="left")
        self.l_snip = ttk.Label(cr, text="", style="Info.TLabel", wraplength=int(260 * s))
        self.l_snip.pack(side="left", padx=(8, 0))
        # activity
        act = ttk.LabelFrame(f, text="Activity (audit log): tool calls and failed logins", padding=(8, 4, 8, 4))
        act.pack(fill="both", expand=True, pady=(6, 0))
        tf2 = ttk.Frame(act)
        tf2.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf2, columns=[c[0] for c in self.ACT_COLS], show="headings", height=5,
                                 style=tree_style(self.app.root, s))
        for cid, lab, wd in self.ACT_COLS:
            self.tree.heading(cid, text=lab, anchor="w")
            self.tree.column(cid, width=int(wd * s), stretch=cid == "args", minwidth=int(40 * s))
        self.tree.tag_configure("failed", foreground="#c5221f", background="#fdecea")
        self.tree.tag_configure("old", foreground=COL["stale"])
        vs2 = ttk.Scrollbar(tf2, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs2.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs2.grid(row=0, column=1, sticky="ns")
        tf2.rowconfigure(0, weight=1)
        tf2.columnconfigure(0, weight=1)
        self.l_audit = ttk.Label(act, text=f"Audit log file: {self.ai.audit_path}   (grey = before this session)",
                                 style="Info.TLabel")
        self.l_audit.pack(fill="x", pady=(2, 0))

    def _fill_addrs(self):
        if not self.alive():
            return
        ips = self.ai.local_ips or []
        vals = ["0.0.0.0  (all network adapters)"] + ips
        cur = self.v_addr.get().split(" ")[0]
        if cur and cur != "0.0.0.0" and cur not in ips:
            vals.append(cur)
        self.cb_addr.configure(values=vals)
        self.l_addr_info.configure(text=("Addresses of this PC: " + ", ".join(ips)) if ips else
                                   "No network address found (only this PC can connect).")
        self.refresh_snippet()

    def reload(self):
        """Fields from app.cfg['ai'] (used at the next start)."""
        c = mon.norm_ai_settings(self.app.cfg.get("ai"))
        self.v_bind.set(c["bind"])
        self.v_addr.set("0.0.0.0  (all network adapters)" if c["lan_addr"] == "0.0.0.0" else c["lan_addr"])
        self.v_host.set(c["host_name"])
        self.v_port.set(c["port"])
        self.v_choose.set(c["allow_connect"])
        self.validate()

    def collect(self):
        return {"bind": self.v_bind.get(), "lan_addr": (self.v_addr.get().split(" ")[0] or "0.0.0.0"),
                "port": self.v_port.get().strip(), "host_name": self.v_host.get().strip(),
                "allow_connect": bool(self.v_choose.get())}

    def validate(self):
        opts = self.collect()
        errs = mon.ai_errors(opts)
        err = self.ai.error
        if err and err[0] == "port" and "port" not in errs and self.ai.state == "off" \
                and self.ai.opts and opts["port"] == self.ai.opts["port"]:
            errs["port"] = err[1]  # inline: busy port of the last start
        set_hints(self.hints, errs)
        pending = opts["bind"] == "lan" and not self.ai.lan_ok and self.app.cfg["ai"].get("bind") != "lan"
        if not mon.ai_errors(opts) and not pending:  # network access is stored only after the confirmation
            self.app.cfg["ai"] = mon.norm_ai_settings(opts)
        self.refresh_snippet()
        return opts, errs

    def _bind_clicked(self):
        if self.v_bind.get() == "lan" and not self.ai.lan_ok:
            self.confirm_lan(on_yes=None)
        self.validate()
        self.refresh()

    def confirm_lan(self, on_yes=None):
        ips = self.ai.local_ips
        port = self.v_port.get().strip() or "8765"
        where = (", ".join(ips) if ips else "no network address detected") if ips is not None else "being detected"
        text = ("Let other computers connect to the AI server?\n\n"
                f"The server will listen on a network address, so any computer that can reach this PC on port {port} "
                "can try to connect. Only clients with the bearer token are let in, but the traffic is plain HTTP: "
                "the token and your device data cross the network unencrypted and can be read by anyone on it - "
                "unless you use a VPN such as Tailscale.\n\n"
                "Bind address choices: 0.0.0.0 (every network adapter of this PC) or one address of this PC "
                f"({where}). If clients use a host name (e.g. mypc.tailnet.ts.net), type it in 'Host name clients "
                "use'.\n\nWrites stay disabled for the AI.")

        def yes():
            self.ai.lan_ok = True
            self.v_bind.set("lan")
            self.validate()
            self.refresh()
            self._status("Network access allowed for this session - choose the bind address and start sharing", "warn")
            if on_yes:
                on_yes()

        def no():
            self.v_bind.set("local")
            self.validate()
            self.refresh()
            self._status("Only this PC (127.0.0.1) may connect", "info")
        self.lan_dlg = ConfirmDialog(self.app, "Share with other computers?", text, "Allow network access", yes, no,
                                     parent=self.w)
        return self.lan_dlg

    def toggle(self):
        st = self.ai.state
        if st == "on":
            self.ai.stop()
            self._status("Stopping AI sharing...", "info")
            return
        if st != "off":
            return
        opts, errs = self.validate()
        errs = mon.ai_errors(opts)
        if errs:
            self._status("Fix the fields marked in red", "bad")
            return
        if opts["bind"] == "lan" and not self.ai.lan_ok:
            self.confirm_lan(on_yes=self.toggle)
            return
        self.app.cfg["ai"] = mon.norm_ai_settings(opts)
        save_settings(self.app.cfg)
        self.ai.start(opts)
        self._status(f"Starting the AI server on {mon.ai_share_label(opts)}...", "info")
        self.refresh()

    # ----- token / snippets -----
    def toggle_show(self):
        self.shown_token = not self.shown_token
        self.e_tok.configure(show="" if self.shown_token else "•")
        self.b_show.configure(text="Hide" if self.shown_token else "Show")
        self.refresh_snippet(force=True)

    def regenerate(self):
        was = self.ai.state == "on"
        self.ai.regenerate()
        self.v_tok.set(self.ai.token)
        self.refresh_snippet(force=True)
        self._status("New token made - the old one no longer works" + ("; sharing restarts" if was else ""), "ok")

    def _copy_secret(self, text, what):
        def do():
            self.ai.copy_ok = self.ai.token
            self.w.clipboard_clear()
            self.w.clipboard_append(text)
            self._status(f"{what} copied to the clipboard - it contains the secret token", "warn")
        if self.ai.copy_ok == self.ai.token:
            do()
            return None
        self.copy_dlg = ConfirmDialog(
            self.app, "Copy the secret token?",
            f"The {what.lower()} contains the bearer token. Anyone who gets it can read your Modbus device while "
            "sharing is on (writes stay disabled).\n\nPaste it only into your own AI client or send it to people "
            "you trust, over a private channel. Regenerate the token to lock them out again.\n\n"
            "You are asked once per token.", "Copy", do, parent=self.w)
        return self.copy_dlg

    def copy_token(self):
        return self._copy_secret(self.ai.token, "Token")

    def snippet_url(self):
        if self.ai.state == "on":
            return self.ai.url
        return mon.ai_client_url(self.collect(), self.ai.local_ips or [])

    def copy_snippet(self):
        key = self.v_snip.get()
        text = mon.ai_snippets(self.snippet_url(), self.ai.token)[key]
        return self._copy_secret(text, dict(self.SNIPPETS)[key] + " snippet")

    def refresh_snippet(self, force=False):
        if not hasattr(self, "t_snip"):
            return
        url, key = self.snippet_url(), self.v_snip.get()
        k = (url, key, self.ai.token, self.shown_token, self.ai.state)
        if k == self.snip_key and not force:
            return
        self.snip_key = k
        tok = self.ai.token if self.shown_token else mon.mask_token(self.ai.token)
        self.t_snip.configure(state="normal")
        self.t_snip.delete("1.0", "end")
        self.t_snip.insert("end", mon.ai_snippets(url, tok)[key])
        self.t_snip.configure(state="disabled")
        self.l_snip.configure(text=("Token hidden above (Show to see it); Copy puts the real token on the clipboard."
                                    if not self.shown_token else "Token visible - mind who sees your screen.")
                              + ("" if self.ai.state == "on" else "  The URL works once sharing is started."))

    # ----- periodic refresh -----
    def refresh(self):
        ai, app = self.ai, self.app
        st = ai.state
        self.lamp.set({"off": COL["bad"], "starting": COL["warn"], "stopping": COL["warn"], "on": COL["ok"]}[st])
        self.l_state.configure(text={"off": "Stopped", "starting": "Starting...", "stopping": "Stopping...",
                                     "on": f"Sharing on {ai.url}"}[st])
        self.b_run.configure(text="Stop sharing" if st in ("on", "stopping") else "Start sharing",
                             state="normal" if st in ("on", "off") else "disabled")
        self.b_regen.configure(state="normal" if st in ("on", "off") else "disabled")
        text, kind = ai.device_text()
        if st == "off":
            text, kind = ("Not sharing. Start to let an AI client read the device this window is connected to "
                          f"(currently: {app.summary if app.state == 'on' else 'not connected'})."), "info"
        self.l_dev.configure(text=text, style={"ok": "SOk.TLabel", "warn": "SWarn.TLabel"}.get(kind, "Info.TLabel"))
        self.l_req.configure(text=f"Requests: {ai.requests}    Failed logins: {ai.failed}" if st == "on" else "")
        off = st == "off"
        for wdg in self.setting_widgets:
            wdg.configure(state=("readonly" if wdg is self.cb_addr else "normal") if off else "disabled")
        if off and self.v_bind.get() != "lan":
            for wdg in (self.cb_addr, self.e_host):
                wdg.configure(state="disabled")
        if self.v_tok.get() != ai.token:
            self.v_tok.set(ai.token)
        self.refresh_snippet()
        new = [ev for ev in ai.events if ev[0] > self.last_seq]
        for seq, e, old in new:
            what = "LOGIN FAILED" if e["failed"] else (e["name"] if e["event"] == "tool" else e["event"])
            args = e["args"] if e["event"] == "tool" else e["name"]
            self.tree.insert("", "end", values=(e["time"], e["ip"], what, args),
                             tags=("failed",) if e["failed"] else (("old",) if old else ()))
            self.last_seq = seq
        if new:
            kids = self.tree.get_children()
            if len(kids) > 1000:
                self.tree.delete(*kids[:len(kids) - 1000])
                kids = self.tree.get_children()
            self.tree.see(kids[-1])
        if ai.error and st == "off":
            self.validate()
        self._report_state()

    def _tick(self):
        if not self.alive():
            return
        try:
            self.refresh()
        finally:
            self.w.after(1000, self._tick)

    # ----- tab 2: report -----
    def _build_report(self, f):
        s = self.s
        ttk.Label(f, text="For AI chat apps without MCP: create a report from the live device (a few samples of the "
                          "poll range, identification, preset values, recent traffic, error checklist) and paste it "
                          "into the chat.", style="Info.TLabel", wraplength=int(900 * s)).pack(fill="x")
        ttk.Label(f, text="⚠ Privacy: the report contains register values and the request / reply traffic of your "
                          "device. Review it before pasting it into a cloud AI service.", style="Warn.TLabel",
                  wraplength=int(900 * s)).pack(fill="x", pady=(4, 0))
        r1 = ttk.Frame(f)
        r1.pack(fill="x", pady=(8, 0))
        self.b_rep = ttk.Button(r1, text="Create diagnostic report", command=self.make_report)
        self.b_rep.pack(side="left")
        self.pb = ttk.Progressbar(r1, mode="indeterminate", length=int(120 * s))
        self.l_rep = ttk.Label(r1, text="", style="Info.TLabel")
        self.l_rep.pack(side="left", padx=(10, 0))
        r2 = ttk.Frame(f)
        r2.pack(fill="x", pady=(8, 0))
        ttk.Label(r2, text="My question (optional):").pack(side="left", padx=(0, 6))
        self.v_q = tk.StringVar()
        eq = ttk.Entry(r2, textvariable=self.v_q)
        eq.pack(side="left", fill="x", expand=True)
        Tooltip(eq, "Added to 'Copy report with question prompt', e.g.\n"
                    "\"Why is the voltage 10 times too high?\"")
        bf = ttk.Frame(f)
        bf.pack(fill="x", side="bottom", pady=(6, 0))
        self.b_copy = ttk.Button(bf, text="Copy report", command=self.copy_report)
        self.b_copy.pack(side="left")
        self.b_copyq = ttk.Button(bf, text="Copy report with question prompt", command=self.copy_prompt)
        self.b_copyq.pack(side="left", padx=(4, 0))
        Tooltip(self.b_copyq, "Report + your setup + a request to answer as\n"
                              "observed / inferred / unknown / next check (+ your question)")
        self.b_save = ttk.Button(bf, text="Save as .md...", command=self.save_report)
        self.b_save.pack(side="left", padx=(4, 0))
        self.l_rmsg = ttk.Label(bf, text="", style="SInfo.TLabel")
        self.l_rmsg.pack(side="left", padx=(10, 0))
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True, pady=(6, 0))
        self.t_rep = tk.Text(tf, wrap="word", font=self.mono, relief="flat", bg=COL["bg"], padx=int(8 * s),
                             pady=int(6 * s), highlightthickness=1, highlightbackground=COL["line"], height=10)
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.t_rep.yview)
        self.t_rep.configure(yscrollcommand=vs.set)
        self.t_rep.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.t_rep.tag_configure("err", foreground=COL["bad"])
        self.t_rep.bind("<Control-a>", lambda e: (self.t_rep.tag_add("sel", "1.0", "end"), "break")[1])
        self._set_report_text("No report yet. Connect in the main window, then press 'Create diagnostic report'.")
        self.busy = False

    def _set_report_text(self, text, tag=()):
        self.t_rep.configure(state="normal")
        self.t_rep.delete("1.0", "end")
        self.t_rep.insert("end", text, tag)
        self.t_rep.configure(state="disabled")

    def _report_plan(self):
        """(request tuple, setup dict) for the report from the main window, or (None, reason)."""
        app = self.app
        if app.m is None or app.state != "on":
            return None, "Connect first (F5 in the main window) - the report is read from the live device."
        p = app.p
        if p is None:
            return None, "Fix the poll definition in the main window first."
        pr = app.preset["obj"] if app.preset else None
        unit, fc, addr, qty = mon.report_request(p, pr)
        setup = {"Connection": app.summary, "Slave ID": unit,
                 "Request sampled": f"{FUNCS.get(fc, f'FC{fc:02d}')}, protocol address {addr}, quantity {qty}",
                 "Address base used in mbtool": BASE_CHOICES[mon.norm_base(p.get('plc', 0))],
                 "Display format": None if pr or fc in (1, 2) else p.get("fmt"),
                 "Preset": (f"{pr.name} ({'verified' if pr.verified else 'UNVERIFIED - written from documents'})"
                            if pr else "none (plain register poll)")}
        return (unit, fc, addr, qty, pr), setup

    def _report_state(self):
        if self.busy:
            return
        plan, why = self._report_plan()
        self.b_rep.configure(state="normal" if plan else "disabled")
        if plan:
            unit, fc, addr, qty, pr = plan
            self.l_rep.configure(text=f"Will read Slave ID {unit}, FC{fc:02d} address {addr} x{qty}"
                                      + (f", preset '{pr.name}'" if pr else ""), style="Info.TLabel")
        else:
            self.l_rep.configure(text=why, style="Warn.TLabel")
        has = self.report_md is not None
        for b in (self.b_copy, self.b_copyq, self.b_save):
            b.configure(state="normal" if has else "disabled")

    def make_report(self):
        plan, setup = self._report_plan()
        if plan is None:
            self.l_rep.configure(text=setup, style="Warn.TLabel")
            return False
        unit, fc, addr, qty, pr = plan
        master, url = self.app.m, self.app.conn_url
        self.rep_gen += 1
        gen = self.rep_gen
        self.busy = True
        self.b_rep.configure(state="disabled")
        self.pb.pack(side="left", padx=(10, 0), before=self.l_rep)
        self.pb.start(12)
        self.l_rep.configure(text="Sampling the device (3 reads), identification, preset, traffic...",
                             style="Info.TLabel")
        self._set_report_text("Creating the report...")

        def work():
            try:
                import ai_report
                rep = ai_report.build_report(master, unit, fc, addr, qty, samples=3, preset=pr, url=url)
                res, err = ai_report.to_markdown(rep), None
            except Exception as e:
                res, err = None, str(e) or repr(e)
            self.app.q.put(("call", None, lambda r, e: self._report_done(gen, setup, r, e), res, err))
        threading.Thread(target=work, daemon=True).start()
        return True

    def _report_done(self, gen, setup, md, err):
        if gen != self.rep_gen or not self.alive():
            return
        self.busy = False
        self.pb.stop()
        self.pb.pack_forget()
        if err:
            self._set_report_text(f"The report could not be created: {err}", ("err",))
            self.l_rmsg.configure(text="Report failed", style="SBad.TLabel")
        else:
            self.report_md, self.report_setup = md, setup
            self._set_report_text(md)
            self.l_rmsg.configure(text=f"Report created {time.strftime('%H:%M:%S')} - review it before sharing",
                                  style="SOk.TLabel")
        self._report_state()

    def prompt_text(self):
        return mon.ai_prompt(self.report_setup, self.report_md or "", self.v_q.get())

    def _clip(self, text, what):
        self.w.clipboard_clear()
        self.w.clipboard_append(text)
        self.l_rmsg.configure(text=f"{what} copied ({len(text)} characters)", style="SOk.TLabel")

    def copy_report(self):
        if self.report_md:
            self._clip(self.report_md, "Report")

    def copy_prompt(self):
        if self.report_md:
            self._clip(self.prompt_text(), "Report with question prompt")

    def save_report(self, path=None):
        if not self.report_md:
            return None
        path = path or filedialog.asksaveasfilename(
            parent=self.w, title="Save diagnostic report", defaultextension=".md",
            initialfile=time.strftime("mbtool_report_%Y%m%d_%H%M%S.md"),
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")])
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.report_md + "\n")
        except OSError as e:
            self.l_rmsg.configure(text=f"Save failed: {e}", style="SBad.TLabel")
            return None
        self.l_rmsg.configure(text=f"Saved {path}", style="SOk.TLabel")
        return path

    # ----- window -----
    def _status(self, text, kind="info"):
        self.l_msg.configure(text=text, style={"ok": "SOk.TLabel", "bad": "SBad.TLabel",
                                               "warn": "SWarn.TLabel"}.get(kind, "SInfo.TLabel"))

    def alive(self):
        try:
            return bool(self.w.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        """Closing the window does not stop sharing (the amber indicator in the main window stays)."""
        if self.alive():
            self.validate()  # valid, confirmed options go to app.cfg['ai']
            save_settings(self.app.cfg)
            self.w.destroy()
