import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import buscalc
import busmap as M


def sample(**kw):
    m = M.default_map([("A", 1), ("B", 2), ("C", 3)])
    m.update(kw)
    return m


def texts(an, level=None):
    return " | ".join(t for lv, t in an["warnings"] if level in (None, lv))


class T(unittest.TestCase):
    def test_distances_and_delay(self):
        m = sample()
        m["nodes"][0].update(segment_m=20, stub_m=1)
        m["nodes"][1].update(segment_m=30)
        m["nodes"][2].update(segment_m=50, stub_m=2)
        an = M.analyze(m)
        self.assertEqual(an["trunk_m"], 100)
        self.assertEqual([r["distance_m"] for r in an["nodes"]], [21, 50, 102])
        v = M.C * 0.75
        self.assertAlmostEqual(an["nodes"][2]["one_way_us"], 102 / v * 1e6, places=6)
        self.assertAlmostEqual(an["nodes"][2]["round_trip_us"], 2 * an["nodes"][2]["one_way_us"])
        self.assertAlmostEqual(an["max_one_way_us"], an["nodes"][2]["one_way_us"])
        self.assertAlmostEqual(an["stub_total_m"], 3)

    def test_bit_is_longer_than_the_cable(self):
        an = M.analyze(sample(baud=9600))
        self.assertGreater(an["bit_length_m"], 10000)
        self.assertIn("electrically very short", texts(an, "info"))

    def test_recommended_baud_and_warning(self):
        self.assertEqual(M.recommended_baud(50), 115200)
        self.assertEqual(M.recommended_baud(500), 38400)
        self.assertEqual(M.recommended_baud(1200), 9600)
        self.assertIsNone(M.recommended_baud(1300))
        m = sample(baud=115200)
        m["nodes"][0]["segment_m"] = 800
        an = M.analyze(m)
        self.assertIn("above the usual limit", texts(an, "warn"))

    def test_too_long_is_error(self):
        m = sample()
        m["nodes"][0]["segment_m"] = 1300
        an = M.analyze(m)
        self.assertIn("repeater", texts(an, "error"))

    def test_unit_load_budget(self):
        m = M.default_map([(f"d{i}", i) for i in range(1, 34)])
        self.assertIn("unit loads", texts(M.analyze(m), "error"))
        for n in m["nodes"]:
            n["unit_load"] = 0.125
        self.assertNotIn("unit loads", texts(M.analyze(m), "error"))

    def test_stub_limit_depends_on_baud(self):
        m = sample(baud=115200)
        m["nodes"][1]["stub_m"] = 5
        self.assertIn("stub 5 m", texts(M.analyze(m), "warn"))
        m["baud"] = 9600
        self.assertNotIn("stub", texts(M.analyze(m), "warn"))

    def test_terminators(self):
        m = sample()
        for n in m["nodes"]:
            n["segment_m"] = 100
        m["terminate_far_end"] = False
        self.assertIn("terminate BOTH ends", texts(M.analyze(m), "warn"))
        m["terminate_far_end"] = True
        self.assertNotIn("terminate BOTH", texts(M.analyze(m), "warn"))
        m["nodes"][0]["terminated"] = True
        self.assertIn("middle of the line", texts(M.analyze(m), "error"))
        m2 = sample()
        m2["nodes"][-1]["terminated"] = True
        m2["terminate_far_end"] = False
        for n in m2["nodes"]:
            n["segment_m"] = 100
        self.assertNotIn("terminate BOTH", texts(M.analyze(m2), "warn"))  # far device carries the terminator

    def test_voltage_drop(self):
        m = sample(supply_v=24)
        for n in m["nodes"]:
            n.update(segment_m=200, supply_ma=500)
        an = M.analyze(m)
        self.assertLess(an["min_voltage"], 24)
        self.assertEqual(len(an["node_voltage"]), 3)
        self.assertTrue(an["node_voltage"][0] > an["node_voltage"][1] > an["node_voltage"][2])
        self.assertIn("Voltage at the far end", texts(an, "warn"))
        for n in m["nodes"]:
            n["supply_ma"] = 0
        self.assertNotIn("min_voltage", M.analyze(m))

    def test_attenuation_grows_with_distance_and_baud(self):
        a = M.analyze(sample(baud=9600))
        m = sample(baud=9600)
        m["nodes"][2]["segment_m"] = 500
        b = M.analyze(m)
        self.assertGreater(b["attenuation_db"], a["attenuation_db"])
        c = sample(baud=115200)
        c["nodes"][2]["segment_m"] = 500
        self.assertGreater(M.analyze(c)["attenuation_db"], b["attenuation_db"])

    def test_custom_cable_and_validation(self):
        m = sample(cable="custom", cable_custom={"vf": 0.5})
        self.assertAlmostEqual(M.analyze(m)["velocity_m_per_s"], M.C * 0.5)
        for bad in ({"baud": 0}, {"cable_custom": {"vf": 2}}):
            with self.assertRaises(M.MapError):
                M.analyze(sample(**bad))
        m = sample()
        m["nodes"][1]["unit"] = 1
        with self.assertRaises(M.MapError):
            M.analyze(m)
        m = sample()
        m["nodes"][0]["segment_m"] = -3
        with self.assertRaises(M.MapError):
            M.analyze(m)
        m = sample()
        m["nodes"][0]["unit"] = 999
        with self.assertRaises(M.MapError):
            M.analyze(m)

    def test_master_in_the_middle_warns(self):
        self.assertIn("middle", texts(M.analyze(sample(master_at_end=False)), "warn"))

    def test_exchange_timeline(self):
        m = sample()
        for n in m["nodes"]:
            n["segment_m"] = 100
        an = M.analyze(m)
        link = buscalc.Link("rtu", 19200, 8, "E", 1)
        tl = M.exchange_timeline(an, link, 2, 3, 10, delay_ms=20)
        times = [t for t, _ in tl["events"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(tl["events"][-1][1], "master has the response")
        est = buscalc.request_ms(link, 3, 10, 20)["total"]
        self.assertAlmostEqual(tl["total_ms"], est, delta=0.05)  # propagation adds only microseconds
        self.assertLess(tl["propagation_ms"], 0.01)
        with self.assertRaises(M.MapError):
            M.exchange_timeline(an, link, 9, 3, 10)

    def test_report_and_input_not_mutated(self):
        m = sample()
        before = copy.deepcopy(m)
        an = M.analyze(m)
        self.assertEqual(m, before)
        rep = M.format_report(an)
        self.assertIn("Trunk", rep)
        self.assertIn("Checks:", rep)


if __name__ == "__main__":
    unittest.main(verbosity=0)
