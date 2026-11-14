"""Devices window (F11): one tab per device and Scan & add, driven in-process against a SimSlave that answers every
Slave ID (SimSlave(unit=None)). Scans a range, ticks only some rows, checks tabs appear / update / disappear,
a rescan never duplicates, workspace round trip and that closing the window leaves no after() jobs behind."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import json
import os
import tempfile
import time
import tkinter as tk
import unittest

import monitor as mon
from modbus_core import SimSlave

PRESET = {"name": "Test boiler", "manufacturer": "Test", "model": "TB-1", "verified": False,
          "points": [{"name": "Temperature", "address": 0, "fc": 3, "type": "uint16", "scale": 0.1, "unit": "°C"},
                     {"name": "Mode", "address": 1, "fc": 3, "type": "uint16", "enum": {"0": "Off", "1": "Heating"}},
                     {"name": "Missing", "address": 300, "fc": 4, "type": "uint16"}]}


class DevTabs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        cls.dir = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE = os.path.join(cls.dir, "settings.json")  # never touch the user's settings
        MB.HOME_SETTINGS = os.path.join(cls.dir, "home_settings.json")
        cls.preset = os.path.join(cls.dir, "boiler.json")
        with open(cls.preset, "w", encoding="utf-8") as f:
            json.dump(PRESET, f)
        cls.slave = SimSlave(unit=None)
        cls.slave.holding[0:2] = [123, 1]
        for i in range(2, 10):
            cls.slave.holding[i] = 100 + i
        cls.slave.input[300] = 0  # FC04 300 is readable on the sim; the preset still decodes it
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = MB.MasterApp(cls.root)
        cls.app.cfg["conn"].update(mode="TCP", host="127.0.0.1", port=str(cls.slave.port), timeout="1000")
        cls.app.cfg["devices"]["gap_ms"] = "0"
        cls.app.devpoll.gap_s = 0.0

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.stop_all_devices()
            cls.root.destroy()
            cls.slave.stop()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS = cls.saved

    def wait(self, cond, sec=8.0):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def win(self):
        return self.app.open_devices()

    def tab_names(self, win):
        return [win.nb.tab(t, "text").strip() for t in win.nb.tabs()]

    def scan(self, win, lo="1", hi="6", auto=False):
        dlg = win.scan_add()
        dlg.v["from"].set(lo)
        dlg.v["to"].set(hi)
        dlg.v["timeout"].set("300")
        dlg.auto.set(auto)
        self.assertTrue(dlg.scan())
        self.assertTrue(dlg.scanning)
        self.assertTrue(self.wait(lambda: not dlg.scanning, 20), "scan did not finish")
        return dlg

    def test_1_scan_and_add_ticked_only(self):
        win = self.win()
        self.assertEqual(self.tab_names(win), ["Overview", "Cycle", "Calculator", "Health test"])
        dlg = self.scan(win)
        self.assertNotIn("error", dlg.result)
        self.assertEqual(list(dlg.rows), [str(u) for u in range(1, 7)])  # unit=None: every ID answers
        self.assertTrue(all(not r["tick"] and not r["exists"] for r in dlg.rows.values()))
        self.assertEqual(dlg.tree.set("3", "model"), "-")  # the sim does not answer FC43 / FC17
        self.assertEqual(dlg.tree.set("3", "name"), "Device 3")
        self.assertIn("Found 6", dlg.l_status.cget("text"))
        self.assertIn("disabled", dlg.b_add.state())  # nothing ticked yet
        dlg.toggle(["2"])
        dlg.toggle(["5"])
        dlg.toggle(["4"])
        dlg.toggle(["4"])  # un-tick again
        self.assertEqual(dlg.ticked(), ["2", "5"])
        self.assertEqual(dlg.tree.set("2", "chk"), dlg.ON)
        self.assertTrue(dlg.set_name("5", "Boiler 5"))
        self.assertFalse(dlg.set_name("2", "boiler 5"))  # unique, case-insensitive
        dlg.set_preset(["5"], self.preset)
        self.assertEqual(dlg.rows["5"]["preset"], self.preset)
        added = dlg.add_selected()
        self.assertEqual(added, ["Device 2", "Boiler 5"])
        names = [d["name"] for d in self.app.devices]
        self.assertEqual(names, ["Device 2", "Boiler 5"])
        d2 = self.app.dev_by_name("Device 2")
        self.assertEqual((d2["unit"], d2["preset"], d2["fc"], d2["addr"], d2["qty"]), ("2", "", "3", "0", "10"))
        self.assertEqual(d2["conn"]["port"], str(self.slave.port))
        self.assertEqual(d2["conn"]["timeout"], "1000")  # the device keeps the connection's timeout, not the scan's
        self.assertEqual(self.app.dev_by_name("Boiler 5")["preset"], self.preset)
        # tabs appear for the ticked rows only, in device order
        self.assertEqual(self.tab_names(win), ["Overview", "Cycle", "Calculator", "Health test", "Device 2", "Boiler 5"])
        self.assertEqual(dlg.tree.set("2", "note"), "added")
        self.assertEqual(dlg.tree.set("1", "note"), "")
        self.assertEqual(dlg.ticked(), [])
        self.assertEqual(dlg.add_selected(), [])  # nothing left ticked: no duplicate
        self.assertEqual(len(self.app.devices), 2)
        # preset tab = point table, plain device = grid
        self.assertEqual(win.tabs["Boiler 5"].kind, "preset")
        self.assertEqual(win.tabs["Device 2"].kind, "grid")
        self.assertEqual(win.tabs["Device 2"].grid.labels[:2], ["0", "1"])
        dlg.close()

    def test_2_tabs_show_full_data(self):
        win = self.win()
        errors = win.start_all()
        self.assertEqual(errors, {})
        tb, td = win.tabs["Boiler 5"], win.tabs["Device 2"]
        self.assertTrue(self.wait(lambda: tb.ptree.set("Temperature", "value") == "12.3"), tb.ptree.item("Temperature"))
        self.assertEqual(tb.ptree.set("Temperature", "unit"), "°C")
        self.assertTrue(tb.ptree.set("Temperature", "raw").startswith("123"))
        self.assertEqual(tb.ptree.set("Temperature", "status"), "OK")
        self.assertEqual(tb.ptree.set("Mode", "value"), "Heating")  # enum name from the poll's decode
        self.assertEqual(tb.ptree.set("Missing", "status"), "OK")
        # a point that failed in the poll is a red row with the reason (fed like a real partial result)
        st = mon.DeviceStatus("x")
        st.start(1, 1)
        st.on_result(time.time(), {"ok": True, "values": {"Temperature": 1.0, "Mode": 0, "Missing": None},
                                   "texts": {"Temperature": "1.0 °C", "Mode": "Off", "Missing": "ERROR"},
                                   "error": "exception 02", "raws": {"Temperature": 10, "Mode": 0},
                                   "point_errors": {"Missing": "exception 02 illegal data address"}})
        tb.fill(st, "ok")
        self.assertEqual(tb.ptree.set("Missing", "status"), "ERROR: exception 02 illegal data address")
        self.assertIn("error", tb.ptree.item("Missing", "tags"))
        self.assertEqual(tb.ptree.set("Mode", "value"), "Off")
        ci = td.grid.cols.index("Unsigned")
        self.assertTrue(self.wait(lambda: td.grid.values[2][ci] == "102"))
        self.assertEqual(td.grid.values[0][ci], "123")
        self.wait(lambda: tb.state == "ok", 3)
        self.assertEqual(str(win.nb.tab(tb.frame, "image")[0]), str(win.dots["ok"]))
        self.assertEqual(tb.l["unit"].cget("text"), "5")
        self.assertIn("TCP 127.0.0.1", tb.l["conn"].cget("text"))
        self.assertEqual(tb.b_run.cget("text"), "Stop")
        # the tab's own Start / Stop
        tb.toggle()
        self.assertFalse(self.app.devpoll.running("Boiler 5"))
        self.assertTrue(self.app.devpoll.running("Device 2"))
        self.wait(lambda: tb.state == "off", 2)
        self.assertEqual(str(win.nb.tab(tb.frame, "image")[0]), str(win.dots["off"]))
        self.assertEqual(tb.b_run.cget("text"), "Start")
        self.assertIn("old value", tb.ptree.set("Temperature", "status"))
        # a failing point shows as an error row: make a device whose preset points do not answer
        self.slave.holding[0] = 456
        tb.toggle()
        self.assertTrue(self.wait(lambda: tb.ptree.set("Temperature", "value") == "45.6"))
        # error state: point the device at a dead port
        self.app.stop_all_devices()
        self.wait(lambda: td.state == "off", 2)
        self.assertEqual(str(win.nb.tab(td.frame, "image")[0]), str(win.dots["off"]))

    def test_3_rescan_does_not_duplicate(self):
        win = self.win()
        dlg = self.scan(win, "1", "6", auto=True)
        self.assertEqual(dlg.tree.set("2", "note"), "already added")
        self.assertEqual(dlg.tree.set("5", "note"), "already added")
        self.assertEqual(dlg.tree.set("5", "chk"), dlg.NA)
        self.assertEqual(sorted(dlg.ticked(), key=int), ["1", "3", "4", "6"])  # auto tick skips existing ones
        dlg.toggle(["2"])  # cannot be ticked
        self.assertNotIn("2", dlg.ticked())
        dlg.set_all(False)
        dlg.toggle(["3"])
        self.assertEqual(dlg.add_selected(), ["Device 3"])
        dlg.set_all(True)
        dlg.toggle(["1", "4", "6"], False)
        self.assertEqual(dlg.ticked(), [])
        self.assertEqual(dlg.add_selected(), [])
        names = [d["name"] for d in self.app.devices]
        self.assertEqual(names, ["Device 2", "Boiler 5", "Device 3"])
        self.assertEqual(len({(d["unit"]) for d in self.app.devices}), 3)
        self.assertEqual(self.tab_names(win)[win.FIXED_TABS:], names)
        dlg.close()
        # a third scan with auto tick: the three added ones are marked, never ticked
        dlg = self.scan(win, "2", "3", auto=True)
        self.assertEqual(dlg.ticked(), [])
        self.assertEqual({dlg.tree.set(i, "note") for i in dlg.rows}, {"already added"})
        dlg.close()

    def test_4_scan_errors_are_status_text(self):
        win = self.win()
        dlg = win.scan_add()
        dlg.v["from"].set("9")
        dlg.v["to"].set("3")
        self.assertFalse(dlg.scan())
        self.assertIn("must be", dlg.hints["to"].cget("text"))
        dlg.v["to"].set("10")
        # nothing listens on this port: a clear status, no crash
        dead = SimSlave()
        port = dead.port
        dead.stop()
        dlg.conn = mon.norm_conn(dict(dlg.conn, port=str(port)))
        self.assertTrue(dlg.scan())
        self.assertTrue(self.wait(lambda: not dlg.scanning, 10))
        self.assertIn("Scan failed", dlg.l_status.cget("text"))
        self.assertIn("cannot open", dlg.l_status.cget("text"))
        # a slave that answers only ID 7: "No device" for another range
        one = SimSlave(unit=7)
        try:
            dlg.conn = mon.norm_conn(dict(dlg.conn, port=str(one.port)))
            dlg.v["from"].set("1")
            dlg.v["to"].set("2")
            dlg.v["timeout"].set("100")
            self.assertTrue(dlg.scan())
            self.assertTrue(self.wait(lambda: not dlg.scanning, 10))
            self.assertIn("No device answered", dlg.l_status.cget("text"))
            self.assertEqual(dlg.rows, {})
            # cancel a running scan
            dlg.v["to"].set("40")
            self.assertTrue(dlg.scan())
            self.root.update()
            dlg.cancel()
            self.assertTrue(self.wait(lambda: not dlg.scanning, 10))
            self.assertIn("Cancelled", dlg.l_status.cget("text"))
        finally:
            one.stop()
            dlg.close()

    def test_5_remove_rename_and_workspace(self):
        win = self.win()
        t3 = win.tabs["Device 3"]
        win.remove("Device 3")
        self.assertNotIn("Device 3", win.tabs)
        self.assertFalse(t3.frame.winfo_exists())
        self.assertEqual(self.tab_names(win)[win.FIXED_TABS:], ["Device 2", "Boiler 5"])
        # rename through the edit path keeps the tab object, retitles it
        t2 = win.tabs["Device 2"]
        dv = json.loads(json.dumps(self.app.dev_by_name("Device 2")))
        dv["name"] = "Meter 2"
        dv["qty"] = "4"
        win._saved("Device 2", dv)
        self.assertIs(win.tabs["Meter 2"], t2)
        self.assertEqual(self.tab_names(win)[win.FIXED_TABS:], ["Meter 2", "Boiler 5"])
        self.assertEqual(len(t2.grid.labels), 4)  # the body was rebuilt for the new range
        # workspace round trip restores the tabs, also after the window was closed
        ws = self.app.gather_workspace()
        self.assertEqual([d["name"] for d in ws["devices"]], ["Meter 2", "Boiler 5"])
        self.app.devices = []
        win.reload()
        self.assertEqual(self.tab_names(win), ["Overview", "Cycle", "Calculator", "Health test"])
        win.close()
        ws["windows"]["devices"] = {"geometry": "", "open": True}
        self.app.apply_workspace(ws)
        win = self.app.devices_win
        self.assertTrue(win.alive())
        self.assertEqual(self.tab_names(win)[win.FIXED_TABS:], ["Meter 2", "Boiler 5"])

    def test_6_close_reopen_leaves_no_after_jobs(self):
        def jobs():
            self.root.update()
            return len(self.root.tk.call("after", "info"))
        win = self.win()
        win.start_all()
        self.wait(lambda: win.tabs["Boiler 5"].state == "ok", 4)
        win.close()
        self.app.stop_all_devices()
        time.sleep(0.4)  # let flash jobs of the grids expire
        base = jobs()
        for _ in range(3):
            w = self.app.open_devices()
            w.start_all()
            self.wait(lambda: w.tabs["Meter 2"].grid.values[0][0] != "", 4)
            w.nb.select(w.tabs["Meter 2"].frame)
            self.root.update()
            w.nb.select(w.cycle_tab.frame)
            self.root.update()
            w.close()
            self.app.stop_all_devices()
        time.sleep(0.4)
        self.assertLessEqual(jobs(), base, "closing the Devices window left after() jobs behind")
        self.app.open_devices()


if __name__ == "__main__":
    unittest.main(verbosity=2)
