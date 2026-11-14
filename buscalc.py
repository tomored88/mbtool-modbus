"""Bus timing calculator: how long does one scan cycle over a Modbus line take, and what changes it?

The model is the one a PLC programmer uses to size a bus. Per request:

    wait t3.5 (RTU) + request frame + device response delay + response frame + master gap (+ USB adapter latency)

All times in milliseconds. Frame time = bytes x bits per character / baud. This is an ESTIMATE from the protocol and
the numbers you give (device response delay, adapter latency); measure the real bus (Devices > Cycle) to confirm.

  python buscalc.py --baud 19200 --parity E --preset presets/library/eastron_sdm230.json --unit 1 \\
                    --preset presets/library/schneider_atv320.json --unit 2 [--delay 20] [--timeout 1000]
"""
import argparse
import math
import sys

from modbus_core import ModbusError

BAUDS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
READ_FCS = (1, 2, 3, 4)


class Link:
    """kind: rtu | ascii | tcp. gap_ms is the master's pause between two requests, timeout_ms what an absent
    device costs per attempt, usb_latency_ms the adapter's receive delay (FTDI default is 16 ms!)."""

    def __init__(self, kind="rtu", baud=9600, data_bits=8, parity="N", stop=1, gap_ms=0.0, timeout_ms=1000.0,
                 usb_latency_ms=0.0, tcp_rtt_ms=2.0, retries=0):
        if kind not in ("rtu", "ascii", "tcp"):
            raise ValueError("kind must be rtu, ascii or tcp")
        if baud <= 0:
            raise ValueError("baud must be positive")
        if parity not in ("N", "E", "O"):
            raise ValueError("parity must be N, E or O")
        if stop not in (1, 2):
            raise ValueError("stop bits must be 1 or 2")
        self.kind, self.baud, self.data_bits, self.parity, self.stop = kind, int(baud), int(data_bits), parity, stop
        self.gap_ms, self.timeout_ms = float(gap_ms), float(timeout_ms)
        self.usb_latency_ms, self.tcp_rtt_ms, self.retries = float(usb_latency_ms), float(tcp_rtt_ms), int(retries)

    @property
    def bits_per_char(self):
        return 1 + self.data_bits + (0 if self.parity == "N" else 1) + self.stop

    def char_ms(self):
        return 1000.0 * self.bits_per_char / self.baud

    def bytes_ms(self, n):
        return n * self.char_ms()

    def t35_ms(self):
        """RTU inter-frame silence: 3.5 characters, but never less than 1.75 ms above 19200 baud (spec)."""
        if self.kind != "rtu":
            return 0.0
        return 1.75 if self.baud > 19200 else 3.5 * self.char_ms()

    def describe(self):
        if self.kind == "tcp":
            return f"TCP (round trip {self.tcp_rtt_ms:g} ms)"
        return f"{self.kind.upper()} {self.baud} {self.data_bits}{self.parity}{self.stop}"


def frame_bytes(fc, qty, write=False):
    """(request bytes, response bytes) on an RTU line including address and CRC. qty = registers or bits."""
    if fc in (1, 2):
        return 8, 5 + math.ceil(qty / 8)
    if fc in (3, 4):
        return 8, 5 + 2 * qty
    if fc in (5, 6):
        return 8, 8
    if fc == 15:
        return 9 + math.ceil(qty / 8), 8
    if fc == 16:
        return 9 + 2 * qty, 8
    if fc == 22:
        return 10, 10
    if fc == 23:
        return 13 + 2 * qty, 5 + 2 * qty
    return 8, 8


