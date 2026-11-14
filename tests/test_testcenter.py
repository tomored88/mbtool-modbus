import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import unittest

import testcenter
from modbus_core import SimSlave, TcpMaster


class FaultySlave(SimSlave):
    """Breaks several rules on purpose so the Test Center must report FAIL."""

    def handle(self, pdu):
        if pdu and pdu[0] == 3 and len(pdu) >= 5 and pdu[3:5] == b"\x00\x00":
            return bytes([3, 0])                       # accepts quantity 0 instead of exception 3
        if pdu and pdu[0] == 0x41:
            return bytes([0x41, 0])                    # answers an unknown function
        if pdu and pdu[0] == 6:
            super().handle(pdu)
            return bytes([6, 0, 0, 0, 0])              # wrong echo
        return super().handle(pdu)


class TestCenter(unittest.TestCase):
    def run_against(self, slave_cls, **kw):
        s = slave_cls()
        s.holding[:8] = [10, 11, 12, 13, 14, 15, 16, 17]
        m = TcpMaster("127.0.0.1", s.port, timeout=0.5)
        try:
            return testcenter.run(m, 1, 0, repeat=5, **kw), s
        finally:
            m.close()
            s.stop()

    def test_conforming_slave_has_no_failures(self):
        res, _ = self.run_against(SimSlave)
        self.assertEqual([str(r) for r in res if r.status == testcenter.FAIL], [])
        self.assertGreater(sum(r.status == testcenter.PASS for r in res), 15)

    def test_write_tests_pass_and_restore_values(self):
        res, s = self.run_against(SimSlave, write=True, scratch=20)
        self.assertEqual([str(r) for r in res if r.status == testcenter.FAIL], [])
        self.assertTrue(any("FC22" in r.name and r.status == testcenter.PASS for r in res))
        self.assertTrue(any("FC23" in r.name and r.status == testcenter.PASS for r in res))
        self.assertEqual(s.holding[20:24], [0, 0, 0, 0])
        self.assertEqual(s.coils[20:24], [False] * 4)

    def test_faulty_slave_is_caught(self):
        res, _ = self.run_against(FaultySlave, write=True, scratch=20)
        failed = " | ".join(r.name for r in res if r.status == testcenter.FAIL)
        self.assertIn("quantity 0", failed)
        self.assertIn("unknown function", failed)
        self.assertTrue(any(r.status == testcenter.FAIL and "echo" in r.detail for r in res), failed)

    def test_read_only_mode_never_writes(self):
        res, s = self.run_against(SimSlave)
        self.assertEqual(s.holding[20:24], [0, 0, 0, 0])
        self.assertTrue(any(r.status == testcenter.SKIP and "write tests" in r.name for r in res))


if __name__ == "__main__":
    unittest.main()
