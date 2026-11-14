"""Check for updates: version compare, GitHub answer parsing, error handling, and the GUI flow (manual check,
automatic once-a-day check, skip a version). No network: the GitHub answer is faked."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import io
import json
import os
import tempfile
import time
import tkinter as tk
import unittest
import urllib.error
from tkinter import messagebox

import updates

RELEASE = {"tag_name": "v9.9.0", "html_url": "https://example.invalid/rel", "published_at": "2027-01-02T03:04:05Z",
           "body": "What is new", "assets": [
               {"name": "mbtool-setup.exe", "browser_download_url": "https://example.invalid/setup.exe"},
               {"name": "mbtool-portable.zip", "browser_download_url": "https://example.invalid/portable.zip"}]}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Logic(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(updates.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updates.parse_version("1.10"), (1, 10, 0))
        self.assertIsNone(updates.parse_version("latest"))
        self.assertTrue(updates.is_newer("v1.10.0", "1.9.9"))
        self.assertTrue(updates.is_newer("1.0.3", "1.0.2"))
        self.assertFalse(updates.is_newer("1.0.2", "1.0.2"))
        self.assertFalse(updates.is_newer("1.0.1", "1.0.2"))
        self.assertFalse(updates.is_newer("garbage", "1.0.2"))

    def test_check_parses_release(self):
        r = updates.check(current="1.0.2", fetch=lambda: RELEASE)
        self.assertTrue(r["newer"])
        self.assertEqual((r["latest"], r["published"]), ("9.9.0", "2027-01-02"))
        self.assertEqual(r["setup_url"], "https://example.invalid/setup.exe")
        self.assertEqual(r["portable_url"], "https://example.invalid/portable.zip")
        self.assertFalse(updates.check(current="9.9.0", fetch=lambda: RELEASE)["newer"])

    def test_fetch_sends_only_version_and_maps_errors(self):
        seen = {}

        def opener(req, timeout):
            seen["ua"], seen["url"], seen["timeout"] = req.get_header("User-agent"), req.full_url, timeout
            return _Resp(json.dumps(RELEASE).encode())
        data = updates.fetch_latest(opener=opener)
        self.assertEqual(data["tag_name"], "v9.9.0")
        self.assertTrue(seen["ua"].startswith("mbtool/"))
        self.assertEqual(seen["url"], updates.API_URL)

        def offline(req, timeout):
            raise urllib.error.URLError("no route")
        with self.assertRaises(updates.UpdateError) as cm:
            updates.fetch_latest(opener=offline)
        self.assertIn("no connection", str(cm.exception))

        def http404(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        with self.assertRaises(updates.UpdateError):
            updates.fetch_latest(opener=http404)
        with self.assertRaises(updates.UpdateError):
            updates.fetch_latest(opener=lambda req, timeout: _Resp(b"<html>"))
        with self.assertRaises(updates.UpdateError):
            updates.fetch_latest(opener=lambda req, timeout: _Resp(b"{}"))


class GuiFlow(unittest.TestCase):
    """One Tk root and one MasterApp for the class (like the other GUI tests); each test resets the update state."""

    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        import webbrowser
        cls.MB = MB
        d = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS, messagebox.showinfo, messagebox.showwarning,
                     updates.fetch_latest, webbrowser.open)
        MB.SETTINGS_FILE, MB.HOME_SETTINGS = os.path.join(d, "s.json"), os.path.join(d, "h.json")
        messagebox.showinfo = lambda *a, **k: cls.inst.msgs.append(("info", a))
        messagebox.showwarning = lambda *a, **k: cls.inst.msgs.append(("warn", a))

        def fake_fetch(*a, **k):
            cls.inst.calls += 1
            if cls.inst.fail:
                raise updates.UpdateError(cls.inst.fail)
            return cls.inst.release
        updates.fetch_latest = fake_fetch
        webbrowser.open = lambda url, *a, **k: cls.inst.opened.append(url)
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = MB.MasterApp(cls.root)

    @classmethod
    def tearDownClass(cls):
        import webbrowser
        try:
            cls.root.destroy()
        finally:
            (cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS, messagebox.showinfo, messagebox.showwarning,
             updates.fetch_latest, webbrowser.open) = cls.saved

    def setUp(self):
        type(self).inst = self
        self.msgs, self.opened = [], []
        self.release = dict(RELEASE)
        self.fail = None
        self.calls = 0
        self.app.cfg["updates"] = {"auto": True, "last_check": 0.0, "skip": ""}
        self.app.upd_auto_var.set(True)
        self.app.update_dlg = None
        self.pump(lambda: False, 0.1)  # drain results of the previous test

    def pump(self, cond, sec=5):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_manual_newer_download_and_skip(self):
        app = self.app
        app.check_updates(manual=True)
        self.assertTrue(self.pump(lambda: getattr(app, "update_dlg", None) is not None))
        dlg = app.update_dlg
        self.assertIn("9.9.0", dlg.w.title() + " ".join(str(c.cget("text")) for c in dlg.body.winfo_children()
                                                         if isinstance(c, tk.ttk.Label)))
        self.assertEqual(dlg.download(), "https://example.invalid/setup.exe")
        self.assertEqual(self.opened, ["https://example.invalid/setup.exe"])
        self.assertGreater(app.cfg["updates"]["last_check"], 0)
        # skip this version: the automatic check stays quiet for it, a manual check still shows it
        app.update_dlg = None
        app.check_updates(manual=True)
        self.assertTrue(self.pump(lambda: app.update_dlg is not None))
        app.update_dlg.skip()
        self.assertEqual(app.cfg["updates"]["skip"], "9.9.0")
        app.update_dlg = None
        app.cfg["updates"]["last_check"] = 0.0
        self.assertTrue(app.auto_check_updates())
        self.pump(lambda: False, 0.6)
        self.assertIsNone(app.update_dlg)
        with open(self.MB.SETTINGS_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["updates"]["skip"], "9.9.0")

    def test_up_to_date_and_errors(self):
        app = self.app
        self.release = dict(RELEASE, tag_name="v" + updates.appinfo.VERSION)
        app.check_updates(manual=True)
        self.assertTrue(self.pump(lambda: self.msgs))
        self.assertEqual(self.msgs[0][0], "info")
        self.assertIn("up to date", self.msgs[0][1][1])
        # automatic check: silent on errors, no dialog
        self.msgs.clear()
        self.fail = "no connection to GitHub (offline)"
        app.cfg["updates"]["last_check"] = 0.0
        self.assertTrue(app.auto_check_updates())
        self.pump(lambda: False, 0.6)
        self.assertEqual(self.msgs, [])
        # manual check: the error is shown
        app.check_updates(manual=True)
        self.assertTrue(self.pump(lambda: self.msgs))
        self.assertEqual(self.msgs[0][0], "warn")

    def test_auto_check_once_a_day_and_switch(self):
        app = self.app
        app.cfg["updates"]["last_check"] = time.time()
        self.assertFalse(app.auto_check_updates())
        self.assertFalse(app.auto_check_updates(now=time.time() + 3600))
        self.assertTrue(app.auto_check_updates(now=time.time() + 25 * 3600))
        app.set_update_auto(False)
        self.assertFalse(app.upd_auto_var.get())
        app.cfg["updates"]["last_check"] = 0.0
        self.assertFalse(app.auto_check_updates())
        self.pump(lambda: False, 0.4)


if __name__ == "__main__":
    unittest.main(verbosity=0)
