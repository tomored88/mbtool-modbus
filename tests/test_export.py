"""export.py: MQTT 3.1.1 publish client against a small local broker stand-in (real protocol bytes on a socket),
both payload styles, broker outage with backlog, auth refusal, SQLite rows."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import json
import os
import socket
import sqlite3
import struct
import tempfile
import threading
import time
import unittest

import export


class FakeBroker:
    """Accepts MQTT clients, answers CONNECT with CONNACK (rc configurable), PINGREQ with PINGRESP, records PUBLISH."""

    def __init__(self, rc=0, port=0):
        self.rc = rc
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        self.msgs, self.connects = [], []
        self.stop_flag = False
        self.conns = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stop_flag:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    @staticmethod
    def _read(c, n):
        b = b""
        while len(b) < n:
            x = c.recv(n - len(b))
            if not x:
                raise EOFError
            b += x
        return b

    def _serve(self, c):
        try:
            while True:
                h = self._read(c, 1)[0]
                mult, n = 1, 0
                while True:
                    d = self._read(c, 1)[0]
                    n += (d & 0x7F) * mult
                    mult *= 128
                    if not d & 0x80:
                        break
                body = self._read(c, n) if n else b""
                typ = h >> 4
                if typ == 1:
                    ln = struct.unpack(">H", body[:2])[0]
                    flags = body[2 + ln + 1]
                    p = 2 + ln + 4
                    cid_len = struct.unpack(">H", body[p:p + 2])[0]
                    cid = body[p + 2:p + 2 + cid_len].decode()
                    user = None
                    if flags & 0x80:
                        q = p + 2 + cid_len
                        ul = struct.unpack(">H", body[q:q + 2])[0]
                        user = body[q + 2:q + 2 + ul].decode()
                    self.connects.append((cid, user))
                    c.sendall(bytes([0x20, 2, 0, self.rc]))
                    if self.rc:
                        c.close()
                        return
                elif typ == 3:
                    tl = struct.unpack(">H", body[:2])[0]
                    self.msgs.append((body[2:2 + tl].decode(), body[2 + tl:].decode(), bool(h & 1)))
                elif typ == 12:
                    c.sendall(b"\xd0\x00")
                elif typ == 14:
                    c.close()
                    return
        except (EOFError, OSError):
            pass

    def kick(self):
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        self.conns = []

    def close(self):
        self.stop_flag = True
        self.kick()
        self.srv.close()


def wait(cond, sec=5):
    end = time.time() + sec
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def cfg(port, **mq):
    c = export.norm_export({})
    c["mqtt"].update(on=True, host="127.0.0.1", port=str(port), prefix="plant/line3", **mq)
    return c


class Mqtt(unittest.TestCase):
    def test_json_style(self):
        b = FakeBroker()
        try:
            ex = export.Exporter(cfg(b.port, retain=True, user="u1", client_id="cid1"), password="pw")
            ex.start()
            ex.submit("Meter/1", time.time(), {"Voltage": 230.5, "Run": True, "Bad": float("nan")},
                      {"Voltage": "230.5 V"}, True)
            ex.submit("Meter/1", time.time(), {}, {}, False, "response timeout")
            self.assertTrue(wait(lambda: len(b.msgs) >= 2), b.msgs)
            ex.stop()
            self.assertEqual(b.connects[0], ("cid1", "u1"))
            topic, payload, retain = b.msgs[0]
            self.assertEqual(topic, "plant/line3/Meter_1")
            self.assertTrue(retain)
            d = json.loads(payload)
            self.assertEqual(d["values"], {"Voltage": 230.5, "Run": True, "Bad": "nan"})
            self.assertTrue(d["ok"])
            self.assertEqual(json.loads(b.msgs[1][1])["error"], "response timeout")
            self.assertEqual(ex.stats["mqtt_sent"], 2)
        finally:
            b.close()

    def test_topics_style_and_min_interval(self):
        b = FakeBroker()
        try:
            ex = export.Exporter(cfg(b.port, style="topics", min_interval_s="60"))
            ex.start()
            ex.submit("Drive", time.time(), {"Hz": 50.0, "State": 3}, {"State": "Run"}, True)
            self.assertTrue(wait(lambda: len(b.msgs) >= 3), b.msgs)
            ex.submit("Drive", time.time(), {"Hz": 49.0}, {}, True)  # within min interval -> skipped
            self.assertTrue(wait(lambda: ex.stats["skipped"] == 1))
            ex.stop()
            topics = {t: p for t, p, _r in b.msgs}
            self.assertEqual(topics["plant/line3/Drive/Hz"], "50.0")
            self.assertEqual(topics["plant/line3/Drive/State"], "3")
            self.assertTrue(json.loads(topics["plant/line3/Drive/status"])["ok"])
        finally:
            b.close()

    def test_outage_backlog_and_reconnect(self):
        b = FakeBroker()
        port = b.port
        try:
            ex = export.Exporter(cfg(port))
            ex.start()
            ex.submit("A", time.time(), {"x": 1}, {}, True)
            self.assertTrue(wait(lambda: len(b.msgs) == 1))
            b.close()  # broker goes away
            for i in range(3):
                ex.submit("A", time.time(), {"x": i + 2}, {}, True)
                time.sleep(0.05)
            self.assertTrue(wait(lambda: ex.stats["mqtt_errors"] >= 1, 8), ex.stats)
            b2 = FakeBroker(port=port)
            try:
                self.assertTrue(wait(lambda: len(b2.msgs) >= 1, 15), (ex.stats, b2.msgs))
                ex.stop()
                xs = [json.loads(p)["values"]["x"] for _t, p, _r in b2.msgs]
                self.assertEqual(xs[-1], 4)
            finally:
                b2.close()
        finally:
            b.close()

    def test_refused_and_test_connection(self):
        b = FakeBroker(rc=4)
        try:
            err = export.test_mqtt(cfg(b.port, user="x"), "bad")
            self.assertIn("bad user name or password", err)
        finally:
            b.close()
        b = FakeBroker()
        try:
            self.assertIsNone(export.test_mqtt(cfg(b.port)))
            self.assertTrue(wait(lambda: b.msgs))
            self.assertEqual(b.msgs[0][0], "plant/line3/_test")
        finally:
            b.close()

    def test_validation(self):
        c = export.norm_export({"mqtt": {"host": "", "port": "99999", "prefix": "a/#", "min_interval_s": "x",
                                         "password": "never"}})
        self.assertNotIn("password", c["mqtt"])
        errs = export.check_mqtt(c["mqtt"])
        self.assertEqual(set(errs), {"host", "port", "prefix", "min_interval_s"})
        with self.assertRaises(export.ExportError):
            export.Exporter(export.norm_export({}))  # nothing switched on
        self.assertEqual(export.topic_part("a/b+c#d"), "a_b_c_d")


class Sqlite(unittest.TestCase):
    def test_rows(self):
        path = os.path.join(tempfile.mkdtemp(), "data.db")
        c = export.norm_export({"sqlite": {"on": True, "path": path}})
        ex = export.Exporter(c)
        ex.start()
        t = time.time()
        ex.submit("M", t, {"V": 230.1, "On": True, "Name": "abc"}, {"Name": "abc"}, True)
        ex.submit("M", t + 1, {}, {}, False, "CRC error")
        self.assertTrue(wait(lambda: ex.stats["sql_rows"] == 4), ex.stats)
        ex.stop()
        db = sqlite3.connect(path)
        rows = db.execute("SELECT device, point, value, text, ok FROM readings ORDER BY ts, point").fetchall()
        db.close()
        self.assertIn(("M", "V", 230.1, None, 1), rows)
        self.assertIn(("M", "On", 1.0, None, 1), rows)
        self.assertIn(("M", "Name", None, "abc", 1), rows)
        self.assertIn(("M", "", None, "CRC error", 0), rows)

    def test_bad_folder(self):
        c = export.norm_export({"sqlite": {"on": True, "path": r"Z:\no\such\folder\x.db"}})
        with self.assertRaises(export.ExportError):
            export.Exporter(c)


if __name__ == "__main__":
    unittest.main(verbosity=0)
