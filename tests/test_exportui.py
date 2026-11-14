"""Tools > Data export window: demo devices -> local MQTT broker stand-in + SQLite, password never saved, stop on
close. Driven in-process (no clicks)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import json
import os
import sqlite3
import tempfile
import time
import tkinter as tk
import unittest
from tkinter import messagebox

from test_export import FakeBroker


class ExportUI(unittest.TestCase):
    def test_demo_devices_to_mqtt_and_sqlite(self):
        import mbtool as MB
        d = tempfile.mkdtemp()
        saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS, messagebox.askyesnocancel)
        MB.SETTINGS_FILE, MB.HOME_SETTINGS = os.path.join(d, "s.json"), os.path.join(d, "h.json")
        messagebox.askyesnocancel = lambda *a, **k: False  # TCP demo devices
        broker = FakeBroker()
        root = tk.Tk()
        root.withdraw()

        def pump(cond, sec=20):
            end = time.time() + sec
            while time.time() < end:
                root.update()
                if cond():
                    return True
                time.sleep(0.02)
            return False
        try:
            app = MB.MasterApp(root)
            dw = app.open_devices()
            dw.b_demo.invoke()
            names = sorted(app.demo_names)
            w = app.open_export()
            w.mq_on.set(True)
            w.v["host"].set("127.0.0.1")
            w.v["port"].set(str(broker.port))
            w.v["prefix"].set("site/a")
            w.v["user"].set("op")
            w.pw.set("secret-pw")
            w.sq_on.set(True)
            db = os.path.join(d, "data.db")
            w.v["path"].set(db)
            self.assertIsNone(w.test())
            self.assertIsNone(w.toggle())
            self.assertIsNotNone(app.exporter)
            self.assertTrue(pump(lambda: {t for t, _p, _r in broker.msgs} >= {f"site/a/{n}" for n in names}),
                            ({t for t, _p, _r in broker.msgs}, w.l_err.cget("text")))
            msg = next(json.loads(p) for t, p, _r in broker.msgs if t == f"site/a/{names[0]}")
            self.assertTrue(msg["ok"])
            self.assertTrue(msg["values"])
            self.assertTrue(pump(lambda: app.exporter.stats["sql_rows"] > 10))
            pump(lambda: False, 1.2)
            self.assertIn("Exporting", w.l_state.cget("text"))
            with open(MB.SETTINGS_FILE, encoding="utf-8") as f:
                txt = f.read()
            self.assertNotIn("secret-pw", txt)  # password never written
            self.assertEqual(json.loads(txt)["export"]["mqtt"]["host"], "127.0.0.1")
            w.toggle()  # stop
            self.assertIsNone(app.exporter)
            con = sqlite3.connect(db)
            devs = {r[0] for r in con.execute("SELECT DISTINCT device FROM readings")}
            con.close()
            self.assertEqual(devs, set(names))
            w.toggle()  # start again, then closing the program stops it
            self.assertIsNotNone(app.exporter)
            ex = app.exporter
            w.close()
            app.close()
            self.assertIsNone(app.exporter)
            self.assertFalse(ex.is_alive())
        finally:
            broker.close()
            try:
                root.destroy()
            except tk.TclError:
                pass
            MB.SETTINGS_FILE, MB.HOME_SETTINGS, messagebox.askyesnocancel = saved


if __name__ == "__main__":
    unittest.main(verbosity=0)
