"""Commissioning report: HTML render (English / Thai, escaping, verdict), PDF through headless Edge, and the
File > Commissioning report dialog driven in-process with the demo devices."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import tempfile
import time
import tkinter as tk
import unittest
from tkinter import messagebox

import commission


def sample(bad=False):
    q = {"requests": 100, "ok": 99 if bad else 100, "errors": 1 if bad else 0, "error_pct": 1.0 if bad else 0.0,
         "kinds": {"crc": 1} if bad else {}, "avg": 12.3, "p95": 15.0, "max": 20.0, "min": 10.0, "since": time.time()}
    return {"project": "Plant <A> & Co", "site": "Line 3", "customer": "ACME", "engineer": "Tester", "notes": "n1\nn2",
            "generated": time.time(), "version": "1.0.0",
            "settings": {"scan_mode": "cycle", "gap_ms": "20", "target_ms": "500"},
            "buses": [{"label": "COM3", "conn": "RTU COM3 19200 8E1", "devices": ["Meter <1>"],
                       "cycle": {"cycles": 10, "avg": 50.0, "min": 45.0, "max": 60.0, "jitter": 15.0, "overruns": 0,
                                 "busy_pct": 40.0}, "estimate_ms": 48.0}],
            "devices": [{"name": "Meter <1>", "unit": "1", "conn": "RTU COM3", "what": "x.json", "state": "ok",
                         "enabled": True, "ok": 100, "errors": 1 if bad else 0, "quality": q, "last_t": time.time(),
                         "last_error": "CRC error" if bad else None, "values": [["Voltage", "230.1 V"]]}],
            "health": None, "busmap": None}


class Render(unittest.TestCase):
    def test_english_escapes_and_passes(self):
        h = commission.render(sample(), "en")
        self.assertIn("Modbus commissioning report", h)
        self.assertIn("Plant &lt;A&gt; &amp; Co", h)
        self.assertNotIn("<A>", h)
        self.assertIn("Meter &lt;1&gt;", h)
        self.assertIn("PASS", h)
        self.assertIn("230.1 V", h)
        self.assertIn("50.0 / 45.0 / 60.0", h)
        self.assertIn("No health test", h)

    def test_thai_and_check_verdict(self):
        h = commission.render(sample(bad=True), "th")
        self.assertIn("รายงานส่งมอบงานระบบ Modbus", h)
        self.assertIn("ต้องตรวจสอบ", h)
        self.assertIn("CRC / LRC 1", h)
        self.assertIn('lang="th"', h)

    def test_health_and_busmap_sections(self):
        import busmap
        d = sample()
        m = busmap.default_map([("Meter <1>", 1), ("Drive", 2)])
        d["busmap"] = {"map": m, "analysis": busmap.analyze(m)}
        d["health"] = {"kind": "rtu", "baud": 19200, "samples": 20, "started": time.time(),
                       "recommended_timeout_ms": 150, "recommended_gap_ms": 5.0, "expected_cycle_ms": 80.0,
                       "advice": ["All good <ok>"],
                       "devices": [{"name": "Meter <1>", "unit": 1, "grade": "good", "ok": 20, "errors": 0, "kinds": {},
                                    "rtt": {"avg": 20.0, "p95": 22.0, "max": 25.0}, "turnaround_ms": 12.0,
                                    "timeout_ms": 100}]}
        h = commission.render(d, "en")
        self.assertIn("<svg", h)
        self.assertIn("Drive", h)
        self.assertIn("Timeout: 150 ms", h)
        self.assertIn("All good &lt;ok&gt;", h)
        self.assertIn("Recommended max. baud", h)

    @unittest.skipUnless(commission.find_edge(), "Microsoft Edge not installed")
    def test_pdf(self):
        d = tempfile.mkdtemp()
        html_path, pdf_path = commission.save(sample(), os.path.join(d, "r.html"), "th", pdf=True)
        self.assertTrue(os.path.isfile(html_path))
        with open(pdf_path, "rb") as f:
            self.assertEqual(f.read(5), b"%PDF-")


class Dialog(unittest.TestCase):
    def test_dialog_writes_report_of_demo_devices(self):
        import mbtool as MB
        d = tempfile.mkdtemp()
        saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS, messagebox.askyesnocancel)
        MB.SETTINGS_FILE, MB.HOME_SETTINGS = os.path.join(d, "s.json"), os.path.join(d, "h.json")
        messagebox.askyesnocancel = lambda *a, **k: False  # TCP demo
        root = tk.Tk()
        root.withdraw()
        try:
            app = MB.MasterApp(root)
            win = app.open_devices()
            win.b_demo.invoke()
            names = sorted(app.demo_names)
            end = time.time() + 20
            while time.time() < end and not all(app.dev_status[n].ok >= 2 for n in names):
                root.update()
                time.sleep(0.02)
            dlg = app.open_report()
            dlg.v["project"].set("Demo plant")
            dlg.pdf.set(False)
            html_path, pdf_path, err = dlg.write(os.path.join(d, "rep.html"))
            self.assertIsNone(pdf_path)
            with open(html_path, encoding="utf-8") as f:
                h = f.read()
            self.assertIn("Demo plant", h)
            for n in names:
                self.assertIn(n, h)
            data = app.commission_data()
            self.assertEqual(len(data["devices"]), 5)
            self.assertTrue(all(x["values"] for x in data["devices"]))
            dlg._remember(dlg.form())
            self.assertEqual(app.cfg["report"]["project"], "Demo plant")
            dlg.close()
            app.stop_demo()
            app.stop_all_devices()
            win.close()
        finally:
            root.destroy()
            MB.SETTINGS_FILE, MB.HOME_SETTINGS, messagebox.askyesnocancel = saved


if __name__ == "__main__":
    unittest.main(verbosity=0)
