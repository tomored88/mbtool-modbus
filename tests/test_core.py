import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import socket
import struct
import threading
import time
import unittest

from modbus_core import *
from modbus_core import _recv_exact


class Core(unittest.TestCase):
    def test_crc_known_vector(self):
        # Standard example: 01 03 00 00 00 0A -> CRC C5CD (low byte first on wire: C5 CD)
        self.assertEqual(crc16(bytes.fromhex("01030000000A")), 0xCDC5)

    def test_lrc(self):
        self.assertEqual(lrc(bytes.fromhex("010300000001")), 0xFB)

    def test_crc_matches_pymodbus(self):
        from pymodbus.framer.rtu import FramerRTU as F
        for d in (b"\x11\x03\x00\x6b\x00\x03", bytes(range(50))):
            c = F.compute_CRC(d)  # pymodbus returns the wire-order (byte-swapped) value
            self.assertEqual(crc16(d), ((c & 0xFF) << 8) | (c >> 8))

    def test_formats_roundtrip(self):
        for fmt, txt in [("Long", "-123456"), ("Long Inverse", "70000"),
                         ("Float", "3.5"), ("Float Inverse", "-0.25"),
                         ("Double", "1234.5678"), ("Double Inverse", "-9.75")]:
            words = encode_value(txt, fmt)
            back = format_registers(words, fmt)[0][1]
            self.assertAlmostEqual(float(back), float(txt), places=4, msg=fmt)

    def test_tcp_all_functions(self):
        s = SimSlave(unit=7)
        m = TcpMaster("127.0.0.1", s.port)
        try:
            m.write_register(7, 10, 1234)
            self.assertEqual(m.read_holding(7, 10, 1), [1234])
            m.write_registers(7, 20, [1, 2, 3])
            self.assertEqual(m.read_holding(7, 20, 3), [1, 2, 3])
            m.write_coil(7, 5, True)
            m.write_coils(7, 6, [True, False, True])
            self.assertEqual(m.read_coils(7, 5, 4), [True, True, False, True])
            s.input[3] = 42
            s.discrete[1] = True
            self.assertEqual(m.read_input(7, 3, 1), [42])
            self.assertEqual(m.read_discrete(7, 0, 3), [False, True, False])
            m.write_register(7, 30, 0x12F0)
            m.mask_write(7, 30, 0x00F2, 0x0025)
            self.assertEqual(m.read_holding(7, 30, 1), [0x12F0 & 0x00F2 | 0x0025 & ~0x00F2 & 0xFFFF])
            self.assertEqual(m.read_write(7, 40, 2, 40, [9, 8]), [9, 8])
            with self.assertRaises(ModbusException) as cx:
                m.read_holding(7, 65535, 5)
            self.assertEqual(cx.exception.code, 2)
        finally:
            m.close()
            s.stop()

    def test_pymodbus_client_against_simslave(self):
        """Independent implementation talking to our slave."""
        from pymodbus.client import ModbusTcpClient
        s = SimSlave()
        s.holding[100:103] = [11, 22, 33]
        c = ModbusTcpClient("127.0.0.1", port=s.port)
        try:
            self.assertTrue(c.connect())
            r = c.read_holding_registers(100, count=3, slave=1)
            self.assertEqual(list(r.registers), [11, 22, 33])
        finally:
            c.close()
            s.stop()

    def test_request_bytes_equal_pymodbus(self):
        from pymodbus.pdu import register_read_message as rr
        req = rr.ReadHoldingRegistersRequest(address=107, count=3)
        self.assertEqual(req.encode(), build_request(3, addr=107, qty=3)[1:])

class Regression(unittest.TestCase):
    def setUp(self):
        self.s = SimSlave()

    def tearDown(self):
        self.s.stop()

    def test_slave_range_and_count_checks(self):
        h = self.s.handle
        self.assertEqual(h(struct.pack(">BHHHHB", 23, 0, 1, 65535, 2, 4) + bytes(4)), bytes([0x97, 2]))
        self.assertEqual(len(self.s.holding), 65536)
        self.assertEqual(h(struct.pack(">BHH", 3, 65535, 5)), bytes([0x83, 2]))
        self.assertEqual(h(struct.pack(">BHH", 3, 0, 0)), bytes([0x83, 3]))
        self.assertEqual(h(struct.pack(">BHH", 3, 0, 126)), bytes([0x83, 3]))
        self.assertEqual(h(struct.pack(">BHHB", 16, 0, 0, 0)), bytes([0x90, 3]))
        self.assertEqual(h(struct.pack(">BHHB", 16, 0, 124, 248) + bytes(248)), bytes([0x90, 3]))
        self.assertEqual(h(struct.pack(">BHHB", 15, 0, 8, 2) + bytes(2)), bytes([0x8F, 3]))
        self.assertEqual(h(b""), bytes([0x80, 1]))
        self.assertEqual(len(h(struct.pack(">BHHH", 22, 0, 0xFF, 0) + b"xx")), 7)

    def test_empty_pdu_does_not_kill_connection(self):
        c = socket.create_connection(("127.0.0.1", self.s.port), timeout=1)
        c.sendall(struct.pack(">HHHB", 1, 0, 1, 1))
        self.assertEqual(c.recv(64)[-2:], bytes([0x80, 1]))
        c.close()

    def test_parse_response_malformed(self):
        req = build_request(3, addr=0, qty=2)
        for bad in (bytes([0x83]), bytes([3]), bytes([3, 3, 0, 1, 0]), bytes([3, 2, 0, 1])):
            with self.assertRaises(ModbusError):
                parse_response(req, bad)
        with self.assertRaises(ModbusError):
            parse_response(build_request(6, addr=1, values=[5]), struct.pack(">BHH", 6, 1, 6))

    def test_tcp_resync_after_timeout(self):
        slow = socket.socket()
        slow.bind(("127.0.0.1", 0))
        slow.listen(1)

        def srv():
            c, _ = slow.accept()
            first = True
            while True:
                try:
                    tid, _p, ln, u = struct.unpack(">HHHB", _recv_exact(c, 7))
                    _recv_exact(c, ln - 1)
                except Exception:
                    return
                if first:
                    first = False
                    time.sleep(0.5)
                c.sendall(struct.pack(">HHHB", tid, 0, 5, u) + bytes([3, 2, 0, tid]))
        threading.Thread(target=srv, daemon=True).start()
        m = TcpMaster("127.0.0.1", slow.getsockname()[1], timeout=0.2)
        with self.assertRaises(ModbusError):
            m.read_holding(1, 0, 1)
        time.sleep(0.5)
        self.assertEqual(m.read_holding(1, 0, 1), [2])  # stale tid 1 discarded, tid 2 answered
        m.close()
        slow.close()

    def test_stop_closes_connections(self):
        m = TcpMaster("127.0.0.1", self.s.port)
        self.assertEqual(m.read_holding(1, 0, 1), [0])
        self.s.stop()
        with self.assertRaises(ModbusError):
            m.read_holding(1, 0, 1)

    def test_exclusive_port(self):
        with self.assertRaises(OSError):
            SimSlave(port=self.s.port)

    def test_encode_range_errors(self):
        for fmt, txt in (("Unsigned", "70000"), ("Signed", "-70000"), ("Long", str(2 ** 40)), ("Float", "1e40")):
            with self.assertRaises(ValueError):
                encode_value(txt, fmt)

    def test_float_text_roundtrips(self):
        for fmt in ("Float", "Double"):
            w = encode_value("3.14159265", fmt)
            self.assertEqual(w, encode_value(format_registers(w, fmt)[0][1], fmt))


if __name__ == "__main__":
    unittest.main()
