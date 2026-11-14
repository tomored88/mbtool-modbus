import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import demo
import monitor as mon
from modbus_core import SerialMaster, TcpMaster
from preset import load_preset


class T(unittest.TestCase):
    def test_all_demo_presets_exist(self):
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets")
        for _n, _u, rel, _d in demo.DEMO_SET:
            load_preset(os.path.join(base, *rel.split("/")))

    def test_tcp_devices_answer_with_moving_values(self):
        d = demo.Demo(count=3, line="tcp", speed=5.0)
        try:
            devs = d.start()
            self.assertEqual([x["name"] for x in devs], ["Meter 1", "Drive 1", "Level 1"])
            for dv in devs:
                self.assertFalse(mon.device_errors(mon.norm_device(dv), []), dv["name"])
            pr = load_preset(devs[0]["preset"])
            p = next(x for x in pr.points if x.name in d.engines[0].values() and x.fc in (3, 4))
            m = TcpMaster("127.0.0.1", int(devs[0]["conn"]["port"]), timeout=2)
            try:
                seen = set()
                read = m.read_holding if p.fc == 3 else m.read_input
                for _ in range(10):
                    seen.add(tuple(read(1, p.address, p.words)))
                    time.sleep(0.3)
            finally:
                m.close()
            self.assertGreater(len(seen), 1)
        finally:
            d.stop()
        self.assertFalse(d.running)

    def test_rtu_line_shares_one_cable(self):
        d = demo.Demo(count=3, line="rtu", baud=38400)
        try:
            devs = d.start()
            self.assertEqual(len({x["conn"]["com"] for x in devs}), 1)
            m = SerialMaster(devs[0]["conn"]["com"], 38400, "E", 1, timeout=2.0)
            try:
                for dv in devs:
                    pr = load_preset(dv["preset"])
                    b = pr.blocks()[0]
                    time.sleep(0.06)
                    regs = (m.read_holding if b["fc"] == 3 else m.read_input)(int(dv["unit"]), b["start"], min(2, b["end"] - b["start"]))
                    self.assertTrue(regs)
            finally:
                m.close()
        finally:
            d.stop()

    def test_set_delay_and_restart(self):
        d = demo.Demo(count=2, line="tcp")
        d.start()
        self.assertTrue(d.set_delay("Meter 1", 40))
        self.assertAlmostEqual(d.slaves[0].response_delay, 0.04)
        self.assertFalse(d.set_delay("Nope", 1))
        d.stop()
        d.start()
        self.assertEqual(len(d.devices), 2)
        d.stop()

    def test_bad_line(self):
        with self.assertRaises(ValueError):
            demo.Demo(line="usb")


if __name__ == "__main__":
    unittest.main(verbosity=0)
