"""Test Center: check how a Modbus slave behaves against the Modbus Application Protocol rules.

Read-only by default (safe for real devices). Write tests change registers and need --write plus a scratch
address that the device lets you modify; every written value is restored afterwards when possible.

  python testcenter.py tcp://127.0.0.1:5020 -u 1 --addr 0
  python testcenter.py serial:COM3?baud=19200&parity=E -u 1 --addr 100 --write --scratch 200

Results are PASS / FAIL / WARN / SKIP. FAIL = clearly breaks the spec, WARN = allowed but unusual
(for example no reply to a malformed request), SKIP = not applicable (area not supported by the device).
"""
import argparse
import statistics
import struct
import sys
import time

from modbus_core import EXC_NAMES, ModbusError, ModbusException

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"


class Result:
    def __init__(self, name, status, detail=""):
        self.name, self.status, self.detail = name, status, detail

    def as_dict(self):
        return {"test": self.name, "status": self.status, "detail": self.detail}

    def __str__(self):
        return f"[{self.status}] {self.name}" + (f" - {self.detail}" if self.detail else "")


def _raw(m, unit, pdu):
    """Send a hand-made PDU, return the response PDU (or raise ModbusError on timeout)."""
    with m.lock:
        return m._transact(unit, pdu)


def _expect_exception(m, unit, pdu, codes, name, allow_silence=False):
    try:
        resp = _raw(m, unit, pdu)
    except ModbusError as e:
        if allow_silence:
            return Result(name, WARN, f"no response ({e}); a slave may stay silent on malformed requests")
        return Result(name, FAIL, f"no response ({e}); expected exception {sorted(codes)}")
    if resp and resp[0] == pdu[0] | 0x80 and len(resp) >= 2:
        if resp[1] in codes:
            return Result(name, PASS, f"exception {resp[1]} ({EXC_NAMES.get(resp[1], '?')})")
        return Result(name, FAIL, f"exception {resp[1]} ({EXC_NAMES.get(resp[1], '?')}), expected one of {sorted(codes)}")
    return Result(name, FAIL, f"normal response {resp.hex(' ')[:40]}, expected exception {sorted(codes)}")


