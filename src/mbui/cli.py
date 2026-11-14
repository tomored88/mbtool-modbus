"""Command line: read / write / sim / vcom / preset-* / busmap, gui() and main()."""
from .common import *  # noqa: F401,F403 - shared imports, settings, constants and helpers


# ---------------- CLI ----------------

def cli_read(a):
    if a.func not in (1, 2, 3, 4):
        raise SystemExit("read function must be 1, 2, 3 or 4")
    m = connect(a.url)
    logf = open(a.log, "a", newline="") if a.log else None
    w = csv.writer(logf) if logf else None
    try:
        while True:
            t = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                rows = render(poll_once(m, a.unit, a.func, a.addr, a.n), a.func, a.fmt)
                print(t, " ".join(f"[{a.addr + i}]={v}" for i, v in rows))
                if w: w.writerow([t] + [v for _, v in rows]); logf.flush()
            except (ModbusError, OSError) as e:
                print(t, "ERROR:", e)
                if w: w.writerow([t, "ERROR", str(e)]); logf.flush()
            if a.scan <= 0:
                break
            time.sleep(a.scan / 1000)
    except KeyboardInterrupt:
        pass
    finally:
        m.close()
        if logf: logf.close()


def cli_write(a):
    m = connect(a.url)
    try:
        if a.func == 5:
            m.write_coil(a.unit, a.addr, a.values[0] not in ("0", "false"))
        elif a.func == 6:
            m.write_register(a.unit, a.addr, encode_value(a.values[0], "Unsigned")[0])
        elif a.func == 15:
            m.write_coils(a.unit, a.addr, [v not in ("0", "false") for v in a.values])
        elif a.func == 16:
            words = [w for v in a.values for w in encode_value(v, a.fmt)]
            m.write_registers(a.unit, a.addr, words)
        else:
            raise SystemExit("write function must be 5, 6, 15 or 16")
        print("OK")
    finally:
        m.close()