def request_ms(link, fc, qty, device_delay_ms=10.0):
    """Timing of one request/response. Returns a dict (all ms) with the parts and 'total'."""
    rq, rs = frame_bytes(fc, qty)
    if link.kind == "tcp":
        pdu_q, pdu_s = rq - 3 + 7, rs - 3 + 7  # MBAP header (7) instead of address+CRC
        total = link.tcp_rtt_ms + device_delay_ms + link.gap_ms
        return {"request": 0.0, "response": 0.0, "wait": link.tcp_rtt_ms, "device": device_delay_ms, "gap": link.gap_ms,
                "total": total, "bytes": pdu_q + pdu_s, "line_ms": 0.0}
    mult = 2 if link.kind == "ascii" else 1  # ASCII sends every byte as 2 characters + ':' CR LF
    extra = 3 if link.kind == "ascii" else 0
    if link.kind == "ascii":
        rq_chars, rs_chars = (rq - 2 + 1) * mult + extra, (rs - 2 + 1) * mult + extra  # LRC (1) replaces CRC (2)
    else:
        rq_chars, rs_chars = rq, rs
    req, resp = link.bytes_ms(rq_chars), link.bytes_ms(rs_chars)
    wait = link.t35_ms()
    usb = link.usb_latency_ms
    total = wait + req + device_delay_ms + resp + usb + link.gap_ms
    return {"request": req, "response": resp, "wait": wait, "device": device_delay_ms, "gap": link.gap_ms,
            "usb": usb, "total": total, "bytes": rq_chars + rs_chars, "line_ms": req + resp}


def requests_for_preset(preset, group=None):
    """[(fc, qty)] one entry per read block the preset needs per cycle."""
    return [(b["fc"], b["end"] - b["start"]) for b in preset.blocks(group)]


def estimate_cycle(link, devices):
    """devices: list of dicts {name, unit, requests:[(fc, qty)], delay_ms=10, online=True}.
    Returns {total_ms, line_busy_ms, utilisation_pct, cycles_per_s, requests, bytes, devices:[...]}."""
    out_dev, total, busy, nreq, nbytes = [], 0.0, 0.0, 0, 0
    for d in devices:
        t, line, rows = 0.0, 0.0, []
        for fc, qty in d.get("requests", []):
            if d.get("online", True):
                r = request_ms(link, fc, qty, d.get("delay_ms", 10.0))
                cost = r["total"]
                line += r["line_ms"]
                nbytes += r["bytes"]
            else:  # no answer: the master waits the timeout (per attempt) and moves on
                r = {"total": (link.timeout_ms + link.gap_ms) * (1 + link.retries), "bytes": 0, "line_ms": 0.0}
                cost = r["total"]
            t += cost
            nreq += 1
            rows.append({"fc": fc, "qty": qty, "ms": cost})
        out_dev.append({"name": d.get("name", ""), "unit": d.get("unit"), "ms": t, "requests": rows,
                        "online": d.get("online", True), "delay_ms": d.get("delay_ms", 10.0)})
        total += t
        busy += line
    return {"total_ms": total, "line_busy_ms": busy, "utilisation_pct": (100.0 * busy / total) if total else 0.0,
            "cycles_per_s": (1000.0 / total) if total else 0.0, "requests": nreq, "bytes": nbytes, "devices": out_dev,
            "link": link.describe()}


def what_if(link, devices, bauds=None):
    """Cycle time for each baud rate with everything else equal: [(baud, total_ms, utilisation_pct)]."""
    rows = []
    for b in bauds or BAUDS:
        l = Link(link.kind, b, link.data_bits, link.parity, link.stop, link.gap_ms, link.timeout_ms,
                 link.usb_latency_ms, link.tcp_rtt_ms, link.retries)
        e = estimate_cycle(l, devices)
        rows.append((b, e["total_ms"], e["utilisation_pct"]))
    return rows


