"""Devices > Health test tab and the error-type / response-time columns, driven in-process with the RTU demo
line (no real clicks, no hardware)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import os
import tempfile
import time
import tkinter as tk
import unittest
from tkinter import messagebox


class HealthUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        cls.dir = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS, messagebox.askyesnocancel)
        MB.SETTINGS_FILE = os.path.join(cls.dir, "settings.json")
        MB.HOME_SETTINGS = os.path.join(cls.dir, "home_settings.json")
        messagebox.askyesnocancel = lambda *a, **k: True  # RTU demo line
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = MB.MasterApp(cls.root)
        cls.win = cls.app.open_devices()
        cls.win.b_demo.invoke()
        cls.names = sorted(cls.app.demo_names)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.stop_demo()
            cls.app.stop_all_devices()
            cls.win.close()
            cls.root.destroy()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS, messagebox.askyesnocancel = cls.saved

    def wait(self, cond, sec=30.0):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_1_columns_show_response_time(self):
        app, win = self.app, self.win
        self.assertTrue(self.wait(lambda: all(app.dev_status[n].quality.n >= 2 for n in self.names)),
                        {n: app.dev_status[n].quality.summary() for n in self.names})
        win.refresh_states()
        vals = win.tree.item(self.names[0], "values")
        cols = [c[0] for c in win.COLS]
        self.assertIn("/", vals[cols.index("resp")])
        tab = win.tabs[self.names[0]]
        tab.refresh()
        self.assertTrue(tab.l["quality"].cget("text").startswith(("good", "fair", "poor", "bad")))
        self.assertIn("avg", tab.l["resp"].cget("text"))
        tab.reset_counters()
        self.assertEqual(app.dev_status[self.names[0]].quality.requests, 0)
        self.assertEqual(tab.l["quality"].cget("text"), "-")

    def test_1b_summary_is_not_cut_off(self):
        """The '5 devices: 5 ok, ...' summary gets the width it needs (it used to be squeezed out of the toolbar)."""
        win = self.win
        win.nb.select(win.overview)
        win.refresh_states()
        self.wait(lambda: False, 0.5)
        lab = win.l_sum
        self.assertTrue(lab.cget("text").startswith("5 devices"), lab.cget("text"))
        self.assertTrue(lab.winfo_ismapped())
        self.assertGreaterEqual(lab.winfo_width(), lab.winfo_reqwidth(), "summary text is cut off")

    def test_2_health_tab_runs_pauses_and_resumes(self):
        app, win = self.app, self.win
        ht = win.health_tab
        ht.reload()
        self.assertTrue(ht.bus.get(), ht.cb_bus.cget("values"))
        running_before = [n for n in self.names if app.devpoll.running(n)]
        self.assertEqual(len(running_before), 5)
        ht.samples.set("4")
        ht.sweep.set(True)
        th = ht.run()
        self.assertIsNotNone(th)
        self.assertFalse(any(app.devpoll.running(n) for n in self.names))  # paused during the test
        self.assertTrue(self.wait(lambda: ht.thread is None, 90), ht.l_prog.cget("text"))
        rep = ht.report
        self.assertIsNotNone(rep, ht.l_prog.cget("text"))
        self.assertEqual(len(rep["devices"]), 5)
        self.assertTrue(all(d["ok"] == 4 for d in rep["devices"]), [(d["name"], d["kinds"]) for d in rep["devices"]])
        self.assertTrue(rep["sweep"])
        self.assertIsNotNone(rep["recommended_timeout_ms"])
        self.assertEqual(len(ht.tree.get_children()), 5)
        self.assertTrue(all(app.devpoll.running(n) for n in self.names))  # resumed
        text = ht.copy_report()
        self.assertIn("Bus health test", text)
        ht.use_delays()
        calc = app.cfg["devices"]["calc"]["dev"]
        self.assertTrue(all(calc[n]["delay_ms"] for n in self.names))
        msg = ht.apply()
        self.assertIn("timeout", msg)
        to = str(int(rep["recommended_timeout_ms"]))
        self.assertTrue(all(app.dev_by_name(n)["conn"]["timeout"] == to for n in self.names))
        self.assertTrue(self.wait(lambda: all(app.devpoll.running(n) for n in self.names), 5))

    def test_3_stop_resumes_polling(self):
        app, ht = self.app, self.win.health_tab
        ht.samples.set("200")
        ht.run()
        self.wait(lambda: False, 0.5)
        ht.stop()
        self.assertTrue(self.wait(lambda: ht.thread is None, 30))
        self.assertTrue(ht.report["aborted"])
        self.assertTrue(all(app.devpoll.running(n) for n in self.names))


if __name__ == "__main__":
    unittest.main(verbosity=0)