def cli_vcom(a):
    from modbus_core import VirtualCable
    baud = getattr(a, "baud", 0) or None
    bits = getattr(a, "bits", 11)
    if baud is not None and (baud < 50 or not 7 <= bits <= 12):
        raise SystemExit("--baud must be 50 or more and --bits 7..12")
    c = VirtualCable("127.0.0.1", a.port, baud=baud, bits_per_char=bits)
    print("virtual null-modem cable ready.")
    if baud:
        print(f"line speed emulated: {baud} baud, {bits} bits per character "
              f"({1000.0 * bits / baud:.3f} ms per byte)")
    else:
        print("line speed: not emulated (as fast as possible); use --baud N to emulate a real bus")
    print(f"Use {c.url} as the COM port on BOTH sides, e.g.:")
    print(f"  python mbtool.py sim --serial {c.url} -u 1 --baud 19200 --parity E")
    print(f"  python mbtool.py read serial:{c.url}?baud=19200&parity=E -f 3 -a 0 -n 5")
    print("In the GUI, type the URL into the COM port box. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def _sim_signals(a, preset, store):
    """--signals MODE: start moving the simulated values (see signals.py)."""
    if not a.signals or a.signals == "static":
        return None
    from signals import SignalEngine
    eng = SignalEngine(preset, store, mode=a.signals, device_type=a.device_type, speed=a.speed)
    eng.tick(0.0)
    eng.start()
    print(f"signals: mode {a.signals}, device type {eng.device_type}, {len(eng.plans)} moving points")
    return eng


def _sim_delay(a, store):
    """--delay MS: the simulator answers after this many milliseconds (like a real device)."""
    ms = float(getattr(a, "delay", 0) or 0)
    if not 0 <= ms <= 60000:
        raise SystemExit("--delay must be 0..60000 ms")
    store.response_delay = ms / 1000.0
    print(f"response delay: {ms:g} ms" + ("" if ms else " (answers at once)"))


def cli_sim(a):
    if a.serial:
        s = SimSlave(listen=False); seed(s)
        _sim_delay(a, s)
        if a.preset:
            pr = load_preset(a.preset)
            pr.fill_simulator(s)
            print(f"simulator filled from preset {a.preset}")
            _sim_signals(a, pr, s)
        SerialSlave(a.serial, a.unit, a.baud, a.parity, store=s, mode=a.mode)
        print(f"{a.mode.upper()} slave id {a.unit} on {a.serial} @ {a.baud} - Ctrl+C to stop")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            return
        return
    s = SimSlave("0.0.0.0" if a.public else "127.0.0.1", a.port)
    _sim_delay(a, s)
    if a.preset:
        from preset import load_preset
        pr = load_preset(a.preset)
        pr.fill_simulator(s)
        print(f"simulator filled from preset {a.preset}")
        _sim_signals(a, pr, s)
    else:
        seed(s)
    print(f"simulator on port {s.port} (all unit ids) - Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def cli_preset_list(a):
    import os
    from preset import load_preset
    for root, _d, files in os.walk(preset_dir()):
        for f in sorted(files):
            if f.endswith(".json"):
                path = os.path.join(root, f)
                try:
                    pr = load_preset(path)
                    flag = "verified" if pr.verified else "UNVERIFIED"
                    print(f"{os.path.relpath(path, preset_dir())}\t{pr.name}\t{len(pr.points)} points\t{flag}")
                except Exception as e:
                    print(f"{f}\tINVALID: {e}")


def cli_preset_read(a):
    from preset import load_preset
    pr = load_preset(a.preset)
    if not pr.verified:
        print("NOTE: preset is not verified against a real device", file=sys.stderr)
    m = connect(a.url)
    try:
        while True:
            res = pr.read(m, a.unit, a.group)
            print(time.strftime("%H:%M:%S"))
            for n, d in res.items():
                print(f"  {n:<28} {d.get('error') or d['text']}")
            if a.scan <= 0:
                break
            time.sleep(a.scan / 1000)
    except KeyboardInterrupt:
        pass
    finally:
        m.close()


def cli_preset_write(a):
    from preset import PresetError, load_preset
    pr = load_preset(a.preset)
    m = connect(a.url)
    try:
        pr.write(m, a.point, float(a.value) if a.value.lower() not in ("true", "false") else a.value.lower() == "true", a.unit)
        print("OK")
    except (PresetError, ModbusError) as e:
        raise SystemExit(f"ERROR: {e}")
    finally:
        m.close()


def cli_busmap(a):
    import busmap
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        with open(a.file, encoding="utf-8") as f:
            m = json.load(f)
        print(busmap.format_report(busmap.analyze(m)))
    except (OSError, ValueError) as e:  # busmap.MapError is a ValueError
        print(f"error: {e}")
        sys.exit(1)


def gui(workspace=None):
    if sys.platform == "win32":  # crisp text on high-DPI screens
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    appinfo.install_crash_handlers(root)
    appinfo.seed_presets()
    try:
        ico = appinfo.icon_path()
        if ico and os.path.isfile(ico):
            root.iconbitmap(ico)
    except tk.TclError:
        pass
    app = MasterApp(root)
    root.after(4000, app.auto_check_updates)  # once a day at most, silent unless a newer version exists
    recent = app.cfg["workspace"].get("recent") or []
    path = workspace or (recent[0] if app.cfg["workspace"].get("autoload") and recent else None)
    if path:
        def load():
            if os.path.isfile(path):
                app.open_workspace(path)
            else:
                app._status(f"Workspace not found: {path}", "bad")
        root.after(150, load)
    root.mainloop()


def main():
    p = argparse.ArgumentParser(prog="mbtool")
    sub = p.add_subparsers(dest="cmd")
    for name, fn in (("read", cli_read), ("write", cli_write)):
        s = sub.add_parser(name); s.set_defaults(fn=fn)
        s.add_argument("url"); s.add_argument("-u", "--unit", type=int, default=1)
        s.add_argument("-f", "--func", type=int, default=3); s.add_argument("-a", "--addr", type=int, default=0)
        s.add_argument("--fmt", default="Unsigned", choices=FORMATS)
        if name == "read":
            s.add_argument("-n", type=int, default=10); s.add_argument("--scan", type=int, default=0)
            s.add_argument("--log")
        else:
            s.add_argument("values", nargs="+")
    s = sub.add_parser("vcom", help="virtual null-modem cable (socket://) for RTU / ASCII tests without a driver")
    s.set_defaults(fn=cli_vcom)
    s.add_argument("--port", type=int, default=7000)
    s.add_argument("--baud", type=int, default=0,
                   help="emulate this line speed (every byte takes bits/baud seconds) so cycle times look like a "
                        "real RS-485 bus; 0 = as fast as possible (default)")
    s.add_argument("--bits", type=int, default=11,
                   help="bits per character for --baud: 10 = 8N1, 11 = 8E1 / 8N2 (default 11)")
    s = sub.add_parser("preset-list"); s.set_defaults(fn=cli_preset_list)
    s = sub.add_parser("preset-read"); s.set_defaults(fn=cli_preset_read)
    s.add_argument("url"); s.add_argument("preset"); s.add_argument("-u", "--unit", type=int)
    s.add_argument("-g", "--group"); s.add_argument("--scan", type=int, default=0)
    s = sub.add_parser("preset-write"); s.set_defaults(fn=cli_preset_write)
    s.add_argument("url"); s.add_argument("preset"); s.add_argument("point"); s.add_argument("value")
    s.add_argument("-u", "--unit", type=int)
    s = sub.add_parser("sim"); s.set_defaults(fn=cli_sim)
    s.add_argument("--port", type=int, default=5020); s.add_argument("--public", action="store_true")
    s.add_argument("--preset", help="fill the simulator from a preset file")
    s.add_argument("--signals", choices=["static", "random", "loop", "sine", "profile", "preset"],
                   help="move the preset values: random walk, ramp loop, sine, device-type behaviour (profile)")
    s.add_argument("--device-type", choices=["generic", "meter", "drive", "level", "pressure", "flow", "temperature", "io"],
                   help="behaviour for --signals profile (default: guessed from the preset)")
    s.add_argument("--speed", type=float, default=1.0, help="time scale for --signals (2 = twice as fast)")
    s.add_argument("--serial", help="COM port: run an RTU slave instead of TCP")
    s.add_argument("--mode", default="rtu", choices=["rtu", "ascii"], help="framing for --serial")
    s.add_argument("-u", "--unit", type=int, default=1); s.add_argument("--baud", type=int, default=9600)
    s.add_argument("--parity", default="N", choices=["N", "E", "O"])
    s.add_argument("--delay", type=float, default=0.0,
                   help="response delay in ms: the simulator waits this long before every answer, like a real "
                        "device thinking (for cycle-time tests, default 0)")
    s = sub.add_parser("busmap", help="print the bus map analysis of a map JSON file (Tools > Bus map > Save map)")
    s.add_argument("file")
    s.set_defaults(fn=cli_busmap)
    s = sub.add_parser("mcp", help="run the MCP server (stdio, or --http); same options as mbtool_mcp.py")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=lambda a: __import__("mbtool_mcp").main(a.rest))
    sub.add_parser("update-check", help="check GitHub for a newer mbtool release (Help > Check for updates)")
    s = sub.add_parser("health", help="bus health test: real response times, errors by kind, recommended timeout "
                                      "and gap (same options as bushealth.py, e.g. --com COM3 --baud 19200 --units 1-5)")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(fn=lambda a: sys.exit(__import__("bushealth").main(a.rest)))
    s = sub.add_parser("selftest", help="check the program on this PC without hardware (prints PASS / FAIL)")
    s.set_defaults(fn=lambda a: sys.exit(__import__("selftest").main()))
    p.add_argument("--version", action="version", version=f"mbtool {appinfo.VERSION}")
    p.add_argument("--workspace", help="GUI: open this workspace (.mbw) at start")
    if sys.argv[1:2] == ["update-check"]:  # is a newer release on GitHub? (prints the answer)
        sys.exit(__import__("updates").main(sys.argv[2:]))
    if sys.argv[1:2] == ["health"]:  # its own options start with "--": hand them over untouched
        sys.exit(__import__("bushealth").main(sys.argv[2:]))
    if "--uninstall" in sys.argv[1:]:  # Apps & features calls: mbtool.exe --uninstall
        import setup_app
        sys.exit(setup_app.uninstall_gui())
    a = p.parse_args()
    appinfo.seed_presets()
    appinfo.install_crash_handlers()
    (a.fn(a) if a.cmd else gui(a.workspace))


# Names from sibling modules, imported last so that the modules can refer to each other.
from .app import MasterApp  # noqa: E402
