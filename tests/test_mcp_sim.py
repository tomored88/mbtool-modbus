import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import mbtool_mcp as M
from modbus_core import TcpMaster


class T(unittest.TestCase):
    def tearDown(self):
        M.sim_stop()

    def test_preset_with_moving_values(self):
        r = M.sim_start(0)
        self.assertTrue(r["ok"], r)
        port = int(r["url"].rsplit(":", 1)[1])
        r = M.sim_load_preset("library/schneider_atv320.json", mode="profile", speed=5.0)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["device_type"], "drive")
        self.assertTrue(r["moving_points"])
        self.assertIn("UNVERIFIED", r["note"])
        m = TcpMaster("127.0.0.1", port, timeout=2)
        try:
            from preset import load_preset
            pr = load_preset(os.path.join(M._PRESET_DIR, "library", "schneider_atv320.json"))
            names = {x["point"] for x in r["moving_points"]}
            pts = [p for p in pr.points if p.name in names and p.fc == 3]
            self.assertTrue(pts)
            seen = set()
            for _ in range(12):
                seen.add(tuple(tuple(m.read_holding(1, p.address, p.words)) for p in pts))
                time.sleep(0.4)
        finally:
            m.close()
        self.assertGreater(len(seen), 1, "values never changed")
        self.assertTrue(M.sim_signals_stop()["ok"])

    def test_errors(self):
        self.assertFalse(M.sim_load_preset("library/schneider_atv320.json")["ok"])  # no simulator yet
        M.sim_start(0)
        self.assertFalse(M.sim_load_preset("library/schneider_atv320.json", mode="wobble")["ok"])
        self.assertFalse(M.sim_load_preset("library/schneider_atv320.json", mode="profile", device_type="toaster")["ok"])
        self.assertFalse(M.sim_load_preset("../mbtool.py")["ok"])
        self.assertFalse(M.sim_load_preset("library/nope.json")["ok"])

    def test_static_load_has_no_engine(self):
        M.sim_start(0)
        r = M.sim_load_preset("example_generic_meter.json")
        self.assertTrue(r["ok"] and "moving_points" not in r, r)
        self.assertIsNone(M._state.get("signals"))


if __name__ == "__main__":
    unittest.main(verbosity=0)
