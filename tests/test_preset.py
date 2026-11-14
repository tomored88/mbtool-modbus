import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import json
import os
import tempfile
import unittest

from modbus_core import SimSlave, TcpMaster
from preset import Preset, PresetError, from_csv, load_preset

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(HERE, "presets", "example_generic_meter.json")


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.p = load_preset(EX)
        self.s = SimSlave()
        self.p.fill_simulator(self.s)
        self.m = TcpMaster("127.0.0.1", self.s.port)

    def tearDown(self):
        self.m.close()
        self.s.stop()

    def test_read_all_decodes_scaled_values(self):
        r = self.p.read(self.m)
        self.assertAlmostEqual(r["Voltage L1"]["value"], 230.4, places=3)
        self.assertEqual(r["Power"]["value"], 2880)
        self.assertAlmostEqual(r["Temperature"]["value"], 25.3, places=6)
        self.assertAlmostEqual(r["Energy"]["value"], 12345.67, places=6)  # little word order, uint32 * 0.01
        self.assertEqual(r["Mode"]["text"], "Run")
        self.assertEqual(r["Alarm"]["text"], "OK")
        self.assertFalse(any("error" in v for v in r.values()))

    def test_blocks_merge_and_respect_fc(self):
        bl = self.p.blocks("measure")
        self.assertEqual(len(bl), 1)
        self.assertEqual((bl[0]["fc"], bl[0]["start"], bl[0]["end"]), (3, 0, 8))
        self.assertEqual(len(self.p.blocks()), 3)  # fc1, fc2, and one fc3 block (measure+config merged across a small gap)

    def test_write_validates_and_writes(self):
        self.p.write(self.m, "Setpoint", 30.5)
        self.assertEqual(self.s.holding[11], 305)
        self.p.write(self.m, "Mode", 2)
        self.p.write(self.m, "Relay", True)
        self.assertTrue(self.s.coils[0])
        for name, val in (("Setpoint", 41), ("Setpoint", 4), ("Mode", 3), ("Voltage L1", 1), ("Nope", 1)):
            with self.assertRaises(PresetError, msg=(name, val)):
                self.p.write(self.m, name, val)
        self.assertEqual(self.s.holding[11], 305)  # rejected writes changed nothing

    def test_write_multiword_roundtrip(self):
        d = json.load(open(EX, encoding="utf-8"))
        d["points"].append({"name": "Gain", "address": 20, "fc": 3, "type": "float32", "word_order": "little",
                            "access": "rw", "group": "config"})
        d["points"].append({"name": "Big", "address": 24, "fc": 3, "type": "float64", "access": "rw"})
        p = Preset(d)
        p.write(self.m, "Gain", 1.25)
        p.write(self.m, "Big", -12345.6789)
        r = p.read(self.m, group=None)
        self.assertEqual(r["Gain"]["value"], 1.25)
        self.assertAlmostEqual(r["Big"]["value"], -12345.6789, places=6)

    def test_read_error_is_reported_per_point(self):
        d = json.load(open(EX, encoding="utf-8"))
        d["points"].append({"name": "Far", "address": 65530, "fc": 3, "type": "uint16"})
        d["points"].append({"name": "Zero", "address": 65500, "fc": 4, "type": "uint16"})
        r = Preset(d).read(self.m)
        self.assertNotIn("error", r["Far"])  # simulator covers the whole space
        self.s.stop()
        r2 = Preset(d).read(self.m)
        self.assertTrue(all("error" in v for v in r2.values()))

    def test_validation_errors(self):
        base = {"points": [{"name": "a", "address": 0}]}
        Preset(base)
        for bad in (
            {"points": [{"name": "a", "address": 0, "type": "wat"}]},
            {"points": [{"name": "a", "address": 0, "fc": 9}]},
            {"points": [{"name": "a", "address": 0, "fc": 1, "type": "uint16"}]},
            {"points": [{"name": "a", "address": 0, "fc": 4, "access": "rw"}]},
            {"points": [{"name": "a", "address": 0}, {"name": "a", "address": 1}]},
            {"points": [{"name": "a", "address": 65535, "type": "float32"}]},
            {"points": [{"name": "a", "address": 0}], "address_base": 2},
            {"points": [{"address": 0}]},
            {"nopoints": 1},
        ):
            with self.assertRaises(PresetError, msg=bad):
                Preset(bad)

    def test_address_base_one(self):
        p = Preset({"address_base": 1, "points": [{"name": "a", "address": 1}]})
        self.assertEqual(p.by_name["a"].address, 0)

    def test_csv_import(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            f.write("name,address,fc,type,scale,unit,access,min,max\n"
                    "Temp,100,3,int16,0.1,C,rw,0,100\nFlag,5,1,bool,,,rw,,\n")
        try:
            d = from_csv(f.name, name="x")
        finally:
            os.unlink(f.name)
        self.assertFalse(d["verified"])
        p = Preset(d)
        p.write(self.m, "Temp", 12.3)
        self.assertEqual(self.s.holding[100], 123)


class PresetExtras(unittest.TestCase):
    def setUp(self):
        self.s = SimSlave()
        self.m = TcpMaster("127.0.0.1", self.s.port)

    def tearDown(self):
        self.m.close()
        self.s.stop()

    def test_int64_string_byteswap_writeonly(self):
        p = Preset({"points": [
            {"name": "Big", "address": 0, "type": "int64", "access": "rw"},
            {"name": "Name", "address": 10, "type": "string", "length": 4, "access": "rw"},
            {"name": "Swapped", "address": 20, "type": "uint32", "byte_swap": True, "word_order": "little", "access": "rw"},
            {"name": "Reset", "address": 30, "type": "uint16", "access": "w", "min": 0, "max": 1},
        ]})
        p.write(self.m, "Big", -5000000000)
        p.write(self.m, "Name", "PUMP-7")
        p.write(self.m, "Swapped", 0x01020304)
        self.assertEqual(self.s.holding[20:22], [0x0403, 0x0201])  # DCBA
        p.write(self.m, "Reset", 1)
        self.assertEqual(self.s.holding[30], 1)
        r = p.read(self.m)
        self.assertEqual(r["Big"]["value"], -5000000000)
        self.assertEqual(r["Name"]["text"], "PUMP-7")
        self.assertEqual(r["Swapped"]["value"], 0x01020304)
        self.assertNotIn("Reset", r)  # write-only points are never read
        with self.assertRaises(PresetError):
            p.write(self.m, "Name", "TOO-LONG-TEXT")

    def test_validation_new_fields(self):
        for bad in (
            {"points": [{"name": "a", "address": 0, "type": "string", "length": 0}]},
            {"points": [{"name": "a", "address": 0, "type": "string", "fc": 1}]},
            {"points": [{"name": "a", "address": 0, "fc": 4, "access": "w"}]},
            {"points": [{"name": "a", "address": 0, "min": 5, "max": 1}]},
        ):
            with self.assertRaises(PresetError, msg=bad):
                Preset(bad)

    def test_csv_enum_and_error_rows(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            f.write("name,address,type,enum,byte_swap\nMode,5,uint16,0=Stop;1=Run,true\n")
        d = from_csv(f.name)
        os.unlink(f.name)
        pt = Preset(d).by_name["Mode"]
        self.assertEqual(pt.enum, {"0": "Stop", "1": "Run"})
        self.assertTrue(pt.byte_swap)
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as f:
            f.write("name,address\nA,notanumber\n")
        try:
            with self.assertRaises(PresetError) as cx:
                from_csv(f.name)
            self.assertIn("row 2", str(cx.exception))
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
