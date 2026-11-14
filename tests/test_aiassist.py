"""AI Assist (GUI feature) helpers in monitor.py: sharing options, snippets, audit lines, report prompt and the
read-only master wrapper that the GUI hands to the in-process MCP server."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import asyncio
import json
import os
import tempfile
import unittest

import monitor as M
from modbus_core import ModbusError, SimSlave, TcpMaster
from preset import load_preset

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "t" * 43


class Options(unittest.TestCase):
    def test_norm_drops_token_and_bad_values(self):
        c = M.norm_ai_settings({"bind": "everyone", "port": 8765, "token": "secret", "allow_connect": "yes",
                                "host_name": "pc.ts.net"})
        self.assertEqual(c["bind"], "local")
        self.assertEqual(c["port"], "8765")          # wrong type -> default
        self.assertFalse(c["allow_connect"])
        self.assertEqual(c["host_name"], "pc.ts.net")
        self.assertNotIn("token", c)
        self.assertEqual(M.norm_ai_settings(None), M.AI_DEFAULT)

    def test_errors(self):
        ok = dict(M.AI_DEFAULT)
        self.assertEqual(M.ai_errors(ok), {})
        self.assertIn("port", M.ai_errors({**ok, "port": "70000"}))
        self.assertIn("port", M.ai_errors({**ok, "port": "abc"}))
        lan = {**ok, "bind": "lan"}
        self.assertEqual(M.ai_errors(lan), {})
        self.assertIn("lan_addr", M.ai_errors({**lan, "lan_addr": "999.1.1.1"}))
        self.assertIn("host_name", M.ai_errors({**lan, "host_name": "bad name!"}))
        self.assertEqual(M.ai_errors({**lan, "host_name": "mypc.tailnet.ts.net:8765"}), {})
        self.assertEqual(M.ai_errors({**ok, "host_name": "ignored when local!"}), {})

    def test_bind_hosts_and_url(self):
        local = dict(M.AI_DEFAULT)
        self.assertEqual(M.ai_bind_host(local), "127.0.0.1")
        self.assertEqual(M.ai_allowed_hosts(local, ["192.168.1.5"]), [])
        self.assertEqual(M.ai_client_url(local, ["192.168.1.5"]), "http://127.0.0.1:8765/mcp")
        lan = {**local, "bind": "lan", "port": "9000"}
        self.assertEqual(M.ai_bind_host(lan), "0.0.0.0")
        self.assertEqual(M.ai_client_url(lan, ["192.168.1.5"]), "http://192.168.1.5:9000/mcp")
        self.assertEqual(M.ai_allowed_hosts(lan, ["192.168.1.5", "100.64.0.7"]),
                         ["192.168.1.5", "192.168.1.5:*", "100.64.0.7", "100.64.0.7:*"])
        named = {**lan, "host_name": "pc.tail.ts.net:9000", "lan_addr": "100.64.0.7"}
        self.assertEqual(M.ai_client_url(named), "http://pc.tail.ts.net:9000/mcp")
        self.assertEqual(M.ai_allowed_hosts(named, ["192.168.1.5"]),
                         ["pc.tail.ts.net", "pc.tail.ts.net:*", "100.64.0.7", "100.64.0.7:*"])
        self.assertEqual(M.ai_share_label(named), "100.64.0.7:9000")

    def test_local_ipv4s_are_plain_addresses(self):
        for ip in M.local_ipv4s():
            self.assertTrue(M._ipv4(ip) and not ip.startswith("127."), ip)

    def test_conn_url(self):
        self.assertEqual(M.conn_url({"mode": "TCP", "host": "10.0.0.2", "port": "502"}), "tcp://10.0.0.2:502")
        self.assertEqual(M.conn_url({"mode": "RTU", "com": "COM3  -  USB", "baud": "19200", "parity": "E"}),
                         "serial:COM3?baud=19200&parity=E&stop=1&data=8&mode=rtu")


class Snippets(unittest.TestCase):
    def test_snippets_contain_url_and_token(self):
        url = "http://127.0.0.1:8765/mcp"
        sn = M.ai_snippets(url, TOKEN)
        self.assertEqual(sn["claude"], f'claude mcp add --transport http modbus {url} '
                                       f'--header "Authorization: Bearer {TOKEN}"')
        cfg = json.loads(sn["json"])["mcpServers"]["modbus"]
        self.assertEqual(cfg, {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {TOKEN}"}})
        self.assertIn(url, sn["text"])
        self.assertIn(f"Authorization: Bearer {TOKEN}", sn["text"])
        self.assertEqual(M.mask_token(TOKEN), "•" * 24)
        self.assertNotIn("t", M.mask_token(TOKEN))

    def test_audit_lines(self):
        tool = json.dumps({"t": "2026-01-02 03:04:05", "ip": "127.0.0.1", "event": "tool", "name": "read",
                           "args": "{'unit': 1, 'function': 3, 'address': 0, 'count': 125}" * 3})
        e = M.parse_audit_line(tool, max_args=40)
        self.assertEqual((e["time"], e["ip"], e["event"], e["name"], e["failed"]),
                         ("03:04:05", "127.0.0.1", "tool", "read", False))
        self.assertEqual(len(e["args"]), 40)
        bad = M.parse_audit_line(json.dumps({"t": "2026-01-02 03:04:06", "ip": "10.0.0.9", "event": "auth_failed",
                                             "path": "/mcp"}))
        self.assertTrue(bad["failed"])
        self.assertEqual(bad["name"], "/mcp")
        self.assertIsNone(M.parse_audit_line("not json"))
        self.assertIsNone(M.parse_audit_line("[1, 2]"))

    def test_prompt_structure(self):
        txt = M.ai_prompt({"Connection": "TCP 127.0.0.1:5020", "Preset": None}, "# Modbus diagnostic report",
                          "Why is the voltage wrong?")
        for want in ("Observed", "Inferred", "Unknown", "Next check", "UNVERIFIED", "My question: Why is the voltage",
                     "- Connection: TCP 127.0.0.1:5020", "# Modbus diagnostic report"):
            self.assertIn(want, txt)
        self.assertNotIn("Preset:", txt)
        self.assertNotIn("My question", M.ai_prompt({}, "r", "   "))

    def test_report_request(self):
        p = {"unit": 7, "fc": 4, "addr": 100, "qty": 20}
        self.assertEqual(M.report_request(p), (7, 4, 100, 20))
        pr = load_preset(os.path.join(HERE, "presets", "example_generic_meter.json"))
        self.assertEqual(M.report_request({"unit": 1}, pr), (1, 3, 0, 8))  # Voltage .. Energy (0..7)


class SharedMaster(unittest.TestCase):
    def setUp(self):
        self.sim = SimSlave()
        load_preset(os.path.join(HERE, "presets", "example_generic_meter.json")).fill_simulator(self.sim)
        self.m = TcpMaster("127.0.0.1", self.sim.port, timeout=0.5)

    def tearDown(self):
        self.m.close()
        self.sim.stop()

    def test_reads_pass_writes_refused_close_ignored(self):
        sh = M.AiSharedMaster(self.m)
        self.assertEqual(sh.read_holding(1, 10, 1), [1])
        self.assertIs(sh.traffic, self.m.traffic)
        self.assertIs(sh.target, self.m)
        for call in (lambda: sh.write_register(1, 10, 2), lambda: sh.write_registers(1, 10, [2]),
                     lambda: sh.write_coil(1, 0, True), lambda: sh.write_coils(1, 0, [True]),
                     lambda: sh.mask_write(1, 10, 0, 1), lambda: sh.read_write(1, 0, 1, 10, [2]),
                     lambda: sh.request(1, 6, addr=10, values=[2])):
            with self.assertRaises(ModbusError):
                call()
        self.assertEqual(self.sim.holding[10], 1)
        sh.close()
        self.assertEqual(self.m.read_holding(1, 10, 1), [1])   # the GUI's connection is still open

    def test_ai_connect_does_not_cut_the_gui_link(self):
        """With 'Let the AI choose the device' on, an AI connect replaces the shared master but must not close it."""
        import mbtool_mcp as MM
        audit = os.path.join(tempfile.mkdtemp(), "audit.log")
        other = SimSlave()
        srv = MM.BackgroundServer("127.0.0.1", 8813, TOKEN, audit, allow_connect=True)
        try:
            MM.attach_master(M.AiSharedMaster(self.m), "tcp://gui")
            from mcp import ClientSession
            from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

            async def go():
                client = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
                async with streamable_http_client("http://127.0.0.1:8813/mcp", http_client=client) as st:
                    async with ClientSession(st[0], st[1]) as s:
                        await s.initialize()
                        w = json.loads((await s.call_tool("write_registers",
                                                          {"unit": 1, "address": 10, "values": [2]})).content[0].text)
                        c = json.loads((await s.call_tool("connect", {"url": f"tcp://127.0.0.1:{other.port}"}))
                                       .content[0].text)
                        return w, c
            w, c = asyncio.run(go())
            self.assertFalse(w["ok"])
            self.assertTrue(c["ok"])
            self.assertEqual(self.m.read_holding(1, 10, 1), [1])  # GUI master still usable
            self.assertNotIsInstance(MM._state["m"], M.AiSharedMaster)
            MM._state["m"].close()
        finally:
            MM.detach_master()
            srv.stop()
            other.stop()
            MM._REMOTE["allow_connect"] = True


class WorkspaceAi(unittest.TestCase):
    def test_ai_options_round_trip_without_token(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "w.mbw")
        ws = M.new_workspace()
        ws["ai"] = {"bind": "lan", "lan_addr": "0.0.0.0", "port": "9001", "host_name": "pc.ts.net",
                    "allow_connect": False, "token": TOKEN}
        M.save_workspace(path, ws)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(TOKEN, text)
        back, warn = M.load_workspace(path)
        self.assertEqual(back["ai"]["port"], "9001")
        self.assertEqual(back["ai"]["bind"], "lan")
        self.assertNotIn("token", back["ai"])
        old, _w = M.normalize_workspace({"version": 1}, d)  # older workspaces have no "ai" part
        self.assertEqual(old["ai"], M.AI_DEFAULT)


if __name__ == "__main__":
    unittest.main()
