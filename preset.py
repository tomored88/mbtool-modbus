"""Device presets: a JSON register map for one device model.

{
  "name": "...", "manufacturer": "...", "model": "...",
  "verified": false,                       # true only after checking against the real device
  "connection": {"type": "rtu", "baud": 9600, "parity": "N", "stop": 1, "data": 8, "unit": 1, "timeout": 1.0},
  "address_base": 0,                       # 0: addresses are protocol (0-based); 1: manual numbering (1-based)
  "alarms": [                              # optional, used by the GUI alert engine
    {"point": "Temperature", "when": "above", "value": 35, "for_s": 5, "hysteresis": 1, "message": "Too hot"}
  ],
  "points": [
    {"name": "Voltage L1", "address": 0, "fc": 3, "type": "float32", "word_order": "big",
     "scale": 1, "offset": 0, "unit": "V", "access": "r", "min": null, "max": null,
     "group": "measure", "enum": {"0": "Stop", "1": "Run"}, "sim": 230.0}
  ]
}
types: uint16 int16 uint32 int32 uint64 int64 float32 float64 bool (coil / discrete input) string ("length": registers, 2 ASCII chars each)
byte_swap: true swaps the two bytes inside every register (with word_order "little" this gives DCBA).
fc: 1 coils, 2 discrete inputs, 3 holding, 4 input registers. access: "r", "rw" or "w" = write-only command (never read; rw/w only for fc 1 and 3).
word_order (multi-register types): "big" = high word first, "little" = low word first.
Engineering value = raw * scale + offset (integer types only; floats use scale/offset the same way).
"""
import csv
import json
import struct

from modbus_core import ModbusError

TYPE_WORDS = {"uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "uint64": 4, "int64": 4,
              "float32": 2, "float64": 4, "bool": 1, "string": 1}  # string: register count from "length"
FMT = {"uint16": ">H", "int16": ">h", "uint32": ">I", "int32": ">i", "uint64": ">Q", "int64": ">q",
       "float32": ">f", "float64": ">d"}
def _swap_bytes(words):
    return [((x & 0xFF) << 8) | (x >> 8) for x in words]



class PresetError(ValueError):
    pass


