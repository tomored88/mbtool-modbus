"""Regression: many slaves on one RS-485 line polled back to back (no gap). Each slave also sees the OTHER slaves'
answers; it must cut frames at their protocol length so an answer glued to the next request is not mistaken for one
long broken frame (that made every second request time out when the gap was under ~40 ms)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import unittest

import load_bus
from modbus_core import _rtu_complete_len, crc16
import struct


def frame(b):
    return b + struct.pack("<H", crc16(b))


class T(unittest.TestCase):
    def test_complete_length_of_requests_answers_and_glued_frames(self):
        req = frame(bytes([1, 3, 0, 0, 0, 10]))
        ans = frame(bytes([2, 3, 4, 0, 1, 0, 2]))
        exc = frame(bytes([3, 0x83, 2]))
        w16 = frame(bytes([1, 16, 0, 5, 0, 2, 4, 0, 1, 0, 2]))
        self.assertEqual(_rtu_complete_len(req), 8)
        self.assertEqual(_rtu_complete_len(ans), 9)
        self.assertEqual(_rtu_complete_len(exc), 5)
        self.assertEqual(_rtu_complete_len(w16), len(w16))
        self.assertEqual(_rtu_complete_len(ans + req), 9)  # an answer glued to the next request
        self.assertEqual(_rtu_complete_len(req[:5]), 0)  # incomplete
        self.assertEqual(_rtu_complete_len(b"\x01\x03\x00\x00\x00\x0a\x00\x00"), 0)  # bad CRC

    def test_ten_slaves_back_to_back_no_errors(self):
        res = load_bus.run(10, 115200, 3, gaps=(0.0,), qty=6, delay=0.0)
        self.assertEqual(res[0]["errors"], 0, res)
        self.assertEqual(res[0]["wrong_data"], 0, res)


if __name__ == "__main__":
    unittest.main(verbosity=0)
