"""PLC cycle mode: the statistics as pure functions, the cycle worker with fake masters (round robin order, gap,
target / overrun), and an in-process run of the GUI: 3 simulated RTU devices behind one virtual cable at 38400
baud with a 10 ms response delay - the measured cycle must be near buscalc's estimate; the independent mode
keeps working as before."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest

import buscalc
import monitor as M
from modbus_core import ModbusError, SerialSlave, SimSlave, VirtualCable


def info(ms, t0, devs=None, overrun=False, target=None, linked=True, kind="rtu", baud=9600, bits=10):
    return {"t0": t0, "ms": ms, "target_ms": target, "overrun": overrun, "linked": linked, "kind": kind,
            "baud": baud, "bits": bits, "error": None if linked else "port busy",
            "devices": devs if devs is not None else [
                {"name": "A", "requests": 2, "ms": ms * 0.6, "times": [10.0, 12.0], "errors": 0, "timeouts": 0,
                 "bytes": 66, "no_link": not linked},
                {"name": "B", "requests": 1, "ms": ms * 0.4, "times": [20.0], "errors": 1, "timeouts": 1,
                 "bytes": 8, "no_link": not linked}]}


class Pure(unittest.TestCase):
    def test_span_and_helpers(self):
        self.assertEqual(M.span_stats([]), {"last": None, "avg": None, "min": None, "max": None, "jitter": None})
        s = M.span_stats([10.0, 14.0, 12.0])
        self.assertEqual((s["last"], s["avg"], s["min"], s["max"], s["jitter"]), (12.0, 12.0, 10.0, 14.0, 4.0))
        # 96 bytes x 10 bits at 9600 baud = 100 ms on the wire out of 200 ms = 50 %
        self.assertAlmostEqual(M.line_busy_pct(96, 10, 9600, 200.0), 50.0)
        self.assertIsNone(M.line_busy_pct(96, 10, None, 200.0))
        self.assertIsNone(M.line_busy_pct(96, 10, 9600, 0))
        self.assertEqual(M.line_busy_pct(10 ** 6, 10, 9600, 1.0), 100.0)
        self.assertAlmostEqual(M.pct_diff(100.0, 120.0), 20.0)
        self.assertAlmostEqual(M.pct_diff(100.0, 90.0), -10.0)
        self.assertIsNone(M.pct_diff(None, 5.0))
        self.assertIsNone(M.pct_diff(10.0, None))
        self.assertEqual(M.fmt_ms(None), "-")
        self.assertEqual(M.fmt_ms(1.234), "1.2")
        self.assertEqual(M.wire_bytes("rtu", 5), 8)
        self.assertEqual(M.wire_bytes("tcp", 5), 12)
        self.assertEqual(M.wire_bytes("ascii", 5), 17)
        self.assertEqual(M.bits_per_char({"data": "8", "parity": "E", "stop": "1"}), 11)
        self.assertEqual(M.bits_per_char({"data": "8", "parity": "N", "stop": "1"}), 10)

    def test_target_and_mode_parsing(self):
        self.assertIsNone(M.parse_target_ms(""))
        self.assertIsNone(M.parse_target_ms("0"))
        self.assertEqual(M.parse_target_ms(" 250 "), 250.0)
        self.assertEqual(M.parse_target_ms("12,5"), 12.5)
        for bad in ("abc", "-3", "0.5", "999999"):
            with self.assertRaises(ValueError):
                M.parse_target_ms(bad)
        self.assertEqual(M.norm_scan_mode("cycle"), "cycle")
        self.assertEqual(M.norm_scan_mode("nope"), "independent")
        self.assertEqual(M.norm_scan_mode(None), "independent")

    def test_bus_stats(self):
        b = M.BusCycleStats(("serial", "com3"))
        self.assertEqual(b.label, "COM3")
        b.add(info(100.0, 10.0, target=150.0))
        b.add(info(120.0, 10.15, target=150.0))
        b.add(info(160.0, 10.30, overrun=True, target=150.0))
        s = b.summary()
        self.assertEqual(s["cycles"], 3)
        self.assertEqual((s["last"], s["min"], s["max"]), (160.0, 100.0, 160.0))
        self.assertAlmostEqual(s["avg"], 380.0 / 3)
        self.assertAlmostEqual(s["jitter"], 60.0)
        self.assertAlmostEqual(s["cps"], 2 / 0.30, places=3)  # from the start times: target waits included
        self.assertEqual(s["overruns"], 1)
        self.assertEqual(s["timeouts"], 3)
        self.assertEqual(s["errors"], 3)
        self.assertEqual(s["bytes"], 3 * 74)
        self.assertAlmostEqual(s["busy_pct"], M.line_busy_pct(3 * 74, 10, 9600, 380.0))
        self.assertEqual(list(b.history), [100.0, 120.0, 160.0])
        a = b.devices["A"].row()
        self.assertEqual(a["requests"], 2)
        self.assertAlmostEqual(a["ms"], 0.6 * 380.0 / 3)
        self.assertAlmostEqual(a["resp_avg"], 11.0)
        self.assertEqual(a["resp_max"], 12.0)
        bb = b.devices["B"].row()
        self.assertEqual((bb["errors"], bb["timeouts"]), (3, 3))
        # a cycle without a connection: counted, not timed
        b.add(info(5.0, 11.0, linked=False))
        self.assertEqual(b.n, 3)
        self.assertEqual(b.link_errors, 1)
        self.assertEqual(b.last_error, "port busy")
        # TCP: no line busy figure
        t = M.BusCycleStats(("tcp", "10.0.0.5", "502"))
        t.add(info(5.0, 1.0, kind="tcp", baud=None))
        self.assertIsNone(t.summary()["busy_pct"])
        self.assertEqual(t.label, "TCP 10.0.0.5:502")
        self.assertEqual(M.bus_label(("tcp", "Meter")), "TCP device Meter")
        # rolling history is bounded
        h = M.BusCycleStats(("serial", "com1"), history=120)
        for i in range(300):
            h.add(info(float(i), i * 0.1))
        self.assertEqual(len(h.history), 120)
        self.assertEqual(h.history[-1], 299.0)

    def test_cycle_stats_report(self):
        cs = M.CycleStats()
        self.assertIn("No cycle measured", cs.report())
        cs.add(("serial", "com3"), info(100.0, 1.0))
        cs.add(("serial", "com3"), info(110.0, 1.2))
        rep = cs.report({("serial", "com3"): 100.0})
        self.assertIn("Bus COM3: 2 cycles", rep)
        self.assertIn("avg 105.0", rep)
        self.assertIn("difference +5 %", rep)
        self.assertIn("A ", rep)
        cs.reset()
        self.assertEqual(cs.buses, {})

    def test_bus_key_modes(self):
        def dev(name, mode="TCP", host="10.0.0.5", port="502", com="COM3"):
            return M.norm_device({"name": name, "conn": {"mode": mode, "host": host, "port": port, "com": com}})
        a, b = dev("a"), dev("b")
        self.assertNotEqual(M.bus_key(a), M.bus_key(b))  # independent: one worker per TCP device (unchanged)
        self.assertEqual(M.bus_key(a, "cycle"), M.bus_key(b, "cycle"))  # cycle: one per TCP target
        self.assertNotEqual(M.bus_key(a, "cycle"), M.bus_key(dev("c", port="503"), "cycle"))
        s1, s2 = dev("s1", "RTU", com="COM3"), dev("s2", "RTU", com="com3")
        self.assertEqual(M.bus_key(s1), M.bus_key(s2, "cycle"))

    def test_calc_settings(self):
        c = M.norm_calc({"kind": "ascii", "baud": 19200, "parity": "e", "stop": "3", "retries": True,
                         "dev": {"A": {"delay_ms": 5, "online": False}, "B": "bad", 3: {}}})
        self.assertEqual((c["kind"], c["baud"], c["parity"], c["stop"], c["retries"]), ("ASCII", "19200", "E", "1", "0"))
        self.assertEqual(c["dev"], {"A": {"delay_ms": "5", "online": False}})
        self.assertEqual(M.norm_calc(None), M.norm_calc({}))
        link, errs = M.calc_link(c)
        self.assertEqual(errs, {})
        self.assertEqual((link.kind, link.baud, link.parity), ("ascii", 19200, "E"))
        link, errs = M.calc_link(dict(c, baud="x", gap_ms="-1"))
        self.assertIsNone(link)
        self.assertEqual(set(errs), {"baud", "gap_ms"})
        conn = M.norm_conn({"mode": "RTU", "com": "COM4", "baud": "38400", "parity": "N", "timeout": "250"})
        link, errs = M.calc_link(c, conn=conn, gap_ms=7)
        self.assertEqual((link.kind, link.baud, link.parity, link.gap_ms, link.timeout_ms), ("rtu", 38400, "N", 7.0, 250.0))

    def test_workspace_keeps_cycle_settings(self):
        ws = M.new_workspace()
        self.assertEqual(ws["devices_cfg"]["scan_mode"], "independent")
        ws["devices_cfg"] = {"scan_mode": "cycle", "cycle_target_ms": "200",
                             "calc": M.norm_calc({"baud": "19200", "dev": {"A": {"delay_ms": "4", "online": True}}})}
        d = tempfile.mkdtemp()
        p = os.path.join(d, "w.mbw")
        M.save_workspace(p, ws)
        back, warn = M.load_workspace(p)
        self.assertEqual(warn, [])
        self.assertEqual(back["devices_cfg"]["scan_mode"], "cycle")
        self.assertEqual(back["devices_cfg"]["cycle_target_ms"], "200")
        self.assertEqual(back["devices_cfg"]["calc"]["baud"], "19200")
        self.assertEqual(back["devices_cfg"]["calc"]["dev"]["A"]["delay_ms"], "4")
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        raw["devices_cfg"] = {"scan_mode": "turbo", "cycle_target_ms": "abc", "calc": "x"}
        back, warn = M.normalize_workspace(raw, d)
        self.assertEqual(back["devices_cfg"]["scan_mode"], "independent")
        self.assertEqual(back["devices_cfg"]["cycle_target_ms"], "")
        self.assertEqual(len(warn), 2)
        back, warn = M.normalize_workspace({"version": 1}, d)  # old workspace: defaults, no warning
        self.assertEqual(back["devices_cfg"]["scan_mode"], "independent")
        self.assertEqual(warn, [])


class FakeMaster:
    """Answers FC3 after `delay` s; unit 9 times out. Records the request order and times."""

    def __init__(self, log, delay=0.002):
        self.log, self.delay = log, delay
        import collections
        self.traffic = collections.deque(maxlen=500)
        self.lock = threading.Lock()

    def request(self, unit, fc, addr=0, qty=1, **kw):
        self.log.append((time.perf_counter(), unit, addr))
        time.sleep(self.delay)
        if unit == 9:
            self.traffic.append({"t": time.time(), "dir": "ERR", "pdu_hex": ""})
            raise ModbusError("response timeout")
        self.traffic.append({"t": time.time(), "dir": "RX", "pdu_hex": " ".join(["00"] * (2 + 2 * qty))})
        return [unit * 100 + i for i in range(qty)]

    def close(self):
        pass


class Worker(unittest.TestCase):
    def dev(self, name, unit, qty=2):
        return M.norm_device({"name": name, "unit": str(unit), "qty": str(qty), "scan": "5000",
                              "conn": {"mode": "RTU", "com": "COM7", "baud": "9600"}})

    def run_poller(self, devs, secs, gap_s=0.0, target_s=None):
        log, msgs = [], []
        p = M.MultiPoller(lambda c: FakeMaster(log), msgs.append, gap_s=gap_s)
        p.mode, p.target_s = "cycle", target_s
        for d in devs:
            p.start_device(d, M.make_reader(d))
        time.sleep(secs)
        for w in p.stop_all():
            w.join(2)
        return log, msgs

    def test_first_cycle_covers_devices_started_together(self):
        """'Start all' adds devices one by one; the first cycle must not run with only the first of them
        (that showed up as reads 1, 1, 2, 3 ... and skewed the cycle statistics)."""
        log, msgs = [], []
        p = M.MultiPoller(lambda c: FakeMaster(log), msgs.append, gap_s=0.0)
        p.mode, p.target_s = "cycle", None
        for i, d in enumerate([self.dev("A", 1), self.dev("B", 2), self.dev("C", 3)]):
            p.start_device(d, M.make_reader(d))
            time.sleep(0.01)  # a slow 'Start all': the worker is already running when B and C arrive
        time.sleep(0.4)
        for w in p.stop_all():
            w.join(2)
        units = [u for _t, u, _a in log]
        self.assertEqual(units[:6], [1, 2, 3, 1, 2, 3], units[:9])
        first = next(m for m in msgs if m[0] == "cycle")[4]
        self.assertEqual([d["name"] for d in first["devices"]], ["A", "B", "C"])

    def test_round_robin_order_and_stats(self):
        devs = [self.dev("A", 1), self.dev("B", 2), self.dev("C", 3)]
        log, msgs = self.run_poller(devs, 0.35)
        units = [u for _t, u, _a in log]
        self.assertGreaterEqual(len(units), 9)
        # every device once per cycle, in order - their scan rate (5 s) is ignored in the cycle mode
        self.assertEqual(units[:9], [1, 2, 3] * 3)
        cyc = [m for m in msgs if m[0] == "cycle"]
        self.assertGreaterEqual(len(cyc), 3)
        inf = cyc[0][4]
        self.assertEqual([d["name"] for d in inf["devices"]], ["A", "B", "C"])
        self.assertEqual([d["requests"] for d in inf["devices"]], [1, 1, 1])
        self.assertTrue(all(d["times"][0] >= 1.5 for d in inf["devices"]))  # ~2 ms per request measured
        self.assertGreaterEqual(inf["ms"], sum(d["ms"] for d in inf["devices"]) - 0.5)
        self.assertEqual(inf["bytes"] if "bytes" in inf else sum(d["bytes"] for d in inf["devices"]), 3 * (8 + 9))
        self.assertEqual(inf["kind"], "rtu")
        self.assertEqual((inf["baud"], inf["bits"]), (9600, 10))
        devmsgs = [m for m in msgs if m[0] == "dev"]
        self.assertEqual([m[1] for m in devmsgs[:6]], ["A", "B", "C"] * 2)
        self.assertTrue(devmsgs[0][4]["ok"])

    def test_gap_before_every_request(self):
        log, _msgs = self.run_poller([self.dev("A", 1), self.dev("B", 2)], 0.3, gap_s=0.03)
        starts = [t for t, _u, _a in log]
        self.assertGreaterEqual(len(starts), 3)
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(g >= 0.03 for g in gaps), gaps)

    def test_target_period_and_overrun(self):
        devs = [self.dev("A", 1), self.dev("B", 2)]
        _log, msgs = self.run_poller(devs, 0.5, target_s=0.05)
        cyc = [m[4] for m in msgs if m[0] == "cycle"]
        self.assertGreaterEqual(len(cyc), 6)
        self.assertFalse(any(c["overrun"] for c in cyc))
        periods = [b["t0"] - a["t0"] for a, b in zip(cyc, cyc[1:])]
        self.assertTrue(all(0.045 <= p <= 0.09 for p in periods), periods)  # fixed 50 ms period
        st = M.BusCycleStats(("serial", "com7"))
        for c in cyc:
            st.add(c)
        self.assertAlmostEqual(st.cycles_per_s(), 20.0, delta=4.0)
        # a target shorter than the work: every cycle is an overrun
        _log, msgs = self.run_poller(devs, 0.3, target_s=0.001)
        cyc = [m[4] for m in msgs if m[0] == "cycle"]
        self.assertTrue(cyc and all(c["overrun"] for c in cyc))

    def test_timeouts_counted(self):
        _log, msgs = self.run_poller([self.dev("A", 1), self.dev("Dead", 9)], 0.2)
        cyc = [m[4] for m in msgs if m[0] == "cycle"]
        dead = cyc[0]["devices"][1]
        self.assertEqual((dead["errors"], dead["timeouts"], dead["requests"]), (1, 1, 1))
        self.assertFalse([m for m in msgs if m[0] == "dev" and m[1] == "Dead"][0][4]["ok"])

    def test_connect_failure_and_independent_unchanged(self):
        msgs = []

        def bad(c):
            raise ModbusError("COM7 is busy")
        p = M.MultiPoller(bad, msgs.append, retry_s=0.1)
        p.mode = "cycle"
        p.start_device(self.dev("A", 1), M.make_reader(self.dev("A", 1)))
        time.sleep(0.35)
        for w in p.stop_all():
            w.join(2)
        cyc = [m[4] for m in msgs if m[0] == "cycle"]
        self.assertTrue(cyc and not cyc[0]["linked"])
        self.assertLessEqual(len(cyc), 6)  # waits for the retry instead of spinning
        self.assertIn("busy", cyc[0]["error"])
        # independent mode: no cycle messages, scan rate respected
        log, msgs = [], []
        p = M.MultiPoller(lambda c: FakeMaster(log), msgs.append, gap_s=0.0)
        self.assertEqual(p.mode, "independent")
        p.start_device(self.dev("A", 1), M.make_reader(self.dev("A", 1)))
        time.sleep(0.3)
        for w in p.stop_all():
            w.join(2)
        self.assertEqual([m for m in msgs if m[0] == "cycle"], [])
        self.assertEqual(len(log), 1)  # 5 s scan rate: one read only


class Gui(unittest.TestCase):
    BAUD = 38400

    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        cls.dir = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE = os.path.join(cls.dir, "settings.json")
        MB.HOME_SETTINGS = os.path.join(cls.dir, "home_settings.json")
        cls.cable = VirtualCable(baud=cls.BAUD, bits_per_char=10)  # 8N1 = 10 bits per character
        cls.slaves = []
        for unit in (1, 2, 3):
            store = SimSlave(listen=False)
            store.response_delay = 0.010
            for i in range(10):
                store.holding[i] = unit * 100 + i
            cls.slaves.append(SerialSlave(cls.cable.url, unit, cls.BAUD, "N", store=store))
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = app = MB.MasterApp(cls.root)
        conn = {"mode": "RTU", "com": cls.cable.url, "baud": str(cls.BAUD), "parity": "N", "stop": "1", "data": "8",
                "timeout": "500"}
        app.devices = [M.norm_device({"name": f"Dev{u}", "conn": conn, "unit": str(u), "scan": "1000",
                                      "fc": "3", "addr": "0", "qty": "10"}) for u in (1, 2, 3)]
        app.cfg["devices"]["gap_ms"] = "50"  # default gap; the sim slaves need ~40 ms silence to split frames on one cable
        app.devpoll.gap_s = 0.05
        app.cfg["devices"]["calc"] = M.norm_calc({"delay_ms": "10"})
        cls.win = app.open_devices()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.stop_all_devices()
            cls.root.destroy()
            for s in cls.slaves:
                s.stop()
            cls.cable.close()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS = cls.saved

    def pump(self, sec):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def wait(self, cond, sec=6.0):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.01)
        return False

    def bus(self):
        return next(iter(self.app.cycle_stats.buses.values()), None)

    def test_1_calculator_without_polling(self):
        win, app = self.win, self.app
        ct = win.calc_tab
        win.nb.select(ct.frame)
        self.root.update()
        ct.v["kind"].set("RTU")
        ct.v["baud"].set(str(self.BAUD))
        ct.v["gap_ms"].set("50")
        est = ct.estimate
        self.assertIsNotNone(est)
        self.assertEqual(len(est["devices"]), 3)
        self.assertEqual(est["requests"], 3)
        link = buscalc.Link("rtu", self.BAUD, 8, "N", 1, 50.0, 1000.0)
        want = buscalc.estimate_cycle(link, [{"name": n, "requests": [(3, 10)], "delay_ms": 10.0} for n in "abc"])
        self.assertAlmostEqual(est["total_ms"], want["total_ms"], places=3)
        text = ct.out.get("1.0", "end")
        for part in ("Estimate for RTU 38400", "Per device:", "Baud what-if", "Advice:", "Dev2"):
            self.assertIn(part, text)
        # per-device delay and offline
        ct.tree.selection_set("Dev2")
        self.root.update()
        ct.d_delay.set("50")
        ct.d_online.set(True)
        self.assertTrue(ct.apply_row())
        self.assertAlmostEqual(ct.estimate["total_ms"], want["total_ms"] + 40.0, places=3)
        ct.d_online.set(False)
        ct.apply_row()
        self.assertIn("OFFLINE", ct.out.get("1.0", "end"))
        ct.d_delay.set("")
        ct.d_online.set(True)
        ct.apply_row()
        self.assertEqual(app.cfg["devices"]["calc"]["dev"]["Dev2"], {"delay_ms": "", "online": True})
        ct.v["baud"].set("abc")
        self.assertIsNone(ct.estimate)
        self.assertTrue(ct.hints["baud"].cget("text"))
        ct.v["baud"].set(str(self.BAUD))
        # persisted in the settings (type-checked on load)
        self.MB.save_settings(app.cfg)
        cfg = self.MB.load_settings()
        self.assertEqual(cfg["devices"]["calc"]["baud"], str(self.BAUD))
        self.assertEqual(cfg["devices"]["calc"]["gap_ms"], "50")

    def test_2_plc_cycle_measured_near_estimate(self):
        win, app = self.win, self.app
        win.mode_var.set(M.SCAN_MODES["cycle"])
        win._mode_changed()
        self.assertEqual(app.devpoll.mode, "cycle")
        self.assertEqual(app.cfg["devices"]["scan_mode"], "cycle")
        self.assertEqual(win.start_all(), {})
        win.nb.select(win.cycle_tab.frame)
        self.assertTrue(self.wait(lambda: self.bus() is not None and self.bus().n >= 3, 8), "no cycles measured")
        self.app.cycle_stats.reset()  # drop the first cycles (port open, warm-up)
        self.pump(3.0)
        b = self.bus()
        self.assertIsNotNone(b)
        s = b.summary()
        self.assertGreaterEqual(s["cycles"], 8)
        self.assertEqual(s["timeouts"], 0, app.cycle_stats.report())
        est = win.calc_tab.estimates_by_bus()
        key = M.bus_key(app.devices[0], "cycle")
        self.assertIn(key, est)
        diff = M.pct_diff(est[key], s["avg"])
        print(f"\n  estimate {est[key]:.1f} ms, measured avg {s['avg']:.1f} ms ({diff:+.0f} %), "
              f"min {s['min']:.1f} max {s['max']:.1f}, line busy {s['busy_pct']:.0f} %, cps {s['cps']:.2f}")
        self.assertLess(abs(diff), 50, app.cycle_stats.report(est))
        self.assertEqual(set(b.devices), {"Dev1", "Dev2", "Dev3"})
        for ds in b.devices.values():
            r = ds.row()
            self.assertEqual(r["requests"], 1)
            self.assertGreater(r["resp_avg"], 10.0)  # at least the device's 10 ms response delay
        self.assertGreater(s["busy_pct"], 5)
        self.assertAlmostEqual(s["cps"], 1000.0 / s["avg"], delta=0.35 * 1000.0 / s["avg"])
        # tables / chart / report / compare in the GUI
        win.cycle_tab.refresh(force=True)
        self.assertEqual(len(win.cycle_tab.bus.get_children()), 1)
        self.assertEqual(len(win.cycle_tab.dev.get_children()), 3)
        self.assertTrue(win.cycle_tab.cv.find_all())
        rep = win.cycle_tab.copy_report()
        self.assertIn("estimate", rep)
        rows = win.calc_tab.compare()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["diff"], M.pct_diff(rows[0]["estimate"], rows[0]["measured"]))
        self.assertIn("Estimated vs measured", win.calc_tab.out.get("1.0", "end"))
        # the device tabs are fed by the cycle too
        tab = win.tabs["Dev2"]
        ci = tab.grid.cols.index("Unsigned")
        self.assertTrue(self.wait(lambda: tab.grid.values[3][ci] == "203"))

    def test_3_overrun_with_tiny_target(self):
        win, app = self.win, self.app
        self.assertEqual(app.devpoll.mode, "cycle")
        win.target.set("5")
        self.assertEqual(app.devpoll.target_s, 0.005)
        self.pump(0.5)
        app.cycle_stats.reset()
        self.pump(1.5)
        s = self.bus().summary()
        self.assertGreaterEqual(s["cycles"], 3)
        self.assertEqual(s["overruns"], s["cycles"])
        win.target.set("1000")
        self.pump(0.3)
        app.cycle_stats.reset()
        self.pump(2.3)
        s = self.bus().summary()
        self.assertEqual(s["overruns"], 0)
        self.assertLessEqual(s["cycles"], 3)  # one cycle per second
        self.assertAlmostEqual(s["cps"] or 1.0, 1.0, delta=0.2)
        win.target.set("abc")
        self.assertTrue(win.l_target.cget("text"))
        self.assertEqual(app.devpoll.target_s, 1.0)  # a bad entry changes nothing
        win.target.set("")
        self.assertIsNone(app.devpoll.target_s)
        # workspace keeps the mode and target
        win.target.set("250")
        ws = app.gather_workspace()
        self.assertEqual(ws["devices_cfg"]["scan_mode"], "cycle")
        self.assertEqual(ws["devices_cfg"]["cycle_target_ms"], "250")
        win.target.set("")

    def test_4_back_to_independent(self):
        win, app = self.win, self.app
        win.mode_var.set(M.SCAN_MODES["independent"])
        win._mode_changed()
        self.assertEqual(app.devpoll.mode, "independent")
        self.assertTrue(all(app.devpoll.running(d["name"]) for d in app.devices))  # restarted in the new mode
        app.cycle_stats.reset()
        st = app.dev_status["Dev1"]
        ok0 = st.ok
        self.pump(2.2)
        self.assertEqual(app.cycle_stats.buses, {})  # no cycle statistics in the independent mode
        self.assertTrue(2 <= st.ok - ok0 <= 4, st.ok - ok0)  # 1000 ms scan rate
        self.assertEqual(win.tree.set("Dev1", "scan"), "1000")
        app.stop_all_devices()

    def test_5_sim_window_response_delay(self):
        from modbus_core import TcpMaster
        app = self.app
        sim = app.open_sim()
        try:
            sim.delay.set("25")
            self.assertAlmostEqual(sim.store.response_delay, 0.025)
            sim.v["mode"].set("TCP")
            sim._mode()
            sim.v["port"].set("0")
            sim.v["unit"].set("")
            sim.delay.set("x")
            self.assertTrue(sim.hints["delay"].cget("text").strip())
            self.assertAlmostEqual(sim.store.response_delay, 0.025)  # a bad entry keeps the last good value
            sim.delay.set("40")
            srv = SimSlave(port=0)  # find a free port for the sim window
            port = srv.port
            srv.stop()
            sim.v["port"].set(str(port))
            sim.start()
            self.assertTrue(sim.running(), sim.l_msg.cget("text"))
            self.assertAlmostEqual(sim.store.response_delay, 0.040)  # carried over to the TCP server's store
            m = TcpMaster("127.0.0.1", port, 2.0)
            try:
                t0 = time.perf_counter()
                m.read_holding(1, 0, 1)
                self.assertGreaterEqual(time.perf_counter() - t0, 0.035)
                sim.delay.set("0")  # live, while running
                t0 = time.perf_counter()
                m.read_holding(1, 0, 1)
                self.assertLess(time.perf_counter() - t0, 0.03)
            finally:
                m.close()
            self.assertEqual(app.cfg["sim"]["delay"], "0")
        finally:
            sim.stop()
            sim.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
