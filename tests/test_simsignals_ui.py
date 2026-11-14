"""Slave Simulator (F9) Signals tab, driven in-process: preset load, moving values over TCP, modes, pause,
pattern override, settings and engine shutdown on close."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import socket
import tempfile
import time
import tkinter as tk
import unittest

import mbtool as MB
from modbus_core import TcpMaster

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATV = os.path.join(HERE, "presets", "library", "schneider_atv320.json")
SPEED_PT = "nM2 Scanner In2: RFRD Output Speed"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Helpers(unittest.TestCase):
    def test_parse_spec(self):
        self.assertEqual(MB.parse_sig_spec("sine", {"min": "1", "max": "5", "period": "", "noise": ""}),
                         {"kind": "sine", "min": 1.0, "max": 5.0})
        self.assertEqual(MB.parse_sig_spec("sequence", {"values": "0, 1;2"})["values"], [0.0, 1.0, 2.0])
        for kind, texts, field in (("sine", {"min": "1"}, "max"), ("sine", {"min": "9", "max": "1"}, "max"),
                                   ("ramp", {"min": "0", "max": "1", "period": "0"}, "period"),
                                   ("noise", {"min": "0", "max": "x"}, "max"), ("bogus", {}, "kind"),
                                   ("random_walk", {"min": "0", "max": "1", "step": "3"}, "step"),
                                   ("sequence", {"values": "a,b"}, "values")):
            with self.assertRaises(ValueError) as cm:
                MB.parse_sig_spec(kind, texts)
            self.assertEqual(cm.exception.args[0][0], field, (kind, texts))
        for kind in MB.simsig.KINDS:
            self.assertIn(kind, MB.SIG_KIND_FIELDS)
        self.assertEqual(set(MB.SIG_MODES.values()), set(MB.simsig.MODES))

    def test_value_text(self):
        pr = MB.load_preset(ATV)
        p = pr.by_name["tFO Modbus Format"]
        self.assertEqual(MB.sig_value_text(p, 3.0), "3 = 8-E-1")
        self.assertEqual(MB.sig_value_text(p, 99.0), "99")
        self.assertEqual(MB.sig_num_text(12.5), "12.5")
        self.assertEqual(MB.sig_num_text(230.0), "230")
        self.assertEqual(MB.sig_value_text(p, None), "")


class SignalsTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE = os.path.join(d, "settings.json")  # never touch the user's settings
        MB.HOME_SETTINGS = os.path.join(d, "home_settings.json")
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = MB.MasterApp(cls.root)

    @classmethod
    def tearDownClass(cls):
        try:
            if cls.app.sim and cls.app.sim.alive():
                cls.app.sim.close()
            cls.root.destroy()
        finally:
            MB.SETTINGS_FILE, MB.HOME_SETTINGS = cls.saved

    def pump(self, sec):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            time.sleep(0.02)

    def read_pt(self, m, pr, name):
        p = pr.by_name[name]
        return p.decode(m.read_holding(1, p.address, p.words))["value"]

    def test_signals_flow(self):
        sim = self.app.open_sim()
        sim.w.withdraw()
        self.assertIn("Signals", [sim.nb.tab(t, "text") for t in sim.nb.tabs()])
        self.assertEqual(str(sim.b_sig_run.cget("state")), "disabled")  # nothing loaded yet
        # load through the picker
        picker = sim.pick_sig_preset()
        picker.w.withdraw()
        self.assertIn(ATV, picker.paths.values())
        self.assertTrue(picker.pick(ATV))
        self.assertFalse(picker.alive())
        eng = sim.engine
        self.assertIsNotNone(eng)
        self.assertFalse(eng.running())  # loading never starts the signals
        self.assertIn("unverified", sim.l_sig_note.cget("text"))
        self.assertIn("left to the master", sim.l_sig_left.cget("text"))
        self.assertEqual(len(sim.sig_tree.get_children()), len(eng.plans))
        # loop mode, fast
        sim.sig_v["mode"].set("Loop (ramp up, wrap around)")
        sim.sig_v["speed"].set("10x")
        self.assertEqual(eng.mode, "loop")
        self.assertEqual(eng.speed, 10.0)
        self.assertEqual(str(sim.cb_sig_dev.cget("state")), "disabled")
        sim.sig_v["mode"].set("Device behaviour (by device type)")
        self.assertEqual(str(sim.cb_sig_dev.cget("state")), "readonly")
        self.assertEqual(eng.mode, "profile")
        self.assertIn("drive", sim.h_sig_dev.cget("text"))
        sim.sig_v["mode"].set("Loop (ramp up, wrap around)")
        # start the TCP slave and the signals
        sim.v["mode"].set("TCP")
        sim.v["port"].set(str(free_port()))
        sim.v["unit"].set("")
        sim.start()
        self.assertTrue(sim.running(), sim.l_msg.cget("text"))
        self.assertTrue(sim.start_signals())
        self.assertTrue(eng.running())
        self.assertEqual(sim.b_sig_run.cget("text"), "Stop signals")
        pr = eng.preset
        m = TcpMaster("127.0.0.1", sim.tcp.port, 1.0)
        try:
            seen = set()
            for _ in range(6):
                self.pump(0.6)
                seen.add(self.read_pt(m, pr, SPEED_PT))
            self.assertGreater(len(seen), 2, f"values over TCP did not move: {seen}")
            # the Signals list and the Data tab show moving values too
            vals = [sim.sig_tree.set(i, "value") for i in sim.sig_tree.get_children()]
            self.assertTrue(any(vals), vals)
            # mode change while running: static stops the motion
            sim.sig_v["mode"].set("Static (fixed values)")
            self.assertEqual(eng.mode, "static")
            self.pump(0.8)
            a = self.read_pt(m, pr, SPEED_PT)
            self.pump(1.2)
            self.assertEqual(a, self.read_pt(m, pr, SPEED_PT))
            sim.sig_v["mode"].set("Loop (ramp up, wrap around)")
            self.pump(1.2)
            # pause freezes the values
            sim.sig_pause.set(True)
            sim._sig_paused()
            self.assertTrue(eng.paused)
            self.pump(0.8)
            a = self.read_pt(m, pr, SPEED_PT)
            self.pump(1.5)
            self.assertEqual(a, self.read_pt(m, pr, SPEED_PT))
            sim.sig_pause.set(False)
            sim._sig_paused()
            # override one point through the pattern dialog
            iid = next(i for i, n in sim.sig_rows.items() if n == SPEED_PT)
            dlg = sim.edit_sig_pattern(iid)
            dlg.w.withdraw()
            dlg.kind.set("sine")
            dlg.vars["min"].set("10")
            dlg.vars["max"].set("5")  # invalid: max < min -> error, dialog stays open
            self.assertFalse(dlg.ok())
            self.assertTrue(dlg.alive())
            self.assertIn("Max", sim.l_msg.cget("text"))
            dlg.kind.set("fixed")
            dlg.vars["value"].set("1234")
            self.assertTrue(dlg.ok())
            self.assertEqual(eng.overrides[SPEED_PT], {"kind": "fixed", "value": 1234.0})
            self.pump(1.2)
            self.assertEqual(self.read_pt(m, pr, SPEED_PT), 1234)
            self.assertIn("custom", sim.sig_tree.set(iid, "beh"))
            self.assertEqual(sim.sig_tree.set(iid, "value"), "1234")
            dlg = sim.edit_sig_pattern(iid)
            dlg.w.withdraw()
            self.assertTrue(dlg.reset())
            self.assertNotIn(SPEED_PT, eng.overrides)
            self.assertEqual(sim.set_sig_override("no such point", None).split(":")[0], "no such point")
        finally:
            m.close()
        # settings
        sim._save_cfg()
        c = self.app.cfg["sim"]
        self.assertEqual((c["sig_mode"], c["sig_speed"], c["sig_device"]), ("loop", "10", ""))
        self.assertEqual(os.path.normcase(c["sig_preset"]), os.path.normcase(os.path.abspath(ATV)))
        # closing stops the engine thread and the slave
        sim.close()
        self.assertFalse(eng.running())
        self.assertFalse(sim.alive())
        self.assertIsNone(sim.sig_job)
        # reopening reloads the preset but does not start the signals
        sim2 = self.app.open_sim()
        sim2.w.withdraw()
        self.assertIsNotNone(sim2.engine)
        self.assertFalse(sim2.engine.running())
        self.assertEqual(sim2.engine.mode, "loop")
        self.assertEqual(sim2.sig_v["speed"].get(), "10x")
        # loading another preset stops the old engine
        sim2.start_signals()
        old = sim2.engine
        self.assertTrue(old.running())
        self.assertTrue(sim2.load_sig_preset(os.path.join(HERE, "presets", "example_generic_meter.json")))
        self.assertFalse(old.running())
        self.assertIsNot(sim2.engine, old)
        self.assertFalse(sim2.load_sig_preset(os.path.join(HERE, "no_such_file.json")))
        self.assertIn("Could not load", sim2.l_msg.cget("text"))
        sim2.close()


if __name__ == "__main__":
    unittest.main()
