"""Bus health test: measures how each device on a bus really answers, so timeout and gap can be set from data
instead of guesses.

Phase 1 - response time: every device is read `samples` times with a long probe timeout (so slow answers are
measured, not cut off) and a safe gap. -> round trip min / avg / p95 / max, device turnaround (round trip minus
the time the frames take on the wire), jitter, errors by kind.
Phase 2 - gap sweep (serial lines only): all devices are read round robin with shrinking gaps between requests.
The smallest gap with no errors is how tightly this line can be polled.

Result: a grade per device (good / warn / bad / no answer), a recommended timeout and gap for the bus, the
expected PLC cycle time with them, and plain advice. Baud rate is NOT swept: the devices keep their own baud
setting, a master that changes its baud just loses them.

Works with any master that has request(unit, fc, addr=, qty=) - TcpMaster, SerialMaster, a shared HubMaster.

  python bushealth.py --host 127.0.0.1 --port 502 --units 1,2,3 [--fc 3 --addr 0 --qty 10] [--samples 20]
  python bushealth.py --com COM3 --baud 19200 --parity E --units 1-5
"""
import argparse
import json
import math
import sys
import time

from monitor import classify_error, ERROR_KIND_NAMES

DEFAULT_GAPS_MS = (50, 20, 10, 5, 2, 0)


def set_master_timeout(m, seconds):
    """Change a master's response timeout in place; returns the old value (seconds) or None if unknown."""
    ser = getattr(m, "ser", None)
    if ser is not None:
        old = ser.timeout
        ser.timeout = seconds
        return old
    if hasattr(m, "sock") or hasattr(m, "host"):
        old = getattr(m, "timeout", None)
        m.timeout = seconds
        sock = getattr(m, "sock", None)
        if sock is not None:
            sock.settimeout(seconds)
        return old
    return None


def percentile(values, p):
    """Nearest-rank percentile of a list (p in 0..100); None when empty."""
    vals = sorted(values)
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, math.ceil(p / 100.0 * len(vals)) - 1))
    return vals[k]


def time_stats(times):
    if not times:
        return {"n": 0, "min": None, "avg": None, "p95": None, "max": None, "jitter": None}
    return {"n": len(times), "min": min(times), "avg": sum(times) / len(times), "p95": percentile(times, 95),
            "max": max(times), "jitter": max(times) - min(times)}


def wire_ms(kind, baud, bits, fc, qty):
    """Time the request + response frames take on a serial line (ms); 0 for TCP."""
    if kind == "tcp" or not baud:
        return 0.0
    from buscalc import frame_bytes
    rq, rs = frame_bytes(fc, qty)
    if kind == "ascii":
        rq, rs = (rq - 1) * 2 + 3, (rs - 1) * 2 + 3
    return (rq + rs) * bits * 1000.0 / baud


def _round_up(ms, step):
    return int(math.ceil(ms / step) * step)


