"""Automatic scans: serial settings (baud/parity/stop), Slave IDs, readable register ranges, device identification.

Everything here only READS (function codes 1-4, 17, 43/14). No write is ever sent.

  python scanner.py serial COM3 --units 1-10,247            find baud / parity / stop bits / Slave ID
  python scanner.py ids tcp://192.168.1.50:502 --units 1-247   which Slave IDs answer
  python scanner.py regs tcp://127.0.0.1:5020 -u 1 -f 3 --start 0 --end 999   readable register ranges
  python scanner.py identify tcp://127.0.0.1:5020 -u 1       vendor / product (FC43) and matching presets

A reply that is valid Modbus - including an exception reply - proves the settings and Slave ID are right.
Garbage that does not decode (CRC / LRC error, wrong Slave ID) is reported as NOISE: something is talking at
that setting, so the baud or parity is probably close.
"""
import argparse
import glob
import json
import os
import re
import sys
import threading
import time

from modbus_core import ModbusError, ModbusException

BAUDS = [9600, 19200, 38400, 115200, 4800, 2400, 1200, 57600]
PARITIES = ["N", "E", "O"]
STOPS = [1, 2]
# most common field configurations first, so a typical device is found within seconds
PRIORITY = [(9600, "N", 1), (9600, "E", 1), (19200, "E", 1), (19200, "N", 1), (9600, "N", 2), (38400, "N", 1),
            (115200, "N", 1), (4800, "N", 1), (19200, "N", 2), (38400, "E", 1), (57600, "N", 1), (2400, "N", 1)]

NOISE_HINTS = ("CRC", "LRC", "malformed", "bad ASCII", "unexpected", "does not echo", "short response",
               "empty response", "MBAP")


def parse_ranges(text, lo=0, hi=255):
    """'1-10,247' -> [1..10, 247]. Raises ValueError on bad input."""
    out = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = (int(x, 0) for x in part.split("-", 1))
        else:
            a = b = int(part, 0)
        if a > b or a < lo or b > hi:
            raise ValueError(f"range {part} is outside {lo}..{hi}")
        out.extend(range(a, b + 1))
    if not out:
        raise ValueError("empty list")
    return list(dict.fromkeys(out))


def settings_order(bauds=None, parities=None, stops=None):
    bauds, parities, stops = bauds or BAUDS, parities or PARITIES, stops or STOPS
    allowed = {(b, p, s) for b in bauds for p in parities for s in stops}
    ordered = [c for c in PRIORITY if c in allowed]
    ordered += [(b, p, s) for b in bauds for p in parities for s in stops if (b, p, s) not in ordered]
    return ordered


def _classify(exc):
    """What a failed probe tells us: 'ok' (exception reply = alive), 'noise' or 'silent'."""
    if isinstance(exc, ModbusException):
        return "ok"
    msg = str(exc)
    if "timeout" in msg.lower():
        return "silent"
    if any(h in msg for h in NOISE_HINTS):
        return "noise"
    return "silent"


def probe(master, unit, fc=3, addr=0):
    """One read. Returns ('ok'|'noise'|'silent', detail)."""
    try:
        master.request(unit, fc, addr=addr, qty=1)
        return "ok", "valid reply"
    except ModbusError as e:
        return _classify(e), str(e)


def scan_serial_settings(open_master, units=(1,), fc=3, addr=0, bauds=None, parities=None, stops=None,
                         stop_on_first=True, progress=None, stop_event=None, max_found=50):
    """Try every serial setting x Slave ID. open_master(baud, parity, stop) -> Master (closed again by this function).
    progress(done, total, text) is called after every setting; stop_event (threading.Event) aborts early."""
    combos = settings_order(bauds, parities, stops)
    result = {"found": [], "noise": [], "errors": [], "tried": 0, "total": len(combos), "aborted": False,
              "started": time.time()}
    for i, (baud, par, stop) in enumerate(combos):
        if stop_event is not None and stop_event.is_set():
            result["aborted"] = True
            break
        try:
            m = open_master(baud, par, stop)
        except Exception as e:  # port busy / missing: no point in continuing silently
            result["errors"].append(f"{baud} {par} {stop}: {e}")
            if len(result["errors"]) >= 3:
                break
            continue
        try:
            noise = False
            for unit in units:
                if stop_event is not None and stop_event.is_set():
                    result["aborted"] = True
                    break
                state, detail = probe(m, unit, fc, addr)
                if state == "ok":
                    result["found"].append({"baud": baud, "parity": par, "stop": stop, "unit": unit,
                                            "detail": detail})
                    if len(result["found"]) >= max_found:
                        break
                elif state == "noise":
                    noise = True
            if noise:
                result["noise"].append({"baud": baud, "parity": par, "stop": stop})
        finally:
            m.close()
        result["tried"] = i + 1
        if progress:
            progress(i + 1, len(combos), f"{baud} {par}{stop}")
        if (stop_on_first and result["found"]) or result["aborted"]:
            break
    result["elapsed"] = round(time.time() - result["started"], 2)
    return result


