"""Poll tabs of the main window, driven in-process against SimSlaves: the first tab stays the classic poll, extra tabs
(devices with tab=True, polled by the Devices engine) poll at the same time with their own definition, write through
their own link, can be renamed / duplicated / closed, follow the first tab's connection or use their own, show their
state as a dot on the tab strip, survive a settings and a workspace round trip, and leave no after() jobs behind."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import json
import os
import struct
import tempfile
import time
import tkinter as tk
import unittest

import monitor as mon
from modbus_core import SimSlave


def words_f(x):
    return list(struct.unpack(">2H", struct.pack(">f", x)))


class PollTabs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        cls.dir = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE = os.path.join(cls.dir, "settings.json")  # never touch the user's settings
        MB.HOME_SETTINGS = os.path.join(cls.dir, "home_settings.json")
        cls.s1 = SimSlave(unit=None)  # every Slave ID answers
        for i in range(40):
            cls.s1.holding[i] = 100 + i
        cls.s2 = SimSlave(unit=None)  # a second "device" for the custom connection
        for i in range(10):
            cls.s2.holding[i] = 7000 + i
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = app = MB.MasterApp(cls.root)
        app.cfg["conn"].update(mode="TCP", host="127.0.0.1", port=str(cls.s1.port), timeout="1000")
        app.cfg["devices"]["gap_ms"] = "0"
        app.devpoll.gap_s = 0.0
        app.pv["func"].set(MB.FUNCS[3])
        app.set_addr_base(0)
        app.pv["unit"].set("1")
        app.pv["addr"].set("0")
        app.pv["qty"].set("10")
        app.pv["scan"].set("100")
        app.pv["fmt"].set("Unsigned")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.stop_all_devices()
            cls.app.disconnect(quiet=True)
            cls.root.destroy()
            cls.s1.stop()
            cls.s2.stop()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS = cls.saved

    def wait(self, cond, sec=6.0):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def pump(self, sec):
        self.wait(lambda: False, sec)

    def connect(self):
        if self.app.state != "on":
            self.app.connect()
            self.assertTrue(self.wait(lambda: self.app.state == "on"), "main tab not connected")

    def tab(self, name):
        return self.app.poll_tabs[name]

    def set_def(self, t, **kw):
        for k, v in kw.items():
            t.pv[k].set(v)
        t.flush()

    # ------------------------------------------------------------------
    def test_1_first_tab_is_the_classic_poll(self):
        app = self.app
        self.assertEqual(app.poll_tab_keys(), [None])
        self.assertIsNone(app.cur_tab)
        self.assertEqual(app.tabstrip.keys, [None])
        self.assertEqual(app.tab_title(None), "Poll 1")
        # the old widgets live in the first page and keep their names / geometry managers
        self.assertIs(app.grid.frame.master, app.main_page)
        self.assertIs(app.poll_lf.master, app.main_page)
        self.assertEqual(app.grid.frame.winfo_manager(), "pack")
        self.assertEqual(app.pages.select(), str(app.main_page))
        # the first tab cannot be closed
        self.assertFalse(app.close_active_tab())
        self.assertIn("cannot be closed", app.l_msg.cget("text"))
        for seq in ("<Control-t>", "<Control-w>", "<Control-Next>", "<Control-Prior>", "<Control-Tab>"):
            self.assertTrue(self.root.bind(seq), seq)
        self.connect()
        self.assertTrue(self.wait(lambda: app.grid.values[0] == "100"))
        self.assertTrue(self.wait(lambda: app.tabstrip.states.get(None) == "ok"))

    def test_2_tabs_poll_at_the_same_time(self):
        app = self.app
        self.connect()
        app.select_poll_tab(None)
        app.tabstrip.b_add.invoke()  # '+' : copies the first tab's definition, starts because Poll 1 polls
        self.assertEqual(app.poll_tab_keys(), [None, "Poll 2"])
        self.assertEqual(app.cur_tab, "Poll 2")
        t2 = self.tab("Poll 2")
        self.assertEqual(app.pages.select(), str(t2.frame))
        dv = app.dev_by_name("Poll 2")
        self.assertTrue(dv["tab"] and dv["tab_link"])
        self.assertEqual((dv["unit"], dv["fc"], dv["addr"], dv["qty"], dv["scan"]), ("1", "3", "0", "10", "100"))
        self.assertTrue(app.devpoll.running("Poll 2"))
        self.assertTrue(self.wait(lambda: t2.grid.values[0] == "100"))
        # another range on the same link (Slave ID 2 on a sim that answers every ID)
        self.set_def(t2, unit="2", addr="20", qty="5")
        self.assertEqual((dv["unit"], dv["addr"], dv["qty"]), ("2", "20", "5"))
        self.assertTrue(self.wait(lambda: t2.grid.values[:2] == ["120", "121"]), t2.grid.values)
        self.assertEqual(t2.grid.labels[0], "20")
        # Ctrl+T handler: a third tab, own connection to the second simulator, multi-type view
        name = app.new_poll_tab()
        self.assertEqual(name, "Poll 3")
        t3 = self.tab("Poll 3")
        self.assertEqual(app.dev_by_name("Poll 3")["addr"], "20")  # copied from the shown tab (Poll 2)
        dlg = t3.custom_conn()
        dlg.v["port"].set(str(self.s2.port))
        dlg.ok(False)
        d3 = app.dev_by_name("Poll 3")
        self.assertFalse(d3["tab_link"])
        self.assertEqual(d3["conn"]["port"], str(self.s2.port))
        self.assertEqual(t3.conn_var.get(), "Custom connection")
        self.set_def(t3, addr="0", qty="6")
        t3.multi_var.set(True)
        t3._changed()
        t3.flush()
        self.assertTrue(t3.multi_shown)
        self.assertEqual(t3.mgrid.frame.winfo_manager(), "pack")
        ui = t3.mgrid.cols.index("Unsigned")
        self.assertTrue(self.wait(lambda: t3.mgrid.values and t3.mgrid.values[0][ui] == "7000"))
        # all three show their own data at once
        self.assertTrue(self.wait(lambda: app.grid.values[0] == "100" and t2.grid.values[0] == "120"
                                  and t3.mgrid.values[1][ui] == "7001"))
        self.assertTrue(self.wait(lambda: app.tabstrip.states.get("Poll 2") == "ok"
                                  and app.tabstrip.states.get("Poll 3") == "ok"))
        self.assertEqual(t3.lamp.color, self.MB.DOT_COLORS["ok"])
        self.assertIn("Polls OK", t3.l_cnt.cget("text"))
        # the Devices window lists them too (no second polling stack)
        win = app.open_devices()
        self.assertIn("Poll 2", win.tabs)
        self.assertIn("Poll 3", [win.tree.item(i, "values")[1] for i in win.tree.get_children()])
        win.close()

    def test_3_editing_writes_the_right_register(self):
        app = self.app
        t2, t3 = self.tab("Poll 2"), self.tab("Poll 3")
        app.select_poll_tab("Poll 2")
        t2.edit_cell(1)  # address 21
        ent = t2.grid.editor
        self.assertIsNotNone(ent)
        ent.delete(0, "end")
        ent.insert(0, "4321")
        ent.mb_commit()
        self.assertTrue(self.wait(lambda: self.s1.holding[21] == 4321), self.s1.holding[21])
        self.assertTrue(self.wait(lambda: t2.grid.values[1] == "4321"))
        self.assertTrue(self.wait(lambda: t2.l_msg.cget("text").startswith(("Written", "OK"))))
        self.assertEqual(self.s2.holding[1], 7001)  # the other device is untouched
        # bad text: the editor stays open, nothing is written
        t2.edit_cell(2)
        t2.grid.editor.delete(0, "end")
        t2.grid.editor.insert(0, "99999")
        t2.grid.editor.mb_commit()
        self.assertIsNotNone(t2.grid.editor)
        self.assertIn("not a valid", t2.l_msg.cget("text"))
        t2.grid.editor.mb_cancel()
        self.assertEqual(self.s1.holding[22], 122)
        # multi-type cell on the custom connection: Float -> 2 registers on the second simulator
        app.select_poll_tab("Poll 3")
        fi = t3.mgrid.cols.index("Float")
        t3.edit_mcell(2, fi)
        t3.mgrid.editor.delete(0, "end")
        t3.mgrid.editor.insert(0, "1.5")
        t3.mgrid.editor.mb_commit()
        self.assertTrue(self.wait(lambda: self.s2.holding[2:4] == words_f(1.5)), self.s2.holding[2:4])
        self.assertTrue(self.wait(lambda: t3.mgrid.values[2][fi] == "1.5"))
        # F6 write dialog writes through the tab that is shown
        dlg = app.open_write()
        self.assertIs(dlg.t, t3)
        self.assertIn("Poll 3", dlg.w.title())
        dlg.v["func"].set(self.MB.WRITE_FUNCS[6])
        dlg._func()
        dlg.v["unit"].set("1")
        dlg.v["addr"].set("5")
        dlg.v["value"].set("555")
        dlg.send()
        self.assertTrue(self.wait(lambda: self.s2.holding[5] == 555))
        self.assertNotEqual(self.s1.holding[5], 555)
        dlg.close()
        # the inspector follows the shown tab
        t3.mgrid.select(2, fi)
        src, why = app.inspect_source()
        self.assertIsNone(why)
        self.assertEqual((src["addr"], src["default_n"]), (2, 2))
        self.assertEqual(src["words"][:2], words_f(1.5))
        # FC 04 is read-only; coils (FC 01) are written with FC05
        self.set_def(t2, func=self.MB.FUNCS[4])
        t2.edit_cell(0)
        self.assertIsNone(t2.grid.editor)
        self.assertIn("read-only", t2.l_msg.cget("text"))
        self.set_def(t2, func=self.MB.FUNCS[1], addr="0", qty="8")
        self.assertTrue(self.wait(lambda: t2.grid.values[3] in ("0", "1") and t2._data_matches()))
        t2.edit_cell(3)
        t2.grid.editor.delete(0, "end")
        t2.grid.editor.insert(0, "1")
        t2.grid.editor.mb_commit()
        self.assertTrue(self.wait(lambda: bool(self.s1.coils[3])))
        self.set_def(t2, func=self.MB.FUNCS[3], addr="20", qty="5")
        self.assertTrue(self.wait(lambda: t2.grid.values[0] == "120"))
        # a stopped tab does not write
        t2.toggle()
        self.assertFalse(app.devpoll.running("Poll 2"))
        t2.edit_cell(0)
        self.assertIsNone(t2.grid.editor)
        self.assertIn("press Start", t2.l_msg.cget("text"))
        t2.toggle()
        self.assertTrue(app.devpoll.running("Poll 2"))

    def test_4_rename_duplicate_close(self):
        app = self.app
        rules_before = len(app.alarms.rules)
        app.alarms.add_rule(mon.Rule("20", "above", value=5000, device="Poll 2"))
        self.assertIsNone(app.rename_poll_tab("Poll 2", "Boiler"))
        self.assertEqual(app.poll_tab_keys(), [None, "Boiler", "Poll 3"])
        self.assertEqual(app.tabstrip.keys, [None, "Boiler", "Poll 3"])
        self.assertIsNotNone(app.dev_by_name("Boiler"))
        self.assertIsNone(app.dev_by_name("Poll 2"))
        self.assertTrue(app.devpoll.running("Boiler"))
        self.assertEqual(app.alarms.rules[-1].device, "Boiler")
        self.assertEqual(app.rename_poll_tab("Boiler", "poll 3"), "name already used")
        self.assertEqual(app.rename_poll_tab("Boiler", "Poll 1"), "name already used")
        self.assertEqual(app.rename_poll_tab("Boiler", "  "), "enter a name")
        # in-place rename on the strip (double-click title / menu Rename...)
        ent = app.tabstrip.start_rename("Poll 3")
        ent.delete(0, "end")
        ent.insert(0, "Meter")
        ent.mb_commit()
        self.assertIsNone(app.tabstrip.editor)
        self.assertEqual(app.poll_tab_keys(), [None, "Boiler", "Meter"])
        ent = app.tabstrip.start_rename("Meter")
        ent.delete(0, "end")
        ent.insert(0, "Boiler")
        ent.mb_commit()  # refused: stays open, red
        self.assertIs(app.tabstrip.editor, ent)
        self.assertEqual(ent.cget("bg"), "#ffd6d6")
        app.tabstrip.end_rename()
        # the first tab can be renamed too; "same as" follows the new name
        self.assertIsNone(app.rename_poll_tab(None, "Main"))
        self.assertEqual(self.tab("Boiler").conn_var.get(), "Same as Main connection")
        self.assertIsNone(app.rename_poll_tab(None, "Poll 1"))
        # context menu
        m = app.tabstrip.build_menu("Boiler")
        labels = [m.entrycget(i, "label") for i in range(m.index("end") + 1) if m.type(i) == "command"]
        for want in ("Rename...", "Duplicate tab", "Stop polling", "Show in Devices window", "Close tab"):
            self.assertIn(want, labels)
        m0 = app.tabstrip.build_menu(None)
        last = m0.index("end")
        self.assertEqual(m0.entrycget(last, "label"), "Close tab")
        self.assertEqual(str(m0.entrycget(last, "state")), "disabled")
        win = app.show_tab_in_devices("Boiler")
        self.assertEqual(win.nb.select(), str(win.tabs["Boiler"].frame))
        win.close()
        # duplicate: same definition and link, polls at once
        name = app.duplicate_poll_tab("Boiler")
        self.assertEqual(name, "Boiler copy")
        d, src = app.dev_by_name(name), app.dev_by_name("Boiler")
        for k in ("unit", "fc", "addr", "qty", "scan", "fmt", "tab_link", "conn"):
            self.assertEqual(d[k], src[k], k)
        self.assertTrue(self.wait(lambda: self.tab(name).grid.values[0] == "120"))
        # close: polling stops, its device goes, nothing else changes
        others = json.loads(json.dumps([x for x in app.devices if x["name"] != name]))
        self.assertEqual(app.cur_tab, name)
        app.close_active_tab()  # Ctrl+W
        self.assertNotIn(name, app.poll_tabs)
        self.assertFalse(app.devpoll.running(name))
        self.assertIsNone(app.dev_by_name(name))
        self.assertNotIn(name, app.dev_status)
        self.assertEqual(json.loads(json.dumps(app.devices)), others)
        self.assertEqual(app.cur_tab, "Meter")  # the neighbour is shown
        self.assertTrue(app.devpoll.running("Boiler") and app.devpoll.running("Meter"))
        self.assertGreater(len(app.alarms.rules), rules_before)  # rules stay (deleted in Alerts)
        # switching with the keyboard handlers wraps around
        app.select_poll_tab(None)
        app.next_poll_tab(-1)
        self.assertEqual(app.cur_tab, "Meter")
        app.next_poll_tab(1)
        self.assertIsNone(app.cur_tab)

    def test_5_dots_follow_the_status(self):
        app = self.app
        t = self.tab("Meter")
        self.assertTrue(self.wait(lambda: app.tabstrip.states["Meter"] == "ok"))
        t.toggle()
        self.assertEqual(app.tabstrip.states["Meter"], "off")
        self.assertEqual(t.b_run.cget("text"), "Start")
        self.assertTrue(t.grid.stale or t.mgrid.stale)
        # a dead port: red
        dead = SimSlave()
        port = dead.port
        dead.stop()
        t._custom_done(dict(app.dev_by_name("Meter")["conn"], port=str(port)))
        t.toggle()
        self.assertTrue(self.wait(lambda: app.tabstrip.states["Meter"] == "error"), app.tabstrip.states)
        self.assertEqual(t.lamp.color, self.MB.DOT_COLORS["error"])
        self.assertIn("Last error", t.l_last.cget("text"))
        self.assertTrue(t.mgrid.stale)
        # stale: no answer for longer than the stale time
        st = app.dev_status["Meter"]
        st.last_t -= 3600
        t.refresh()
        self.assertEqual(app.tabstrip.states["Meter"], "stale")
        t._custom_done(dict(app.dev_by_name("Meter")["conn"], port=str(self.s2.port)))
        self.assertTrue(self.wait(lambda: app.tabstrip.states["Meter"] == "ok"))
        # the first tab's dot follows its connection
        self.assertEqual(app.tabstrip.states[None], "ok")
        app.disconnect(quiet=True)
        self.assertEqual(app.tabstrip.states[None], "off")
        self.connect()
        self.assertTrue(self.wait(lambda: app.tabstrip.states[None] == "ok"))

    def test_6_same_as_main_follows_and_custom_stays(self):
        app = self.app
        b, m = self.tab("Boiler"), self.tab("Meter")
        self.assertTrue(app.dev_by_name("Boiler")["tab_link"])
        self.assertFalse(app.dev_by_name("Meter")["tab_link"])
        self.assertTrue(self.wait(lambda: b.grid.values[0] == "120"))
        # Setup (F3) of the first tab -> the linked tab follows and restarts; the custom one does not move
        conn = dict(app.cfg["conn"], port=str(self.s2.port))
        app.apply_conn(conn, connect=False)
        self.assertEqual(app.dev_by_name("Boiler")["conn"]["port"], str(self.s2.port))
        self.assertIn(str(self.s2.port), b.l_conn.cget("text"))
        self.assertTrue(self.wait(lambda: b.grid.values[0] == "0"), b.grid.values)  # s2 has 0 at 20
        self.assertEqual(app.dev_by_name("Meter")["conn"]["port"], str(self.s2.port))  # was already s2
        app.apply_conn(dict(conn, port=str(self.s1.port)), connect=False)
        self.assertTrue(self.wait(lambda: b.grid.values[0] == "120"))
        # custom -> same as Poll 1 again
        m.cb_conn.current(0)
        m._conn_picked()
        self.assertTrue(app.dev_by_name("Meter")["tab_link"])
        self.assertEqual(app.dev_by_name("Meter")["conn"]["port"], str(self.s1.port))
        self.assertTrue(self.wait(lambda: m.mgrid.values[0][m.mgrid.cols.index("Unsigned")] == "100"))
        dlg = m.custom_conn()
        dlg.v["port"].set(str(self.s2.port))
        dlg.ok(False)
        self.assertFalse(app.dev_by_name("Meter")["tab_link"])

    def test_6b_preset_in_a_tab(self):
        app = self.app
        path = os.path.join(self.dir, "meter.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"name": "Tab meter", "verified": False,
                       "points": [{"name": "Volt", "address": 0, "fc": 3, "type": "uint16", "scale": 0.1, "unit": "V"},
                                  {"name": "Amp", "address": 1, "fc": 3, "type": "uint16"}]}, f)
        b = self.tab("Boiler")
        self.assertFalse(b.set_preset(os.path.join(self.dir, "missing.json")))
        self.assertIn("not usable", b.l_msg.cget("text"))
        self.assertTrue(b.set_preset(path))
        self.assertEqual(b.kind, "preset")
        self.assertEqual(b.ptf.winfo_manager(), "pack")
        self.assertEqual(b.grid.frame.winfo_manager(), "")
        self.assertEqual(str(b.pw["addr"].cget("state")), "disabled")
        self.assertTrue(self.wait(lambda: b.ptree.set("Volt", "value") == "10.0"), b.ptree.item("Volt"))
        self.assertEqual(b.ptree.set("Volt", "unit"), "V")
        self.assertEqual(b.ptree.set("Amp", "status"), "OK")
        self.assertEqual(b.b_nopreset.winfo_manager(), "pack")
        b.clear_preset()
        self.assertEqual(b.kind, "grid")
        self.assertEqual(b.grid.frame.winfo_manager(), "pack")
        self.assertEqual(b.b_nopreset.winfo_manager(), "")
        self.assertTrue(self.wait(lambda: b.grid.values[0] == "120"))

    def test_7_persistence_settings_and_workspace(self):
        app = self.app
        b = self.tab("Boiler")
        b.base_var.set(self.MB.BASE_CHOICES["plc"])  # like the first tab: the typed address is read in the new base
        self.assertIn("= protocol 19", b.hints["addr"].cget("text"))
        b.pv["addr"].set("40021")
        b.rows_var.set("20")
        b.flush()
        self.assertEqual(app.dev_by_name("Boiler")["addr"], "20")
        self.assertEqual(b.grid.labels[0], "40021")
        app.select_poll_tab("Meter")
        app.rename_poll_tab(None, "Main poll")
        # a demo device never becomes a tab and is never saved
        app.devices.append(mon.norm_device({"name": "Demo X", "tab": True, "unit": "3"}))
        app.demo_names = {"Demo X"}
        app.sync_poll_tabs()
        self.assertNotIn("Demo X", app.poll_tab_keys())
        saved = app._saved_devices()
        self.assertNotIn("Demo X", [d["name"] for d in saved])
        app.devices = [d for d in app.devices if d["name"] != "Demo X"]
        app.demo_names = set()
        # settings round trip: what close() saves, a new window rebuilds (stopped)
        for t in app.poll_tabs.values():
            t.flush()
        app.cfg["devices"]["list"] = app._saved_devices()
        app.cfg["tabs"]["active"] = app.active_tab_index()
        self.MB.save_settings(app.cfg)
        cfg = self.MB.load_settings()
        self.assertEqual([d["name"] for d in cfg["devices"]["list"] if d.get("tab")], ["Boiler", "Meter"])
        self.assertEqual(cfg["tabs"], {"main_name": "Main poll", "active": 2})
        top = tk.Toplevel(self.root)
        top.withdraw()
        app2 = self.MB.MasterApp(top)
        try:
            self.assertEqual(app2.poll_tab_keys(), [None, "Boiler", "Meter"])
            self.assertEqual(app2.cur_tab, "Meter")
            self.assertEqual(app2.tab_title(None), "Main poll")
            self.assertFalse(app2.devpoll.running("Boiler"))
            b2 = app2.poll_tabs["Boiler"]
            self.assertEqual(b2.pv["addr"].get(), "40021")  # protocol 20 shown in the PLC base
            self.assertEqual((b2.pv["unit"].get(), b2.pv["qty"].get(), b2.rows_var.get()), ("2", "5", "20"))
            m2 = app2.poll_tabs["Meter"]
            self.assertTrue(m2.multi_var.get() and m2.multi_shown)
            self.assertEqual(m2.conn_var.get(), "Custom connection")
            self.assertEqual(app2.tabstrip.states.get("Boiler"), "off")
        finally:
            app2.stop_all_devices()
            app2.close()
        # workspace round trip (devices running -> the tabs start again)
        path = os.path.join(self.dir, "tabs.mbw")
        self.assertEqual(app.save_workspace(path=path), path)
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual([d["name"] for d in raw["devices"] if d["tab"]], ["Boiler", "Meter"])
        self.assertEqual(raw["display"]["active_tab"], 2)
        for n in ("Boiler", "Meter"):
            app.close_poll_tab(n)
        app.rename_poll_tab(None, "Poll 1")
        self.assertEqual(app.poll_tab_keys(), [None])
        app.open_workspace(path)
        self.assertEqual(app.poll_tab_keys(), [None, "Boiler", "Meter"])
        self.assertEqual(app.cur_tab, "Meter")
        self.assertEqual(app.tab_title(None), "Main poll")
        self.assertTrue(app.devpoll.running("Boiler") and app.devpoll.running("Meter"))
        self.assertEqual(self.tab("Boiler").pv["addr"].get(), "40021")
        self.assertTrue(self.wait(lambda: self.tab("Boiler").grid.values[0] == "120"))
        app.rename_poll_tab(None, "Poll 1")

    def test_8_many_tabs_scroll_and_no_after_jobs(self):
        app = self.app

        def jobs():
            self.root.update()
            return len(self.root.tk.call("after", "info"))
        app.stop_all_devices()
        for n in list(app.poll_tabs):
            app.close_poll_tab(n)
        app.select_poll_tab(None)
        self.pump(0.3)
        base = jobs()
        self.root.deiconify()
        self.root.geometry("760x500")
        try:
            for _ in range(10):
                app.new_poll_tab()
            self.pump(0.3)
            self.assertEqual(len(app.poll_tab_keys()), 11)
            ts = app.tabstrip
            self.assertTrue(ts.arrows, "the tab strip should scroll when the tabs do not fit")
            last = app.poll_tab_keys()[-1]
            self.assertEqual(app.cur_tab, last)
            r = ts._rect(last)
            self.assertLessEqual(r[2] - ts.off, ts.cv.winfo_width() + 1)  # the shown tab is scrolled into view
            app.select_poll_tab(None)
            self.root.update()
            self.assertEqual(ts.off, 0)
            for n in list(app.poll_tabs):
                app.poll_tabs[n].start()
            self.pump(0.6)
            for n in list(app.poll_tabs):
                app.close_poll_tab(n)
            self.root.update()
            self.assertFalse(ts.arrows)
        finally:
            self.root.withdraw()
        time.sleep(1.5)  # flash jobs of the grids expire
        self.assertLessEqual(jobs(), base, "closing poll tabs left after() jobs behind")
        self.assertEqual([d for d in app.devices if d.get("tab")], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
