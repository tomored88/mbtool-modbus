"""Devices > Demo devices: simulated devices appear as tabs without any hardware, values move, nothing is saved,
stopping removes them. Driven in-process (no real clicks)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import os
import tempfile
import time
import tkinter as tk
import unittest
from tkinter import messagebox


class DemoDevices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        cls.dir = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS, messagebox.askyesnocancel)
        MB.SETTINGS_FILE = os.path.join(cls.dir, "settings.json")
        MB.HOME_SETTINGS = os.path.join(cls.dir, "home_settings.json")
        cls.answer = True
        messagebox.askyesnocancel = lambda *a, **k: cls.answer
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = MB.MasterApp(cls.root)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.stop_demo()
            cls.app.stop_all_devices()
            cls.root.destroy()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS, messagebox.askyesnocancel = cls.saved

    def wait(self, cond, sec=15.0):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def tab_names(self, win):
        return [win.nb.tab(t, "text").strip().lstrip("● ").strip() for t in win.nb.tabs()]

    def run_demo(self, rtu):
        type(self).answer = rtu
        win = self.app.open_devices()
        win.b_demo.invoke()
        names = sorted(self.app.demo_names)
        self.assertEqual(len(names), 5, win.l_msg.cget("text"))
        self.assertEqual(win.b_demo.cget("text"), "Stop demo")
        tabs = self.tab_names(win)
        for n in names:
            self.assertIn(n, tabs)
        self.assertTrue(self.wait(lambda: all(self.app.dev_status[n].ok >= 2 for n in names), 30),
                        {n: (self.app.dev_status[n].ok, self.app.dev_status[n].errors,
                             self.app.dev_status[n].last_error) for n in names})
        # at least one device shows a value that changes over time
        n0 = "Meter 1"
        first = dict(self.app.dev_status[n0].values)
        self.assertTrue(self.wait(lambda: self.app.dev_status[n0].values != first, 20), "values did not move")
        # never persisted
        self.app.save_devices()
        self.assertEqual([d for d in self.app.cfg["devices"]["list"] if d["name"] in names], [])
        win.b_demo.invoke()
        self.assertIsNone(self.app.demo)
        self.assertEqual(win.b_demo.cget("text"), "Demo devices")
        self.assertEqual([d for d in self.app.devices if d["name"] in names], [])
        self.assertEqual(set(self.tab_names(win)), {"Overview", "Cycle", "Calculator", "Health test"})
        win.close()

    def test_1_rtu_line(self):
        self.run_demo(True)

    def test_2_tcp_devices(self):
        self.run_demo(False)

    def test_3_cancel_does_nothing_and_names_do_not_clash(self):
        type(self).answer = None
        win = self.app.open_devices()
        win.b_demo.invoke()
        self.assertIsNone(self.app.demo)
        self.app.devices.append(self.MB.mon.norm_device({"name": "Meter 1", "unit": "9"}))
        names = self.app.start_demo("tcp")
        try:
            self.assertIn("Meter 1 (demo)", names)
            self.assertEqual(len({d["name"].lower() for d in self.app.devices}), len(self.app.devices))
        finally:
            self.app.stop_demo()
            self.app.devices = [d for d in self.app.devices if d["name"] != "Meter 1"]
        win.close()

    def test_4_closing_the_app_stops_the_demo(self):
        self.app.start_demo("tcp")
        d = self.app.demo
        self.app.stop_demo()
        self.assertFalse(d.running)
        self.assertEqual(self.app.demo_names, set())


if __name__ == "__main__":
    unittest.main(verbosity=0)
