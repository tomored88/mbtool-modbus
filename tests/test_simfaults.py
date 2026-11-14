"""Slave Sim > Faults tab: rates go live onto the store (TCP and the shared store), counters, quick presets, the
master sees the matching error type. Driven in-process (no clicks)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import socket
import tempfile
import time
import tkinter as tk
import unittest

import monitor as mon
from modbus_core import ModbusError, TcpMaster


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class SimFaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        d = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE, MB.HOME_SETTINGS = os.path.join(d, "s.json"), os.path.join(d, "h.json")
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = MB.MasterApp(cls.root)
        cls.sim = cls.app.open_sim()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.sim.stop()
            cls.root.destroy()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS = cls.saved

    def pump(self, sec=0.2):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            time.sleep(0.02)

    def test_1_off_by_default_and_validation(self):
        w = self.sim
        self.assertFalse(w.fault_on.get())
        self.assertIsNone(w.store.faults)
        w.fault_v["no_reply"].set("abc")
        self.assertIn("not a number", w.l_fhint.cget("text"))
        w.fault_v["no_reply"].set("150")
        self.assertIn("0 - 100", w.l_fhint.cget("text"))
        w.fault_v["no_reply"].set("0")
        self.assertEqual(w.l_fhint.cget("text"), "")

    def test_2_tcp_faults_reach_the_master(self):
        w = self.sim
        port = free_port()
        w.v["mode"].set("TCP")
        w._mode()
        w.v["port"].set(str(port))
        w.v["unit"].set("")
        w.start()
        self.assertTrue(w.running(), w.l_msg.cget("text"))
        m = TcpMaster("127.0.0.1", port, timeout=0.3)
        try:
            self.assertEqual(len(m.read_holding(1, 0, 2)), 2)
            w.fault_preset({"exception": "100"})
            self.assertTrue(w.fault_on.get())
            self.assertIsNotNone(w.store.faults)
            w.fault_exc.set(w.EXC_CODES[1])
            with self.assertRaises(ModbusError) as cm:
                m.read_holding(1, 0, 2)
            self.assertEqual(mon.classify_error(str(cm.exception)), "exception")
            self.assertIn("Exception 2", str(cm.exception))
            w.fault_preset({"no_reply": "100"})
            with self.assertRaises(ModbusError) as cm:
                m.read_holding(1, 0, 2)
            self.assertEqual(mon.classify_error(str(cm.exception)), "timeout")
            self.pump(0.6)
            self.assertNotEqual(w.fault_n["no_reply"].cget("text"), "0")
            self.assertIn("faults ON", w.l_fault.cget("text"))
            w.fault_preset({})  # Clear all
            self.assertIsNone(w.store.faults)
            self.assertEqual(len(m.read_holding(1, 0, 2)), 2)
            w.fault_on.set(False)
            w._apply_faults()
        finally:
            m.close()
            w.stop()

    def test_3_faults_survive_a_restart(self):
        w = self.sim
        w.fault_preset({"slow": "50"})
        port = free_port()
        w.v["port"].set(str(port))
        w.start()
        try:
            self.assertIs(w.tcp, w.store)
            self.assertIsNotNone(w.tcp.faults)
            self.assertAlmostEqual(w.tcp.faults.rates["slow"], 0.5)
        finally:
            w.stop()
            w.fault_preset({})
            w.fault_on.set(False)
            w._apply_faults()


if __name__ == "__main__":
    unittest.main(verbosity=0)
