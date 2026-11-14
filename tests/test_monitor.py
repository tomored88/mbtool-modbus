"""Unit tests for monitor.py (no Tk). Alerts use a fake clock; the multi-device scheduler uses fake masters."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import csv
import json
import os
import tempfile
import threading
import time
import unittest

import monitor as M
from interpret import interpret
from modbus_core import ModbusError, SimSlave, TcpMaster
from preset import Preset


def read_csv(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def read_text(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write_text(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ----------------------------------------------------------------------------------------------- presets
class PresetHelpers(unittest.TestCase):
    def test_inspector_rows_round_trip_through_preset_decode(self):
        """Every inspector row that maps to a preset point must decode to the same value the inspector shows."""
        words = [0x4348, 0xCCCD, 0x1234, 0xABCD]
        rows = interpret(words)
        mapped = 0
        for g, l, text in rows:
            spec, why = M.inspector_point_spec(g, l)
            if spec is None:
                self.assertTrue(why)
                continue
            if l == "Unix time":
                continue
            pt = dict(name="x", address=0, fc=3, **spec)
            p = Preset({"points": [pt]}).points[0]
            d = p.decode(words[:p.words])
            if spec["type"] == "string":
                self.assertEqual(d["text"], text.replace(".", "").strip(), (g, l))
            elif spec["type"].startswith("float"):
                self.assertAlmostEqual(float(d["value"]), float(text), delta=abs(float(text)) * 1e-6 + 1e-30)
            else:
                self.assertEqual(str(d["value"]), text, (g, l))
            mapped += 1
        self.assertGreaterEqual(mapped, 20)

    def test_byte_swapped_orders_are_supported(self):
        self.assertEqual(M.inspector_point_spec("32-bit BADC (byte-swapped)", "Float32")[0],
                         {"type": "float32", "word_order": "big", "byte_swap": True})
        self.assertEqual(M.inspector_point_spec("32-bit DCBA (little-endian)", "Signed")[0],
                         {"type": "int32", "word_order": "little", "byte_swap": True})
        self.assertEqual(M.inspector_point_spec("64-bit little-endian", "Unsigned")[0]["type"], "uint64")
        self.assertEqual(M.inspector_point_spec("64-bit word-swapped", "Signed")[0],
                         {"type": "int64", "word_order": "little", "byte_swap": False})
        self.assertIsNone(M.inspector_point_spec("16-bit", "Binary")[0])

    def test_parse_point_input(self):
        p = Preset({"points": [
            {"name": "Mode", "address": 0, "type": "uint16", "access": "rw", "enum": {"0": "Stop", "1": "Run"}},
            {"name": "SP", "address": 1, "type": "int16", "scale": 0.1, "unit": "C", "access": "w"},
            {"name": "Tag", "address": 2, "type": "string", "length": 2, "access": "w"},
            {"name": "Go", "address": 0, "fc": 1, "type": "bool", "access": "w", "enum": {"1": "Start"}}]}).by_name
        self.assertEqual(M.parse_point_input(p["Mode"], "Run")[0], 1)
        self.assertEqual(M.parse_point_input(p["SP"], "21.5 C")[0], 21.5)
        self.assertEqual(M.parse_point_input(p["Tag"], "AB12")[0], "AB12")
        self.assertIs(M.parse_point_input(p["Go"], "Start")[0], True)
        self.assertIs(M.parse_point_input(p["Go"], "off")[0], False)
        for pt, bad in ((p["SP"], "abc"), (p["Tag"], "12345"), (p["Go"], "maybe")):
            with self.assertRaises(ValueError):
                M.parse_point_input(pt, bad)
        self.assertEqual(M.point_cell_text(p["SP"]), "▶ Send...")

    def test_numeric(self):
        self.assertEqual(M.numeric(True), 1.0)
        self.assertEqual(M.numeric("2.5"), 2.5)
        self.assertIsNone(M.numeric("ABC"))
        self.assertIsNone(M.numeric(float("nan")))


# ----------------------------------------------------------------------------------------------- addresses
class Addresses(unittest.TestCase):
    def test_three_bases_point_at_the_same_register(self):
        self.assertEqual(M.parse_addr("10", 3, 0), 10)
        self.assertEqual(M.parse_addr("11", 3, 1), 10)
        self.assertEqual(M.parse_addr("400011", 3, "plc"), 10)
        self.assertEqual(M.parse_addr("40011", 3, "plc"), 10)
        self.assertEqual(M.parse_addr("11", 3, "plc"), 10)
        self.assertEqual(M.addr_label(3, 10, 0), "10")
        self.assertEqual(M.addr_label(3, 10, 1), "11")
        self.assertEqual(M.addr_label(3, 10, "plc"), "40011")
        self.assertEqual(M.addr_label(4, 10, "plc"), "30011")
        self.assertEqual(M.addr_label(3, 20000, "plc"), "420001")

    def test_out_of_range(self):
        for text, base in (("0", 1), ("65537", 1), ("0", "plc"), ("65536", 0), ("-1", 0), ("x", 1)):
            with self.assertRaises(ValueError):
                M.parse_addr(text, 3, base)
        with self.assertRaises(ValueError):
            M.parse_addr("30011", 3, "plc")  # input-register prefix on a holding read

    def test_old_boolean_setting_migrates(self):
        self.assertEqual(M.norm_base(True), "plc")
        self.assertEqual(M.norm_base(False), 0)
        self.assertEqual(M.norm_base("1"), 1)
        self.assertEqual(M.base_from_label(M.ADDRESS_BASES[1]), 1)

    def test_grid_layout(self):
        self.assertEqual(M.grid_layout(100, 3, "Auto"), (34, 3))
        self.assertEqual(M.grid_layout(5, 3, "Auto"), (5, 1))
        for rows, cols in ((10, 10), (20, 5), (30, 4), (40, 3)):
            self.assertEqual(M.grid_layout(100, 3, str(rows)), (rows, cols))
        self.assertEqual(M.grid_layout(7, 3, "20"), (7, 1))
        self.assertEqual(M.grid_layout(0, 3, "20"), (0, 0))

    def test_shift_and_flip_base(self):
        raw = {"address_base": 1, "points": [{"name": "a", "address": 11, "type": "uint16"}]}
        same = M.shift_addresses(raw, -1, adjust_base=True)
        self.assertEqual((same["address_base"], same["points"][0]["address"]), (0, 10))
        self.assertEqual(Preset(same).points[0].address, Preset(raw).points[0].address)
        moved = M.shift_addresses(raw, 1, adjust_base=False)
        self.assertEqual(Preset(moved).points[0].address, 11)
        self.assertEqual(Preset(M.with_base(raw, 0)).points[0].address, 11)
        self.assertEqual(raw["points"][0]["address"], 11)  # original untouched
        with self.assertRaises(ValueError):
            M.shift_addresses(raw, 1, adjust_base=True)


# ----------------------------------------------------------------------------------------------- trend
class Trend(unittest.TestCase):
    def test_ring_buffer_is_bounded_and_gaps_collapse(self):
        b = M.TrendBuffer(max_points=100)
        b.set_series([("a", "A", "V"), ("b", "B", "")])
        for i in range(500):
            b.add_sample(i, {"a": i, "b": None if i % 50 < 5 else -i})
        self.assertEqual(len(b.series["a"]), 100)
        self.assertLessEqual(len(b.series["b"]), 100)
        b.add_gap(600)
        b.add_gap(601)
        self.assertEqual(list(b.series["a"].v)[-1], None)
        self.assertNotEqual(list(b.series["a"].v)[-2], None)  # only one gap marker

    def test_set_series_keeps_data_and_limits_count(self):
        b = M.TrendBuffer()
        b.set_series([("a", "A", "")])
        b.add_sample(1, {"a": 5})
        b.set_series([("a", "A", ""), ("c", "C", "")])
        self.assertEqual(list(b.series["a"].v), [5.0])
        with self.assertRaises(ValueError):
            b.set_series([(str(i), str(i), "") for i in range(9)])

    def test_segments_break_at_gaps(self):
        pts = [(0, 1), (1, 2), (2, None), (3, 4), (4, 5), (20, 6)]
        self.assertEqual(M.segments(pts), [[(0, 1), (1, 2)], [(3, 4), (4, 5), (20, 6)]])
        self.assertEqual(M.segments(pts, max_gap=5), [[(0, 1), (1, 2)], [(3, 4), (4, 5)], [(20, 6)]])

    def test_decimation_keeps_extremes_and_gaps(self):
        pts = [(i * 0.1, (i % 37) - (100 if i == 5000 else 0)) for i in range(10000)]
        pts[7000] = (700.0, None)
        d = M.decimate(pts, 0, 1000, 200)
        self.assertLessEqual(len(d), 2 * 200 + 10)
        vals = [v for _t, v in d if v is not None]
        self.assertEqual(min(vals), min(v for _t, v in pts if v is not None))
        self.assertEqual(max(vals), 36)
        self.assertIn(None, [v for _t, v in d])
        self.assertEqual([t for t, _v in d], sorted(t for t, _v in d))
        self.assertEqual(M.decimate(pts[:50], 0, 5, 200), pts[:50])

    def test_visible_value_at_and_export(self):
        b = M.TrendBuffer()
        b.set_series([("V", "Voltage", "V"), ("I", "Current", "A")])
        for i in range(10):
            b.add_sample(100 + i, {"V": 230 + i, "I": 1.5} if i != 4 else {})
        self.assertEqual(b.visible("V", 105, 107)[0], (104, None))
        self.assertEqual(b.value_at("V", 106.5), 236.0)
        self.assertIsNone(b.value_at("V", 104.2))
        self.assertIsNone(b.value_at("V", 200, max_dt=5))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.csv")
            n = b.write_csv(p)
            rows = read_csv(p)
        self.assertEqual(n, 10)
        self.assertEqual(rows[0], ["Time", "Unix time", "Voltage [V]", "Current [A]"])
        header, data = b.export_rows()
        for r, dr in zip(rows[1:], data):
            self.assertAlmostEqual(float(r[1]), dr[0], places=3)
            self.assertEqual(r[2:], ["" if v == "" else repr(float(v)) for v in dr[1:]])
        self.assertEqual(rows[5][2:], ["", ""])  # the failed poll is an empty cell

    def test_ticks(self):
        self.assertEqual(M.nice_ticks(0, 10, 5), [0, 2, 4, 6, 8, 10])
        self.assertEqual(M.nice_ticks(-1, 1, 4), [-1, -0.5, 0, 0.5, 1])
        t0 = time.mktime((2026, 11, 3, 12, 0, 3, 0, 0, -1))
        ts = M.time_ticks(t0, t0 + 60, 6)
        self.assertEqual([M.fmt_hms(t) for t in ts[:2]], ["12:00:10", "12:00:20"])
        self.assertEqual(M.auto_range([5, 5]), (4.5, 5.5))
        self.assertEqual(M.auto_range([]), (0.0, 1.0))


# ----------------------------------------------------------------------------------------------- alerts
class Alerts(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.e = M.AlarmEngine(clock=self.clk)

    def feed(self, v, dt=1.0, src="", ok=True, point="T"):
        self.clk.t += dt
        return self.e.update(src, {point: v} if ok else {}, ok)

    def rule(self, **d):
        r, errs = M.validate_rule(d)
        self.assertEqual(errs, {})
        return self.e.add_rule(r)

    def test_validation_messages(self):
        _r, e = M.validate_rule({"point": "", "when": "above"})
        self.assertEqual(set(e), {"point", "value"})
        self.assertIn("high", M.validate_rule({"point": "T", "when": "outside", "value": 5, "high": 1})[1])
        self.assertIn("for_s", M.validate_rule({"point": "T", "when": "silent"})[1])
        self.assertIn("when", M.validate_rule({"point": "T", "when": "sometimes"})[1])
        self.assertIn("point", M.validate_rule({"point": "X", "when": "changed"}, points=["T"])[1])
        self.assertIn("point", M.validate_rule({"point": "*", "when": "above", "value": 1})[1])
        self.assertIn("hysteresis", M.validate_rule({"point": "T", "when": "above", "value": 1, "hysteresis": -1})[1])
        r, e = M.validate_rule({"point": "T", "when": "above", "value": "35", "for_s": "5", "hysteresis": "1"})
        self.assertEqual((r.value, r.for_s, r.hysteresis), (35.0, 5.0, 1.0))
        self.assertEqual(M.validate_rule(r.to_dict())[0].to_dict(), r.to_dict())

    def test_above_with_delay_hysteresis_ack_and_recovery(self):
        r = self.rule(point="T", when="above", value=35, for_s=5, hysteresis=1, message="hot")
        self.assertEqual(self.feed(36), [])  # condition starts, delay running
        self.assertEqual(self.feed(36, dt=3), [])  # 3 s
        ev = self.feed(36.5, dt=2)  # 5 s -> fires
        self.assertEqual([x["state"] for x in ev], ["ALARM"])
        self.assertEqual(ev[0]["value"], 36.5)
        self.assertEqual(ev[0]["message"], "hot")
        self.assertEqual(self.e.active_count(), 1)
        self.assertEqual(self.feed(34.5), [])  # inside the hysteresis band: still active
        self.assertEqual([x["state"] for x in self.e.ack(now=self.clk.t)], ["ACK"])
        self.assertEqual(self.e.state(r.id), "ACKED")
        self.assertEqual([x["state"] for x in self.feed(33.9)], ["OK"])  # below 35 - 1 -> back to OK
        st = self.e.states[r.id]
        self.assertLess(st.t_raised, st.t_acked)
        self.assertLess(st.t_acked, st.t_cleared)

    def test_short_spike_does_not_fire(self):
        self.rule(point="T", when="above", value=35, for_s=5)
        self.feed(40)
        self.feed(30, dt=4)
        self.assertEqual(self.e.tick(now=self.clk.t + 10), [])

    def test_delay_fires_from_tick_without_new_samples(self):
        self.rule(point="T", when="below", value=10, for_s=5)
        self.feed(5)
        self.assertEqual([x["state"] for x in self.e.tick(now=self.clk.t + 5)], ["ALARM"])

    def test_unacked_return_needs_ack(self):
        r = self.rule(point="T", when="above", value=35)
        self.assertEqual([x["state"] for x in self.feed(40)], ["ALARM"])
        self.assertEqual([x["state"] for x in self.feed(20)], ["CLEARED"])
        self.assertEqual(self.e.active_count(), 0)
        self.assertEqual(len(self.e.unacked()), 1)
        self.assertEqual([x["state"] for x in self.feed(41)], ["ALARM"])  # comes back before the ack
        self.feed(20)
        self.e.ack(r.id)
        self.assertEqual(self.e.state(r.id), "OK")

    def test_outside_equals_changed(self):
        o = self.rule(point="T", when="outside", value=10, high=20, hysteresis=1)
        q = self.rule(point="T", when="equals", value=25)
        c = self.rule(point="T", when="changed")
        self.feed(15)
        self.assertEqual(self.e.state(c.id), "OK")
        self.feed(25)
        self.assertEqual((self.e.state(o.id), self.e.state(q.id), self.e.state(c.id)), ("ALARM", "ALARM", "ALARM"))
        self.feed(19.5)  # inside but within hysteresis of the high limit
        self.assertEqual((self.e.state(o.id), self.e.state(q.id), self.e.state(c.id)), ("ALARM", "CLEARED", "ALARM"))
        self.feed(19.5)
        self.assertEqual((self.e.state(o.id), self.e.state(c.id)), ("ALARM", "CLEARED"))
        self.feed(18.9)
        self.assertEqual(self.e.state(o.id), "CLEARED")
        self.feed(9)
        self.assertEqual(self.e.state(o.id), "ALARM")

    def test_equals_text(self):
        r = self.rule(point="T", when="equals", value="Fault")
        self.feed("Run")
        self.assertEqual(self.e.state(r.id), "OK")
        self.feed("Fault")
        self.assertEqual(self.e.state(r.id), "ALARM")

    def test_silent_device_and_point(self):
        dev = self.rule(point="*", when="silent", for_s=10, device="meter")
        pt = self.rule(point="T", when="silent", for_s=10, device="meter")
        self.assertEqual(self.e.tick(now=self.clk.t + 100), [])  # not polled yet: no silent alarm
        self.feed(1, src="meter")
        self.feed(None, src="meter", dt=5)  # point invalid, device answered
        self.feed(None, src="meter", dt=5)
        self.assertEqual((self.e.state(dev.id), self.e.state(pt.id)), ("OK", "ALARM"))
        self.feed(None, src="meter", ok=False, dt=4)
        self.assertEqual(self.e.state(dev.id), "OK")
        ev = self.e.tick(now=self.clk.t + 6.1)  # 10 s after the last good poll, no further results
        self.assertEqual([x["state"] for x in ev], ["ALARM"])
        self.clk.t += 7
        self.feed(2, src="meter")
        self.assertEqual((self.e.state(dev.id), self.e.state(pt.id)), ("CLEARED", "CLEARED"))
        self.e.suspend("meter")
        self.assertEqual(self.e.tick(now=self.clk.t + 1000), [])  # stopped on purpose

    def test_stuck_with_deadband_and_source_isolation(self):
        r = self.rule(point="T", when="stuck", for_s=10, hysteresis=0.5, device="a")
        for _ in range(9):
            self.feed(5.0 + 0.1 * (_ % 2), src="a")  # jitter inside the deadband
        self.feed(7, src="b", dt=5)  # another device does not matter
        self.e.tick()
        self.assertEqual(self.e.state(r.id), "ALARM")
        self.feed(6, src="a")
        self.assertEqual(self.e.state(r.id), "CLEARED")

    def test_invalid_value_holds_threshold_state_and_disabled_rules(self):
        r = self.rule(point="T", when="above", value=1)
        d = self.rule(point="T", when="above", value=1, enabled=False)
        self.feed(5)
        self.feed(None)
        self.feed(None, ok=False)
        self.assertEqual(self.e.state(r.id), "ALARM")
        self.assertEqual(self.e.state(d.id), "OK")

    def test_preset_alarms_and_export(self):
        raw = {"points": [], "alarms": [{"point": "Temperature", "when": "above", "value": 35, "for_s": 5,
                                         "hysteresis": 1, "message": "too hot"}, {"point": "x", "when": "bogus"}]}
        added, errs = self.e.load_dicts(M.preset_alarms(raw), device="", origin="preset")
        self.assertEqual(len(added), 1)
        self.assertEqual(len(errs), 1)
        self.assertEqual(added[0].origin, "preset")
        self.feed(40, point="Temperature")
        self.feed(40, point="Temperature", dt=6)
        self.e.ack()
        self.e.remove_origin("", "preset")
        self.assertEqual(self.e.rules, [])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ev.csv")
            self.assertEqual(self.e.export_events(p), 2)
            rows = read_csv(p)
        self.assertEqual(rows[0][:3], ["Time", "Device", "Rule"])
        self.assertEqual([r[5] for r in rows[1:]], ["ALARM", "ACK"])
        self.assertEqual(rows[1][4], "40")
        self.assertEqual(M.preset_alarms({"alarms": "x"}), [])


# ----------------------------------------------------------------------------------------------- devices
class FakeWire:
    """Shared medium: records (start, end, device unit) of every transaction and detects overlaps."""

    def __init__(self):
        self.lock = threading.Lock()
        self.log = []
        self.overlap = False


class FakeMaster:
    def __init__(self, wire, delay=0.01, dead_units=(), name=""):
        self.wire, self.delay, self.dead, self.name = wire, delay, set(dead_units), name
        self.closed = False
        self.traffic = []

    def request(self, unit, fc, addr=0, qty=1, **kw):
        if not self.wire.lock.acquire(blocking=False):
            self.wire.overlap = True
            self.wire.lock.acquire()
        try:
            t0 = time.perf_counter()
            if unit in self.dead:
                time.sleep(0.15)  # timeout
                self.wire.log.append((t0, time.perf_counter(), self.name, unit, False))
                raise ModbusError("response timeout")
            time.sleep(self.delay)
            self.wire.log.append((t0, time.perf_counter(), self.name, unit, True))
            return [unit * 100 + i for i in range(qty)]
        finally:
            self.wire.lock.release()

    def close(self):
        self.closed = True


def dev(name, mode="RTU", com="COM9", unit=1, scan=50, **kw):
    d = M.norm_device({"name": name, "unit": str(unit), "scan": str(scan), "qty": "2",
                       "conn": {"mode": mode, "com": com, "host": "h", "port": "1"}, **kw})
    return d


class Devices(unittest.TestCase):
    def test_validation_and_bus_plan(self):
        a, b = dev("a", unit=1), dev("b", unit=2)
        c = dev("c", mode="TCP")
        d = dev("d", com="COM9")
        d["conn"]["baud"] = "19200"
        self.assertEqual(M.device_errors(a, [b, c]), {})
        self.assertIn("name", M.device_errors(dev("A"), [a]))
        self.assertIn("conn", M.device_errors(d, [a]))
        self.assertIn("unit", M.device_errors(dev("x", unit=300), []))
        buses, errs = M.plan_buses([a, b, c, d])
        self.assertEqual([[x["name"] for x in v] for v in buses.values()], [["a", "b"], ["c"]])
        self.assertIn("d", errs)
        self.assertEqual(M.bus_key(dev("q", com="com9")), M.bus_key(a))

    def run_poller(self, devs, secs, wires, dead=(), gap=0.02):
        masters = []

        def factory(conn):
            key = conn["com"] if conn["mode"] != "TCP" else conn["host"]
            m = FakeMaster(wires[key], dead_units=dead, name=key)
            masters.append(m)
            return m
        msgs = []
        p = M.MultiPoller(factory, msgs.append, gap_s=gap)
        for d in devs:
            p.start_device(d, M.make_reader(d))
        time.sleep(secs)
        ws = p.stop_all()
        for w in ws:
            w.join(2)
        self.assertFalse(any(w.is_alive() for w in ws))
        return p, msgs, masters

    def test_shared_bus_is_sequential_with_gap_and_one_master(self):
        wire = FakeWire()
        devs = [dev("a", unit=1), dev("b", unit=2)]
        p, msgs, masters = self.run_poller(devs, 0.6, {"COM9": wire})
        self.assertEqual(len(masters), 1)
        self.assertFalse(wire.overlap)
        units = [u for _s, _e, _n, u, _ok in wire.log]
        self.assertIn(1, units)
        self.assertIn(2, units)
        for (s0, e0, *_), (s1, e1, *_) in zip(wire.log, wire.log[1:]):
            self.assertGreaterEqual(s1 - e0, 0.015)  # configured gap between frames
        self.assertTrue(all(m[0] == "dev" and m[4]["ok"] for m in msgs))
        self.assertTrue(masters[0].closed)

    def test_buses_run_in_parallel_and_dead_device_is_isolated(self):
        w9, w8, wt = FakeWire(), FakeWire(), FakeWire()
        devs = [dev("a", unit=1), dev("dead", unit=7), dev("b", com="COM8", unit=2),
                dev("t", mode="TCP", unit=3, conn={"mode": "TCP", "host": "h"})]
        devs[3]["conn"] = M.norm_conn({"mode": "TCP", "host": "h", "port": "1"})
        p, msgs, masters = self.run_poller(devs, 1.0, {"COM9": w9, "COM8": w8, "h": wt}, dead=(7,))
        count = {n: sum(1 for m in msgs if m[1] == n and m[4]["ok"]) for n in ("a", "b", "t")}
        fails = sum(1 for m in msgs if m[1] == "dead" and not m[4]["ok"])
        self.assertGreater(fails, 2)
        # 'a' shares the bus with the dead device: slowed by its 0.15 s timeout but still polled regularly
        self.assertGreater(count["a"], 3)
        # other buses are not slowed at all (50 ms scan -> about 15+ polls a second)
        self.assertGreater(count["b"], 10)
        self.assertGreater(count["t"], 10)
        # parallel: polls on COM8 happen while COM9 is in a transaction
        busy9 = [(s, e) for s, e, *_ in w9.log]
        self.assertTrue(any(s9 < s8 < e9 for s8, _e8, *_ in w8.log for s9, e9 in busy9))
        self.assertFalse(w9.overlap or w8.overlap)

    def test_generation_tokens_and_restart(self):
        wire = FakeWire()
        msgs = []
        p = M.MultiPoller(lambda c: FakeMaster(wire), msgs.append, gap_s=0.0)
        d = dev("a")
        g1 = p.start_device(d, M.make_reader(d))
        time.sleep(0.15)
        g2 = p.start_device(d, M.make_reader(d))  # restart: one loop only
        self.assertGreater(g2, g1)
        time.sleep(0.15)
        old = [m for m in msgs if m[2] == g1]
        self.assertTrue(old and not any(p.accept(m) for m in old))
        self.assertTrue(p.accept(msgs[-1]))
        self.assertEqual(len(p.workers), 1)
        self.assertFalse(wire.overlap)
        p.stop_device("a")
        time.sleep(0.2)
        n = len(msgs)
        time.sleep(0.2)
        self.assertLessEqual(len(msgs) - n, 1)
        self.assertEqual(p.workers, {})

    def test_connect_failure_reported_and_retried(self):
        calls = []

        def factory(conn):
            calls.append(1)
            raise ModbusError("COM77 does not exist")
        msgs = []
        p = M.MultiPoller(factory, msgs.append, retry_s=0.1)
        d = dev("x")
        p.start_device(d, M.make_reader(d))
        time.sleep(0.45)
        for w in p.stop_all():
            w.join(1)
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all(not m[4]["ok"] and "COM77" in m[4]["error"] for m in msgs))

    def test_status_bookkeeping(self):
        s = M.DeviceStatus("a")
        self.assertEqual(s.state(0), "off")
        s.start(1.0, 1.0)
        self.assertEqual(s.state(0), "stale")
        s.on_result(10, {"ok": True, "values": {"V": 1}, "texts": {"V": "1 V", "A": "2", "W": "3", "X": "4"},
                         "error": None})
        self.assertEqual(s.state(11), "ok")
        self.assertEqual(s.summary, "V=1 V  A=2  W=3  ...")
        s.on_result(12, {"ok": False, "values": {}, "texts": {}, "error": "timeout"})
        self.assertEqual((s.state(12), s.ok, s.errors, s.last_ok), ("error", 1, 1, 10))
        self.assertEqual(s.state(30), "stale")
        s.stop()
        self.assertEqual(s.state(30), "off")

    def test_reader_with_preset_and_real_simulator(self):
        pr = Preset({"points": [{"name": "V", "address": 0, "type": "float32", "sim": 230.5},
                                {"name": "Tag", "address": 4, "type": "string", "length": 2, "sim": "AB"},
                                {"name": "Cmd", "address": 9, "type": "uint16", "access": "w"}]})
        s = SimSlave()
        pr.fill_simulator(s)
        m = TcpMaster("127.0.0.1", s.port)
        try:
            r = M.make_reader(dev("x", mode="TCP"), pr)(m)
            self.assertTrue(r["ok"])
            self.assertEqual(r["values"], {"V": 230.5, "Tag": "AB"})  # write-only never read
            plain = M.make_reader(M.norm_device({"name": "p", "fc": "3", "addr": "0", "qty": "2", "fmt": "Float"}))(m)
            self.assertEqual(plain["values"], {"0": 230.5})
        finally:
            m.close()
            s.stop()
        s.stop()
        bad = M.make_reader(dev("x", mode="TCP"), pr)(FakeMaster(FakeWire(), dead_units=(1,)))
        self.assertFalse(bad["ok"])
        self.assertIn("timeout", bad["error"])

    def test_log_values(self):
        self.assertEqual(M.log_values(["a", "b", "c", "d"], {"a": 1.5, "b": True, "c": None, "d": "x"},
                                      {"c": "ERROR"}), [1.5, 1, "ERROR", "x"])


# ----------------------------------------------------------------------------------------------- workspace
class Workspace(unittest.TestCase):
    def test_round_trip_relative_paths_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "presets"))
            pp = os.path.join(d, "presets", "m.json")
            write_text(pp, json.dumps({"points": []}))
            ws = M.new_workspace()
            ws["main"]["preset"] = pp
            ws["devices"] = [M.norm_device({"name": "m1", "preset": pp,
                                            "conn": {"mode": "RTU", "com": "COM99", "baud": "19200"}})]
            ws["alarms"] = [{"point": "T", "when": "above", "value": 3}]
            ws["trend"]["series"] = ["T", "V"]
            ws["windows"]["trend"] = {"geometry": "800x400+10+10", "open": True}
            ws["log_file"] = os.path.join(os.path.dirname(d), "elsewhere.csv")
            ws["grid_rows"] = "20"
            path = os.path.join(d, "plant.mbw")
            raw = M.save_workspace(path, ws)
            self.assertEqual(raw["main"]["preset"], "presets/m.json")
            self.assertTrue(os.path.isabs(raw["log_file"]))
            data = json.loads(read_text(path))
            data["future_feature"] = {"x": 1}
            data["devices"][0]["future"] = 2
            write_text(path, json.dumps(data))
            back, warn = M.load_workspace(path)
            self.assertEqual(warn, [])
            self.assertEqual(os.path.normcase(back["main"]["preset"]), os.path.normcase(pp))
            self.assertEqual(back["devices"][0]["conn"]["baud"], "19200")
            self.assertEqual(back["alarms"], [{"point": "T", "when": "above", "value": 3.0}])
            self.assertEqual(back["trend"]["series"], ["T", "V"])
            self.assertEqual(back["windows"]["trend"], {"geometry": "800x400+10+10", "open": True})
            probs = M.workspace_problems(back, ["COM1"])
            self.assertEqual(len(probs), 1)
            self.assertIn("COM99", probs[0])
            os.remove(pp)
            self.assertEqual(len(M.workspace_problems(back, ["COM99"])), 2)

    def test_tolerant_of_bad_values(self):
        d = {"version": 7, "main": {"conn": {"mode": "weird"}, "preset": 5}, "devices": [{"name": ""}, "x",
             {"name": "ok", "unit": "999"}], "alarms": [{"point": "T", "when": "nope"}],
             "trend": {"window": "2 years", "series": "abc"}, "windows": {"main": {"geometry": "huge"}},
             "bus_gap_ms": 99999}
        ws, warn = M.normalize_workspace(d, ".")
        self.assertEqual(ws["main"]["conn"]["mode"], "TCP")
        self.assertEqual([x["name"] for x in ws["devices"]], ["ok"])
        self.assertFalse(ws["devices"][0]["enabled"])
        self.assertEqual(ws["alarms"], [])
        self.assertEqual(ws["trend"]["window"], "10 min")
        self.assertEqual(ws["bus_gap_ms"], 5000)
        self.assertEqual(ws["windows"]["main"]["geometry"], "")
        self.assertTrue(any("newer" in w for w in warn))
        self.assertGreaterEqual(len(warn), 6)
        with self.assertRaises(ValueError):
            M.normalize_workspace([], ".")

    def test_load_errors(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.mbw")
            write_text(p, "{not json")
            with self.assertRaises(ValueError):
                M.load_workspace(p)
            with self.assertRaises(OSError):
                M.load_workspace(os.path.join(d, "missing.mbw"))

    def test_recent(self):
        r = M.add_recent([], "a.mbw")
        r = M.add_recent(r, "b.mbw")
        r = M.add_recent(r, "A.mbw" if os.name == "nt" else "a.mbw")
        self.assertEqual(len(r), 2)
        self.assertEqual(len(M.add_recent([str(i) for i in range(20)], "x")), 8)


class LogOptions(unittest.TestCase):
    def test_validation(self):
        kw, e = M.log_kwargs({})
        self.assertEqual(e, {})
        self.assertEqual(kw["rotate"], "none")
        kw, e = M.log_kwargs({"rotate": "size", "size_mb": "0.5", "keep_on": True, "keep": "5"})
        self.assertEqual((kw["max_bytes"], kw["max_files"]), (524288, 5))
        self.assertIn("size_mb", M.log_kwargs({"rotate": "size", "size_mb": "abc"})[1])
        self.assertIn("keep", M.log_kwargs({"keep_on": True})[1])
        self.assertIn("decimal", M.log_kwargs({"decimal": ",", "delimiter": ","})[1])
        self.assertIn("delimiter", M.log_kwargs({"delimiter": "x"})[1])


if __name__ == "__main__":
    unittest.main()
