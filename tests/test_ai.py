"""AI-facing pieces: diagnostic report, the HTTP guard (token, lock-out, audit) and the restartable in-process server."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import asyncio
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import ai_report
import mbtool_mcp as M
from modbus_core import ModbusError, SimSlave, TcpMaster
from preset import load_preset

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "x" * 32


class Report(unittest.TestCase):
    def test_report_with_preset_and_checklist(self):
        sim = SimSlave()
        pr = load_preset(os.path.join(HERE, "presets", "example_generic_meter.json"))
        pr.fill_simulator(sim)
        m = TcpMaster("127.0.0.1", sim.port, timeout=0.3)
        try:
            with self.assertRaises(ModbusError):
                m.read_holding(1, 65535, 5)  # produce an exception 2 in the traffic log
            rep = ai_report.build_report(m, 1, 3, 0, 6, samples=2, interval_s=0.01, preset=pr, url="tcp://test")
            self.assertEqual(rep["samples"]["ok"], 2)
            self.assertEqual(rep["last_values"][0], 0x4366)
            self.assertTrue(all(rep["stable"]))
            self.assertIn("exc2", rep["error_kinds"])
            self.assertTrue(any("off by one" in c for c in rep["checklist"]))
            self.assertIn("UNVERIFIED", rep["notes"][0])
            self.assertEqual(rep["preset"]["points"]["Mode"], "Run")
            md = ai_report.to_markdown(rep)
            for want in ("# Modbus diagnostic report", "Recent traffic", "Suggested checks", "not instructions"):
                self.assertIn(want, md)
        finally:
            m.close()
            sim.stop()

    def test_report_on_dead_device_suggests_wiring_checks(self):
        sim = SimSlave()
        m = TcpMaster("127.0.0.1", sim.port, timeout=0.2)
        sim.stop()
        m.close()
        m2 = TcpMaster.__new__(TcpMaster)  # master whose requests always time out
        from modbus_core import Master
        Master.__init__(m2)
        m2._transact = lambda unit, pdu: (_ for _ in ()).throw(ModbusError("response timeout"))
        rep = ai_report.build_report(m2, 1, 3, 0, 4, samples=2, interval_s=0.0, identify=False)
        self.assertEqual(rep["samples"]["ok"], 0)
        self.assertTrue(any("baud" in c for c in rep["checklist"]))


class HttpServer(unittest.TestCase):
    def setUp(self):
        self.sim = SimSlave()
        load_preset(os.path.join(HERE, "presets", "example_generic_meter.json")).fill_simulator(self.sim)
        self.master = TcpMaster("127.0.0.1", self.sim.port, timeout=0.5)
        M.attach_master(self.master, "tcp://shared")
        self.audit = os.path.join(tempfile.mkdtemp(), "audit.log")
        self.port = 8811

    def tearDown(self):
        M.detach_master()
        self.master.close()
        self.sim.stop()

    def post(self, headers):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/mcp", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream", **headers})
        try:
            return urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_auth_audit_tools_and_restart(self):
        for _ in range(2):  # stop / start twice
            srv = M.BackgroundServer("127.0.0.1", self.port, TOKEN, self.audit)
            try:
                self.assertEqual(self.post({}), 401)
                self.assertEqual(self.post({"Authorization": "Bearer wrong"}), 401)
                self.assertNotEqual(self.post({"Authorization": f"Bearer {TOKEN}"}), 401)

                from mcp import ClientSession
                from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

                async def go():
                    client = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
                    async with streamable_http_client(f"http://127.0.0.1:{self.port}/mcp", http_client=client) as st:
                        async with ClientSession(st[0], st[1]) as s:
                            await s.initialize()
                            out = {}
                            for name, args in (("status", {}), ("connect", {"url": "tcp://10.1.2.3:502"}),
                                               ("write_registers", {"unit": 1, "address": 0, "values": [1]}),
                                               ("diagnostic_report", {"unit": 1, "count": 4, "samples": 1})):
                                r = await s.call_tool(name, args)
                                out[name] = json.loads(r.content[0].text)
                            return out
                out = asyncio.run(go())
                self.assertTrue(out["status"]["connected"])
                self.assertFalse(out["connect"]["ok"])           # remote clients cannot pick targets
                self.assertFalse(out["write_registers"]["ok"])   # and cannot write
                self.assertTrue(out["diagnostic_report"]["ok"])
            finally:
                srv.stop()
        log = open(self.audit, encoding="utf-8").read()
        self.assertIn('"event": "auth_failed"', log)
        self.assertIn('"name": "write_registers"', log)
        self.assertNotIn(TOKEN, log)                             # the token never lands in the audit log

    def test_lockout_after_repeated_bad_tokens(self):
        srv = M.BackgroundServer("127.0.0.1", self.port, TOKEN, self.audit)
        try:
            for _ in range(11):
                self.post({"Authorization": "Bearer bad"})
            self.assertEqual(self.post({"Authorization": f"Bearer {TOKEN}"}), 429)
        finally:
            srv.stop()

    def test_busy_port_raises_oserror(self):
        a = M.BackgroundServer("127.0.0.1", self.port, TOKEN, self.audit)
        try:
            with self.assertRaises(OSError):
                M.BackgroundServer("127.0.0.1", self.port, TOKEN, self.audit)
        finally:
            a.stop()


class Followups(unittest.TestCase):
    def test_port_zero_reports_actual_port_and_stats_count(self):
        srv = M.BackgroundServer("127.0.0.1", 0, TOKEN, os.path.join(tempfile.mkdtemp(), "a.log"))
        try:
            self.assertGreater(srv.port, 0)
            self.assertIn(str(srv.port), srv.url)
            before = dict(M.STATS)
            req = urllib.request.Request(srv.url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(M.STATS["auth_failed"], before["auth_failed"] + 1)
        finally:
            srv.stop()

    def test_serial_port_listing_obeys_connect_restriction_and_accessor(self):
        M._REMOTE["allow_connect"] = False
        try:
            self.assertFalse(M.list_serial_ports()["ok"] if isinstance(M.list_serial_ports(), dict) else True)
        finally:
            M._REMOTE["allow_connect"] = True
        self.assertIsInstance(M.list_serial_ports(), list)
        M.detach_master()
        self.assertEqual(M.current_master(), (None, None))


if __name__ == "__main__":
    unittest.main()
