"""One COM port, many users: Poll 1, poll tabs and devices share one SerialMaster and take turns (PortHub)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest

from modbus_core import HUB, ModbusError, PortHub, SerialMaster, SerialSlave, SimSlave, VirtualCable


class Hub(unittest.TestCase):
    def setUp(self):
        self.cable = VirtualCable()
        self.store = SimSlave(listen=False)
        for i in range(30):
            self.store.holding[i] = 500 + i
        self.slave = SerialSlave(self.cable.url, 1, 9600, "N", store=self.store)
        self.hub = PortHub()

    def tearDown(self):
        self.slave.stop()
        self.cable.close()

    def opener(self):
        return lambda: SerialMaster(self.cable.url, 9600, "N", 1, 8, "rtu", 1.0)

    def test_second_user_shares_the_open_port(self):
        a = self.hub.acquire(self.cable.url, (9600, "N"), self.opener())
        b = self.hub.acquire(self.cable.url, (9600, "N"), self.opener())  # would be 'port busy' without the hub
        self.assertEqual(self.hub.users(self.cable.url), 2)
        self.assertEqual(a.read_holding(1, 0, 3), [500, 501, 502])
        self.assertEqual(b.read_holding(1, 3, 2), [503, 504])
        self.assertIs(a._m, b._m)
        a.close()
        a.close()  # closing twice must not release twice
        self.assertEqual(self.hub.users(self.cable.url), 1)
        self.assertEqual(b.read_holding(1, 0, 1), [500])  # still open for the other user
        b.close()
        self.assertEqual(self.hub.users(self.cable.url), 0)

    def test_port_reopens_after_everyone_left(self):
        a = self.hub.acquire(self.cable.url, (9600, "N"), self.opener())
        first = a._m
        a.close()
        b = self.hub.acquire(self.cable.url, (9600, "N"), self.opener())
        self.assertIsNot(b._m, first)
        self.assertEqual(b.read_holding(1, 0, 1), [500])
        b.close()

    def test_different_settings_are_refused_with_a_clear_message(self):
        a = self.hub.acquire(self.cable.url, (9600, "N"), self.opener())
        try:
            with self.assertRaises(ModbusError) as cm:
                self.hub.acquire(self.cable.url, (19200, "E"), self.opener())
            self.assertIn("different settings", str(cm.exception))
            self.assertEqual(self.hub.users(self.cable.url), 1)
        finally:
            a.close()

    def test_requests_from_several_threads_take_turns(self):
        users = [self.hub.acquire(self.cable.url, (9600, "N"), self.opener()) for _ in range(3)]
        errors, results = [], []

        def work(u, base):
            try:
                for _ in range(15):
                    results.append(u.read_holding(1, base, 2) == [500 + base, 501 + base])
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        ts = [threading.Thread(target=work, args=(u, 4 * i)) for i, u in enumerate(users)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        for u in users:
            u.close()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 45)
        self.assertTrue(all(results))  # no reply ever ended up with the wrong requester

    def test_attributes_and_setattr_reach_the_shared_master(self):
        a = self.hub.acquire(self.cable.url, (9600, "N"), self.opener())
        try:
            self.assertTrue(hasattr(a, "traffic") and hasattr(a, "stats"))
            a.t35 = 0.01
            self.assertEqual(a._m.t35, 0.01)
        finally:
            a.close()

    def test_key_normalisation(self):
        self.assertEqual(PortHub.key("com3 "), "COM3")
        self.assertEqual(PortHub.key("socket://127.0.0.1:7000"), "socket://127.0.0.1:7000")


class MainAndTabs(unittest.TestCase):
    """The case from the user: Poll 1 is connected to a COM port and a poll tab uses the same port."""

    @classmethod
    def setUpClass(cls):
        import mbtool as MB
        cls.MB = MB
        cls.dir = tempfile.mkdtemp()
        cls.saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE = os.path.join(cls.dir, "settings.json")
        MB.HOME_SETTINGS = os.path.join(cls.dir, "home_settings.json")
        cls.cable = VirtualCable()
        cls.s1, cls.s2 = SimSlave(listen=False), SimSlave(listen=False)
        for i in range(20):
            cls.s1.holding[i], cls.s2.holding[i] = 100 + i, 9000 + i
        cls.sl1 = SerialSlave(cls.cable.url, 1, 19200, "E", store=cls.s1)
        cls.sl2 = SerialSlave(cls.cable.url, 2, 19200, "E", store=cls.s2)
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = app = MB.MasterApp(cls.root)
        app.cfg["conn"].update(mode="RTU", com=cls.cable.url, baud="19200", parity="E", stop="1", data="8",
                               timeout="1000")
        app.cfg["devices"]["gap_ms"] = "50"
        app.devpoll.gap_s = 0.05
        app.pv["func"].set(MB.FUNCS[3])
        app.set_addr_base(0)
        app.pv["unit"].set("1")
        app.pv["addr"].set("0")
        app.pv["qty"].set("5")
        app.pv["scan"].set("300")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.stop_all_devices()
            cls.app.disconnect(quiet=True)
            cls.root.destroy()
            cls.sl1.stop()
            cls.sl2.stop()
            cls.cable.close()
        finally:
            cls.MB.SETTINGS_FILE, cls.MB.HOME_SETTINGS = cls.saved

    def wait(self, cond, sec=15.0):
        end = time.time() + sec
        while time.time() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_poll1_and_a_tab_poll_the_same_com_port_in_turns(self):
        app = self.app
        app.connect()
        self.assertTrue(self.wait(lambda: app.state == "on"), "Poll 1 could not connect")
        self.assertTrue(self.wait(lambda: app.grid.values and app.grid.values[0] == "100"))
        name = app.new_poll_tab()
        tab = app.poll_tabs[name]
        tab.pv["unit"].set("2")
        tab.pv["addr"].set("0")
        tab.pv["qty"].set("5")
        tab.pv["scan"].set("300")
        tab.flush()
        tab.start()
        # the tab reads device 2 although Poll 1 holds the port (before: 'busy')
        self.assertTrue(self.wait(lambda: app.dev_status[name].ok >= 2), app.dev_status[name].last_error)
        self.assertEqual(app.dev_status[name].errors, 0, app.dev_status[name].last_error)
        self.assertEqual(tab.grid.values[0], "9000")
        # ... and Poll 1 keeps reading device 1 meanwhile
        s1 = self.s1
        s1.holding[0] = 4242
        self.assertTrue(self.wait(lambda: app.grid.values[0] == "4242"))
        self.assertEqual(HUB.users(self.cable.url), 2)
        app.close_poll_tab(name)
        self.assertTrue(self.wait(lambda: HUB.users(self.cable.url) == 1))
        self.assertEqual(app.state, "on")  # closing the tab did not close Poll 1's port
        app.disconnect(quiet=True)
        self.assertTrue(self.wait(lambda: HUB.users(self.cable.url) == 0))


if __name__ == "__main__":
    unittest.main(verbosity=0)