def run(m, unit=1, addr=0, write=False, scratch=None, repeat=20, areas=(1, 2, 3, 4)):
    out = []
    add = out.append
    supported = {}

    # ---- basic reads, one area at a time ----
    names = {1: "coils", 2: "discrete inputs", 3: "holding registers", 4: "input registers"}
    for fc in areas:
        try:
            vals = m.request(unit, fc, addr=addr, qty=1)
            supported[fc] = True
            add(Result(f"FC{fc:02d} read 1 {names[fc]} at {addr}", PASS, f"value {vals[0]}"))
        except ModbusException as e:
            supported[fc] = False
            add(Result(f"FC{fc:02d} read 1 {names[fc]} at {addr}", SKIP if e.code in (1, 2) else FAIL,
                       f"exception {e.code} ({EXC_NAMES.get(e.code, '?')})"))
        except ModbusError as e:
            supported[fc] = False
            add(Result(f"FC{fc:02d} read 1 {names[fc]} at {addr}", FAIL, str(e)))

    # ---- response size / count checks (parse_response validates byte counts) ----
    for fc in areas:
        if not supported.get(fc):
            continue
        n = 8 if fc in (1, 2) else 4
        try:
            vals = m.request(unit, fc, addr=addr, qty=n)
            add(Result(f"FC{fc:02d} multi-read {n} returns exactly {n} items", PASS if len(vals) == n else FAIL,
                       f"got {len(vals)}"))
        except ModbusException as e:
            add(Result(f"FC{fc:02d} multi-read {n}", WARN, f"exception {e.code}: address range may not exist"))
        except ModbusError as e:
            add(Result(f"FC{fc:02d} multi-read {n}", FAIL, str(e)))

    # ---- illegal quantities (exception 3) and addresses (exception 2) ----
    limits = {1: 2000, 2: 2000, 3: 125, 4: 125}
    for fc in areas:
        if not supported.get(fc):
            continue
        add(_expect_exception(m, unit, struct.pack(">BHH", fc, addr, 0), {3}, f"FC{fc:02d} quantity 0 -> exception 3"))
        add(_expect_exception(m, unit, struct.pack(">BHH", fc, addr, limits[fc] + 1), {3},
                              f"FC{fc:02d} quantity {limits[fc] + 1} (over limit) -> exception 3"))
        add(_expect_exception(m, unit, struct.pack(">BHH", fc, 65535, 2), {2, 3},
                              f"FC{fc:02d} read past address 65535 -> exception 2"))

    # ---- unsupported / malformed requests ----
    add(_expect_exception(m, unit, bytes([0x41, 0, 0, 0, 1]), {1}, "unknown function 0x41 -> exception 1"))
    add(_expect_exception(m, unit, bytes([0x03, 0x00]), {3, 1}, "truncated FC03 request -> exception 3",
                          allow_silence=True))

    # ---- timing ----
    fc = next((f for f in areas if supported.get(f)), None)
    if fc and repeat >= 3:
        times, errors = [], 0
        for _ in range(repeat):
            t = time.perf_counter()
            try:
                m.request(unit, fc, addr=addr, qty=1)
                times.append((time.perf_counter() - t) * 1000)
            except ModbusError:
                errors += 1
        if times:
            add(Result(f"response time over {repeat} reads", PASS if errors == 0 else WARN,
                       f"min {min(times):.1f} / avg {statistics.fmean(times):.1f} / max {max(times):.1f} ms, "
                       f"errors {errors}"))
        else:
            add(Result(f"response time over {repeat} reads", FAIL, "all reads failed"))

    # ---- write tests (only on request, on a scratch address) ----
    if not write:
        add(Result("write tests (FC05/06/15/16/22/23)", SKIP, "not requested; use --write --scratch <address>"))
        return out
    s = addr if scratch is None else scratch
    add(Result("write tests scratch address", WARN, f"writing to address {s} (values are restored afterwards)"))

    def guarded(name, body):
        try:
            body()
        except ModbusException as e:
            add(Result(name, SKIP if e.code in (1, 2) else FAIL, f"exception {e.code} ({EXC_NAMES.get(e.code, '?')})"))
        except ModbusError as e:
            add(Result(name, FAIL, str(e)))

    if supported.get(3):
        def regs():
            old = m.read_holding(unit, s, 4)
            try:
                m.write_register(unit, s, 0x1234)
                ok = m.read_holding(unit, s, 1) == [0x1234]
                add(Result("FC06 write single register then read back", PASS if ok else FAIL))
                m.write_registers(unit, s, [1, 2, 3, 4])
                ok = m.read_holding(unit, s, 4) == [1, 2, 3, 4]
                add(Result("FC16 write 4 registers then read back", PASS if ok else FAIL))
                m.write_register(unit, s, 0x12F0)
                m.mask_write(unit, s, 0x00F2, 0x0025)
                want = (0x12F0 & 0x00F2) | (0x0025 & ~0x00F2 & 0xFFFF)
                got = m.read_holding(unit, s, 1)[0]
                add(Result("FC22 mask write register", PASS if got == want else FAIL, f"got 0x{got:04X}, want 0x{want:04X}"))
                got = m.read_write(unit, s, 2, s, [9, 8])
                add(Result("FC23 read/write multiple registers", PASS if got == [9, 8] else FAIL, f"read back {got}"))
            finally:
                m.write_registers(unit, s, old)
        guarded("holding register write tests", regs)
    else:
        add(Result("holding register write tests", SKIP, "FC03 not readable here"))

    if supported.get(1):
        def coils():
            old = m.read_coils(unit, s, 4)
            try:
                m.write_coil(unit, s, True)
                add(Result("FC05 write coil ON then read back", PASS if m.read_coils(unit, s, 1) == [True] else FAIL))
                m.write_coils(unit, s, [True, False, True, True])
                got = m.read_coils(unit, s, 4)
                add(Result("FC15 write 4 coils then read back", PASS if got == [True, False, True, True] else FAIL,
                           f"read {got}"))
                add(_expect_exception(m, unit, struct.pack(">BHH", 5, s, 0x1234), {3},
                                      "FC05 value 0x1234 (neither 0000 nor FF00) -> exception 3"))
            finally:
                m.write_coils(unit, s, old)
        guarded("coil write tests", coils)
    else:
        add(Result("coil write tests", SKIP, "FC01 not readable here"))
    return out


def summary(results):
    c = {k: sum(r.status == k for r in results) for k in (PASS, FAIL, WARN, SKIP)}
    return f"{c[PASS]} passed, {c[FAIL]} failed, {c[WARN]} warnings, {c[SKIP]} skipped"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("-u", "--unit", type=int, default=1)
    ap.add_argument("--addr", type=int, default=0, help="address that exists in the device (default 0)")
    ap.add_argument("--write", action="store_true", help="also run write tests (changes then restores values)")
    ap.add_argument("--scratch", type=int, help="address safe to overwrite for write tests (default: --addr)")
    ap.add_argument("--repeat", type=int, default=20)
    a = ap.parse_args()
    from mbtool import connect
    m = connect(a.url)
    try:
        res = run(m, a.unit, a.addr, a.write, a.scratch, a.repeat)
    finally:
        m.close()
    for r in res:
        print(r)
    print("\n" + summary(res))
    sys.exit(1 if any(r.status == FAIL for r in res) else 0)


if __name__ == "__main__":
    main()
