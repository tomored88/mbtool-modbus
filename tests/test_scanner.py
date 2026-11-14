import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import threading
import unittest

import scanner
from modbus_core import (Master, ModbusError, ModbusException, SerialMaster, SerialSlave, SimSlave,
                         TcpMaster, VirtualCable)


class FakeBus(Master):
    """A master whose line only works at the right serial settings, like a real RS-485 bus:
    right setting -> valid reply for the right unit; wrong parity/baud -> garbage (CRC error) or silence."""

    def __init__(self, want, settings, unit=7, garbage_when=None):
        super().__init__()
        self.want, self.settings, self.unit, self.garbage_when = want, settings, unit, garbage_when
        self.closed = False

    def close(self):
        self.closed = True

    def _transact(self, unit, pdu):
        if self.settings == self.want:
            if unit == self.unit:
                if pdu[0] == 3 and int.from_bytes(pdu[1:3], "big") > 1000:
                    return bytes([0x83, 2])
                return bytes([pdu[0], 2, 0, 5])
            raise ModbusError("response timeout")
        if self.garbage_when and self.garbage_when(self.settings):
            raise ModbusError("CRC error")
        raise ModbusError("response timeout")


class SerialScan(unittest.TestCase):
    def factory(self, want, **kw):
        opened = []

        def make(b, p, s):
            m = FakeBus(want, (b, p, s), **kw)
            opened.append(m)
            return m
        return make, opened

    def test_finds_settings_and_unit_and_closes_ports(self):
        make, opened = self.factory((19200, "E", 1))
        r = scanner.scan_serial_settings(make, units=[1, 2, 7, 247])
        self.assertEqual([(f["baud"], f["parity"], f["stop"], f["unit"]) for f in r["found"]],
                         [(19200, "E", 1, 7)])
        self.assertTrue(all(m.closed for m in opened))
        self.assertLess(r["tried"], 6)  # common settings are tried first

    def test_exception_reply_counts_as_found(self):
        make, _ = self.factory((9600, "N", 1))
        r = scanner.scan_serial_settings(make, units=[7], fc=3, addr=2000)
        self.assertEqual(len(r["found"]), 1)

    def test_noise_marks_close_settings_but_does_not_stop(self):
        make, _ = self.factory((19200, "E", 1), garbage_when=lambda s: s[:2] == (9600, "E"))
        r = scanner.scan_serial_settings(make, units=[7])
        self.assertEqual(r["found"][0]["baud"], 19200)
        self.assertTrue(any(n["baud"] == 9600 and n["parity"] == "E" for n in r["noise"]))

    def test_nothing_found(self):
        make, _ = self.factory(None)
        r = scanner.scan_serial_settings(make, units=[1], bauds=[9600, 19200], parities=["N"], stops=[1])
        self.assertEqual((r["found"], r["tried"], r["total"]), ([], 2, 2))

    def test_abort_and_port_errors(self):
        ev = threading.Event()
        ev.set()
        make, _ = self.factory((9600, "N", 1))
        self.assertTrue(scanner.scan_serial_settings(make, [7], stop_event=ev)["aborted"])

        def broken(b, p, s):
            raise OSError("could not open port COM9")
        r = scanner.scan_serial_settings(broken, [1])
        self.assertEqual(len(r["errors"]), 3)  # gives up after 3 identical-looking failures
        self.assertEqual(r["found"], [])

    def test_all_flag_collects_every_unit(self):
        make, _ = self.factory((9600, "N", 1))
        r = scanner.scan_serial_settings(make, units=[1, 7], stop_on_first=False)
        self.assertEqual([f["unit"] for f in r["found"] if (f["baud"], f["parity"]) == (9600, "N")], [7])

    def test_parse_ranges(self):
        self.assertEqual(scanner.parse_ranges("1-3,247,2"), [1, 2, 3, 247])
        for bad in ("", "5-1", "300", "x"):
            with self.assertRaises(ValueError):
                scanner.parse_ranges(bad, 0, 247)


class OverRealFrames(unittest.TestCase):
    """Slave IDs, register ranges and device id against the real RTU/TCP code paths (no hardware)."""

    def test_unit_scan_over_rtu_cable(self):
        cable = VirtualCable()
        slave = SerialSlave(cable.url, unit=5, baud=19200, parity="E")
        m = SerialMaster(cable.url, 19200, "E", timeout=0.15)
        try:
            r = scanner.scan_unit_ids(m, [1, 2, 3, 4, 5, 6])
            self.assertEqual(r["found"], [5])
        finally:
            m.close()
            slave.stop()
            cable.close()

    def test_register_ranges_find_exact_boundaries(self):
        class Limited(SimSlave):
            def handle(self, pdu):
                if pdu[0] == 3:
                    a = int.from_bytes(pdu[1:3], "big")
                    q = int.from_bytes(pdu[3:5], "big")
                    ok = lambda x: 10 <= x <= 37 or 100 <= x <= 103
                    if not all(ok(x) for x in range(a, a + q)):
                        return bytes([0x83, 2])
                return super().handle(pdu)
        s = Limited()
        m = TcpMaster("127.0.0.1", s.port, timeout=0.5)
        try:
            r = scanner.scan_register_ranges(m, 1, 3, 0, 199, block=16)
            self.assertEqual(r["ranges"], [[10, 37], [100, 103]])
            self.assertEqual(r["timeouts"], 0)
        finally:
            m.close()
            s.stop()

    def test_request_cap(self):
        s = SimSlave()
        m = TcpMaster("127.0.0.1", s.port, timeout=0.5)
        try:
            r = scanner.scan_register_ranges(m, 1, 3, 0, 5000, block=1, max_requests=20)
            self.assertTrue(r["truncated"])
            self.assertEqual(r["requests"], 20)
        finally:
            m.close()
            s.stop()


class Identify(unittest.TestCase):
    def test_device_id_and_preset_suggestion(self):
        class IdSlave(SimSlave):
            def handle(self, pdu):
                if pdu[0] == 43:
                    objs = [(0, b"Eastron"), (1, b"SDM120"), (2, b"V2.4")]
                    body = b"".join(bytes([i, len(v)]) + v for i, v in objs)
                    return bytes([43, 14, 1, 1, 0, 0, len(objs)]) + body
                if pdu[0] == 17:
                    return bytes([17, 5]) + b"\x01\xffSDM"
                return super().handle(pdu)
        s = IdSlave()
        m = TcpMaster("127.0.0.1", s.port, timeout=0.5)
        try:
            info = scanner.read_device_id(m, 1)
            self.assertEqual((info["VendorName"], info["ProductCode"]), ("Eastron", "SDM120"))
            self.assertIn("ReportSlaveId", info)
            sug = scanner.suggest_presets(info)
            self.assertTrue(sug and "sdm120" in sug[0]["preset"].lower(), sug)
        finally:
            m.close()
            s.stop()

    def test_unsupported_returns_empty(self):
        s = SimSlave()  # answers exception 1 to FC43 / FC17
        m = TcpMaster("127.0.0.1", s.port, timeout=0.5)
        try:
            self.assertEqual(scanner.read_device_id(m, 1), {})
        finally:
            m.close()
            s.stop()


if __name__ == "__main__":
    unittest.main()