class HealthTest:
    """targets: [{name, unit, fc, addr, qty}] (one representative read per device).
    open_master(): returns a master (closed with .close() at the end).
    kind: rtu | ascii | tcp; baud / bits for the wire-time part; base_gap_ms: gap used in phase 1.
    progress(text, fraction) is called from the testing thread; stop() -> True aborts."""

    def __init__(self, open_master, targets, kind="rtu", baud=None, bits=11, samples=20, probe_timeout_s=2.0,
                 base_gap_ms=50.0, gaps_ms=DEFAULT_GAPS_MS, sweep_rounds=5, current_timeout_ms=None,
                 progress=None, stop=None, sleep=time.sleep, clock=time.perf_counter):
        self.open_master, self.targets = open_master, list(targets)
        self.kind, self.baud, self.bits = kind, baud, bits
        self.samples, self.probe_timeout_s = max(1, int(samples)), float(probe_timeout_s)
        self.base_gap_ms, self.gaps_ms = float(base_gap_ms), sorted(set(float(g) for g in gaps_ms), reverse=True)
        self.sweep_rounds = max(1, int(sweep_rounds))
        self.current_timeout_ms = current_timeout_ms
        self.progress = progress or (lambda text, frac: None)
        self.stop = stop or (lambda: False)
        self.sleep, self.clock = sleep, clock
        self._last_end = 0.0

    # ----- one request -----
    def _one(self, m, t, gap_ms):
        wait = self._last_end + gap_ms / 1000.0 - self.clock()
        if wait > 0:
            self.sleep(wait)
        t0 = self.clock()
        try:
            m.request(int(t["unit"]), int(t["fc"]), addr=int(t["addr"]), qty=int(t["qty"]))
            err = None
        except Exception as e:  # noqa: BLE001 - every failure is a measured result
            err = str(e) or repr(e)
        t1 = self.clock()
        self._last_end = t1
        return (t1 - t0) * 1000.0, err

    def run(self):
        started = time.time()
        m = self.open_master()
        old_to = set_master_timeout(m, self.probe_timeout_s)
        if self.current_timeout_ms is None and old_to is not None:
            self.current_timeout_ms = old_to * 1000.0
        devices = []
        aborted = False
        try:
            n_total = len(self.targets) * self.samples
            done = 0
            for t in self.targets:
                rec = {"name": t["name"], "unit": int(t["unit"]), "fc": int(t["fc"]), "addr": int(t["addr"]),
                       "qty": int(t["qty"]), "times": [], "errors": {}, "requests": 0, "last_error": None}
                for _ in range(self.samples):
                    if self.stop():
                        aborted = True
                        break
                    ms, err = self._one(m, t, self.base_gap_ms)
                    rec["requests"] += 1
                    if err is None:
                        rec["times"].append(ms)
                    else:
                        k = classify_error(err)
                        rec["errors"][k] = rec["errors"].get(k, 0) + 1
                        rec["last_error"] = err
                        if k == "link" and not rec["times"]:
                            break  # the port / connection itself is gone; more tries tell nothing
                    done += 1
                    self.progress(f"Response time: {t['name']} ({rec['requests']}/{self.samples})", done / max(n_total, 1) * 0.7)
                devices.append(rec)
                if aborted:
                    break
            sweep = []
            reachable = [d for d in devices if d["times"]]
            if not aborted and self.kind != "tcp" and reachable and len(self.gaps_ms) > 1:
                set_master_timeout(m, self._sweep_timeout_s(devices))
                tmap = {t["name"]: t for t in self.targets}
                for gi, gap in enumerate(self.gaps_ms):
                    errs, kinds, n = 0, {}, 0
                    for _r in range(self.sweep_rounds):
                        for d in reachable:
                            if self.stop():
                                aborted = True
                                break
                            _ms, err = self._one(m, tmap[d["name"]], gap)
                            n += 1
                            if err is not None:
                                errs += 1
                                k = classify_error(err)
                                kinds[k] = kinds.get(k, 0) + 1
                        if aborted:
                            break
                    sweep.append({"gap_ms": gap, "requests": n, "errors": errs, "kinds": kinds})
                    self.progress(f"Gap sweep: {gap:g} ms - {errs} error(s)", 0.7 + 0.3 * (gi + 1) / len(self.gaps_ms))
                    if aborted:
                        break
        finally:
            try:
                set_master_timeout(m, old_to if old_to is not None else 1.0)
            finally:
                try:
                    m.close()
                except Exception:  # noqa: BLE001
                    pass
        return self._report(devices, sweep, aborted, started)

    def _sweep_timeout_s(self, devices):
        worst = max((max(d["times"]) for d in devices if d["times"]), default=500.0)
        return max(0.1, min(self.probe_timeout_s, worst * 2.0 / 1000.0 + 0.05))

    # ----- evaluation -----
    def _report(self, devices, sweep, aborted, started):
        out_dev = []
        for d in devices:
            st = time_stats(d["times"])
            nerr = sum(d["errors"].values())
            rate = 100.0 * nerr / d["requests"] if d["requests"] else 0.0
            wire = wire_ms(self.kind, self.baud, self.bits, d["fc"], d["qty"])
            turn = None if st["avg"] is None else max(0.0, st["avg"] - wire)
            grade, why = self._grade(st, nerr, rate, d)
            rec_to = None
            if st["max"] is not None:
                rec_to = max(100, _round_up(max(st["max"] * 1.5, st["max"] + 50.0), 50))
            out_dev.append({"name": d["name"], "unit": d["unit"], "fc": d["fc"], "addr": d["addr"], "qty": d["qty"],
                            "requests": d["requests"], "ok": len(d["times"]), "errors": nerr, "error_pct": rate,
                            "kinds": d["errors"], "last_error": d["last_error"], "rtt": st, "wire_ms": wire,
                            "turnaround_ms": turn, "grade": grade, "why": why, "timeout_ms": rec_to})
        timeouts = [d["timeout_ms"] for d in out_dev if d["timeout_ms"]]
        rec_timeout = max(timeouts) if timeouts else None
        clean = [s["gap_ms"] for s in sweep if s["errors"] == 0 and s["requests"]]
        # the smallest gap that is clean AND every larger gap tested was clean too
        min_gap = None
        for s in sweep:  # sweep runs from the largest gap down
            if s["errors"] == 0 and s["requests"]:
                min_gap = s["gap_ms"]
            else:
                break
        rec_gap = None
        if self.kind == "tcp":
            rec_gap = 0.0
        elif min_gap is not None:
            rec_gap = 0.0 if min_gap == 0 else min(self.base_gap_ms, max(min_gap * 2, min_gap + 5))
        cycle_ms = None
        ok_devs = [d for d in out_dev if d["rtt"]["avg"] is not None]
        if ok_devs:
            cycle_ms = sum(d["rtt"]["avg"] for d in ok_devs) + (rec_gap if rec_gap is not None else self.base_gap_ms) * len(ok_devs)
            dead = [d for d in out_dev if d["rtt"]["avg"] is None]
            cycle_ms += len(dead) * (rec_timeout or self.current_timeout_ms or 1000.0)
        rep = {"kind": self.kind, "baud": self.baud, "bits": self.bits, "samples": self.samples,
               "probe_timeout_ms": self.probe_timeout_s * 1000.0, "base_gap_ms": self.base_gap_ms,
               "current_timeout_ms": self.current_timeout_ms, "devices": out_dev, "sweep": sweep,
               "clean_gaps": clean, "min_clean_gap_ms": min_gap, "recommended_gap_ms": rec_gap,
               "recommended_timeout_ms": rec_timeout, "expected_cycle_ms": cycle_ms, "aborted": aborted,
               "started": started, "seconds": time.time() - started}
        rep["advice"] = advice(rep)
        return rep

    def _grade(self, st, nerr, rate, d):
        if st["n"] == 0:
            k = max(d["errors"], key=d["errors"].get) if d["errors"] else "timeout"
            return "no answer", f"no valid answer ({ERROR_KIND_NAMES.get(k, k)})"
        if rate >= 5.0:
            return "bad", f"{rate:.0f}% of the requests failed"
        if nerr:
            return "warn", f"{nerr} error(s) in {d['requests']} requests"
        if st["n"] >= 5 and st["avg"] and st["max"] > 3 * st["avg"] and st["max"] - st["avg"] > 20:
            return "warn", f"irregular: slowest answer {st['max']:.0f} ms vs average {st['avg']:.0f} ms"
        if self.current_timeout_ms and st["max"] > 0.7 * self.current_timeout_ms:
            return "warn", f"slowest answer {st['max']:.0f} ms is close to the timeout {self.current_timeout_ms:.0f} ms"
        return "good", "every request answered, steady timing"