def advice(link, est, target_ms=None):
    """Plain-language hints from an estimate."""
    tips = []
    total = est["total_ms"]
    if not total:
        return ["nothing to poll"]
    if link.kind != "tcp" and est["utilisation_pct"] < 35:
        tips.append(f"The line is busy only {est['utilisation_pct']:.0f} % of the cycle: a faster baud rate will help little; "
                    "device response delay, gap and adapter latency dominate.")
    elif link.kind != "tcp" and link.baud < 38400:
        tips.append(f"The line is busy {est['utilisation_pct']:.0f} % of the cycle: a higher baud rate shortens the cycle "
                    "if every device supports it.")
    if link.usb_latency_ms >= 8:
        tips.append("USB adapter latency is large (FTDI default 16 ms): lower the latency timer to 1-2 ms in the driver.")
    if link.gap_ms > 20:
        tips.append("The gap between requests is long; lower it if the devices answer reliably.")
    offline = [d["name"] for d in est["devices"] if not d["online"]]
    if offline:
        tips.append("Offline devices cost a full timeout each cycle: " + ", ".join(offline)
                    + ". Remove them or lower the timeout.")
    many = [d for d in est["devices"] if len(d["requests"]) > 3]
    if many:
        tips.append("Devices with many separate blocks (" + ", ".join(d["name"] for d in many[:3])
                    + ") cost one request each: group points that are close together or use a larger max_block.")
    if target_ms and total > target_ms:
        tips.append(f"Cycle {total:.0f} ms is longer than the target {target_ms:g} ms.")
    elif target_ms:
        tips.append(f"Cycle {total:.0f} ms fits the target {target_ms:g} ms with {target_ms - total:.0f} ms to spare.")
    return tips or ["Nothing stands out."]


def format_report(link, est, target_ms=None):
    lines = [f"Link: {est['link']}   gap {link.gap_ms:g} ms   timeout {link.timeout_ms:g} ms   USB latency {link.usb_latency_ms:g} ms",
             f"Cycle: {est['total_ms']:.1f} ms  ({est['cycles_per_s']:.2f} cycles/s)   requests {est['requests']}   "
             f"bytes {est['bytes']}   line busy {est['utilisation_pct']:.0f} %", ""]
    for d in est["devices"]:
        lines.append(f"  {d['name'] or 'device'} (ID {d['unit']}): {d['ms']:.1f} ms in {len(d['requests'])} request(s)"
                     + ("" if d["online"] else "  [offline: timeout]"))
        for r in d["requests"]:
            lines.append(f"      FC{r['fc']:02d} x{r['qty']:<4} {r['ms']:.1f} ms")
    lines.append("")
    lines.append("Baud what-if: " + "  ".join(f"{b}: {t:.0f} ms" for b, t, _u in what_if(link, [
        {"name": d["name"], "unit": d["unit"], "requests": [(r["fc"], r["qty"]) for r in d["requests"]],
         "online": d["online"], "delay_ms": d["delay_ms"]} for d in est["devices"]])) if link.kind != "tcp" else "")
    lines += ["", "Hints:"] + ["  - " + t for t in advice(link, est, target_ms)]
    return "\n".join(lines)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="Estimate Modbus scan cycle time")
    ap.add_argument("--kind", default="rtu", choices=["rtu", "ascii", "tcp"])
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--parity", default="N", choices=["N", "E", "O"])
    ap.add_argument("--stop", type=int, default=1, choices=[1, 2])
    ap.add_argument("--gap", type=float, default=0.0, help="master gap between requests, ms")
    ap.add_argument("--timeout", type=float, default=1000.0)
    ap.add_argument("--usb-latency", type=float, default=0.0, help="USB adapter latency, ms (FTDI default 16)")
    ap.add_argument("--delay", type=float, default=10.0, help="device response delay, ms")
    ap.add_argument("--target", type=float, help="wanted cycle time, ms")
    ap.add_argument("--preset", action="append", default=[], help="preset file (repeat for each device)")
    ap.add_argument("--unit", action="append", type=int, default=[], help="Slave ID for each --preset, same order")
    a = ap.parse_args(argv)
    from preset import load_preset
    link = Link(a.kind, a.baud, 8, a.parity, a.stop, a.gap, a.timeout, a.usb_latency)
    devs = []
    for i, path in enumerate(a.preset):
        pr = load_preset(path)
        devs.append({"name": pr.name, "unit": a.unit[i] if i < len(a.unit) else i + 1,
                     "requests": requests_for_preset(pr), "delay_ms": a.delay})
    if not devs:
        ap.error("give at least one --preset")
    print(format_report(link, estimate_cycle(link, devs), a.target))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ModbusError, ValueError, OSError) as e:
        print(f"error: {e}")
        sys.exit(1)
