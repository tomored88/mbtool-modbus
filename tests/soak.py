"""Long-running soak test (not part of the quick suite): the whole GUI runs for N minutes with demo devices on an
emulated RS-485 line, a TCP simulator with moving values, 3 poll tabs, the Devices window in PLC-cycle mode, per-device
CSV logs, the trend window and the bus map animation. Every 30 s it samples memory, threads, Tk after() jobs, open
files, poll counters and errors, and at the end reports growth so leaks or stalls show up.

  python tests/soak.py [minutes]  (default 20)  -> prints a report and writes soak_report.json in the temp folder
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import ctypes
import ctypes.wintypes
import gc
import json
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox


def _proc():
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    return k.GetCurrentProcess()


def rss_mb():
    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.wintypes.DWORD), ("PageFaultCount", ctypes.wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    f = ctypes.windll.psapi.GetProcessMemoryInfo
    f.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(PMC), ctypes.wintypes.DWORD]
    if not f(_proc(), ctypes.byref(c), c.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return c.WorkingSetSize / 1e6


def handles():
    n = ctypes.wintypes.DWORD()
    f = ctypes.windll.kernel32.GetProcessHandleCount
    f.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.wintypes.DWORD)]
    if not f(_proc(), ctypes.byref(n)):
        raise OSError("GetProcessHandleCount failed")
    return n.value


def main(minutes=20.0):
    import mbtool as MB
    from modbus_core import SimSlave
    from preset import load_preset
    from signals import SignalEngine
    import appinfo
    work = tempfile.mkdtemp(prefix="mbtool_soak_")
    MB.SETTINGS_FILE = os.path.join(work, "settings.json")
    MB.HOME_SETTINGS = os.path.join(work, "home.json")
    messagebox.askyesnocancel = lambda *a, **k: True
    for name in ("showerror", "showwarning", "showinfo"):
        setattr(messagebox, name, lambda *a, **k: None)
    crash_before = os.path.getsize(appinfo.crash_log_path()) if os.path.exists(appinfo.crash_log_path()) else 0

    # TCP device with moving values for Poll 1 and the tabs
    sim = SimSlave("127.0.0.1", 0, unit=None)
    pr = load_preset(os.path.join(appinfo.preset_dir(), "library", "eastron_sdm630_v2.json"))
    pr.fill_simulator(sim)
    for i in range(200):
        sim.holding[i] = i
    eng = SignalEngine(pr, sim, mode="random", speed=3.0)
    eng.start()

    root = tk.Tk()
    root.withdraw()
    app = MB.MasterApp(root)
    errors = []
    root.report_callback_exception = lambda t, e, tb: errors.append(f"tk: {t.__name__}: {e}")
    app.cfg["conn"].update(mode="TCP", host="127.0.0.1", port=str(sim.port), timeout="1000")
    app.pv["func"].set(MB.FUNCS[3])
    app.pv["addr"].set("0")
    app.pv["qty"].set("40")
    app.pv["scan"].set("200")
    app.connect()
    tabs = []
    for k in range(3):
        n = app.new_poll_tab()
        t = app.poll_tabs[n]
        t.pv["addr"].set(str(40 * (k + 1)))
        t.pv["qty"].set("40")
        t.pv["scan"].set("250")
        t.flush()
        t.start()
        tabs.append(n)
    app.multi_var.set(True)
    app._multi_changed()
    win = app.open_devices()
    win.b_demo.invoke()  # 5 demo devices on one emulated 19200 baud line
    win.folder.set(work)  # per-device CSV logs (rotation, file handles) are part of the soak
    win.log_on.set(True)
    win._log_toggled()
    try:
        app.open_trend()
    except Exception as e:  # noqa: BLE001
        errors.append(f"trend: {e}")
    try:
        bm = app.open_busmap()
        if bm is not None and hasattr(bm, "b_play"):
            bm.b_play.invoke()
    except Exception as e:  # noqa: BLE001
        errors.append(f"busmap: {e}")

    samples, t0, next_sample = [], time.time(), time.time() + 30
    end = t0 + minutes * 60

    def snapshot():
        gc.collect()
        st = {n: app.dev_status[n] for n in list(app.dev_status)}
        return {"t_min": round((time.time() - t0) / 60, 2), "rss_mb": round(rss_mb(), 1), "threads": threading.active_count(),
                "handles": handles(), "after_jobs": len(root.tk.call("after", "info")),
                "main_ok": app.cnt_ok if hasattr(app, "cnt_ok") else None,
                "dev_ok": sum(s.ok for s in st.values()), "dev_err": sum(s.errors for s in st.values()),
                "log_files": sum(1 for r, _d, fs in os.walk(work) for f in fs if f.endswith(".csv")),
                "log_mb": round(sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(work) for f in fs) / 1e6, 2)}

    first = None
    while time.time() < end:
        root.update()
        time.sleep(0.01)
        if time.time() >= next_sample:
            s = snapshot()
            if first is None:
                first = s
            samples.append(s)
            print(json.dumps(s), flush=True)
            next_sample += 30
    last = snapshot()
    # stop everything and see that threads/jobs drain
    app.stop_demo()
    app.stop_all_devices()
    app.disconnect(quiet=True)
    for _ in range(100):
        root.update()
        time.sleep(0.02)
    after_stop = {"threads": threading.active_count(), "after_jobs": len(root.tk.call("after", "info"))}
    root.destroy()
    eng.stop()
    sim.stop()
    crash_after = os.path.getsize(appinfo.crash_log_path()) if os.path.exists(appinfo.crash_log_path()) else 0
    first = first or last
    report = {"minutes": minutes, "first": first, "last": last, "after_stop": after_stop,
              "rss_growth_mb": round(last["rss_mb"] - first["rss_mb"], 1),
              "thread_growth": last["threads"] - first["threads"], "handle_growth": last["handles"] - first["handles"],
              "after_job_growth": last["after_jobs"] - first["after_jobs"],
              "device_errors": last["dev_err"], "device_ok": last["dev_ok"],
              "tk_errors": errors, "crash_log_grew": crash_after > crash_before, "samples": samples,
              "log_folder": work}
    out = os.path.join(tempfile.gettempdir(), "soak_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print("REPORT", json.dumps({k: v for k, v in report.items() if k != "samples"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 20.0))
