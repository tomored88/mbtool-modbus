"""Multi-type view: the pure helper (monitor.multi_rows / multi_encode) and the main window driven in-process
against a SimSlave (toggle, columns persisted, editing a cell writes the right registers)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import json
import os
import struct
import tempfile
import time
import tkinter as tk
import unittest

import monitor as mon
from modbus_core import FORMATS, SimSlave, encode_value, format_registers


def words_f(x, inverse=False):
    w = list(struct.unpack(">2H", struct.pack(">f", x)))
    return w[::-1] if inverse else w


class Helper(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(mon.MULTI_FORMATS, list(FORMATS) + ["ASCII"])
        self.assertTrue(set(mon.MULTI_DEFAULT) <= set(mon.MULTI_FORMATS))
        self.assertEqual([mon.multi_width(f) for f in ("Unsigned", "Hex", "ASCII", "Long Inverse", "Float", "Double")],
                         [1, 1, 1, 2, 2, 4])

    def test_rows_basic(self):
        vals = [0xFFFF, 1, 0x4142]
        rows = mon.multi_rows(3, 100, vals, ["Unsigned", "Signed", "Hex", "Binary", "ASCII"], 0)
        self.assertEqual(rows[0], ["100", "65535", "-1", "0xFFFF", "1" * 16, ".."])
        self.assertEqual(rows[1], ["101", "1", "1", "0x0001", "0" * 15 + "1", ".."])
        self.assertEqual(rows[2], ["102", "16706", "16706", "0x4142", "0100000101000010", "AB"])

    def test_multi_word_and_insufficient(self):
        vals = words_f(1.5) + [7] + [0, 0, 0]  # 6 registers
        rows = mon.multi_rows(3, 0, vals, ["Float", "Long", "Double"], 0)
        self.assertEqual(rows[0][1], "1.5")
        self.assertEqual(len(rows), 6)
        # the last row: no second register for Float / Long; Double needs 4 -> rows 3..5 have none
        self.assertEqual(rows[5][1:], [mon.MULTI_NONE] * 3)  # covered by the value above / no registers left
        self.assertEqual(rows[4][1:3], [format_registers(vals[4:6], "Float")[0][1], "0"])
        self.assertEqual(rows[4][3], mon.MULTI_NONE)  # Double needs 4 registers, only 2 are left
        self.assertNotEqual(rows[0][3], mon.MULTI_NONE)  # Double starts on row 0 ...
        self.assertEqual([r[3] for r in rows[1:4]], [mon.MULTI_NONE] * 3)  # ... and covers rows 1-3

    def test_two_register_values_skip_the_row_they_cover(self):
        vals = words_f(1.5) + words_f(-2.25) + words_f(3.0)
        rows = mon.multi_rows(3, 0, vals, ["Unsigned", "Float", "Long"], 0)
        self.assertEqual([r[2] for r in rows], ["1.5", "", "-2.25", "", "3", "" ])
        self.assertTrue(all(r[1] != "" for r in rows))  # one-register types show on every row
        self.assertEqual([bool(r[3]) for r in rows], [True, False] * 3)

    def test_align_shifts_where_pairs_start(self):
        vals = [7] + words_f(1.5) + words_f(2.5)
        rows = mon.multi_rows(3, 0, vals, ["Float"], 0, align=1)
        self.assertEqual([r[1] for r in rows], ["", "1.5", "", "2.5", "", ])
        rows = mon.multi_rows(3, 0, vals, ["Double"], 0, align=1)
        self.assertEqual([bool(r[1]) for r in rows], [False, True, False, False, False])
        self.assertEqual(mon.multi_rows(3, 0, vals, ["Unsigned"], 0, align=3)[0][1], "7")

    def test_matches_single_format_display(self):
        """At the start of every value, each column equals what the single-format grid shows (incl. Inverse)."""
        vals = words_f(-12.25) + words_f(3.0e9, True) + [0x1234, 0xABCD, 0x0001, 0xFFFE, 0, 9, 0x8000, 0x7FFF]
        for fmt in FORMATS:
            rows = mon.multi_rows(4, 0, vals, [fmt], 0)
            for i, text in format_registers(vals, fmt):
                self.assertEqual(rows[i][1], text, (fmt, i))
        self.assertEqual(mon.multi_rows(3, 0, vals, ["Float Inverse"], 0)[2][1], "3e+09")

    def test_address_bases(self):
        self.assertEqual([r[0] for r in mon.multi_rows(3, 0, [1, 2], ["Hex"], 0)], ["0", "1"])
        self.assertEqual([r[0] for r in mon.multi_rows(3, 0, [1, 2], ["Hex"], 1)], ["1", "2"])
        self.assertEqual([r[0] for r in mon.multi_rows(3, 0, [1, 2], ["Hex"], "plc")], ["40001", "40002"])
        self.assertEqual([r[0] for r in mon.multi_rows(4, 9, [1], ["Hex"], "plc")], ["30010"])

    def test_edge_cases(self):
        self.assertEqual(mon.multi_rows(3, 0, [], ["Unsigned", "Float"], 0), [])
        self.assertEqual(mon.multi_rows(3, 0, [5], [], 0), [["0"]])
        self.assertEqual(mon.multi_rows(3, 0, [5], ["Double"], 0), [["0", mon.MULTI_NONE]])
        # coils / discrete inputs: a single value column, the formats do not apply
        self.assertEqual(mon.multi_rows(1, 0, [True, False], ["Float"], 1), [["1", "1"], ["2", "0"]])

    def test_norm_cols(self):
        self.assertEqual(mon.norm_multi_cols(["Float", "Hex", "Hex", "Nope", 3]), ["Float", "Hex"])
        self.assertEqual(mon.norm_multi_cols([]), mon.MULTI_DEFAULT)
        self.assertEqual(mon.norm_multi_cols(None), mon.MULTI_DEFAULT)
        self.assertEqual(mon.norm_multi_cols("Hex"), mon.MULTI_DEFAULT)

    def test_encode(self):
        self.assertEqual(mon.multi_encode(" 1.5 ", "Float"), encode_value("1.5", "Float"))
        self.assertEqual(mon.multi_encode("0x0102", "Hex"), [0x0102])
        self.assertEqual(mon.multi_encode("AB", "ASCII"), [0x4142])
        self.assertEqual(mon.multi_encode("A", "ASCII"), [0x4100])
        for bad, fmt in (("", "ASCII"), ("ABC", "ASCII"), ("é", "ASCII"), ("x", "Float"), ("70000", "Unsigned")):
            with self.assertRaises(ValueError):
                mon.multi_encode(bad, fmt)
        # round trip through the display for every format
        for fmt in FORMATS:
            words = mon.multi_encode("12", fmt) if fmt not in ("Hex", "Binary") else mon.multi_encode("1", fmt)
            text = mon.multi_rows(3, 0, words, [fmt], 0)[0][1]
            self.assertEqual(mon.multi_encode(text, fmt), words, fmt)


class Gui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        cls.dir = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE = os.path.join(cls.dir, "settings.json")  # never touch the user's settings
        MB.HOME_SETTINGS = os.path.join(cls.dir, "home_settings.json")
        cls.slave = SimSlave(unit=None)
        h = cls.slave.holding
        h[0:2] = words_f(1.5)
        h[2:4] = [1, 0x1170]  # Long 70000 at 2
        h[4] = 0x4142
        h[5] = 0xFFFF
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = MB.MasterApp(cls.root)
        app = cls.app
        app.cfg["conn"].update(mode="TCP", host="127.0.0.1", port=str(cls.slave.port), timeout="1000")
        app.pv["func"].set(MB.FUNCS[3])
        app.set_addr_base(0)
        app.pv["addr"].set("0")
        app.pv["qty"].set("10")
        app.pv["scan"].set("100")
        app.pv["fmt"].set("Unsigned")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.disconnect(quiet=True)
            cls.root.destroy()
            cls.slave.stop()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS = cls.saved

    def pump(self, sec):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            time.sleep(0.02)

    def wait(self, cond, sec=5.0):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def connect(self):
        app = self.app
        if app.state != "on":
            app.connect()
            self.assertTrue(self.wait(lambda: app.state == "on"), "not connected")

    def test_1_toggle_and_columns(self):
        app = self.app
        self.connect()
        self.assertTrue(self.wait(lambda: app.grid.values and app.grid.values[0] != ""))
        self.assertFalse(app.multi_shown)
        app.multi_var.set(True)
        app._multi_changed()
        self.assertTrue(app.multi_shown)
        self.assertEqual(app.mgrid.frame.winfo_manager(), "pack")
        self.assertEqual(app.grid.frame.winfo_manager(), "")
        self.assertEqual(app.b_mcols.winfo_manager(), "pack")
        self.assertEqual(app.cb_rows.winfo_manager(), "")
        self.assertEqual(app.mgrid.cols, mon.MULTI_DEFAULT)
        self.assertEqual(len(app.mgrid.labels), 10)
        ci = app.mgrid.cols.index("Float")
        self.assertTrue(self.wait(lambda: app.mgrid.values[0][ci] == "1.5"))
        self.assertEqual(app.mgrid.values[2][app.mgrid.cols.index("Long")], "70000")
        self.assertEqual(app.mgrid.values[9][ci], mon.MULTI_NONE)
        self.assertEqual(app.mgrid.values[5][app.mgrid.cols.index("Signed")], "-1")
        # the single-format grid keeps being fed (log / trend / alerts read it)
        self.assertEqual(app.grid.values[5], "65535")
        # columns dialog -> persisted
        dlg = app.open_multi_cols()
        for f, v in dlg.vars.items():
            v.set(f in ("Hex", "ASCII", "Double Inverse"))
        dlg.ok()
        self.assertEqual(app.mgrid.cols, ["Hex", "Double Inverse", "ASCII"])
        self.assertTrue(self.wait(lambda: app.mgrid.values[4][2] == "AB"))
        self.assertEqual(app.mgrid.values[7][1], mon.MULTI_NONE)
        self.MB.save_settings(app.cfg)
        with open(self.MB.SETTINGS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertTrue(saved["poll"]["multi"])
        self.assertEqual(saved["poll"]["multi_cols"], ["Hex", "Double Inverse", "ASCII"])
        cfg = self.MB.load_settings()
        self.assertEqual(cfg["poll"]["multi_cols"], ["Hex", "Double Inverse", "ASCII"])
        # workspace round trip keeps the view
        ws = app.gather_workspace()
        self.assertTrue(ws["main"]["poll"]["multi"])
        # FC 01: the toggle is disabled and the normal grid comes back
        app.pv["func"].set(self.MB.FUNCS[1])
        self.pump(0.1)
        self.assertFalse(app.multi_shown)
        self.assertIn("disabled", app.cb_multi.state())
        self.assertEqual(app.grid.frame.winfo_manager(), "pack")
        app.pv["func"].set(self.MB.FUNCS[3])
        self.pump(0.1)
        self.assertTrue(app.multi_shown)
        self.assertEqual(app.mgrid.frame.winfo_manager(), "pack")
        app.set_multi_cols(mon.MULTI_DEFAULT)

    def test_2_edit_writes_registers(self):
        app = self.app
        self.connect()
        if not app.multi_shown:
            app.multi_var.set(True)
            app._multi_changed()
        cols = app.mgrid.cols
        # Float at row 6 -> registers 6 and 7
        ent = None
        app.mgrid.select(6, cols.index("Float"))
        app.edit_mcell(6, cols.index("Float"))
        ent = app.mgrid.editor
        self.assertIsNotNone(ent)
        ent.delete(0, "end")
        ent.insert(0, "-2.5")
        ent.mb_commit()
        self.assertTrue(self.wait(lambda: self.slave.holding[6:8] == words_f(-2.5)), self.slave.holding[6:8])
        self.assertTrue(self.wait(lambda: "Written" in app.l_msg.cget("text")), app.l_msg.cget("text"))
        # Signed at row 8 -> one register
        app.edit_mcell(8, cols.index("Signed"))
        app.mgrid.editor.delete(0, "end")
        app.mgrid.editor.insert(0, "-3")
        app.mgrid.editor.mb_commit()
        self.assertTrue(self.wait(lambda: self.slave.holding[8] == 65533))
        # bad text: the editor stays open with an error, nothing written
        app.edit_mcell(9, cols.index("Unsigned"))
        app.mgrid.editor.delete(0, "end")
        app.mgrid.editor.insert(0, "99999")
        app.mgrid.editor.mb_commit()
        self.assertIn("not a valid Unsigned", app.l_msg.cget("text"))
        self.pump(0.2)
        self.assertIsNotNone(app.mgrid.editor)
        self.assertIn("not a valid Unsigned", app.mgrid.edit_err.cget("text"))
        self.assertEqual(self.slave.holding[9], 0)
        app.mgrid.editor.mb_cancel()
        self.pump(0.05)
        self.assertIsNone(app.mgrid.editor)
        # the table shows the written values after the re-poll
        self.assertTrue(self.wait(lambda: app.mgrid.values[6][cols.index("Float")] == "-2.5"))

    def test_3_inspector_and_write_dialog(self):
        app = self.app
        self.connect()
        if not app.multi_shown:
            app.multi_var.set(True)
            app._multi_changed()
        self.assertTrue(self.wait(lambda: app.mgrid.values[0][0] != ""))
        cols = app.mgrid.cols
        app.mgrid.select(2, cols.index("Long"))
        src, why = app.inspect_source()
        self.assertIsNone(why)
        self.assertEqual((src["addr"], src["default_n"]), (2, 2))
        self.assertEqual(src["words"][:2], [1, 0x1170])
        app.open_write()
        wd = app.write_dlg
        self.assertEqual(wd.v["func"].get(), self.MB.WRITE_FUNCS[16])
        self.assertEqual(wd.v["fmt"].get(), "Long")
        self.assertEqual(wd.v["values"].get(), "70000")
        wd.close()
        app.open_inspector()
        self.pump(0.3)
        self.assertTrue(app.inspector.alive())
        app.inspector.w.destroy()
        # stale look on disconnect
        app.disconnect(quiet=True)
        self.assertTrue(app.mgrid.stale)
        self.assertEqual(app.mgrid.cv.itemcget(app.mgrid.items[0][1][0], "fill"), self.MB.COL["stale"])

    def test_5_sim_data_tab(self):
        app = self.app
        sim = app.open_sim()
        try:
            sim.v["area"].set("Holding Registers (4x)")
            sim.v["addr"].set("100")
            sim.v["qty"].set("6")
            sim.store.holding[100:102] = words_f(2.25)
            sim.multi.set(True)
            sim._validate_view()
            self.assertTrue(sim.multi_shown)
            self.assertEqual(sim.mgrid.frame.winfo_manager(), "pack")
            self.assertEqual(sim.grid.frame.winfo_manager(), "")
            self.assertEqual(sim.mgrid.labels, [str(a) for a in range(100, 106)])
            fi = sim.mgrid.cols.index("Float")
            self.assertEqual(sim.mgrid.values[0][fi], "2.25")
            self.assertEqual(sim.mgrid.values[5][fi], mon.MULTI_NONE)
            sim._medit(2, fi)
            sim.mgrid.editor.delete(0, "end")
            sim.mgrid.editor.insert(0, "10.5")
            sim.mgrid.editor.mb_commit()
            self.assertEqual(sim.store.holding[102:104], words_f(10.5))
            self.assertTrue(app.cfg["sim"]["multi"])
            sim.v["area"].set("Coils (0x)")  # bits: back to the plain grid, toggle disabled
            self.assertFalse(sim.multi_shown)
            self.assertIn("disabled", sim.cb_multi.state())
            sim.v["area"].set("Holding Registers (4x)")
            sim.multi.set(False)
            sim._validate_view()
            self.assertEqual(sim.grid.frame.winfo_manager(), "pack")
        finally:
            sim.close()

    def test_4_toggle_off_restores_grid(self):
        app = self.app
        app.multi_var.set(False)
        app._multi_changed()
        self.assertFalse(app.multi_shown)
        self.assertEqual(app.grid.frame.winfo_manager(), "pack")
        self.assertEqual(app.mgrid.frame.winfo_manager(), "")
        self.assertEqual(app.cb_rows.winfo_manager(), "pack")
        self.assertEqual(app.b_mcols.winfo_manager(), "")
        self.assertFalse(app.cfg["poll"]["multi"])

    def test_6_pair_alignment(self):
        app = self.app
        self.connect()
        if not app.multi_shown:
            app.multi_var.set(True)
            app._multi_changed()
        app.set_multi_cols(["Unsigned", "Float"])
        self.assertTrue(self.wait(lambda: app.mgrid.values[0][1] == "1.5"))
        fi = app.mgrid.cols.index("Float")
        shown = lambda: [bool(app.mgrid.values[i][fi]) for i in range(len(app.mgrid.labels))]
        self.assertEqual(shown(), [True, False] * 5)  # a 2-register value only on the row where it starts
        self.assertTrue(all(app.mgrid.values[i][0] for i in range(10)))  # 1-register types: every row
        # editing a blank cell is refused (no editor opens)
        app.state = "on"
        app.mgrid.start_edit(1, fi, lambda *a: None)
        self.assertIsNone(app.mgrid.editor)
        # alignment 1 moves the pairs to rows 1, 3, 5, 7 (row 9 has no second register)
        app.set_multi_align(1)
        self.assertEqual(app.multi_align, 1)
        self.assertTrue(self.wait(lambda: shown() == [False, True, False, True, False, True, False, True, False, False]))
        self.assertEqual(app.cfg["poll"]["multi_align"], "1")
        ws = app.gather_workspace()
        self.assertEqual(ws["main"]["poll"]["multi_align"], "1")
        # the columns dialog carries the setting
        dlg = app.open_multi_cols()
        self.assertEqual(dlg.align.get(), "1")
        dlg.align.set("0")
        dlg.ok()
        self.assertEqual(app.multi_align, 0)
        self.assertTrue(self.wait(lambda: shown() == [True, False] * 5))
        self.assertEqual([mon.norm_multi_align(v) for v in (None, "x", "2", 3, 4, -1, "1")], [0, 0, 2, 3, 0, 0, 1])
        app.set_multi_cols(mon.MULTI_DEFAULT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