def advice(rep):
    """Plain advice lines from a health report."""
    out = []
    devs = rep["devices"]
    if rep.get("aborted"):
        out.append("The test was stopped early: results cover only part of the bus.")
    dead = [d["name"] for d in devs if d["grade"] == "no answer"]
    if dead:
        out.append(f"No answer from {', '.join(dead)}: check Slave ID, baud/parity, wiring (A/B swapped?) and power. "
                   "On a PLC cycle every absent device costs a full timeout.")
    for d in devs:
        k = d["kinds"]
        if k.get("crc"):
            out.append(f"{d['name']}: {k['crc']} CRC error(s) = damaged frames. Check termination (120 ohm at both ends "
                       "only), shield / ground, cable length for the baud rate, and stubs.")
        if k.get("wrong_id"):
            out.append(f"{d['name']}: answers from a different Slave ID - two devices may share an ID, or a late answer "
                       "arrived after the timeout.")
        if k.get("exception"):
            out.append(f"{d['name']}: the device rejects the request (Modbus exception) - the register address / "
                       "count or function code is not supported. This is configuration, not wiring.")
        if k.get("frame"):
            out.append(f"{d['name']}: incomplete or malformed answers - noise, a too short timeout or an adapter that "
                       "splits frames (USB latency).")
    cur = rep.get("current_timeout_ms")
    rt = rep.get("recommended_timeout_ms")
    if rt:
        slow = max(devs, key=lambda d: d["rtt"]["max"] or 0)
        if cur and cur < rt:
            out.append(f"Timeout {cur:.0f} ms is too short: the slowest answer ({slow['name']}) took "
                       f"{slow['rtt']['max']:.0f} ms. Use at least {rt} ms.")
        elif cur and cur > 4 * rt and cur - rt > 300:
            out.append(f"Timeout {cur:.0f} ms is much longer than needed ({rt} ms is enough): an absent device then "
                       "blocks the bus for less time.")
        else:
            out.append(f"Recommended timeout: {rt} ms (slowest answer {slow['rtt']['max']:.0f} ms x 1.5, rounded).")
    if rep["kind"] != "tcp":
        if rep.get("min_clean_gap_ms") is not None:
            g = rep["min_clean_gap_ms"]
            out.append(f"The line stayed error free down to a gap of {g:g} ms; recommended gap {rep['recommended_gap_ms']:g} ms "
                       "(a little margin).")
        elif rep["sweep"]:
            out.append("Errors even at the largest gap tested: fix the errors above before tuning speed.")
    if rep.get("expected_cycle_ms"):
        out.append(f"Expected PLC cycle with the recommended settings: about {rep['expected_cycle_ms']:.0f} ms for "
                   f"{len(devs)} device(s).")
    slow = [d for d in devs if d["turnaround_ms"] is not None and d["turnaround_ms"] > 100]
    for d in slow:
        out.append(f"{d['name']} needs {d['turnaround_ms']:.0f} ms to answer - it dominates the cycle; read fewer "
                   "blocks from it or poll it less often.")
    if not out:
        out.append("All devices answered quickly and without errors.")
    return out


