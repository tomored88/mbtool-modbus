"""Signal generator for the Modbus simulator: makes a simulated slave's values move like a real device.

  mode      what it does
  static    values stay at the preset's "sim" value
  random    every numeric point does a bounded random walk, booleans flip now and then
  loop      every numeric point ramps min -> max and wraps around (sawtooth), enums/booleans cycle
  sine      smooth waves, a different phase per point
  profile   behaves like the device TYPE (meter, drive, level, pressure, flow, temperature, io)
  preset    only what the preset's own "sim_pattern" fields say, everything else stays static

A preset can describe a point's motion itself:
  {"name": "Flow", ..., "sim": 12.5, "sim_pattern": {"kind": "random_walk", "min": 5, "max": 20, "step": 0.05}}
and name its device type: "device_type": "flow" (otherwise it is guessed from names and units).
Points a master may write (access rw / w) are never moved: a setpoint written by the master stays where it is.

Pattern kinds: fixed, noise, random_walk, sine, ramp, triangle, square, sequence, counter, follow.
"""
import math
import random
import threading
import time

MODES = ("static", "random", "loop", "sine", "profile", "preset")
DEVICE_TYPES = ("generic", "meter", "drive", "level", "pressure", "flow", "temperature", "io")
KINDS = ("fixed", "noise", "random_walk", "sine", "ramp", "triangle", "square", "sequence", "counter", "follow")

_TYPE_LIMITS = {"uint16": (0, 65535), "int16": (-32768, 32767), "uint32": (0, 2 ** 32 - 1),
                "int32": (-2 ** 31, 2 ** 31 - 1), "uint64": (0, 2 ** 64 - 1), "int64": (-2 ** 63, 2 ** 63 - 1)}

DEVICE_WORDS = {  # words in the preset text that reveal the device type (checked in this order)
    "drive": ("inverter", "drive", "vfd", "frequency converter", "acs", "atv", "ms300", "sinamics", "motor"),
    "flow": ("flow", "promag", "ufc", "micro motion", "mag 6000", "coriolis"),
    "level": ("level", "vegapuls", "ultrasonic", "radar"),
    "pressure": ("pressure", "keller", "a2g", "transmitter"),
    "temperature": ("temperature", "thermo", "tk4s", "e5cc", "xy-md02", "controller"),
    "io": ("relay", "adam", "waveshare", "i/o", "digital"),
    "meter": ("meter", "energy", "sdm", "pzem", "ddsu", "dtsu", "kwh", "solar", "sun2000", "sunspec"),
}


def guess_device_type(preset):
    text = " ".join([preset.name, preset.manufacturer, preset.model,
                     str(preset.raw.get("description", "")), str(preset.raw.get("category", ""))]).lower()
    declared = str(preset.raw.get("device_type", "")).lower()
    if declared in DEVICE_TYPES:
        return declared
    for kind, words in DEVICE_WORDS.items():
        if any(w in text for w in words):
            return kind
    units = {str(p.unit).lower() for p in preset.points}
    if units & {"bar", "kpa", "mpa", "psi", "mbar"}:
        return "pressure"
    if units & {"kwh", "kw", "w", "a", "v"}:
        return "meter"
    if units & {"m3/h", "l/min", "l/h", "kg/h", "t/h"}:
        return "flow"
    if units & {"c", "°c", "degc"}:
        return "temperature"
    return "generic"


STATIC_WORDS = ("baud", "address", "format", "parity", "time-out", "timeout", "version", "serial", "model", "code",
                "setting", "config", "slave id", "unit id", "rated", "nominal", "range", "limit", "password", "type")
COUNTER_WORDS = ("error", "count", "hours", "operating time", "starts", "runtime")
STATE_WORDS = ("status", "state", "run", "fault", "alarm", "warning", "mode", "drive", "relay", "output", "input")


