import glob
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modbus_core import SimSlave
from preset import Preset, PresetError, load_preset
from signals import DEVICE_TYPES, MODES, Pattern, SignalEngine, guess_device_type
import random

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRESET = {
    "name": "Test device", "manufacturer": "ACME", "model": "T1",
    "points": [
        {"name": "Voltage", "address": 0, "fc": 3, "type": "float32", "unit": "V", "min": 200, "max": 250, "sim": 230},
        {"name": "Level", "address": 2, "fc": 3, "type": "uint16", "unit": "%", "min": 0, "max": 100, "sim": 50},
        {"name": "Total", "address": 3, "fc": 3, "type": "uint32", "unit": "kWh", "sim": 1000},
        {"name": "Status", "address": 5, "fc": 3, "type": "uint16", "enum": {"0": "Stop", "1": "Run", "2": "Fault"}},
        {"name": "Setpoint", "address": 6, "fc": 3, "type": "uint16", "access": "rw", "sim": 42},
        {"name": "Door", "address": 0, "fc": 2, "type": "bool"},
        {"name": "Baud", "address": 7, "fc": 3, "type": "uint16", "enum": {"0": "9600", "1": "19200"}, "sim": 0},
        {"name": "Tag", "address": 10, "fc": 3, "type": "string", "length": 4, "sim": "ABC"},
    ],
}


def make(mode, **kw):
    pr = Preset(PRESET)
    s = SimSlave(listen=False)
    pr.fill_simulator(s)
    return pr, s, SignalEngine(pr, s, mode=mode, seed=3, **kw)


def read(pr, s, name):
    p = pr.by_name[name]
    if p.type == "bool":
        return (s.coils if p.fc == 1 else s.discrete)[p.address]
    store = s.holding if p.fc == 3 else s.input
    return p.decode(store[p.address:p.address + p.words])["value"]


