"""Bus health test (bushealth.py) and the per-device error breakdown (monitor.classify_error / LineQuality)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import unittest

import bushealth
import monitor as mon
from modbus_core import Faults, SerialMaster, SerialSlave, SimSlave, TcpMaster, VirtualCable


class Classify(unittest.TestCase):
    def test_kinds(self):
        c = mon.classify_error
        self.assertEqual(c("response timeout"), "timeout")
        self.assertEqual(c("CRC error"), "crc")
        self.assertEqual(c("LRC error"), "crc")
        self.assertEqual(c("Exception 2: Illegal data address (FC 03)"), "exception")
        self.assertEqual(c("unexpected slave id"), "wrong_id")
        self.assertEqual(c("short response"), "frame")
        self.assertEqual(c("MBAP header invalid; connection closed"), "frame")
        self.assertEqual(c("Modbus Connection Failed: [WinError 10061]"), "link")
        self.assertEqual(c("could not open port 'COM9'"), "link")
        self.assertEqual(c("something odd"), "other")
        self.assertEqual(mon.kinds_text({"timeout": 3, "crc": 1}), "T3 C1")
        self.assertEqual(mon.kinds_text({"crc": 1}, short=False), "CRC / LRC 1")

    def test_line_quality_and_device_status(self):
        st = mon.DeviceStatus("a")
        st.on_result(1.0, {"ok": True, "values": {}, "texts": {}, "error": None,
                           "req": {"times": [10.0, 20.0], "kinds": {}, "requests": 2}})
        st.on_result(2.0, {"ok": False, "values": {}, "texts": {}, "error": "CRC error",
                           "req": {"times": [], "kinds": {"crc": 1}, "requests": 1}})
        st.on_result(3.0, {"ok": False, "values": {}, "texts": {}, "error": "response timeout"})  # no req stats
        q = st.quality.summary()
        self.assertEqual(q["requests"], 4)
        self.assertEqual(q["errors"], 2)
        self.assertEqual(q["kinds"], {"crc": 1, "timeout": 1})
        self.assertAlmostEqual(q["error_pct"], 50.0)
        self.assertEqual((q["min"], q["max"], q["avg"]), (10.0, 20.0, 15.0))
        st.reset_counters()
        self.assertEqual(st.quality.requests, 0)
        self.assertEqual(st.errors, 0)

    def test_worker_reports_request_stats(self):
        s = SimSlave("127.0.0.1", 0, unit=None)
        msgs = []
        try:
            p = mon.MultiPoller(lambda c: TcpMaster(c["host"], int(c["port"]), timeout=0.5), msgs.append, gap_s=0)
            dev = mon.norm_device({"name": "d", "conn": {"mode": "TCP", "host": "127.0.0.1", "port": str(s.port)},
                                   "unit": "1", "fc": 3, "addr": "0", "qty": "4", "scan": "50"})
            import time
            p.start_device(dev, mon.make_reader(dev, None))
            end = time.time() + 5
            while time.time() < end and not any(m[0] == "dev" for m in msgs):
                time.sleep(0.02)
            p.stop_all()
            res = next(m[4] for m in msgs if m[0] == "dev")
            self.assertEqual(res["req"]["requests"], 1)
            self.assertEqual(len(res["req"]["times"]), 1)
        finally:
            s.stop()


class Health(unittest.TestCase):
    def test_tcp_good_and_dead_device(self):
        s = SimSlave("127.0.0.1", 0, unit=1)
        s.response_delay = 0.02
        try:
            t = [{"name": "good", "unit": 1, "fc": 3, "addr": 0, "qty": 4},
                 {"name": "dead", "unit": 7, "fc": 3, "addr": 0, "qty": 4}]
            ht = bushealth.HealthTest(lambda: TcpMaster("127.0.0.1", s.port, timeout=1), t, kind="tcp", samples=5,
                                      probe_timeout_s=0.3, base_gap_ms=0)
            rep = ht.run()
            g, d = rep["devices"]
            self.assertEqual(g["grade"], "good", g)
            self.assertEqual(g["ok"], 5)
            self.assertGreaterEqual(g["rtt"]["min"], 15)
            self.assertEqual(d["grade"], "no answer")
            self.assertTrue(d["kinds"].get("timeout"))
            self.assertEqual(rep["sweep"], [])  # no gap sweep on TCP
            self.assertGreaterEqual(rep["recommended_timeout_ms"], 100)
            self.assertTrue(any("No answer from dead" in a for a in rep["advice"]))
            self.assertIn("good", bushealth.format_report(rep))
        finally:
            s.stop()

    def test_rtu_crc_errors_and_gap_sweep(self):
        cable = VirtualCable(baud=38400)
        slaves = []
        try:
            for u in (1, 2):
                st = SimSlave(listen=False)
                if u == 2:
                    st.faults = Faults(seed=3, bad_crc=0.5)
                slaves.append(SerialSlave(cable.url, u, 38400, "E", store=st))
            t = [{"name": f"u{u}", "unit": u, "fc": 3, "addr": 0, "qty": 6} for u in (1, 2)]
            ht = bushealth.HealthTest(lambda: SerialMaster(cable.url, 38400, "E", 1, timeout=1), t, kind="rtu",
                                      baud=38400, bits=11, samples=12, probe_timeout_s=0.5, base_gap_ms=10,
                                      gaps_ms=(10, 0), sweep_rounds=2, current_timeout_ms=1000)
            rep = ht.run()
            a, b = rep["devices"]
            self.assertEqual(a["grade"], "good", a)
            self.assertIn(b["grade"], ("warn", "bad"))
            self.assertTrue(b["kinds"].get("crc"), b)
            self.assertGreater(a["wire_ms"], 0)
            self.assertEqual([x["gap_ms"] for x in rep["sweep"]], [10.0, 0.0])
            self.assertTrue(any("CRC" in x for x in rep["advice"]))
        finally:
            for x in slaves:
                x.stop()
            cable.close()

    def test_stop_aborts(self):
        s = SimSlave("127.0.0.1", 0, unit=None)
        try:
            n = {"c": 0}

            def stop():
                n["c"] += 1
                return n["c"] > 3
            t = [{"name": "a", "unit": 1, "fc": 3, "addr": 0, "qty": 1}]
            rep = bushealth.HealthTest(lambda: TcpMaster("127.0.0.1", s.port), t, kind="tcp", samples=50,
                                       base_gap_ms=0, stop=stop).run()
            self.assertTrue(rep["aborted"])
            self.assertLess(rep["devices"][0]["requests"], 50)
        finally:
            s.stop()

    def test_timeout_advice(self):
        rep = {"kind": "rtu", "aborted": False, "current_timeout_ms": 100, "recommended_timeout_ms": 300,
               "min_clean_gap_ms": 5.0, "recommended_gap_ms": 10.0, "sweep": [{}], "expected_cycle_ms": 120,
               "devices": [{"name": "x", "grade": "good", "kinds": {}, "rtt": {"max": 180.0}, "turnaround_ms": 150.0}]}
        adv = bushealth.advice(rep)
        self.assertTrue(any("too short" in a for a in adv))
        self.assertTrue(any("needs 150 ms" in a for a in adv))


if __name__ == "__main__":
    unittest.main(verbosity=0)
