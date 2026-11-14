"""Built-in self test: exercises the real program (protocol, simulator, GUI windows) on this PC without hardware
and prints PASS / FAIL per check. Works from source and from the installed / portable .exe:

  mbtool-cli.exe selftest          (or: python mbtool.py selftest)

It never touches your settings (uses a temporary folder), never opens a COM port and closes everything it opens.
"""
import os
import sys
import tempfile
import time
import traceback

RESULTS = []


def check(name):
    def deco(fn):
        def run(*a):
            t0 = time.time()
            try:
                fn(*a)
                RESULTS.append((name, True, "", time.time() - t0))
            except Exception as e:  # noqa: BLE001 - report every failure, keep going
                RESULTS.append((name, False, f"{type(e).__name__}: {e}", time.time() - t0))
                if os.environ.get("MBTOOL_SELFTEST_TRACE"):
                    traceback.print_exc()
        return run
    return deco


def pump(root, sec, cond=None):
    end = time.time() + sec
    while time.time() < end:
        root.update()
        if cond is not None and cond():
            return True
        time.sleep(0.02)
    if cond is not None:
        raise AssertionError("timed out waiting")
    return True


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    import appinfo
    work = tempfile.mkdtemp(prefix="mbtool_selftest_")
    os.environ["MBTOOL_HOME"] = work  # settings / presets / logs of this run live here only
    import mbtool as MB
    MB.SETTINGS_FILE = os.path.join(work, "settings.json")
    MB.HOME_SETTINGS = os.path.join(work, "home_settings.json")
    from modbus_core import Faults, ModbusError, SerialMaster, SerialSlave, SimSlave, TcpMaster, VirtualCable
    from preset import load_preset
    print(f"mbtool {appinfo.VERSION} self test{' (exe)' if appinfo.FROZEN else ''} - work folder {work}")
    appinfo.seed_presets()

    @check("presets: bundled library loads")
    def _presets():
        n = 0
        for r, _d, fs in os.walk(appinfo.preset_dir()):
            for f in fs:
                if f.endswith(".json"):
                    load_preset(os.path.join(r, f))
                    n += 1
        assert n >= 30, f"only {n} presets"

    @check("protocol: TCP read/write against the simulator")
    def _tcp():
        s = SimSlave("127.0.0.1", 0, unit=None)
        m = TcpMaster("127.0.0.1", s.port, timeout=1)
        try:
            m.write_registers(1, 10, [1, 2, 3])
            assert m.read_holding(1, 10, 3) == [1, 2, 3]
        finally:
            m.close()
            s.stop()

    @check("protocol: RTU over an emulated line, 3 slaves, no gap")
    def _rtu():
        cable = VirtualCable(baud=38400)
        sl = []
        for u in (1, 2, 3):
            st = SimSlave(listen=False)
            st.holding[0] = u * 11
            sl.append(SerialSlave(cable.url, u, 38400, "E", store=st))
        m = SerialMaster(cable.url, 38400, "E", 1, timeout=1)
        try:
            for _ in range(5):
                for u in (1, 2, 3):
                    assert m.read_holding(u, 0, 1) == [u * 11]
        finally:
            m.close()
            for x in sl:
                x.stop()
            cable.close()

    @check("protocol: TCP master recovers after a broken connection")
    def _faults():
        s = SimSlave("127.0.0.1", 0, unit=None)
        s.holding[5] = 55
        m = TcpMaster("127.0.0.1", s.port, timeout=0.4)
        try:
            for f in ("drop_conn", "truncate", "garbage", "no_reply"):
                s.faults = Faults(**{f: 1.0})
                try:
                    m.read_holding(1, 5, 1)
                    raise AssertionError(f"{f}: no error")
                except ModbusError:
                    pass
                s.faults = None
                assert m.read_holding(1, 5, 1) == [55], f"no recovery after {f}"
        finally:
            m.close()
            s.stop()

    @check("modules: MCP server, scanner, calculator, bus map, signals")
    def _modules():
        import buscalc
        import busmap
        for name in ("mbtool_mcp", "scanner", "signals", "bushealth", "commission", "export",
                     "serial.urlhandler.protocol_socket"):
            __import__(name)
        est = buscalc.estimate_cycle(buscalc.Link("rtu", 19200), [{"name": "a", "unit": 1, "requests": [(3, 10)]}])
        assert est["total_ms"] > 0
        assert busmap.analyze(busmap.default_map([("a", 1)]))["trunk_m"] == 10

    import tkinter as tk
    from tkinter import messagebox
    for name in ("showerror", "showwarning", "showinfo"):
        setattr(messagebox, name, lambda *a, **k: None)
    messagebox.askyesnocancel = lambda *a, **k: True
    root = tk.Tk()
    root.withdraw()
    tk_errors = []
    root.report_callback_exception = lambda t, e, tb: tk_errors.append(f"{t.__name__}: {e}")
    app = MB.MasterApp(root)
    sim = SimSlave("127.0.0.1", 0, unit=None)
    for i in range(50):
        sim.holding[i] = 100 + i

    @check("GUI: main poll connects and shows values")
    def _main():
        app.cfg["conn"].update(mode="TCP", host="127.0.0.1", port=str(sim.port), timeout="1000")
        app.pv["func"].set(MB.FUNCS[3])
        app.pv["addr"].set("0")
        app.pv["qty"].set("10")
        app.pv["scan"].set("200")
        app.connect()
        pump(root, 8, lambda: app.state == "on" and app.grid.values and app.grid.values[0] == "100")

    @check("GUI: multi-type view")
    def _multi():
        app.multi_var.set(True)
        app._multi_changed()
        pump(root, 3, lambda: app.mgrid.values and app.mgrid.values[0][0] == "100")
        app.multi_var.set(False)
        app._multi_changed()

    @check("GUI: poll tab on the same connection")
    def _tabs():
        n = app.new_poll_tab()
        t = app.poll_tabs[n]
        t.pv["addr"].set("20")
        t.flush()
        t.start()
        pump(root, 8, lambda: app.dev_status[n].ok >= 1 and t.grid.values and t.grid.values[0] == "120")
        app.close_poll_tab(n)

    @check("GUI: Devices window, demo devices with tabs")
    def _demo():
        win = app.open_devices()
        win.b_demo.invoke()
        names = sorted(app.demo_names)
        assert len(names) == 5, names
        pump(root, 20, lambda: all(app.dev_status[x].ok >= 1 for x in names))
        win.b_demo.invoke()
        assert app.demo is None
        win.close()

    @check("GUI: Slave Sim, Trend, Traffic, Bus Map, Help, AI Assist windows open and close")
    def _windows():
        for opener in ("open_sim", "open_trend", "open_traffic", "open_busmap", "open_help", "open_ai"):
            fn = getattr(app, opener, None)
            if fn is None:
                continue
            w = fn()
            pump(root, 0.3)
            for closer in ("close",):
                if w is not None and hasattr(w, closer):
                    getattr(w, closer)()
            pump(root, 0.1)

    for fn in (_presets, _tcp, _rtu, _faults, _modules, _main, _multi, _tabs, _demo, _windows):
        fn()
    try:
        app.stop_all_devices()
        app.disconnect(quiet=True)
        pump(root, 0.3)
        root.destroy()
    finally:
        sim.stop()
    if tk_errors:
        RESULTS.append(("GUI: no errors inside Tk callbacks", False, "; ".join(tk_errors[:3]), 0.0))
    else:
        RESULTS.append(("GUI: no errors inside Tk callbacks", True, "", 0.0))
    ok = sum(1 for r in RESULTS if r[1])
    for name, passed, msg, dt in RESULTS:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}  ({dt:.1f} s){'  - ' + msg if msg else ''}")
    print(f"{ok}/{len(RESULTS)} checks passed")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
