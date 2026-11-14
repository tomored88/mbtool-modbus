"""RTU master <-> RTU slave over a software null-modem (no COM driver needed)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import time
import unittest

from modbus_core import (ModbusException, ModbusError, SerialMaster, SerialSlave, SimSlave,
                         VirtualCable)


class RtuOverVirtualCable(unittest.TestCase):
    def setUp(self):
        self.cable = VirtualCable()
        self.store = SimSlave(listen=False)
        self.slave = SerialSlave(self.cable.url, unit=5, baud=19200, parity="E", store=self.store)
        time.sleep(0.2)
        self.m = SerialMaster(self.cable.url, 19200, "E", timeout=1.0)

    def tearDown(self):
        self.m.close()
        self.slave.stop()
        self.cable.close()

    def test_all_function_codes(self):
        m, st = self.m, self.store
        m.write_register(5, 10, 1234)
        self.assertEqual(m.read_holding(5, 10, 1), [1234])
        m.write_registers(5, 20, [1, 2, 3, 40000])
        self.assertEqual(m.read_holding(5, 20, 4), [1, 2, 3, 40000])
        m.write_coil(5, 3, True)
        m.write_coils(5, 4, [True, False, True, True, False, True, True, True, True])
        self.assertEqual(m.read_coils(5, 3, 10)[:4], [True, True, False, True])
        st.input[7] = 999
        st.discrete[2] = True
        self.assertEqual(m.read_input(5, 7, 1), [999])
        self.assertEqual(m.read_discrete(5, 1, 3), [False, True, False])
        m.write_register(5, 30, 0x12F0)
        m.mask_write(5, 30, 0x00F2, 0x0025)
        self.assertEqual(m.read_holding(5, 30, 1), [0x12F0 & 0x00F2 | 0x0025 & ~0x00F2 & 0xFFFF])
        self.assertEqual(m.read_write(5, 40, 2, 40, [9, 8]), [9, 8])

    def test_large_read(self):
        self.store.holding[:125] = list(range(125))
        self.assertEqual(self.m.read_holding(5, 0, 125), list(range(125)))

    def test_exception_and_wrong_unit(self):
        with self.assertRaises(ModbusException) as cx:
            self.m.read_holding(5, 65535, 5)
        self.assertEqual(cx.exception.code, 2)
        with self.assertRaises(ModbusError):
            self.m.read_holding(6, 0, 1)  # nobody answers unit 6 -> timeout

    def test_broadcast_does_not_wait_and_is_applied(self):
        t = time.time()
        self.assertIsNone(self.m.write_register(0, 50, 77))
        self.assertLess(time.time() - t, 0.5)
        time.sleep(0.2)
        self.assertEqual(self.store.holding[50], 77)

    def test_many_back_to_back_requests(self):
        for i in range(200):
            self.m.write_register(5, 100, i)
            self.assertEqual(self.m.read_holding(5, 100, 1), [i])

    def test_rtu_master_waits_t35_between_frames(self):
        m = self.m
        m.t35 = 0.15  # exaggerated so the gap is measurable
        m.read_holding(5, 0, 1)
        t = time.monotonic()
        m._last_activity = time.monotonic()
        sent = []
        orig = m.ser.write
        m.ser.write = lambda b: (sent.append(time.monotonic()), orig(b))[1]
        m.read_holding(5, 0, 1)
        self.assertGreaterEqual(sent[0] - t, 0.14)


class AsciiAndSlaveLog(unittest.TestCase):
    def test_ascii_master_slave_and_slave_traffic(self):
        cable = VirtualCable()
        store = SimSlave(listen=False)
        slave = SerialSlave(cable.url, unit=3, baud=9600, parity="N", store=store, mode="ascii")
        time.sleep(0.2)
        m = SerialMaster(cable.url, 9600, "N", 1, 8, "ascii", timeout=1.0)
        try:
            m.write_registers(3, 10, [1, 2, 3])
            self.assertEqual(m.read_holding(3, 10, 3), [1, 2, 3])
            m.write_coils(3, 0, [True, False, True])
            self.assertEqual(m.read_coils(3, 0, 3), [True, False, True])
            with self.assertRaises(ModbusException):
                m.read_holding(3, 65535, 5)
            log = list(store.traffic)
            self.assertEqual([e["dir"] for e in log[:2]], ["RX", "TX"])
            self.assertIn("Write Multiple Registers", log[0]["text"])
            self.assertTrue(any("EXCEPTION" in e["text"] for e in log))
        finally:
            m.close()
            slave.stop()
            cable.close()

    def test_tcp_slave_traffic(self):
        from modbus_core import TcpMaster
        s = SimSlave()
        m = TcpMaster("127.0.0.1", s.port)
        try:
            m.read_input(1, 0, 2)
            self.assertEqual([e["dir"] for e in s.traffic], ["RX", "TX"])
        finally:
            m.close()
            s.stop()


if __name__ == "__main__":
    unittest.main()
