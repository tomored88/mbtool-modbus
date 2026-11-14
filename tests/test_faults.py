"""Fault injection: the master must report every kind of bad answer as a ModbusError, never hang, never mix up
answers, and work again as soon as the line is good. Covers TCP and RTU masters, a random mix, the GUI's main poll
and a shared RS-485 bus where one device is broken."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import os
import tempfile
import time
import tkinter as tk
import unittest

from modbus_core import (Faults, ModbusError, SerialMaster, SerialSlave, SimSlave, TcpMaster, VirtualCable)

SINGLE_TCP = ["no_reply", "exception", "truncate", "garbage", "wrong_unit", "wrong_tid", "slow", "drop_conn"]
SINGLE_RTU = ["no_reply", "exception", "truncate", "garbage", "wrong_unit", "bad_crc", "slow"]


def fill(store):
    for i in range(100):
        store.holding[i] = 1000 + i


class FaultsObject(unittest.TestCase):
    def test_rates_and_counts(self):
        f = Faults(seed=1, no_reply=0.5)
        hits = sum(f.roll("no_reply") for _ in range(1000))
        self.assertTrue(400 < hits < 600, hits)
        self.assertEqual(f.counts["no_reply"], hits)
        self.assertFalse(f.roll("bad_crc"))
        self.assertEqual(f.active(), {"no_reply": 0.5})
        with self.assertRaises(ValueError):
            Faults(explode=1)
        self.assertEqual(Faults(bad_crc=5).rates["bad_crc"], 1.0)


class TcpFaults(unittest.TestCase):
    def setUp(self):
        self.s = SimSlave("127.0.0.1", 0, unit=None)
        fill(self.s)
        self.m = TcpMaster("127.0.0.1", self.s.port, timeout=0.5)

    def tearDown(self):
        self.m.close()
        self.s.stop()

    def test_each_fault_is_an_error_and_the_next_request_works(self):
        for name in SINGLE_TCP:
            with self.subTest(fault=name):
                self.s.faults = Faults(seed=2, slow_ms=900, **{name: 1.0})
                t0 = time.time()
                with self.assertRaises(ModbusError):
                    self.m.read_holding(1, 0, 4)
                self.assertLess(time.time() - t0, 3.0, "master hung")
                self.s.faults = None
                time.sleep(0.05 if name != "slow" else 1.0)  # let a late answer arrive and be discarded
                self.assertEqual(self.m.read_holding(1, 10, 3), [1010, 1011, 1012], f"no recovery after {name}")

    def test_random_mix_never_mixes_up_answers(self):
        self.s.faults = Faults(seed=3, slow_ms=700, no_reply=0.08, exception=0.08, truncate=0.05, garbage=0.05,
                               wrong_unit=0.05, wrong_tid=0.05, slow=0.03, drop_conn=0.03)
        ok = bad = 0
        for i in range(150):
            a = i % 90
            try:
                got = self.m.read_holding(1, a, 3)
            except ModbusError:
                bad += 1
                continue
            self.assertEqual(got, [1000 + a, 1001 + a, 1002 + a], f"wrong data at request {i}")
            ok += 1
        self.assertGreater(ok, 60)
        self.assertGreater(bad, 10)
        self.s.faults = None
        time.sleep(0.8)
        self.assertEqual(self.m.read_holding(1, 0, 2), [1000, 1001])


class RtuFaults(unittest.TestCase):
    def setUp(self):
        self.cable = VirtualCable()
        self.store = SimSlave(listen=False)
        fill(self.store)
        self.slave = SerialSlave(self.cable.url, 1, 19200, "E", store=self.store)
        self.m = SerialMaster(self.cable.url, 19200, "E", 1, timeout=0.5)

    def tearDown(self):
        self.m.close()
        self.slave.stop()
        self.cable.close()

    def test_each_fault_is_an_error_and_the_next_request_works(self):
        for name in SINGLE_RTU:
            with self.subTest(fault=name):
                self.store.faults = Faults(seed=4, slow_ms=900, **{name: 1.0})
                t0 = time.time()
                with self.assertRaises(ModbusError):
                    self.m.read_holding(1, 0, 4)
                self.assertLess(time.time() - t0, 3.0, "master hung")
                self.store.faults = None
                time.sleep(0.1 if name != "slow" else 1.0)
                self.assertEqual(self.m.read_holding(1, 20, 2), [1020, 1021], f"no recovery after {name}")

    def test_random_mix_never_mixes_up_answers(self):
        self.store.faults = Faults(seed=5, no_reply=0.08, exception=0.08, truncate=0.05,
                                   garbage=0.05, wrong_unit=0.05, bad_crc=0.08)
        ok = bad = 0
        for i in range(120):
            a = i % 90
            try:
                got = self.m.read_holding(1, a, 2)
            except ModbusError:
                bad += 1
                continue
            self.assertEqual(got, [1000 + a, 1001 + a], f"wrong data at request {i}")
            ok += 1
        self.assertGreater(ok, 50)
        self.assertGreater(bad, 10)

    def test_answer_slightly_after_the_timeout_is_not_taken_for_the_next_request(self):
        """A device that answers a little after the timeout (here 80 ms late): the master waits up to
        SerialMaster.late_guard after a timeout, throws the late answer away, and the next reply is the right one."""
        for i in range(12):
            self.store.faults = Faults(seed=8, slow_ms=580, slow=1.0) if i % 3 == 0 else None
            a = i * 3
            try:
                got = self.m.read_holding(1, a, 2)
            except ModbusError:
                continue
            self.assertEqual(got, [1000 + a, 1001 + a], f"late answer taken at request {i}")

    def test_limit_answer_much_later_than_the_timeout(self):
        """Known limit of Modbus RTU (no transaction ID): an answer that comes later than timeout + late_guard can
        still be taken for the next request when both read the same number of registers. The fix is a longer
        timeout; this test only documents it (no assertion on the data)."""
        self.store.faults = Faults(seed=9, slow_ms=900, slow=1.0)
        with self.assertRaises(ModbusError):
            self.m.read_holding(1, 0, 2)
        self.store.faults = None
        try:
            self.m.read_holding(1, 50, 2)
        except ModbusError:
            pass


class GuiFaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        d = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE, MB.HOME_SETTINGS = os.path.join(d, "s.json"), os.path.join(d, "h.json")
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.errors = []
        cls.app = MB.MasterApp(cls.root)
        cls.root.report_callback_exception = lambda t, e, tb: cls.errors.append(f"{t.__name__}: {e}")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.stop_all_devices()
            cls.app.disconnect(quiet=True)
            cls.root.destroy()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS = cls.saved

    def pump(self, sec, cond=None):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            if cond and cond():
                return True
            time.sleep(0.02)
        return cond is None

    def test_1_main_poll_survives_a_bad_device_and_recovers(self):
        app, MB = self.app, self.MB
        s = SimSlave("127.0.0.1", 0, unit=None)
        fill(s)
        try:
            app.cfg["conn"].update(mode="TCP", host="127.0.0.1", port=str(s.port), timeout="400")
            app.pv["func"].set(MB.FUNCS[3])
            app.pv["addr"].set("0")
            app.pv["qty"].set("10")
            app.pv["scan"].set("100")
            app.connect()
            self.assertTrue(self.pump(5, lambda: app.state == "on" and app.grid.values[0] == "1000"))
            s.faults = Faults(seed=6, slow_ms=600, no_reply=0.15, exception=0.15, truncate=0.1, garbage=0.1,
                              wrong_tid=0.1, drop_conn=0.05)
            self.pump(8)
            self.assertGreater(sum(s.faults.counts.values()), 5)
            shown = app.grid.values[:10]
            self.assertTrue(all(v in ("", str(1000 + i)) for i, v in enumerate(shown)), shown)  # never wrong data
            s.faults = None
            self.assertTrue(self.pump(10, lambda: app.state == "on" and not app.poll_bad), "did not recover")
            self.assertEqual(app.grid.values[3], "1003")
            self.assertEqual(self.errors, [])
        finally:
            app.disconnect(quiet=True)
            s.stop()

    def test_2_broken_device_does_not_stop_the_others_on_the_bus(self):
        app = self.app
        cable = VirtualCable()
        good, bad = SimSlave(listen=False), SimSlave(listen=False)
        fill(good)
        fill(bad)
        bad.faults = Faults(seed=7, no_reply=0.5, bad_crc=0.3, garbage=0.2)
        sl = [SerialSlave(cable.url, 1, 19200, "E", store=good), SerialSlave(cable.url, 2, 19200, "E", store=bad)]
        conn = {"mode": "RTU", "com": cable.url, "baud": "19200", "parity": "E", "stop": "1", "data": "8",
                "timeout": "300"}
        try:
            for name, unit in (("Good", "1"), ("Bad", "2")):
                app.devices.append(self.MB.mon.norm_device({"name": name, "conn": conn, "unit": unit, "scan": "200",
                                                            "enabled": True, "fc": "3", "addr": "0", "qty": "5"}))
                app.start_device(name, load_alarms=False)
            self.assertTrue(self.pump(15, lambda: app.dev_status["Good"].ok >= 15 and app.dev_status["Bad"].errors >= 5),
                            (app.dev_status["Good"].ok, app.dev_status["Good"].errors, app.dev_status["Bad"].errors))
            g = app.dev_status["Good"]
            self.assertLessEqual(g.errors, max(2, g.ok // 10), "the good device suffers from the bad one")
            self.assertEqual(self.errors, [])
        finally:
            for n in ("Good", "Bad"):
                app.stop_device(n)
            app.devices = [d for d in app.devices if d["name"] not in ("Good", "Bad")]
            for x in sl:
                x.stop()
            cable.close()


if __name__ == "__main__":
    unittest.main(verbosity=0)
