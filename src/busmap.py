"""Bus map: where each device sits on the RS-485 line, and what that lets us calculate.

The program cannot SEE where a device is wired (Modbus carries no distance information), so the user draws the map:
the order of the devices along the trunk, the cable length of each segment, the length of each stub (branch), and
which ends carry a 120 ohm terminator. From that map we compute, with documented rules of thumb and the cable's
datasheet values:

  - cumulative distance, signal propagation delay and round trip per device (tiny, but shown to prove it),
  - total trunk and stub length, cable capacitance, loop resistance, attenuation at the signalling frequency,
  - whether the line is "electrically long" (terminators needed) from the transceiver rise time,
  - a recommended maximum baud rate for the trunk length, stub length limits, unit-load budget (32 UL),
  - voltage drop at powered nodes, termination / bias / topology warnings,
  - an exchange timeline (request out, device delay, response back) to animate.

All numbers are ESTIMATES from typical cable values; replace them with your cable's datasheet. A map is a dict:
  {"cable": "rs485", "baud": 19200, "master_at_end": true, "terminate_master_end": true, "terminate_far_end": true,
   "bias": true, "supply_v": 24, "rise_ns": 1000,
   "nodes": [{"name": "Meter1", "unit": 1, "segment_m": 20, "stub_m": 0.5, "unit_load": 1.0, "supply_ma": 0,
              "terminated": false}, ...]}
  python busmap.py map.json     prints the analysis
"""
import json
import math
import sys

C = 299_792_458.0  # m/s

CABLES = {  # typical values - replace with the datasheet of the cable you really use
    "rs485": {"label": "RS-485 shielded twisted pair 22 AWG (typical)", "vf": 0.75, "ohm_per_km": 55.0,
              "pf_per_m": 42.0, "db100_1mhz": 2.0, "z0": 120.0},
    "cat5e": {"label": "Cat5e UTP 24 AWG (typical)", "vf": 0.64, "ohm_per_km": 94.0,
              "pf_per_m": 52.0, "db100_1mhz": 2.0, "z0": 100.0},
    "custom": {"label": "Custom (enter your own values)", "vf": 0.66, "ohm_per_km": 80.0,
               "pf_per_m": 50.0, "db100_1mhz": 2.0, "z0": 120.0},
}
MAX_UNIT_LOADS = 32.0
SPEC_MAX_TRUNK_M = 1200.0
# rule of thumb for twisted pair at 120 ohm: (max trunk length in m, highest baud that is usually reliable)
BAUD_BY_LENGTH = [(100, 115200), (300, 57600), (600, 38400), (1000, 19200), (1200, 9600)]
# rule of thumb: longest stub (branch) by baud rate
STUB_BY_BAUD = [(9600, 20.0), (19200, 10.0), (38400, 5.0), (57600, 3.0), (10 ** 9, 1.0)]


class MapError(ValueError):
    pass


def default_map(devices=()):
    """A starter map: devices in Slave ID order, 10 m apart. devices: [(name, unit)]."""
    nodes = [{"name": n, "unit": u, "segment_m": 10.0, "stub_m": 0.0, "unit_load": 1.0, "supply_ma": 0.0,
              "terminated": False} for n, u in sorted(devices, key=lambda x: x[1])]
    return {"cable": "rs485", "baud": 19200, "master_at_end": True, "terminate_master_end": True,
            "terminate_far_end": True, "bias": True, "supply_v": 24.0, "rise_ns": 1000.0, "nodes": nodes}


def _f(d, key, default, lo=0.0, hi=None):
    try:
        v = float(d.get(key, default))
    except (TypeError, ValueError):
        raise MapError(f"{key}: not a number")
    if v < lo or (hi is not None and v > hi) or math.isnan(v):
        raise MapError(f"{key}: must be {'>= ' + str(lo) if hi is None else f'{lo}..{hi}'}")
    return v