def format_report(rep):
    lines = []
    link = "TCP" if rep["kind"] == "tcp" else f"{rep['kind'].upper()} {rep['baud']} baud"
    lines.append(f"Bus health test - {link}, {rep['samples']} requests per device, probe timeout "
                 f"{rep['probe_timeout_ms']:.0f} ms ({rep['seconds']:.1f} s)")
    lines.append(f"{'Device':<16}{'ID':>4}  {'Grade':<10}{'OK':>5}{'Err':>5}  {'avg':>7}{'p95':>7}{'max':>7}  "
                 f"{'turn':>6}  errors by kind")
    for d in rep["devices"]:
        r = d["rtt"]
        f = lambda v: "-" if v is None else f"{v:.1f}"  # noqa: E731
        kinds = ", ".join(f"{ERROR_KIND_NAMES.get(k, k)} {n}" for k, n in d["kinds"].items())
        lines.append(f"{d['name'][:15]:<16}{d['unit']:>4}  {d['grade']:<10}{d['ok']:>5}{d['errors']:>5}  "
                     f"{f(r['avg']):>7}{f(r['p95']):>7}{f(r['max']):>7}  {f(d['turnaround_ms']):>6}  {kinds}")
    if rep["sweep"]:
        lines.append("Gap sweep: " + ",  ".join(f"{s['gap_ms']:g} ms: {s['errors']}/{s['requests']} err"
                                                for s in rep["sweep"]))
    rec = f"Recommended: timeout {rep['recommended_timeout_ms'] or '-'} ms"
    if rep["kind"] != "tcp":
        rec += f", gap {'-' if rep['recommended_gap_ms'] is None else format(rep['recommended_gap_ms'], 'g')} ms"
    lines.append(rec)
    lines.extend("- " + a for a in rep["advice"])
    return "\n".join(lines)


def _units(text):
    out = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bus health test: real response times, errors by kind, "
                                             "recommended timeout and gap")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int, default=502)
    ap.add_argument("--com")
    ap.add_argument("--baud", type=int, default=19200)
    ap.add_argument("--parity", default="E", choices=["N", "E", "O"])
    ap.add_argument("--stop", type=int, default=1, choices=[1, 2])
    ap.add_argument("--ascii", action="store_true")
    ap.add_argument("--units", default="1")
    ap.add_argument("--fc", type=int, default=3, choices=[1, 2, 3, 4])
    ap.add_argument("--addr", type=int, default=0)
    ap.add_argument("--qty", type=int, default=10)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--probe-timeout", type=float, default=2.0, help="seconds")
    ap.add_argument("--timeout", type=float, default=1000.0,
                    help="the timeout (ms) your master / PLC uses now, to compare with the recommendation")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    from modbus_core import SerialMaster, TcpMaster
    if a.host:
        kind = "tcp"
        def opener():
            return TcpMaster(a.host, a.port, timeout=a.probe_timeout)
    elif a.com:
        kind = "ascii" if a.ascii else "rtu"
        def opener():
            return SerialMaster(a.com, a.baud, a.parity, a.stop, 8, kind, timeout=a.probe_timeout)
    else:
        ap.error("give --host or --com")
    targets = [{"name": f"ID {u}", "unit": u, "fc": a.fc, "addr": a.addr, "qty": a.qty} for u in _units(a.units)]
    bits = 1 + 8 + (0 if a.parity == "N" else 1) + a.stop
    ht = HealthTest(opener, targets, kind=kind, baud=None if kind == "tcp" else a.baud, bits=bits, samples=a.samples,
                    probe_timeout_s=a.probe_timeout, current_timeout_ms=a.timeout,
                    progress=lambda text, frac: print(f"  {frac * 100:5.1f}%  {text}", file=sys.stderr))
    rep = ht.run()
    print(json.dumps(rep, indent=1, default=str) if a.json else format_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
