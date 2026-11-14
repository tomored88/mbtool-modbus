import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import buscalc as B
from modbus_core import SerialMaster, SerialSlave, SimSlave, TcpMaster, VirtualCable
from preset import load_preset

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class T(unittest.TestCase):
    def test_frame_sizes(self):
        self.assertEqual(B.frame_bytes(3, 10), (8, 25))
        self.assertEqual(B.frame_bytes(1, 9), (8, 7))
        self.assertEqual(B.frame_bytes(16, 4), (17, 8))
        self.assertEqual(B.frame_bytes(6, 1), (8, 8))
        self.assertEqual(B.frame_bytes(5, 1), (8, 8))
        self.assertEqual(B.frame_bytes(15, 10), (11, 8))

    def test_char_and_t35(self):
        l = B.Link("rtu", 9600, 8, "N", 1)
        self.assertEqual(l.bits_per_char, 10)
        self.assertAlmostEqual(l.char_ms(), 1.0417, places=3)
        self.assertAlmostEqual(l.t35_ms(), 3.6458, places=3)
        self.assertEqual(B.Link("rtu", 115200).t35_ms(), 1.75)
        self.assertEqual(B.Link("rtu", 9600, 8, "E", 1).bits_per_char, 11)
        self.assertEqual(B.Link("tcp").t35_ms(), 0.0)

    def test_request_total_adds_up(self):
        l = B.Link("rtu", 19200, 8, "E", 1, gap_ms=5, usb_latency_ms=2)
        r = B.request_ms(l, 3, 10, device_delay_ms=20)
        parts = r["wait"] + r["request"] + r["device"] + r["response"] + r["usb"] + r["gap"]
        self.assertAlmostEqual(r["total"], parts)
        self.assertAlmostEqual(r["request"], 8 * l.char_ms())
        self.assertAlmostEqual(r["response"], 25 * l.char_ms())

    def test_ascii_slower_than_rtu(self):
        rtu = B.request_ms(B.Link("rtu", 9600), 3, 10)["total"]
        asc = B.request_ms(B.Link("ascii", 9600, 7, "E", 1), 3, 10)["total"]
        self.assertGreater(asc, rtu * 1.5)

    def test_faster_baud_shorter_cycle(self):
        devs = [{"name": "a", "unit": 1, "requests": [(3, 20), (4, 10)]}]
        rows = B.what_if(B.Link("rtu", 9600), devs)
        times = [t for _b, t, _u in rows]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_offline_device_costs_timeout(self):
        l = B.Link("rtu", 19200, timeout_ms=500, retries=1)
        e = B.estimate_cycle(l, [{"name": "dead", "unit": 9, "requests": [(3, 5)], "online": False},
                                 {"name": "ok", "unit": 1, "requests": [(3, 5)]}])
        self.assertAlmostEqual(e["devices"][0]["ms"], 1000.0)
        self.assertTrue(any("timeout" in t.lower() for t in B.advice(l, e)))

    def test_estimate_sums_devices_and_reports(self):
        l = B.Link("rtu", 9600)
        devs = [{"name": "a", "unit": 1, "requests": [(3, 10)]}, {"name": "b", "unit": 2, "requests": [(3, 10), (4, 2)]}]
        e = B.estimate_cycle(l, devs)
        self.assertAlmostEqual(e["total_ms"], sum(d["ms"] for d in e["devices"]))
        self.assertEqual(e["requests"], 3)
        self.assertIn("Cycle:", B.format_report(l, e, 100))

    def test_invalid_link(self):
        for kw in ({"kind": "x"}, {"baud": 0}, {"parity": "Q"}, {"stop": 3}):
            with self.assertRaises(ValueError):
                B.Link(**kw)

    def test_preset_requests(self):
        pr = load_preset(os.path.join(HERE, "presets", "library", "schneider_atv320.json"))
        reqs = B.requests_for_preset(pr)
        self.assertTrue(reqs and all(q >= 1 for _f, q in reqs))

    def test_estimate_matches_measurement_on_emulated_line(self):
        """Master -> emulated 19200 8E1 line -> slave that needs 20 ms: measured time is close to the estimate."""
        cable = VirtualCable(baud=19200, bits_per_char=11)
        store = SimSlave(listen=False)
        store.response_delay = 0.020
        SerialSlave(cable.url, 1, 19200, "E", store=store)
        m = SerialMaster(cable.url, 19200, "E", 1, timeout=2.0)
        try:
            m.read_holding(1, 0, 10)  # warm up
            n, t0 = 8, time.perf_counter()
            for _ in range(n):
                m.read_holding(1, 0, 10)
            measured = (time.perf_counter() - t0) * 1000 / n
        finally:
            m.close()
            cable.close()
        est = B.request_ms(B.Link("rtu", 19200, 8, "E", 1), 3, 10, device_delay_ms=20)["total"]
        self.assertLess(abs(measured - est) / est, 0.45, f"measured {measured:.1f} ms, estimated {est:.1f} ms")

    def test_tcp_sim_response_delay(self):
        s = SimSlave("127.0.0.1", 0)
        s.response_delay = 0.05
        m = TcpMaster("127.0.0.1", s.port, timeout=2)
        try:
            t0 = time.perf_counter()
            m.read_holding(1, 0, 2)
            self.assertGreater(time.perf_counter() - t0, 0.045)
        finally:
            m.close()
            s.stop()


if __name__ == "__main__":
    unittest.main(verbosity=0)