def cable_of(m):
    base = dict(CABLES.get(m.get("cable", "rs485"), CABLES["custom"]))
    for k in ("vf", "ohm_per_km", "pf_per_m", "db100_1mhz", "z0"):
        if k in (m.get("cable_custom") or {}):
            base[k] = float((m["cable_custom"])[k])
    if not 0.3 <= base["vf"] <= 1.0:
        raise MapError("velocity factor must be between 0.3 and 1.0")
    return base


def validate(m):
    if not isinstance(m, dict):
        raise MapError("map must be an object")
    _f(m, "baud", 9600, 1)
    _f(m, "supply_v", 24, 0)
    _f(m, "rise_ns", 1000, 1)
    nodes = m.get("nodes", [])
    if not isinstance(nodes, list):
        raise MapError("nodes must be a list")
    units = []
    for i, n in enumerate(nodes, 1):
        if not isinstance(n, dict):
            raise MapError(f"node {i}: must be an object")
        _f(n, "segment_m", 0, 0, 5000)
        _f(n, "stub_m", 0, 0, 500)
        _f(n, "unit_load", 1.0, 0, 8)
        _f(n, "supply_ma", 0, 0, 100000)
        if "unit" in n and n["unit"] is not None:
            try:
                u = int(n["unit"])
            except (TypeError, ValueError):
                raise MapError(f"node {i}: Slave ID must be a whole number")
            if not 0 <= u <= 255:
                raise MapError(f"node {i}: Slave ID out of range")
            units.append(u)
    if len(set(units)) != len(units):
        raise MapError("two devices have the same Slave ID")
    cable_of(m)
    return m


def recommended_baud(trunk_m):
    for limit, baud in BAUD_BY_LENGTH:
        if trunk_m <= limit:
            return baud
    return None  # longer than the standard allows: needs a repeater


def max_stub_m(baud):
    for limit, m in STUB_BY_BAUD:
        if baud <= limit:
            return m
    return 1.0