def _num(x, default):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class Pattern:
    """One moving value. value(t, dt, ctx) -> engineering value (float) for time t seconds since start."""

    def __init__(self, spec, rng, phase=0.0):
        self.spec = dict(spec)
        k = self.spec.get("kind", "fixed")
        if k not in KINDS:
            raise ValueError(f"unknown pattern kind '{k}' (use one of {', '.join(KINDS)})")
        self.kind, self.rng = k, rng
        s = self.spec
        self.lo, self.hi = _num(s.get("min"), 0.0), _num(s.get("max"), 100.0)
        if self.lo > self.hi:
            self.lo, self.hi = self.hi, self.lo
        self.period = max(0.2, _num(s.get("period"), 20.0))
        self.phase = _num(s.get("phase"), phase)  # 0..1 of a period
        self.noise = max(0.0, _num(s.get("noise"), 0.0))  # gaussian noise, fraction of the span
        self.step = max(0.0, _num(s.get("step"), 0.05))  # random walk: typical move per second, fraction of span
        self.values = list(s.get("values") or [0])
        self.hold = max(0.2, _num(s.get("hold"), 3.0))
        self.rate = _num(s.get("rate"), 1.0)  # counter: units per second
        self.signal = s.get("signal", "load")
        self.v = _num(s.get("start"), (self.lo + self.hi) / 2)

    def value(self, t, dt, ctx):
        span = self.hi - self.lo
        k = self.kind
        if k == "fixed":
            v = _num(self.spec.get("value"), self.v)
        elif k == "noise":
            v = (self.lo + self.hi) / 2 + self.rng.gauss(0, 1) * span * (self.noise or 0.02)
        elif k == "random_walk":
            mid = (self.lo + self.hi) / 2
            pull = 0.08 * dt * (mid - self.v)  # gentle pull to the middle keeps it from sticking to a limit
            self.v += pull + self.rng.gauss(0, 1) * span * self.step * math.sqrt(max(dt, 1e-3))
            self.v = min(self.hi, max(self.lo, self.v))
            v = self.v
        elif k == "sine":
            v = (self.lo + self.hi) / 2 + span / 2 * math.sin(2 * math.pi * (t / self.period + self.phase))
        elif k == "ramp":
            v = self.lo + span * ((t / self.period + self.phase) % 1.0)
        elif k == "triangle":
            x = (t / self.period + self.phase) % 1.0
            v = self.lo + span * (2 * x if x < 0.5 else 2 * (1 - x))
        elif k == "square":
            v = self.hi if ((t / self.period + self.phase) % 1.0) < 0.5 else self.lo
        elif k == "sequence":
            v = _num(self.values[int(t / self.hold + self.phase * len(self.values)) % len(self.values)], 0.0)
        elif k == "counter":
            v = _num(self.spec.get("start"), self.lo) + self.rate * t
            if self.hi > self.lo and self.spec.get("max") is not None:
                v = self.lo + (v - self.lo) % (self.hi - self.lo)
        else:  # follow: track a shared signal (0..1) of the device
            v = self.lo + span * min(1.0, max(0.0, ctx.get(self.signal, 0.0)))
        if self.noise and k not in ("noise", "fixed"):
            v += self.rng.gauss(0, 1) * span * self.noise
        return v


def _range_for(point, unit_default=None):
    """(lo, hi) for a point: its min/max, else around its sim value, else a sensible span for its unit."""
    if point.min is not None and point.max is not None:
        return float(point.min), float(point.max)
    base = _num(point.sim, None) if point.sim is not None else None
    if unit_default:
        return unit_default
    if base not in (None, 0):
        a, b = base * 0.8, base * 1.2
        return (min(a, b), max(a, b))
    if point.type in _TYPE_LIMITS and _TYPE_LIMITS[point.type][0] == 0:
        return 0.0, 100.0
    return -100.0, 100.0


def _unit_kind(point):
    """Rough physical meaning from the unit/name: voltage, current, power, freq, energy, temp, pressure, level, flow, rpm."""
    u, n = str(point.unit).lower(), point.name.lower()
    if u in ("kwh", "wh", "mwh", "kvarh", "m3", "l", "m³") or any(w in n for w in ("energy", "total", "volume", "counter", "mass total")):
        return "counter"
    if u == "v" or "voltage" in n:
        return "voltage"
    if u in ("a", "ma") and "current" in n or u == "a":
        return "current"
    if u in ("w", "kw", "va", "kva", "var", "kvar") or "power" in n:
        return "power"
    if u == "hz" or "frequency" in n:
        return "freq"
    if u in ("c", "°c", "degc", "k", "f", "°f") or "temp" in n:
        return "temp"
    if u in ("bar", "kpa", "mpa", "pa", "psi", "mbar") or "pressure" in n:
        return "pressure"
    if u in ("m", "mm", "cm", "%") and ("level" in n or "distance" in n or u == "%") or "level" in n:
        return "level"
    if "flow" in u or "/h" in u or "/min" in u or "flow" in n:
        return "flow"
    if u == "rpm" or "speed" in n:
        return "rpm"
    return "other"


class PointPlan:
    def __init__(self, point, pattern, label):
        self.point, self.pattern, self.label = point, pattern, label
        self.value = None  # last value written (engineering units)