def scan_unit_ids(master, units, fc=3, addr=0, progress=None, stop_event=None):
    """Which Slave IDs answer (a valid exception counts as present). Master must already be connected."""
    found, noise = [], []
    for n, unit in enumerate(units, 1):
        if stop_event is not None and stop_event.is_set():
            break
        state, detail = probe(master, unit, fc, addr)
        if state == "ok":
            found.append(unit)
        elif state == "noise":
            noise.append(unit)
        if progress:
            progress(n, len(units), f"unit {unit}")
    return {"found": found, "noise": noise, "scanned": len(units)}


def _merge(ranges):
    out = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def scan_register_ranges(master, unit, fc=3, start=0, end=999, block=10, max_requests=3000, progress=None,
                         stop_event=None):
    """Find readable address ranges. Reads in blocks; a block that fails with an address exception is split
    down to single registers so the exact boundaries are found. Timeouts are counted and skipped."""
    limit = 2000 if fc in (1, 2) else 125
    block = max(1, min(block, limit))
    good, requests, timeouts = [], 0, 0
    stack = [(a, min(a + block - 1, end)) for a in range(start, end + 1, block)][::-1]
    total = len(stack)
    done = 0
    while stack:
        if stop_event is not None and stop_event.is_set():
            break
        if requests >= max_requests:
            break
        lo, hi = stack.pop()
        requests += 1
        try:
            master.request(unit, fc, addr=lo, qty=hi - lo + 1)
            good.append((lo, hi))
        except ModbusException as e:
            if hi > lo and e.code in (2, 3):  # part of the block is missing: split and look closer
                mid = (lo + hi) // 2
                stack.append((mid + 1, hi))
                stack.append((lo, mid))
        except ModbusError:
            timeouts += 1
        done += 1
        if progress and done % 10 == 0:
            progress(done, max(total, done), f"{lo}-{hi}")
    return {"ranges": _merge(good), "requests": requests, "timeouts": timeouts,
            "truncated": requests >= max_requests and bool(stack)}


# ---------- device identification ----------
OBJECT_NAMES = {0: "VendorName", 1: "ProductCode", 2: "MajorMinorRevision", 3: "VendorUrl", 4: "ProductName",
                5: "ModelName", 6: "UserApplicationName"}


def read_device_id(master, unit, max_requests=8):
    """FC43/14 Read Device Identification (basic category) and FC17 Report Slave ID. Returns a dict, {} if neither
    is supported."""
    info = {}
    obj, loops = 0, 0
    while loops < max_requests:
        loops += 1
        try:
            with master.lock:
                resp = master._transact(unit, bytes([43, 14, 1, obj]))
        except ModbusError:
            break
        if not resp or resp[0] != 43 or len(resp) < 7 or resp[1] != 14:
            break  # exception reply (function not supported) or garbage
        more, nxt, count = resp[4], resp[5], resp[6]
        pos = 7
        for _ in range(count):
            if pos + 2 > len(resp):
                break
            oid, ln = resp[pos], resp[pos + 1]
            val = resp[pos + 2:pos + 2 + ln]
            info[OBJECT_NAMES.get(oid, f"object{oid}")] = val.decode("ascii", "replace").strip("\x00 ")
            pos += 2 + ln
        if more == 0xFF:
            obj = nxt
        else:
            break
    try:
        with master.lock:
            resp = master._transact(unit, bytes([17]))
        if resp and resp[0] == 17 and len(resp) >= 2:
            data = resp[2:2 + resp[1]]
            info["ReportSlaveId"] = {"hex": data.hex(" "), "ascii": "".join(chr(c) if 32 <= c < 127 else "." for c in data)}
    except ModbusError:
        pass
    return info


