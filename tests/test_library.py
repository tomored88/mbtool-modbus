"""Every preset in presets/ must load, carry sources, be flagged unverified unless a verified_note exists,
and read back cleanly from a simulator filled from it."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import glob
import os
import unittest

from modbus_core import SimSlave, TcpMaster
from preset import load_preset

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = sorted(glob.glob(os.path.join(HERE, "presets", "**", "*.json"), recursive=True))


class Library(unittest.TestCase):
    def test_library_not_empty(self):
        self.assertGreaterEqual(len(FILES), 10)

    def test_each_preset(self):
        for f in FILES:
            with self.subTest(preset=os.path.relpath(f, HERE)):
                p = load_preset(f)
                if "library" in f:
                    self.assertTrue(os.path.getsize(f) > 0)
                    import json
                    d = json.load(open(f, encoding="utf-8"))
                    self.assertTrue(d.get("source"), "library presets must cite sources")
                    if p.verified:
                        self.assertTrue(d.get("verified_note"), "verified presets need a verified_note")
                s = SimSlave()
                m = TcpMaster("127.0.0.1", s.port)
                try:
                    p.fill_simulator(s)
                    res = p.read(m)
                    errs = {k: v["error"] for k, v in res.items() if "error" in v}
                    self.assertEqual(errs, {})
                finally:
                    m.close()
                    s.stop()


if __name__ == "__main__":
    unittest.main()
