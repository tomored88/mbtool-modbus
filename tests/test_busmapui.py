"""Bus map window (Tools > Bus map): the pure helpers in monitor.py (saved map check, animation state machine,
live-follow queue, layout) and the window driven in-process (import from Devices, in-place editing, warnings,
persistence in settings / workspace / JSON, animation phases, clean close, live follow from a traffic deque)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import collections
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import unittest

import busmap
import buscalc
import monitor as M

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Pure(unittest.TestCase):
    def test_norm_busmap(self):
        d, err = M.norm_busmap(None)
        self.assertIsNone(err)
        self.assertEqual({k: v for k, v in d.items() if k != "cable_custom"}, busmap.default_map())
        good = busmap.default_map([("A", 2), ("B", 1)])
        good["supply_v"] = 12  # an int where a float is expected is fine
        good["nodes"][0]["unit"] = "1"  # text Slave ID from a hand-edited file
        m, err = M.norm_busmap(good)
        self.assertIsNone(err)
        self.assertEqual([n["name"] for n in m["nodes"]], ["B", "A"])
        self.assertEqual(m["nodes"][0]["unit"], 1)
        self.assertEqual(m["supply_v"], 12.0)
        for bad in ("x", {"nodes": "x"}, {"cable": "fibre"}, {"baud": "fast"}, {"bias": "yes"},
                    {"nodes": [{"name": "a", "unit": 1}, {"name": "b", "unit": 1}]},
                    {"nodes": [{"name": "a", "segment_m": -3}]}, {"nodes": [{"name": "a", "segment_m": "abc"}]},
                    {"nodes": [{"name": "a", "terminated": 1}]}, {"cable": "custom", "cable_custom": {"vf": 2}}):
            m, err = M.norm_busmap(bad)
            self.assertTrue(err, bad)
            self.assertEqual(m, M.busmap_default(), bad)
        m, err = M.norm_busmap({"cable": "custom", "cable_custom": {"vf": 0.8, "junk": 1}})
        self.assertIsNone(err)
        self.assertEqual(m["cable_custom"], {"vf": 0.8})

    def test_exchange_phases_and_anim(self):
        an = busmap.analyze(busmap.default_map([("A", 1), ("B", 2)]))
        link = buscalc.Link("rtu", 9600, 8, "N", 1, gap_ms=10.0)
        tl = busmap.exchange_timeline(an, link, 2, 3, 10, 20.0)
        ph = M.exchange_phases(tl)
        self.assertEqual([p[0] for p in ph], list(M.ANIM_PHASES))
        self.assertAlmostEqual(sum(ms for _n, ms in ph), tl["total_ms"], places=6)
        a = M.ExchangeAnim(ph, unit=2, name="B")
        self.assertEqual(a.current(), ("idle", 0.0))
        self.assertAlmostEqual(a.total_ms, tl["total_ms"], places=6)
        # the schematic scale: 1 s of animation = ANIM_BUS_MS_PER_S ms of bus time (short phases stretched)
        want = sum(max(ms / M.ANIM_BUS_MS_PER_S, M.ANIM_MIN_PHASE_S) for _n, ms in ph if ms > 0)
        self.assertAlmostEqual(a.total_wall, want)
        for _ in range(1000):
            a.advance(0.02, 2.0)
            if a.done:
                break
        self.assertTrue(a.done)
        self.assertEqual(a.seen, list(M.ANIM_PHASES))
        self.assertEqual(a.current(), (None, 1.0))
        self.assertAlmostEqual(a.bus_ms(), a.total_ms)
        b = M.ExchangeAnim([("idle", 0.0), ("request", 50.0), ("gap", 0.0)])
        self.assertEqual([p[0] for p in b.phases], ["request"])  # zero-length phases are skipped
        b.advance(0.5)
        self.assertEqual(b.current()[0], "request")
        self.assertAlmostEqual(b.current()[1], 0.5)
        self.assertAlmostEqual(b.bus_ms(), 25.0)

    def test_live_queue(self):
        now = 1000.0
        q = [{"t": now - 5, "kind": "req", "unit": 1}] + [{"t": now - 0.9 + i * 0.05, "kind": "req", "unit": 1}
                                                          for i in range(15)]
        out = M.trim_live_queue(q, now)
        self.assertEqual(len(out), M.LIVE_MAX_ITEMS)
        self.assertTrue(all(now - e["t"] <= M.LIVE_MAX_LAG_S for e in out))
        self.assertEqual(out[-1], q[-1])  # the newest are kept
        self.assertEqual(M.trim_live_queue(q, now + 10), [])
        self.assertEqual(M.trim_live_queue([], now), [])
        tr = [{"t": 1.0, "dir": "TX", "unit": 1}, {"t": 2.0, "dir": "RX", "unit": 1},
              {"t": 3.0, "dir": "TX", "unit": 9}, {"t": 4.0, "dir": "ERR", "unit": 2}, {"t": 5.0, "dir": "??", "unit": 1},
              "junk"]
        ev = M.live_events(tr, 1.0, {1, 2})
        self.assertEqual([(e["kind"], e["unit"]) for e in ev], [("rsp", 1), ("err", 2)])

    def test_spacing(self):
        sp, tot = M.busmap_spacing(6, 600, 100, 50, 120, 230)
        self.assertEqual(sp, 120)
        self.assertEqual(tot, 100 + 50 + 720)  # wider than the canvas: scrolls
        sp, tot = M.busmap_spacing(6, 600, 100, 50, 120, 230, fit=True)
        self.assertAlmostEqual(sp, 75.0)
        self.assertEqual(tot, 600)
        sp, tot = M.busmap_spacing(1, 2000, 100, 50, 120, 230)
        self.assertEqual(sp, 230)
        self.assertEqual(M.busmap_spacing(0, 500, 10, 10, 1, 2), (0.0, 500.0))

    def test_workspace_section(self):
        ws, warn = M.normalize_workspace({"version": 1}, ".")
        self.assertIsNone(ws["busmap"])  # older workspace: no map, the current one stays
        self.assertEqual(warn, [])
        m = busmap.default_map([("A", 3)])
        ws, warn = M.normalize_workspace({"version": 1, "busmap": m}, ".")
        self.assertEqual(ws["busmap"]["nodes"][0]["unit"], 3)
        ws, warn = M.normalize_workspace({"version": 1, "busmap": {"nodes": 5}}, ".")
        self.assertIsNone(ws["busmap"])
        self.assertTrue(any("bus map" in w for w in warn))


class Gui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        cls.dir = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE = os.path.join(cls.dir, "settings.json")  # never touch the user's settings
        MB.HOME_SETTINGS = os.path.join(cls.dir, "home_settings.json")
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = app = MB.MasterApp(cls.root)
        conn = {"mode": "RTU", "com": "COM9", "baud": "19200", "parity": "E", "stop": "1", "data": "8",
                "timeout": "500"}
        app.devices = [M.norm_device({"name": n, "conn": conn, "unit": str(u), "fc": "3", "addr": "0", "qty": "10"})
                       for n, u in (("Meter", 3), ("Drive", 1), ("Sensor", 2))]
        app.cfg["conn"].update(mode="RTU", baud="9600", parity="E", stop="1")

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.app.busmap_win and cls.app.busmap_win.alive():
                cls.app.busmap_win.close()
            cls.root.destroy()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS = cls.saved

    def pump(self, sec):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def win(self):
        return self.app.open_busmap()

    def test_1_open_and_import(self):
        app = self.app
        w = self.win()
        self.assertIs(app.open_busmap(), w)  # one instance
        self.assertEqual(w.w.title(), "mbtool - Bus Map")
        self.assertEqual(w.v["baud"].get(), "9600")  # a new map starts at the main window's serial baud
        self.assertEqual(w.av["parity"].get(), "E")
        self.assertEqual([w.lb_un.get(i).split()[0] for i in range(w.lb_un.size())], ["Meter", "Drive", "Sensor"])
        self.assertTrue(w.import_devices())
        self.assertEqual([(n["name"], n["unit"]) for n in w.map["nodes"]], [("Drive", 1), ("Sensor", 2), ("Meter", 3)])
        self.assertEqual(w.lb_un.size(), 0)
        self.assertEqual(len(w.tree.get_children()), 3)
        self.assertEqual(len(w.cv.find_withtag("node")), 3 * 4)  # box + 3 text lines per device
        # lengths are kept on a second import; a hand-made node stays at the end
        self.assertIsNone(w.commit_edit(2, "segment_m", "42"))
        self.assertIsNone(w.add_node("Spare", 7))
        app.devices.append(M.norm_device({"name": "Extra", "conn": app.devices[0]["conn"], "unit": "4"}))
        w.refresh_unmapped()
        self.assertEqual(w.lb_un.get(0).split()[0], "Extra")
        self.assertTrue(w.import_devices())
        self.assertEqual([n["name"] for n in w.map["nodes"]], ["Drive", "Sensor", "Meter", "Extra", "Spare"])
        self.assertEqual(w.map["nodes"][2]["segment_m"], 42.0)
        w.select(4)
        w.remove_node()
        w.select(3)
        w.remove_node()
        app.devices.pop()
        self.assertEqual(len(w.map["nodes"]), 3)
        # opened from the menu entry and the Devices window too
        tools = app.menubar.nametowidget(app.menubar.entrycget("Tools", "menu"))
        labels = [tools.entrycget(i, "label") for i in range(tools.index("end") + 1) if tools.type(i) == "command"]
        self.assertIn("Bus map...", labels)
        dw = app.open_devices()
        dw.b_busmap.invoke()
        self.assertIs(app.busmap_win, w)
        dw.close()

    def test_2_edit_cells(self):
        w = self.win()
        ed = w.start_edit(0, "segment_m")
        self.assertIsNotNone(ed)
        ed.delete(0, "end")
        ed.insert(0, "25,5")  # decimal comma accepted
        ed.mb_commit()
        self.assertIsNone(w.editor)
        self.assertEqual(w.map["nodes"][0]["segment_m"], 25.5)
        self.assertEqual(w.tree.set("0", "segment_m"), "25.5")
        # bad value: the editor stays open, the status line explains, nothing changes
        ed = w.start_edit(1, "stub_m")
        ed.delete(0, "end")
        ed.insert(0, "abc")
        ed.mb_commit()
        self.assertIsNotNone(w.editor)
        self.assertIn("Not applied", w.l_msg.cget("text"))
        self.assertIn("Stub", w.l_msg.cget("text"))
        ed.mb_cancel()
        self.assertIsNone(w.editor)
        self.assertEqual(w.map["nodes"][1]["stub_m"], 0.0)
        # out of range / duplicate Slave ID -> busmap.validate refuses
        self.assertTrue(w.commit_edit(1, "segment_m", "9000"))
        self.assertTrue(w.commit_edit(1, "unit", "1"))
        self.assertIn("same Slave ID", w.l_msg.cget("text"))
        self.assertTrue(w.commit_edit(1, "unit", "300"))
        self.assertIsNone(w.commit_edit(1, "name", "Sensor"))
        # terminator toggle + move
        w.toggle_terminator(2)
        self.assertTrue(w.map["nodes"][2]["terminated"])
        self.assertEqual(w.tree.set("2", "terminated"), "☑")
        w.toggle_terminator(2)
        w.select(0)
        w.move_node(1)
        self.assertEqual(w.map["nodes"][1]["name"], "Drive")
        w.move_node(-1)
        self.assertEqual(w.map["nodes"][0]["name"], "Drive")
        self.assertEqual(self.app.cfg["busmap"]["nodes"][0]["segment_m"], 25.5)  # kept in the settings

    def test_3_warnings_follow_baud_and_length(self):
        w = self.win()
        w.v["baud"].set("115200")
        self.pump(0.3)  # debounced recalc
        self.assertEqual(w.an["baud"], 115200)
        for i, seg in enumerate(("400", "350", "300")):
            self.assertIsNone(w.commit_edit(i, "segment_m", seg))
        texts = [t for _lv, t in w.an["warnings"]]
        self.assertTrue(any("above the usual limit" in t for t in texts), texts)
        res = w.out.get("1.0", "end")
        self.assertIn("TOO HIGH", res)
        self.assertIn("WARN", res)
        self.assertIn("~2.0 km", w.l_note.cget("text"))  # one bit ~2 km of cable at 115200 baud
        w.v["baud"].set("abc")
        self.assertIn("Not applied", w.l_msg.cget("text"))
        self.assertEqual(w.map["baud"], 115200)
        w.v["baud"].set("9600")
        w.recalc()
        self.assertFalse(any("above the usual limit" in t for _lv, t in w.an["warnings"]))
        self.assertIn("OK", w.out.get("1.0", "end"))
        self.assertIn("~23.4 km", w.l_note.cget("text"))  # 9600 baud, velocity factor 0.75
        # custom cable values become editable and are used
        w.v["cable"].set(busmap.CABLES["custom"]["label"])
        self.assertEqual(str(w.e_custom["vf"].cget("state")), "normal")
        w.v["vf"].set("0.5")
        w.recalc()
        self.assertEqual(w.map["cable_custom"]["vf"], 0.5)
        self.assertAlmostEqual(w.an["velocity_m_per_s"], busmap.C * 0.5)
        w.v["cable"].set(busmap.CABLES["rs485"]["label"])
        w.recalc()
        self.assertEqual(str(w.e_custom["vf"].cget("state")), "disabled")
        for i in range(3):
            w.commit_edit(i, "segment_m", "20")
        self.assertIn("Cycle time for all devices on the map (3)", w.l_cycle.cget("text"))
        self.assertTrue(w.tl_cv.find_all())

    def test_4_settings_and_workspace(self):
        app, MB = self.app, self.MB
        w = self.win()
        w.commit_edit(0, "stub_m", "1.5")
        MB.save_settings(app.cfg)
        cfg = MB.load_settings()
        self.assertEqual(cfg["busmap"], app.cfg["busmap"])
        self.assertEqual(cfg["busmap"]["nodes"][0]["stub_m"], 1.5)
        with open(MB.SETTINGS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        raw["busmap"]["nodes"][0]["segment_m"] = "far"  # broken by hand -> default map
        with open(MB.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        self.assertEqual(MB.load_settings()["busmap"], M.busmap_default())
        # workspace round trip
        path = os.path.join(tempfile.mkdtemp(), "bus.mbw")
        app.save_workspace(path=path)
        saved = json.loads(json.dumps(app.cfg["busmap"]))
        w.commit_edit(0, "stub_m", "3")
        w.v["baud"].set("38400")
        w.recalc()
        app.open_workspace(path)
        w = self.win()
        self.assertEqual(app.cfg["busmap"], saved)
        self.assertEqual(w.map["nodes"][0]["stub_m"], 1.5)
        self.assertEqual(w.v["baud"].get(), str(saved["baud"]))
        # an older workspace without a bus map leaves the current map alone
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        del data["busmap"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        w = self.win()
        w.commit_edit(0, "stub_m", "2")
        app.open_workspace(path)
        self.assertEqual(app.cfg["busmap"]["nodes"][0]["stub_m"], 2.0)

    def test_5_json_files(self):
        w = self.win()
        d = tempfile.mkdtemp()
        p = os.path.join(d, "map.json")
        self.assertEqual(w.save_json(p), p)
        before = json.loads(json.dumps(w.map))
        w.commit_edit(1, "segment_m", "77")
        self.assertIsNotNone(w.load_json(p))
        self.assertEqual(w.map, before)
        self.assertEqual(w.tree.set("1", "segment_m"), f"{before['nodes'][1]['segment_m']:g}")
        bad = os.path.join(d, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            json.dump({"nodes": [{"name": "x", "unit": 1}, {"name": "y", "unit": 1}]}, f)
        self.assertIsNone(w.load_json(bad))
        self.assertIn("Not a valid bus map", w.l_msg.cget("text"))
        self.assertEqual(w.map, before)
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(w.load_json(bad))
        # the same file works with the CLI subcommand and busmap.py
        out = subprocess.run([sys.executable, "mbtool.py", "busmap", p], cwd=HERE, capture_output=True, text=True,
                             encoding="utf-8").stdout
        self.assertIn("Trunk", out)
        self.assertIn("Checks:", out)
        self.assertEqual(w.copy_report(), busmap.format_report(w.an))

    def test_6_live_follow(self):
        w = self.win()

        class Fake:
            traffic = collections.deque(maxlen=500)
        fake = Fake()
        w.live_sources = lambda: [fake]
        now = time.time()
        fake.traffic.append({"t": now - 5, "dir": "TX", "unit": 1})  # history: not replayed
        w.live.set(True)
        w._live_changed()
        w.poll_live(time.time())
        self.assertEqual(w.live_q, [])
        for i in range(20):
            fake.traffic.append({"t": time.time(), "dir": "TX" if i % 2 == 0 else "RX", "unit": 2})
        fake.traffic.append({"t": time.time(), "dir": "TX", "unit": 99})  # not on the map
        w.poll_live(time.time())
        self.assertEqual(len(w.live_q), M.LIVE_MAX_ITEMS)  # never more than ~1 s behind
        self.assertTrue(all(e["unit"] == 2 for e in w.live_q))
        w._live_step(time.time())
        self.assertIsNotNone(w.live_cur)
        w.update_dynamic()
        self.assertTrue(w.cv.find_withtag("packet") or w.live_cur["ev"]["kind"] == "err")
        w.live.set(False)
        w._live_changed()
        del w.live_sources
        # the real thing: the main window polls a simulated slave over TCP, the map follows its traffic
        from modbus_core import SimSlave
        app = self.app
        slave = SimSlave(unit=None)
        saved_conn = dict(app.cfg["conn"])
        try:
            app.cfg["conn"].update(mode="TCP", host="127.0.0.1", port=str(slave.port), timeout="1000")
            app.pv["unit"].set("1")
            app.pv["scan"].set("100")
            app.connect()
            end = time.time() + 5
            while app.state != "on" and time.time() < end:
                self.pump(0.02)
            self.assertEqual(app.state, "on")
            w.live.set(True)
            w._live_changed()
            w.poll_live(time.time())  # baseline
            self.pump(0.5)
            w.poll_live(time.time())
            self.assertTrue(w.live_q, "no live events from the main connection")
            self.assertEqual({e["unit"] for e in w.live_q}, {1})
            self.assertTrue({"req", "rsp"} <= {e["kind"] for e in w.live_q})
            self.pump(0.3)  # the tick animates them
            self.assertIsNotNone(w._job)
        finally:
            w.live.set(False)
            w._live_changed()
            app.disconnect(quiet=True)
            slave.stop()
            app.cfg["conn"] = saved_conn

    def test_7_animation_and_close(self):
        w = self.win()
        w.dev_v.set(next(v for v in w.cb_dev.cget("values") if "(ID 2)" in v))
        w._anim_inputs_changed()
        self.assertTrue(w.play())
        self.assertEqual(w.anim.unit, 2)
        seen_packet = False
        for _ in range(2000):
            w.step(0.02)
            ph = w.anim.current()[0] if w.anim else None
            if ph == "request" and w.cv.find_withtag("packet"):
                seen_packet = True
                tgt = w._idx_of_unit(2)
                self.assertEqual(w.cv.itemcget(f"box{tgt}", "fill"), "#fff3b0")  # the addressed device lights up
            if w.completed >= 1:
                break
        self.assertTrue(seen_packet)
        self.assertEqual(w.completed, 1)
        self.assertEqual(w.last_seen, list(M.ANIM_PHASES))
        self.assertEqual(w.anim.unit, 2)  # the same device again
        # "All in turn" goes through the devices in map order
        w.dev_v.set("All in turn")
        w._anim_inputs_changed()
        w.anim = None
        w.turn = 0
        units = []
        for _ in range(3):
            units.append(w.start_exchange().unit)
        self.assertEqual(units, [n["unit"] for n in w.map["nodes"]])
        self.pump(0.2)  # the real tick runs too
        self.assertIsNotNone(w._job)
        w.pause()
        w.v["baud"].set("19200")  # leaves a pending debounce job
        self.assertIsNotNone(w._calc_job)
        jobs = [w._job, w._calc_job]
        w.close()
        self.assertFalse(w.alive())
        self.assertIsNone(w._job)
        self.assertIsNone(w._calc_job)
        pending = self.root.tk.splitlist(self.root.tk.call("after", "info"))
        for j in jobs:
            self.assertNotIn(j, pending)
        self.pump(0.2)  # nothing fires after the close
        self.assertIsNot(self.app.open_busmap(), w)  # a new window after the close
        self.app.busmap_win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