def analyze(m):
    """Everything computed from a map. Returns a dict with 'nodes', totals, 'warnings' (list of (level, text))
    where level is 'error' | 'warn' | 'info'."""
    validate(m)
    cab = cable_of(m)
    baud = _f(m, "baud", 9600, 1)
    v = C * cab["vf"]
    pd_ns_per_m = 1e9 / v
    nodes_in = m.get("nodes", [])
    warns = []
    trunk, rows = 0.0, []
    stub_total = 0.0
    for n in nodes_in:
        trunk += float(n.get("segment_m", 0))
        stub = float(n.get("stub_m", 0))
        stub_total += stub
        dist = trunk + stub
        rows.append({"name": n.get("name", ""), "unit": n.get("unit"), "trunk_m": trunk, "stub_m": stub,
                     "distance_m": dist, "one_way_us": dist / v * 1e6, "round_trip_us": 2 * dist / v * 1e6,
                     "unit_load": float(n.get("unit_load", 1.0)), "segment_m": float(n.get("segment_m", 0))})
    if not m.get("master_at_end", True) and rows:  # master in the middle: the longest arm decides
        warns.append(("warn", "Master is in the middle of the line: both ends need a terminator and the master "
                              "connection counts as a stub - a daisy chain with the master at one end is more reliable."))
    far = max((r["distance_m"] for r in rows), default=0.0)
    total_len = trunk + stub_total
    loads = sum(r["unit_load"] for r in rows) + 1.0  # + the master's transceiver
    bit_us = 1e6 / baud
    rise_ns = _f(m, "rise_ns", 1000, 1)
    crit_m = (rise_ns / (4 * pd_ns_per_m))
    long_line = trunk > crit_m
    f_sig = baud / 2.0
    atten = cab["db100_1mhz"] * math.sqrt(max(f_sig, 1.0) / 1e6) * far / 100.0
    rec = recommended_baud(trunk)
    out = {
        "nodes": rows, "trunk_m": trunk, "stub_total_m": stub_total, "farthest_m": far, "cable": cab,
        "velocity_m_per_s": v, "ns_per_m": pd_ns_per_m, "max_one_way_us": far / v * 1e6,
        "max_round_trip_us": 2 * far / v * 1e6, "bit_time_us": bit_us, "bit_length_m": v * bit_us * 1e-6,
        "critical_length_m": crit_m, "electrically_long": long_line,
        "capacitance_nf": total_len * cab["pf_per_m"] / 1000.0,
        "loop_resistance_ohm": 2 * trunk * cab["ohm_per_km"] / 1000.0,
        "attenuation_db": atten, "signal_frequency_hz": f_sig,
        "unit_loads": loads, "unit_load_limit": MAX_UNIT_LOADS,
        "recommended_baud": rec, "baud": baud, "max_stub_m": max_stub_m(baud),
    }
    # ---- warnings ----
    if trunk > SPEC_MAX_TRUNK_M:
        warns.append(("error", f"Trunk is {trunk:.0f} m: longer than the {SPEC_MAX_TRUNK_M:.0f} m the RS-485 standard "
                               "covers. Use a repeater or split the bus."))
    if rec is not None and baud > rec:
        warns.append(("warn", f"{baud:.0f} baud is above the usual limit for a {trunk:.0f} m trunk (about {rec} baud). "
                              "Lower the baud rate or shorten the cable."))
    elif rec is None and trunk > 0:
        pass
    if loads > MAX_UNIT_LOADS:
        warns.append(("error", f"{loads:.1f} unit loads on the bus; a standard transceiver drives {MAX_UNIT_LOADS:.0f}. "
                               "Use 1/4 or 1/8 unit-load devices or a repeater."))
    lim = max_stub_m(baud)
    for r in rows:
        if r["stub_m"] > lim:
            warns.append(("warn", f"{r['name'] or 'Device'}: stub {r['stub_m']:g} m is longer than the usual limit "
                                  f"{lim:g} m at {baud:.0f} baud. Wire it as a daisy chain (in and out of the device)."))
    ends = int(bool(m.get("terminate_master_end", True))) + int(bool(m.get("terminate_far_end", True)))
    mid = [n.get("name") or n.get("unit") for n in nodes_in[:-1] if n.get("terminated")]
    if mid:
        warns.append(("error", "Terminator on a device in the middle of the line (" + ", ".join(map(str, mid)) +
                               "): only the two ends of the trunk get 120 ohm."))
    last_term = bool(nodes_in and nodes_in[-1].get("terminated"))
    far_term = bool(m.get("terminate_far_end", True)) or last_term
    if long_line and not (bool(m.get("terminate_master_end", True)) and far_term):
        warns.append(("warn", f"The trunk ({trunk:.0f} m) is longer than the critical length ({crit_m:.0f} m for "
                              f"{rise_ns:g} ns rise time): terminate BOTH ends with 120 ohm to stop reflections."))
    if not long_line and ends == 2 and trunk > 0:
        warns.append(("info", f"The trunk is shorter than the critical length ({crit_m:.0f} m): terminators are "
                              "optional here, but harmless at both ends."))
    if ends == 2 and not m.get("bias", True) and trunk > 0:
        warns.append(("info", "No bias resistors: add fail-safe bias (one point, usually at the master) so an idle line "
                              "does not float."))
    if out["bit_length_m"] > 0 and trunk < out["bit_length_m"] / 100:
        warns.append(("info", f"One bit is about {out['bit_length_m'] / 1000:.1f} km long at {baud:.0f} baud, so this "
                              "cable is electrically very short: the signal arrives at every device 'at once' and "
                              "propagation delay does not affect Modbus timing."))
    # ---- power: voltage drop along the trunk ----
    supply_v = _f(m, "supply_v", 24.0, 0)
    if any(float(n.get("supply_ma", 0)) > 0 for n in nodes_in) and supply_v > 0:
        remaining = sum(float(n.get("supply_ma", 0)) for n in nodes_in) / 1000.0  # amps still to be delivered
        drop, v_nodes = 0.0, []
        for n, r in zip(nodes_in, rows):
            drop += remaining * 2 * r["segment_m"] * cab["ohm_per_km"] / 1000.0
            v_nodes.append(supply_v - drop)
            remaining -= float(n.get("supply_ma", 0)) / 1000.0
        out["node_voltage"] = v_nodes
        out["min_voltage"] = min(v_nodes)
        if out["min_voltage"] < supply_v * 0.9:
            warns.append(("warn", f"Voltage at the far end is {out['min_voltage']:.1f} V of {supply_v:g} V "
                                  f"({100 * (1 - out['min_voltage'] / supply_v):.0f} % drop). Use thicker wires "
                                  "or a local supply."))
    out["warnings"] = warns
    return out


