"""Load test (not in the quick suite): N simulated RTU devices on ONE emulated RS-485 line, read in PLC-cycle order
by one master, for several bus gaps. Reports measured cycle vs buscalc's estimate and every error, so frame merging
between slaves (a slave seeing another slave's answer glued to the next request) shows up.

  python tests/load_bus.py [devices=40] [baud=115200] [cycles=5]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src"))
import sys
import time

import buscalc
from modbus_core import ModbusError, SerialMaster, SerialSlave, SimSlave, VirtualCable


def run(n=40, baud=115200, cycles=5, gaps=(0.05, 0.02, 0.005, 0.0), qty=10, delay=0.005):
    bits = 11
    cable = VirtualCable(baud=baud, bits_per_char=bits)
    stores, slaves = [], []
    for u in range(1, n + 1):
        st = SimSlave(listen=False)
        st.response_delay = delay
        for i in range(qty):
            st.holding[i] = u * 100 + i
        stores.append(st)
        slaves.append(SerialSlave(cable.url, u, baud, "E", store=st))
    m = SerialMaster(cable.url, baud, "E", 1, timeout=0.5)
    out = []
    try:
        for gap in gaps:
            errs, wrong, times = 0, 0, []
            for _c in range(cycles):
                t0 = time.perf_counter()
                for u in range(1, n + 1):
                    if gap:
                        time.sleep(gap)
                    try:
                        got = m.read_holding(u, 0, qty)
                        if got != [u * 100 + i for i in range(qty)]:
                            wrong += 1
                    except ModbusError:
                        errs += 1
                times.append((time.perf_counter() - t0) * 1000)
            link = buscalc.Link("rtu", baud, 8, "E", 1, gap_ms=gap * 1000)
            est = buscalc.estimate_cycle(link, [{"name": str(u), "unit": u, "requests": [(3, qty)],
                                                 "delay_ms": delay * 1000} for u in range(1, n + 1)])["total_ms"]
            avg = sum(times) / len(times)
            out.append({"gap_ms": gap * 1000, "avg_cycle_ms": round(avg, 1), "estimate_ms": round(est, 1),
                        "diff_pct": round(100 * (avg - est) / est, 1), "errors": errs, "wrong_data": wrong,
                        "requests": n * cycles})
            print(out[-1], flush=True)
    finally:
        m.close()
        for s in slaves:
            s.stop()
        cable.close()
    return out


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:]]
    run(*(a[:3] if a else []))