def suggest_presets(info, folder=None, limit=5):
    """Rank preset files by how well their manufacturer/model/name match the identification strings."""
    text = " ".join(str(v) for k, v in info.items() if k != "ReportSlaveId").lower()
    if "ReportSlaveId" in info:
        text += " " + info["ReportSlaveId"]["ascii"].lower()
    tokens = {t for t in re.split(r"[^a-z0-9]+", text) if len(t) >= 3}
    if not tokens:
        return []
    import appinfo
    folder = folder or appinfo.preset_dir()
    scored = []
    for path in glob.glob(os.path.join(folder, "**", "*.json"), recursive=True):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        hay = " ".join(str(d.get(k, "")) for k in ("name", "manufacturer", "model")).lower()
        htoks = {t for t in re.split(r"[^a-z0-9]+", hay) if len(t) >= 3}
        score = len(tokens & htoks)
        if score:
            scored.append((score, os.path.relpath(path, folder).replace("\\", "/"), d.get("name", "")))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [{"preset": p, "name": n, "matching_words": s} for s, p, n in scored[:limit]]


# ---------- command line ----------
def _progress(done, total, text):
    print(f"\r  {done}/{total}  {text:<24}", end="", file=sys.stderr, flush=True)


def main():
    from modbus_core import SerialMaster
    from mbtool import connect
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serial")
    s.add_argument("port", help="COM3 or socket://host:port")
    s.add_argument("--units", default="1,2,3,247", help="Slave IDs to try, e.g. 1-10,247")
    s.add_argument("--mode", default="rtu", choices=["rtu", "ascii"])
    s.add_argument("--timeout", type=float, default=0.35)
    s.add_argument("--fc", type=int, default=3, choices=[1, 2, 3, 4])
    s.add_argument("--addr", type=int, default=0)
    s.add_argument("--bauds", help="comma list, default all common speeds")
    s.add_argument("--all", action="store_true", help="do not stop at the first match")
    for name in ("ids", "regs", "identify"):
        p = sub.add_parser(name)
        p.add_argument("url")
        p.add_argument("-u", "--unit", type=int, default=1)
        if name == "ids":
            p.add_argument("--units", default="1-247")
        if name == "regs":
            p.add_argument("-f", "--fc", type=int, default=3, choices=[1, 2, 3, 4])
            p.add_argument("--start", type=int, default=0)
            p.add_argument("--end", type=int, default=999)
            p.add_argument("--block", type=int, default=10)
    a = ap.parse_args()

    if a.cmd == "serial":
        units = parse_ranges(a.units, 0, 247)
        bauds = [int(x) for x in a.bauds.split(",")] if a.bauds else None
        print(f"scanning {a.port}: up to {len(settings_order(bauds))} settings x {len(units)} IDs, "
              f"worst case about {len(settings_order(bauds)) * len(units) * a.timeout:.0f} s", file=sys.stderr)
        res = scan_serial_settings(
            lambda b, p, st: SerialMaster(a.port, b, p, st, 8, a.mode, a.timeout), units, a.fc, a.addr,
            bauds=bauds, stop_on_first=not a.all, progress=_progress)
        print(file=sys.stderr)
        print(json.dumps(res, indent=2))
        return 0 if res["found"] else 1
    m = connect(a.url)
    try:
        if a.cmd == "ids":
            print(json.dumps(scan_unit_ids(m, parse_ranges(a.units, 0, 247), progress=_progress), indent=2))
        elif a.cmd == "regs":
            print(json.dumps(scan_register_ranges(m, a.unit, a.fc, a.start, a.end, a.block, progress=_progress), indent=2))
        else:
            info = read_device_id(m, a.unit)
            print(json.dumps({"identification": info, "suggested_presets": suggest_presets(info)}, indent=2))
    finally:
        m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