class Point:
    def __init__(self, d, base):
        try:
            self.name = str(d["name"])
            self.fc = int(d.get("fc", 3))
            self.type = d.get("type", "bool" if self.fc in (1, 2) else "uint16")
            self.address = int(d["address"]) - base
        except KeyError as e:
            raise PresetError(f"point is missing field {e}: {d}")
        if self.type not in TYPE_WORDS:
            raise PresetError(f"{self.name}: unknown type {self.type}")
        if self.fc not in (1, 2, 3, 4):
            raise PresetError(f"{self.name}: fc must be 1, 2, 3 or 4")
        if (self.fc in (1, 2)) != (self.type == "bool"):
            raise PresetError(f"{self.name}: bool type goes with fc 1/2 only")
        if self.type == "string" and self.fc not in (3, 4):
            raise PresetError(f"{self.name}: string type needs fc 3 or 4")
        if not 0 <= self.address <= 65535:
            raise PresetError(f"{self.name}: address out of range after address_base")
        self.words = TYPE_WORDS[self.type]
        if self.type == "string":
            try:
                self.words = int(d.get("length", 1))
            except (TypeError, ValueError):
                raise PresetError(f"{self.name}: length must be a whole number of registers")
            if not 1 <= self.words <= 60:
                raise PresetError(f"{self.name}: string length must be 1..60 registers")
        if self.address + self.words > 65536:
            raise PresetError(f"{self.name}: runs past address 65535")
        self.word_order = d.get("word_order", "big")
        if self.word_order not in ("big", "little"):
            raise PresetError(f"{self.name}: word_order must be big or little")
        self.byte_swap = bool(d.get("byte_swap", False))  # swap the two bytes inside each register (BADC / DCBA)
        self.scale = float(d.get("scale", 1))
        self.offset = float(d.get("offset", 0))
        self.unit = d.get("unit", "")
        self.access = d.get("access", "r")
        if self.access not in ("r", "rw", "w") or (self.access in ("rw", "w") and self.fc in (2, 4)):
            raise PresetError(f"{self.name}: access must be r, rw or w (write-only); rw/w only on fc 1/3")
        self.min = d.get("min")
        self.max = d.get("max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise PresetError(f"{self.name}: min is greater than max")
        self.group = d.get("group", "default")
        self.enum = {str(k): v for k, v in (d.get("enum") or {}).items()}
        self.sim = d.get("sim")
        self.sim_pattern = d.get("sim_pattern")  # how the simulator moves this value, see signals.py
        if self.sim_pattern is not None:
            from signals import KINDS
            if not isinstance(self.sim_pattern, dict) or self.sim_pattern.get("kind") not in KINDS:
                raise PresetError(f"{self.name}: sim_pattern must be an object with kind one of {', '.join(KINDS)}")

    # ---- decode / encode ----
    def decode(self, words):
        """words: list of ints (registers) or [bool]. Returns dict(raw, value, text)."""
        if self.type == "bool":
            raw = bool(words[0])
            return {"raw": raw, "value": raw, "text": self.enum.get(str(int(raw)), str(int(raw)))}
        w = list(words[:self.words])
        if self.type == "string":
            text = "".join(chr(c) if 32 <= c < 127 else "" for c in
                           b"".join(struct.pack(">H", x) for x in w)).strip()
            return {"raw": text, "value": text, "text": text}
        if self.byte_swap:
            w = _swap_bytes(w)
        if self.word_order == "little":
            w = w[::-1]
        raw = struct.unpack(FMT[self.type], b"".join(struct.pack(">H", x) for x in w))[0]
        value = raw * self.scale + self.offset if (self.scale != 1 or self.offset) else raw
        whole = float(self.scale).is_integer() and float(self.offset).is_integer()
        if isinstance(value, float) and whole and value == int(value) and self.type.startswith(("uint", "int")):
            value = int(value)  # whole-number scale on an integer register stays an integer
        if isinstance(value, float):
            value = float(f"{value:.{7 if self.type == 'float32' else 12}g}")  # hide float32 representation noise
        text = self.enum.get(str(raw)) or (f"{value} {self.unit}".strip())
        return {"raw": raw, "value": value, "text": text}

    def encode_sim(self, value):
        """Like encode() for the simulator: no access/min/max checks, integers are rounded and limited to the type."""
        if self.type == "bool":
            return [bool(value)]
        raw = (float(value) - self.offset) / self.scale
        if self.type.startswith(("uint", "int")):
            bits = int(self.type.lstrip("uint"))
            lo, hi = (0, 2 ** bits - 1) if self.type.startswith("uint") else (-2 ** (bits - 1), 2 ** (bits - 1) - 1)
            raw = min(hi, max(lo, int(round(raw))))
        data = struct.pack(FMT[self.type], raw)
        w = list(struct.unpack(f">{self.words}H", data))
        if self.word_order == "little":
            w = w[::-1]
        return _swap_bytes(w) if self.byte_swap else w

    def encode(self, value):
        """Engineering value -> list of words (or [bool]). Enforces access, min/max and type range."""
        if self.access not in ("rw", "w"):
            raise PresetError(f"{self.name} is read-only")
        if self.type == "bool":
            return [bool(value)]
        if self.type == "string":
            data = str(value).encode("ascii", "replace")
            if len(data) > 2 * self.words:
                raise PresetError(f"{self.name}: text is longer than {2 * self.words} characters")
            data = data.ljust(2 * self.words, b"\0")
            return list(struct.unpack(f">{self.words}H", data))
        value = float(value)
        if self.min is not None and value < self.min or self.max is not None and value > self.max:
            raise PresetError(f"{self.name}: {value} outside allowed range [{self.min}, {self.max}]")
        raw = (value - self.offset) / self.scale
        if self.type.startswith(("uint", "int")):
            if raw != round(raw):
                raise PresetError(f"{self.name}: {value} is not a multiple of scale {self.scale}")
            raw = int(round(raw))
        try:
            data = struct.pack(FMT[self.type], raw)
        except struct.error as e:
            raise PresetError(f"{self.name}: value out of range for {self.type}") from e
        w = list(struct.unpack(f">{self.words}H", data))
        if self.word_order == "little":
            w = w[::-1]
        return _swap_bytes(w) if self.byte_swap else w


class Preset:
    def __init__(self, d):
        if not isinstance(d, dict) or "points" not in d:
            raise PresetError("preset must be an object with a 'points' list")
        self.raw = d  # the original dict (unknown keys such as "alarms" stay available)
        self.alarms = list(d.get("alarms") or [])
        self.name = d.get("name", "unnamed")
        self.manufacturer = d.get("manufacturer", "")
        self.model = d.get("model", "")
        self.verified = bool(d.get("verified", False))
        self.connection = d.get("connection", {})
        self.max_block = int(d.get("max_block", 125))  # largest register count one request may ask for
        if not 1 <= self.max_block <= 125:
            raise PresetError("max_block must be 1..125")
        base = int(d.get("address_base", 0))
        if base not in (0, 1):
            raise PresetError("address_base must be 0 or 1")
        self.points = [Point(p, base) for p in d["points"]]
        names = [p.name for p in self.points]
        if len(set(names)) != len(names):
            raise PresetError("point names must be unique")
        self.by_name = {p.name: p for p in self.points}

    def groups(self):
        return sorted({p.group for p in self.points})

    def blocks(self, group=None, max_gap=4):
        """Merge points of the same fc into read blocks (<=125 registers / 2000 bits, small gaps allowed)."""
        limit = {1: 2000, 2: 2000, 3: self.max_block, 4: self.max_block}
        out = []
        pts = sorted((p for p in self.points if group in (None, p.group) and p.access != "w"), key=lambda p: (p.fc, p.address))
        for p in pts:
            end = p.address + p.words
            b = out[-1] if out else None
            if b and b["fc"] == p.fc and p.address - b["end"] <= max_gap and end - b["start"] <= limit[p.fc]:
                b["end"] = max(b["end"], end)
                b["points"].append(p)
            else:
                out.append({"fc": p.fc, "start": p.address, "end": end, "points": [p]})
        return out

    def read(self, master, unit=None, group=None):
        """Read the points (all, or one group). Returns {name: {raw,value,text,unit}} or {name: {'error':..}}."""
        unit = unit if unit is not None else self.connection.get("unit", 1)
        result = {}
        for b in self.blocks(group):
            try:
                data = master.request(unit, b["fc"], addr=b["start"], qty=b["end"] - b["start"])
            except ModbusError as e:
                for p in b["points"]:
                    result[p.name] = {"error": str(e)}
                continue
            for p in b["points"]:
                i = p.address - b["start"]
                d = p.decode(data[i:i + p.words])
                d["unit"] = p.unit
                result[p.name] = d
        return result

    def write(self, master, name, value, unit=None):
        """Validate against the preset and write one point (FC 05 / 06 / 16)."""
        p = self.by_name.get(name)
        if p is None:
            raise PresetError(f"unknown point {name}")
        unit = unit if unit is not None else self.connection.get("unit", 1)
        words = p.encode(value)
        if p.type == "bool":
            master.write_coil(unit, p.address, words[0])
        elif len(words) == 1:
            master.write_register(unit, p.address, words[0])
        else:
            master.write_registers(unit, p.address, words)

    def fill_simulator(self, slave):
        """Load each point's 'sim' value (or its min/0) into a SimSlave data store."""
        for p in self.points:
            v = p.sim if p.sim is not None else (p.min if p.min is not None else 0)
            if p.type == "bool":
                (slave.coils if p.fc == 1 else slave.discrete)[p.address] = bool(v)
                continue
            if p.type == "string":
                saved, p.access = p.access, "rw"
                try:
                    words = p.encode(str(v) if p.sim is not None else "")
                finally:
                    p.access = saved
                (slave.holding if p.fc == 3 else slave.input)[p.address:p.address + p.words] = words
                continue
            saved, p.access = p.access, "rw"
            try:
                words = p.encode(v)
            finally:
                p.access = saved
            store = slave.holding if p.fc == 3 else slave.input
            store[p.address:p.address + p.words] = words


def load_preset(path):
    with open(path, encoding="utf-8") as f:
        return Preset(json.load(f))


def from_csv(path, name="imported", **meta):
    """Columns: name,address,fc,type,scale,offset,unit,access,min,max,group,word_order,byte_swap,length,enum
    (only name,address required; enum looks like '0=Stop;1=Run'). The result is marked verified=false."""
    pts = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            d = {k.strip(): v.strip() for k, v in row.items() if k and v is not None and v.strip() != ""}
            try:
                for k in ("address", "fc", "length"):
                    if k in d: d[k] = int(d[k], 0)
                for k in ("scale", "offset", "min", "max"):
                    if k in d: d[k] = float(d[k])
                if "byte_swap" in d:
                    d["byte_swap"] = d["byte_swap"].lower() in ("1", "true", "yes", "y")
                if "enum" in d:
                    d["enum"] = dict(item.split("=", 1) for item in d["enum"].split(";") if item.strip())
            except ValueError as e:
                raise PresetError(f"CSV row {n} ({d.get('name', '?')}): {e}") from e
            pts.append(d)
    return {"name": name, "verified": False, "points": pts, **meta}
