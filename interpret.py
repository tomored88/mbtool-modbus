"""Show one register (or 2 / 4 consecutive registers) in every common interpretation at once.

Use this when a register map is unknown or doubtful: the right reading usually looks sane (a plausible
temperature, a clean ASCII name, a timestamp) while the wrong ones look random.

  python interpret.py tcp://127.0.0.1:5020 -u 1 -f 3 -a 0 -n 4
  python interpret.py --words 0x4348 0xCCCD          # offline, no device
"""
import argparse
import datetime
import math
import struct
import sys

# Byte orders of a 32-bit value spelled in Modbus word notation: A B C D = bytes from the first register
# (A high byte) to the second register (D low byte).
ORDERS32 = {"ABCD (big-endian)": (0, 1, 2, 3), "CDAB (word-swapped)": (2, 3, 0, 1),
            "BADC (byte-swapped)": (1, 0, 3, 2), "DCBA (little-endian)": (3, 2, 1, 0)}
ORDERS64 = {"big-endian": (0, 1, 2, 3, 4, 5, 6, 7),
            "word-swapped": (6, 7, 4, 5, 2, 3, 0, 1),
            "byte-swapped": (1, 0, 3, 2, 5, 4, 7, 6),
            "little-endian": (7, 6, 5, 4, 3, 2, 1, 0)}


def _bytes(words):
    return b"".join(struct.pack(">H", w & 0xFFFF) for w in words)


def _fmt_float(x):
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "inf" if x > 0 else "-inf"
    return f"{x:.7g}"


def _ascii(b):
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


def _bcd(b):
    digits = ""
    for c in b:
        hi, lo = c >> 4, c & 15
        if hi > 9 or lo > 9:
            return None
        digits += f"{hi}{lo}"
    return digits.lstrip("0") or "0"


def _epoch(v):
    if 946684800 <= v <= 4102444800:  # 2000-01-01 .. 2100-01-01
        return datetime.datetime.fromtimestamp(v, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return None


def interpret(words):
    """words: 1..4 register values (ints 0..65535). Returns a list of (group, label, text) rows."""
    words = [int(w) & 0xFFFF for w in words][:4]
    n = len(words)
    rows = []
    add = lambda g, l, t: t is None or rows.append((g, l, t))

    w = words[0]
    b = _bytes([w])
    add("16-bit", "Unsigned", str(w))
    add("16-bit", "Signed", str(w - 65536 if w > 32767 else w))
    add("16-bit", "Hex", f"0x{w:04X}")
    add("16-bit", "Binary", f"{w >> 8:08b} {w & 255:08b}")
    add("16-bit", "Bits set", ", ".join(str(i) for i in range(16) if w >> i & 1) or "none")
    add("16-bit", "Byte-swapped unsigned", str(((w & 255) << 8) | (w >> 8)))
    add("16-bit", "Float16 (half)", _fmt_float(struct.unpack(">e", b)[0]))
    add("16-bit", "BCD", _bcd(b))
    add("16-bit", "ASCII", _ascii(b))
    add("16-bit", "Scaled /10 /100 /1000 (signed)",
        " | ".join(f"{(w - 65536 if w > 32767 else w) / d:g}" for d in (10, 100, 1000)))

    if n >= 2:
        raw = _bytes(words[:2])
        for name, order in ORDERS32.items():
            r = bytes(raw[i] for i in order)
            u, s = struct.unpack(">I", r)[0], struct.unpack(">i", r)[0]
            add("32-bit " + name, "Unsigned", str(u))
            add("32-bit " + name, "Signed", str(s))
            add("32-bit " + name, "Float32", _fmt_float(struct.unpack(">f", r)[0]))
            add("32-bit " + name, "Unix time", _epoch(u))
            add("32-bit " + name, "BCD", _bcd(r) if u else None)
        add("32-bit", "ASCII (2 regs)", _ascii(raw))

    if n >= 4:
        raw = _bytes(words)
        for name, order in ORDERS64.items():
            r = bytes(raw[i] for i in order)
            add("64-bit " + name, "Unsigned", str(struct.unpack(">Q", r)[0]))
            add("64-bit " + name, "Signed", str(struct.unpack(">q", r)[0]))
            add("64-bit " + name, "Float64", _fmt_float(struct.unpack(">d", r)[0]))
        add("64-bit", "ASCII (4 regs)", _ascii(raw))
    return rows


def format_table(rows):
    out, last = [], None
    for g, l, t in rows:
        if g != last:
            out.append(f"\n[{g}]")
            last = g
        out.append(f"  {l:<32} {t}")
    return "\n".join(out).lstrip("\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="tcp://host:port or serial:COMx?baud=..&parity=..")
    ap.add_argument("-u", "--unit", type=int, default=1)
    ap.add_argument("-f", "--func", type=int, default=3, choices=[3, 4])
    ap.add_argument("-a", "--addr", type=int, default=0)
    ap.add_argument("-n", type=int, default=2, choices=[1, 2, 4], help="registers to interpret together")
    ap.add_argument("--words", nargs="+", help="interpret these values offline (decimal or 0x hex)")
    a = ap.parse_args()
    if a.words:
        words = [int(x, 0) for x in a.words]
    elif a.url:
        from mbtool import connect
        m = connect(a.url)
        try:
            words = m.request(a.unit, a.func, addr=a.addr, qty=a.n)
        finally:
            m.close()
        print(f"unit {a.unit}  FC{a.func:02d}  address {a.addr}..{a.addr + a.n - 1}  raw: "
              + " ".join(f"0x{w:04X}" for w in words))
    else:
        ap.error("give a device URL or --words")
    print(format_table(interpret(words)))


if __name__ == "__main__":
    main()