class SignalEngine:
    """Drives a SimSlave's data store from a Preset. tick() can be called directly (tests) or start() runs a thread."""

    def __init__(self, preset, slave, mode="profile", device_type=None, interval=0.5, speed=1.0, seed=None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        self.preset, self.slave = preset, slave
        self.mode = mode
        self.device_type = device_type if device_type in DEVICE_TYPES else guess_device_type(preset)
        self.interval = max(0.05, float(interval))
        self.speed = max(0.01, float(speed))  # 2.0 = everything moves twice as fast
        self.paused = False
        self.overrides = {}  # point name -> pattern spec
        self.plans = []
        self.ticks = 0
        self.errors = {}
        self._seed = seed
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._t = 0.0  # virtual seconds, advanced by speed * real dt
        self._last = None
        self.rebuild()

    # ---------- planning ----------
    def rebuild(self):
        with self._lock:
            self.rng = random.Random(self._seed)
            self.plans = []
            for i, p in enumerate(self.preset.points):
                if p.type == "string" or p.access in ("rw", "w"):
                    continue  # text and settable points are left to the master
                spec, label = self._spec_for(p, i)
                self.plans.append(PointPlan(p, Pattern(spec, self.rng, phase=(i * 0.137) % 1.0), label))
            self._t, self._last = 0.0, None

    def _enum_values(self, p):
        vals = []
        for k in p.enum:
            try:
                vals.append(float(k) * p.scale + p.offset)
            except ValueError:
                pass
        return vals or [0.0]

    def _spec_for(self, p, i):
        if p.name in self.overrides:
            return self.overrides[p.name], "custom"
        own = getattr(p, "sim_pattern", None)
        mode = self.mode
        if mode == "static":
            return {"kind": "fixed", "value": self._base(p)}, "fixed"
        if mode == "preset":
            return (own, own.get("kind", "?")) if own else ({"kind": "fixed", "value": self._base(p)}, "fixed")
        if p.type == "bool":
            fast = 4.0 + (i % 5) * 3.0
            if mode == "loop":
                return {"kind": "square", "min": 0, "max": 1, "period": fast}, "toggle"
            if mode == "random":
                return {"kind": "random_walk", "min": 0, "max": 1, "step": 0.5}, "random flip"
            if mode == "profile" and own:
                return own, own.get("kind", "?")
            return {"kind": "square", "min": 0, "max": 1, "period": fast * 2}, "toggle"
        if p.enum and mode != "profile":
            vals = self._enum_values(p)
            if mode == "random":
                return {"kind": "sequence", "values": vals, "hold": 2.0 + (i % 3)}, "state cycle"
            return {"kind": "sequence", "values": vals, "hold": 4.0}, "state cycle"
        lo, hi = _range_for(p)
        if mode == "random":
            return {"kind": "random_walk", "min": lo, "max": hi, "step": 0.06}, "random walk"
        if mode == "loop":
            return {"kind": "ramp", "min": lo, "max": hi, "period": 20.0}, "ramp loop"
        if mode == "sine":
            return {"kind": "sine", "min": lo, "max": hi, "period": 20.0}, "sine"
        return self._profile_spec(p, i, own)

    @staticmethod
    def _base(p):
        v = _num(p.sim, None) if p.sim is not None else None
        if v is None:
            v = _num(p.min, 0.0)
        return v

    def _profile_spec(self, p, i, own):
        if own:
            return own, own.get("kind", "?")
        dev, kind = self.device_type, _unit_kind(p)
        base = self._base(p)
        lname = p.name.lower()
        if any(w in lname for w in STATIC_WORDS):  # settings, identity and limits do not move on a real device
            return {"kind": "fixed", "value": base}, "setting (fixed)"
        if any(w in lname for w in COUNTER_WORDS) and kind != "counter" and not p.enum:
            return {"kind": "counter", "start": base, "rate": 0.05}, "counter (always increasing)"
        if p.enum:
            if not any(w in lname for w in STATE_WORDS):
                return {"kind": "fixed", "value": base}, "setting (fixed)"
            vals = self._enum_values(p)
            return {"kind": "sequence", "values": vals, "hold": 10.0 if dev == "drive" else 8.0}, "status cycle"
        lo, hi = _range_for(p)
        has_range = p.min is not None and p.max is not None
        if kind == "counter":
            rate = max(abs(base) * 0.0004, 0.01) if base else 0.05
            return {"kind": "counter", "start": base, "rate": rate}, "counter (always increasing)"
        if dev == "drive":
            if kind == "freq":
                return {"kind": "follow", "signal": "speed", "min": 0, "max": hi if has_range else max(50.0, base)}, "follows motor speed"
            if kind in ("current", "power", "rpm"):
                top = hi if has_range else (abs(base) * 1.2 or 100.0)
                return {"kind": "follow", "signal": "speed", "min": 0, "max": top, "noise": 0.01}, "follows motor speed"
            if kind == "temp":
                return {"kind": "follow", "signal": "speed", "min": base * 0.8, "max": base * 1.4 if base else 70, "noise": 0.005}, "heats with load"
            return {"kind": "noise", "min": lo, "max": hi, "noise": 0.005}, "steady + noise"
        if dev == "meter":
            if kind == "voltage":
                b = base or 230.0
                return {"kind": "noise", "min": b * 0.99, "max": b * 1.01, "noise": 0.25}, "mains voltage + jitter"
            if kind in ("current", "power"):
                unit = str(p.unit).lower()
                typical = 2300.0 if unit in ("w", "va", "var") else 2.3 if unit in ("kw", "kva", "kvar") else 10.0
                top = hi if has_range else (abs(base) * 1.5 or typical)  # 230 V x 10 A, so power matches the current
                return {"kind": "follow", "signal": "load", "min": top * 0.1, "max": top, "noise": 0.01}, "follows load"
            if kind == "freq":
                return {"kind": "noise", "min": 49.9, "max": 50.1, "noise": 0.2}, "grid frequency jitter"
            if kind == "temp":
                return {"kind": "sine", "min": base - 2, "max": base + 2, "period": 90.0}, "slow drift"
        if dev == "level" or kind == "level":
            top = hi if has_range else (abs(base) * 1.5 or 100.0)
            return {"kind": "triangle", "min": top * 0.1, "max": top * 0.9, "period": 120.0}, "tank fills and empties"
        if dev == "pressure" or kind == "pressure":
            return {"kind": "random_walk", "min": lo, "max": hi, "step": 0.03}, "random walk around setpoint"
        if dev == "flow" or kind == "flow":
            return {"kind": "random_walk", "min": lo, "max": hi, "step": 0.05}, "fluctuating flow"
        if dev == "temperature" or kind == "temp":
            b = base if base else 25.0
            return {"kind": "sine", "min": b - 3, "max": b + 3, "period": 60.0, "noise": 0.01}, "slow drift + noise"
        if dev == "io":
            return {"kind": "sine", "min": lo, "max": hi, "period": 12.0 + 3 * (i % 4)}, "analog wave"
        return {"kind": "noise", "min": lo, "max": hi, "noise": 0.02}, "noise around value"

    # ---------- running ----------
    def context(self, t):
        """Shared signals (0..1) that several points follow: mains/process 'load' and a motor 'speed' cycle."""
        load = 0.55 + 0.3 * math.sin(2 * math.pi * t / 75.0) + 0.1 * math.sin(2 * math.pi * t / 11.0)
        cyc = t % 55.0  # stopped 5 s, accelerate 10 s, run 25 s, decelerate 10 s, stopped 5 s
        if cyc < 5:
            speed = 0.0
        elif cyc < 15:
            speed = (cyc - 5) / 10
        elif cyc < 40:
            speed = 1.0
        elif cyc < 50:
            speed = (50 - cyc) / 10
        else:
            speed = 0.0
        return {"load": min(1.0, max(0.0, load)), "speed": speed}

    def _write(self, p, v):
        sl = self.slave
        if p.type == "bool":
            (sl.coils if p.fc == 1 else sl.discrete)[p.address] = bool(round(v))
            return
        words = p.encode_sim(v)
        (sl.holding if p.fc == 3 else sl.input)[p.address:p.address + p.words] = words

    def tick(self, dt=None):
        """Advance by dt real seconds (default: the interval) and write every point. Returns {name: value}."""
        with self._lock:
            if dt is None:
                dt = self.interval
            self._t += dt * self.speed
            ctx = self.context(self._t)
            out = {}
            for pl in self.plans:
                try:
                    v = pl.pattern.value(self._t, dt * self.speed, ctx)
                    self._write(pl.point, v)
                    pl.value = v
                    out[pl.point.name] = v
                    self.errors.pop(pl.point.name, None)
                except Exception as e:  # one bad point must not stop the others
                    self.errors[pl.point.name] = str(e)
            self.ticks += 1
            self._last = out
            return out

    def values(self):
        return dict(self._last or {})

    def describe(self):
        """[(point name, unit, what it does)] for display."""
        return [(pl.point.name, pl.point.unit, pl.label) for pl in self.plans]

    def set_mode(self, mode, device_type=None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        self.mode = mode
        if device_type in DEVICE_TYPES:
            self.device_type = device_type
        self.rebuild()

    def set_override(self, name, spec):
        """Give one point its own pattern (spec dict) or None to go back to the mode's behaviour."""
        if name not in self.preset.by_name:
            raise KeyError(name)
        if spec is None:
            self.overrides.pop(name, None)
        else:
            Pattern(spec, random.Random(0))  # validates the kind
            self.overrides[name] = dict(spec)
        self.rebuild()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        last = time.monotonic()
        while not self._stop.wait(self.interval):
            now = time.monotonic()
            if not self.paused:
                self.tick(now - last)
            last = now

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def running(self):
        return bool(self._thread and self._thread.is_alive())