def exchange_timeline(an, link, unit, fc, qty, delay_ms=10.0):
    """Timeline (ms from the start of the request) of one request/response for a device on the map, for the animation.
    link: buscalc.Link. Returns dict with 'events' (t_ms, what) and 'total_ms'; propagation is included but tiny."""
    import buscalc
    node = next((r for r in an["nodes"] if r["unit"] == unit), None)
    if node is None:
        raise MapError(f"no device with Slave ID {unit} on the map")
    t = buscalc.request_ms(link, fc, qty, delay_ms)
    prop = node["one_way_us"] / 1000.0
    ev, now = [], 0.0
    if t["wait"]:
        ev.append((now, "bus idle (t3.5)"))
        now += t["wait"]
    ev.append((now, "master sends request"))
    ev.append((now + prop, "first bit reaches the device"))
    now += t["request"]
    ev.append((now + prop, "device has the whole request"))
    now += prop + t["device"]
    ev.append((now, "device sends response"))
    now += t["response"]
    ev.append((now + prop, "master has the response"))
    now += prop
    return {"events": ev, "total_ms": now + t.get("usb", 0.0) + t["gap"], "propagation_ms": 2 * prop,
            "parts": t}


def format_report(an):
    L = [f"Trunk {an['trunk_m']:.1f} m, stubs {an['stub_total_m']:.1f} m, farthest device {an['farthest_m']:.1f} m",
         f"Cable: {an['cable']['label']}  (signal {an['velocity_m_per_s'] / 1e8:.2f} x 10^8 m/s, {an['ns_per_m']:.2f} ns/m)",
         f"Propagation: farthest device {an['max_one_way_us']:.2f} us one way, {an['max_round_trip_us']:.2f} us round trip; "
         f"one bit lasts {an['bit_time_us']:.0f} us (= {an['bit_length_m'] / 1000:.1f} km of cable)",
         f"Capacitance {an['capacitance_nf']:.2f} nF, loop resistance {an['loop_resistance_ohm']:.1f} ohm, "
         f"attenuation {an['attenuation_db']:.2f} dB at {an['signal_frequency_hz'] / 1000:.1f} kHz",
         f"Unit loads {an['unit_loads']:.2f} of {an['unit_load_limit']:.0f};  critical length {an['critical_length_m']:.0f} m "
         f"({'electrically long: terminate' if an['electrically_long'] else 'short line'})",
         "Recommended max baud for this trunk: " + (str(an['recommended_baud']) if an['recommended_baud'] else
                                                    "n/a (longer than the standard)"), ""]
    for r in an["nodes"]:
        L.append(f"  {r['name'] or 'device':<16} ID {r['unit']!s:<4} at {r['distance_m']:7.1f} m  "
                 f"one way {r['one_way_us']:6.2f} us")
    L += ["", "Checks:"] + [f"  [{lv}] {t}" for lv, t in an["warnings"]]
    return "\n".join(L)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) != 1:
        print(__doc__)
        return 2
    with open(a[0], encoding="utf-8") as f:
        print(format_report(analyze(json.load(f))))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (MapError, OSError, ValueError) as e:
        print(f"error: {e}")
        sys.exit(1)