class T(unittest.TestCase):
    def test_static_never_moves(self):
        pr, s, e = make("static")
        for _ in range(20):
            e.tick(1.0)
        self.assertAlmostEqual(read(pr, s, "Voltage"), 230, places=3)
        self.assertEqual(read(pr, s, "Level"), 50)

    def test_random_walk_bounded_and_moves(self):
        pr, s, e = make("random")
        seen = set()
        for _ in range(300):
            e.tick(1.0)
            v = read(pr, s, "Voltage")
            self.assertTrue(200 - 1e-3 <= v <= 250 + 1e-3, v)
            seen.add(round(v, 1))
        self.assertGreater(len(seen), 20)

    def test_loop_ramps_and_wraps(self):
        pr, s, e = make("loop")
        vals = []
        for _ in range(45):
            e.tick(1.0)
            vals.append(read(pr, s, "Level"))
        self.assertLess(min(vals), 10)
        self.assertGreater(max(vals), 90)
        drops = sum(1 for a, b in zip(vals, vals[1:]) if b < a - 50)
        self.assertGreaterEqual(drops, 2)  # wraps around at least twice in 45 s with a 20 s period

    def test_loop_cycles_enum_and_bool(self):
        pr, s, e = make("loop")
        states, doors = set(), set()
        for _ in range(60):
            e.tick(1.0)
            states.add(read(pr, s, "Status"))
            doors.add(read(pr, s, "Door"))
        self.assertEqual(states, {0, 1, 2})
        self.assertEqual(doors, {True, False})

    def test_sine_in_range(self):
        pr, s, e = make("sine")
        for _ in range(100):
            e.tick(0.7)
            self.assertTrue(200 - 1e-3 <= read(pr, s, "Voltage") <= 250 + 1e-3)

    def test_setpoint_and_text_untouched(self):
        pr, s, e = make("random")
        s.holding[6] = 77  # a master wrote the setpoint
        for _ in range(30):
            e.tick(1.0)
        self.assertEqual(s.holding[6], 77)
        self.assertNotIn("Setpoint", e.values())
        self.assertNotIn("Tag", e.values())
        self.assertEqual(read(pr, s, "Tag"), "ABC")

    def test_profile_counter_only_increases_and_settings_fixed(self):
        pr, s, e = make("profile")
        last = read(pr, s, "Total")
        for _ in range(50):
            e.tick(2.0)
            now = read(pr, s, "Total")
            self.assertGreaterEqual(now, last)
            last = now
            self.assertEqual(read(pr, s, "Baud"), 0)  # an enum that is a setting, not a state
        self.assertGreater(last, 1000)

    def test_preset_mode_uses_sim_pattern_only(self):
        d = {**PRESET, "points": [dict(p) for p in PRESET["points"]]}
        d["points"][1]["sim_pattern"] = {"kind": "ramp", "min": 0, "max": 100, "period": 10}
        pr = Preset(d)
        s = SimSlave(listen=False)
        pr.fill_simulator(s)
        e = SignalEngine(pr, s, mode="preset", seed=1)
        vals = []
        for _ in range(20):
            e.tick(1.0)
            vals.append(read(pr, s, "Level"))
        self.assertGreater(max(vals) - min(vals), 50)
        self.assertAlmostEqual(read(pr, s, "Voltage"), 230, places=3)  # no pattern -> stays put

    def test_bad_sim_pattern_rejected(self):
        d = {**PRESET, "points": [dict(PRESET["points"][0], sim_pattern={"kind": "wobble"})]}
        with self.assertRaises(PresetError):
            Preset(d)

    def test_override_and_reset(self):
        pr, s, e = make("static")
        e.set_override("Level", {"kind": "triangle", "min": 10, "max": 20, "period": 4})
        vals = []
        for _ in range(8):
            e.tick(1.0)
            vals.append(read(pr, s, "Level"))
        self.assertTrue(all(10 <= v <= 20 for v in vals) and max(vals) > min(vals))
        e.set_override("Level", None)
        e.tick(1.0)
        self.assertEqual(read(pr, s, "Level"), 50)
        with self.assertRaises(KeyError):
            e.set_override("Nope", None)
        with self.assertRaises(ValueError):
            e.set_override("Level", {"kind": "wobble"})

    def test_sequence_and_square_patterns(self):
        rng = random.Random(0)
        seq = Pattern({"kind": "sequence", "values": [5, 6, 7], "hold": 2}, rng)
        self.assertEqual([seq.value(t, 1, {}) for t in (0, 1.9, 2.1, 4.1, 6.1)], [5, 5, 6, 7, 5])
        sq = Pattern({"kind": "square", "min": 0, "max": 1, "period": 4}, rng)
        self.assertEqual([sq.value(t, 1, {}) for t in (0, 1.9, 2.1, 3.9)], [1, 1, 0, 0])

    def test_seed_is_repeatable_and_speed_scales(self):
        a = make("random")[2]
        b = make("random")[2]
        self.assertEqual([a.tick(1.0) for _ in range(5)], [b.tick(1.0) for _ in range(5)])
        pr, s, fast = make("loop", speed=4.0)
        fast.tick(2.5)  # 10 virtual seconds
        pr2, s2, ref = make("loop")
        for _ in range(10):
            ref.tick(1.0)
        self.assertAlmostEqual(read(pr, s, "Level"), read(pr2, s2, "Level"), delta=1)

    def test_device_type_guess_and_declared(self):
        self.assertEqual(guess_device_type(Preset({**PRESET, "device_type": "drive"})), "drive")
        self.assertEqual(guess_device_type(Preset({**PRESET, "name": "x", "manufacturer": "", "model": "Flow meter",
                                                   "points": PRESET["points"][:1]})), "flow")
        self.assertIn(guess_device_type(Preset(PRESET)), DEVICE_TYPES)

    def test_drive_profile_follows_motor_cycle(self):
        d = {"name": "VFD", "device_type": "drive", "points": [
            {"name": "Output frequency", "address": 0, "fc": 3, "type": "uint16", "scale": 0.1, "unit": "Hz",
             "min": 0, "max": 50, "sim": 0},
            {"name": "Drive status", "address": 1, "fc": 3, "type": "uint16", "enum": {"0": "Stop", "1": "Run"}}]}
        pr = Preset(d)
        s = SimSlave(listen=False)
        pr.fill_simulator(s)
        e = SignalEngine(pr, s, mode="profile", seed=1)
        e.tick(2.0)
        self.assertEqual(read(pr, s, "Output frequency"), 0)  # stopped at the start of the cycle
        e.tick(20.0)  # t = 22 s: running at full speed
        self.assertAlmostEqual(read(pr, s, "Output frequency"), 50, delta=0.1)

    def test_every_library_preset_runs_in_every_mode(self):
        files = glob.glob(os.path.join(HERE, "presets", "**", "*.json"), recursive=True)
        self.assertGreaterEqual(len(files), 30)
        for f in files:
            pr = load_preset(f)
            s = SimSlave(listen=False)
            pr.fill_simulator(s)
            for m in MODES:
                e = SignalEngine(pr, s, mode=m, seed=1)
                for _ in range(60):
                    e.tick(1.0)
                self.assertEqual(e.errors, {}, (os.path.basename(f), m, e.errors))

    def test_thread_start_stop_pause(self):
        pr, s, e = make("random")
        e.interval = 0.05
        e.start()
        self.assertTrue(e.running())
        time.sleep(0.4)
        n = e.ticks
        self.assertGreater(n, 2)
        e.paused = True
        time.sleep(0.2)
        n2 = e.ticks
        time.sleep(0.2)
        self.assertEqual(e.ticks, n2)
        e.stop()
        self.assertFalse(e.running())

    def test_set_mode_rebuilds_plan(self):
        pr, s, e = make("static")
        self.assertTrue(all(lbl == "fixed" for _, _, lbl in e.describe()))
        e.set_mode("random")
        self.assertTrue(any(lbl == "random walk" for _, _, lbl in e.describe()))
        with self.assertRaises(ValueError):
            e.set_mode("nope")


if __name__ == "__main__":
    unittest.main(verbosity=0)
