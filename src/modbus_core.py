"""Modbus master/slave core: TCP, RTU and ASCII framing written from the public Modbus spec.

Covers function codes 01-06, 15, 16, 22, 23 and the common register display formats
(unsigned/signed/hex/binary, long, float, double with word-swapped "inverse" variants).
"""
import collections
import socket
import struct
import threading
import time

EXC_NAMES = {
    1: "Illegal function", 2: "Illegal data address", 3: "Illegal data value",
    4: "Slave device failure", 6: "Slave device busy",
    10: "Gateway path unavailable", 11: "Gateway target device failed to respond",
}


class ModbusError(Exception):
    pass


class ModbusException(ModbusError):
    def __init__(self, fc, code):
        self.fc, self.code = fc, code
        super().__init__(f"Exception {code}: {EXC_NAMES.get(code, 'unknown')} (FC {fc:02d})")


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def lrc(data: bytes) -> int:
    return (-sum(data)) & 0xFF


# ---------- PDU building / parsing ----------

def pack_bits(bits):
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def unpack_bits(data, n):
    return [bool(data[i // 8] >> (i % 8) & 1) for i in range(n)]


def build_request(fc, addr=0, qty=0, values=None, **kw):
    if fc in (1, 2, 3, 4):
        return struct.pack(">BHH", fc, addr, qty)
    if fc == 5:
        return struct.pack(">BHH", fc, addr, 0xFF00 if values[0] else 0)
    if fc == 6:
        return struct.pack(">BHH", fc, addr, values[0] & 0xFFFF)
    if fc == 15:
        body = pack_bits(values)
        return struct.pack(">BHHB", fc, addr, len(values), len(body)) + body
    if fc == 16:
        body = b"".join(struct.pack(">H", v & 0xFFFF) for v in values)
        return struct.pack(">BHHB", fc, addr, len(values), len(body)) + body
    if fc == 22:
        return struct.pack(">BHHH", fc, addr, kw["and_mask"], kw["or_mask"])
    if fc == 23:
        body = b"".join(struct.pack(">H", v & 0xFFFF) for v in values)
        return struct.pack(">BHHHHB", fc, addr, qty, kw["write_addr"], len(values), len(body)) + body
    raise ModbusError(f"unsupported function {fc}")


def parse_response(req_pdu, resp_pdu):
    fc = req_pdu[0]
    if not resp_pdu:
        raise ModbusError("empty response")
    if resp_pdu[0] == fc | 0x80:
        if len(resp_pdu) < 2:
            raise ModbusError("malformed exception response")
        raise ModbusException(fc, resp_pdu[1])
    if resp_pdu[0] != fc:
        raise ModbusError(f"unexpected function {resp_pdu[0]} in response to {fc}")
    if fc in (1, 2, 3, 4, 23):
        if len(resp_pdu) < 2 or len(resp_pdu) != 2 + resp_pdu[1]:
            raise ModbusError("malformed response (byte count)")
        n = resp_pdu[1]
        qty = struct.unpack(">H", req_pdu[3:5])[0]
        if fc in (1, 2):
            if n != (qty + 7) // 8:
                raise ModbusError("malformed response (byte count)")
            return unpack_bits(resp_pdu[2:], qty)
        if n != 2 * qty:
            raise ModbusError("malformed response (register count)")
        return list(struct.unpack(f">{qty}H", resp_pdu[2:]))
    if fc in (5, 6, 15, 16, 22):
        if len(resp_pdu) != {5: 5, 6: 5, 15: 5, 16: 5, 22: 7}[fc] or resp_pdu != req_pdu[:len(resp_pdu)]:
            raise ModbusError("response does not echo request")
        return resp_pdu[1:]
    raise ModbusError(f"unsupported function {fc}")


# ---------- transports ----------

FC_NAMES = {1: "Read Coils", 2: "Read Discrete Inputs", 3: "Read Holding Registers",
            4: "Read Input Registers", 5: "Write Single Coil", 6: "Write Single Register",
            15: "Write Multiple Coils", 16: "Write Multiple Registers",
            22: "Mask Write Register", 23: "Read/Write Multiple Registers"}


def describe_pdu(pdu: bytes, response=False) -> str:
    """Human-readable one-line summary of a request or response PDU (never raises)."""
    try:
        fc = pdu[0]
        if fc & 0x80:
            return f"EXCEPTION FC{fc & 0x7F:02d} code {pdu[1]}: {EXC_NAMES.get(pdu[1], 'unknown')}"
        name = FC_NAMES.get(fc, "FC%d" % fc)
        if response:
            if fc in (1, 2, 3, 4, 23):
                return f"{name}: {pdu[1]} data bytes"
            return f"{name}: echo"
        if fc in (1, 2, 3, 4):
            a, q = struct.unpack(">HH", pdu[1:5]); return f"{name} addr={a} qty={q}"
        if fc in (5, 6):
            a, v = struct.unpack(">HH", pdu[1:5]); return f"{name} addr={a} value=0x{v:04X}"
        if fc in (15, 16):
            a, q = struct.unpack(">HH", pdu[1:5]); return f"{name} addr={a} qty={q}"
        if fc == 22:
            a, am, om = struct.unpack(">HHH", pdu[1:7]); return f"{name} addr={a} and=0x{am:04X} or=0x{om:04X}"
        if fc == 23:
            ra, rq, wa, wq = struct.unpack(">HHHH", pdu[1:9]); return f"{name} read {ra}x{rq} write {wa}x{wq}"
    except (IndexError, struct.error):
        pass
    return "malformed " + pdu.hex(" ")


class Master:
    """Base class: subclasses implement _transact(unit, pdu) -> response pdu."""

    def __init__(self):
        self.lock = threading.Lock()
        self.traffic = collections.deque(maxlen=500)  # newest last; entries are dicts
        self.stats = {"ok": 0, "errors": 0}

    def close(self):
        pass

    def request(self, unit, fc, **kw):
        req = build_request(fc, **kw)
        with self.lock:
            self._log("TX", unit, req, describe_pdu(req))
            try:
                resp = self._transact(unit, req)
            except ModbusError as e:
                self.stats["errors"] += 1
                self._log("ERR", unit, b"", str(e))
                raise
        if resp is None:  # serial broadcast
            return None
        self._log("RX", unit, resp, describe_pdu(resp, response=True))
        try:
            out = parse_response(req, resp)
        except ModbusError:
            self.stats["errors"] += 1
            raise
        self.stats["ok"] += 1
        return out

    def _log(self, direction, unit, pdu, text):
        self.traffic.append({"t": time.time(), "dir": direction, "unit": unit,
                             "pdu_hex": pdu.hex(" "), "text": text})

    # convenience wrappers
    def read_coils(self, unit, addr, qty): return self.request(unit, 1, addr=addr, qty=qty)
    def read_discrete(self, unit, addr, qty): return self.request(unit, 2, addr=addr, qty=qty)
    def read_holding(self, unit, addr, qty): return self.request(unit, 3, addr=addr, qty=qty)
    def read_input(self, unit, addr, qty): return self.request(unit, 4, addr=addr, qty=qty)
    def write_coil(self, unit, addr, v): return self.request(unit, 5, addr=addr, values=[v])
    def write_register(self, unit, addr, v): return self.request(unit, 6, addr=addr, values=[v])
    def write_coils(self, unit, addr, vs): return self.request(unit, 15, addr=addr, values=list(vs))
    def write_registers(self, unit, addr, vs): return self.request(unit, 16, addr=addr, values=list(vs))
    def mask_write(self, unit, addr, and_mask, or_mask):
        return self.request(unit, 22, addr=addr, and_mask=and_mask, or_mask=or_mask)
    def read_write(self, unit, raddr, qty, waddr, vs):
        return self.request(unit, 23, addr=raddr, qty=qty, write_addr=waddr, values=list(vs))


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ModbusError("connection closed")
        buf += chunk
    return buf


class TcpMaster(Master):
    def __init__(self, host, port=502, timeout=1.0):
        super().__init__()
        self.tid = 0
        self.host, self.port, self.timeout = host, port, timeout
        self.reconnects = 0
        self._closed = False
        self.sock = self._open()

    def _open(self):
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise ModbusError(f"Modbus Connection Failed: {e}")
        sock.settimeout(self.timeout)
        return sock

    def close(self):
        self._closed = True
        if self.sock is not None:
            self.sock.close()

    def _drop(self):
        """The byte stream can no longer be trusted (closed, cut off mid-frame, garbage): drop the socket; the next
        request opens a fresh connection."""
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _transact(self, unit, pdu):
        if self.sock is None:
            if self._closed:
                raise ModbusError("connection closed")
            self.sock = self._open()  # reconnect after a broken connection
            self.reconnects += 1
        self.tid = (self.tid + 1) & 0xFFFF
        mid_frame = False
        try:
            self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, unit) + pdu)
            deadline = time.monotonic() + (self.sock.gettimeout() or 1.0)
            while True:
                head = _recv_exact(self.sock, 7)
                mid_frame = True
                tid, proto, length, u = struct.unpack(">HHHB", head)
                if proto != 0 or not 1 <= length <= 254:
                    self._drop()
                    raise ModbusError("MBAP header invalid; connection closed")
                body = _recv_exact(self.sock, length - 1)
                mid_frame = False
                if tid == self.tid:  # stale replies from earlier timed-out requests are discarded
                    if u != unit:
                        raise ModbusError("unexpected unit id in response")
                    return body
                if time.monotonic() > deadline:
                    raise ModbusError("response timeout")
        except socket.timeout:
            if mid_frame:  # half a frame arrived: the stream is out of step
                self._drop()
            raise ModbusError("response timeout")
        except ModbusError as e:
            if "connection closed" in str(e):
                self._drop()
            raise
        except OSError as e:
            self._drop()
            raise ModbusError(f"connection error: {e}")


class SerialMaster(Master):
    """RTU or ASCII over a pyserial port."""

    def __init__(self, port, baud=9600, parity="N", stopbits=1, databits=8, mode="rtu", timeout=1.0):
        super().__init__()
        import serial
        self.mode = mode
        self.ser = serial.serial_for_url(port, baudrate=baud, bytesize=databits, parity=parity,
                                 stopbits=stopbits, timeout=timeout)
        # RTU inter-frame gap: 3.5 chars (min 1.75 ms above 19200 baud)
        bits = 1 + databits + (0 if parity == "N" else 1) + stopbits
        self.t35 = max(3.5 * bits / baud, 0.00175) if baud <= 19200 else 0.00175

    def close(self):
        self.ser.close()

    def _transact(self, unit, pdu):
        try:
            return self._transact_inner(unit, pdu)
        except ModbusError as e:
            if "timeout" in str(e) or "short response" in str(e):
                self._timed_out = True
            raise
        finally:
            # end of the previous frame, for the t3.5 gap. perf_counter: time.monotonic() on Windows ticks only every
            # ~15.6 ms before Python 3.13, far coarser than a t3.5 of 1.75-4 ms
            self._last_activity = time.perf_counter()

    late_guard = 0.15  # s: after a timeout, wait this long for a late answer and discard it (RTU has no ID)

    def _drain_late(self):
        """A device that answers after our timeout would otherwise have its late answer taken as the reply to the
        NEXT request (RTU frames carry no transaction ID). Wait for the line to stay quiet before sending again."""
        end = time.perf_counter() + self.late_guard
        quiet = max(0.02, 3 * self.t35)
        saved = self.ser.timeout
        try:
            self.ser.timeout = quiet
            while time.perf_counter() < end:
                if not self.ser.read(256) and time.perf_counter() + quiet > end:
                    break
        finally:
            self.ser.timeout = saved
        self.ser.reset_input_buffer()

    def _transact_inner(self, unit, pdu):
        if getattr(self, "_timed_out", False):
            self._timed_out = False
            self._drain_late()
        if self.mode == "rtu":
            idle = time.perf_counter() - getattr(self, "_last_activity", 0.0)
            if idle < self.t35:  # RTU requires >= 3.5 character times of silence between frames
                time.sleep(self.t35 - idle)
        self.ser.reset_input_buffer()
        if self.mode == "rtu":
            adu = bytes([unit]) + pdu
            self.ser.write(adu + struct.pack("<H", crc16(adu)))
            if unit == 0:  # broadcast: slaves do not answer
                time.sleep(0.1)
                return None
            return self._read_rtu(unit, pdu)
        adu = bytes([unit]) + pdu
        self.ser.write(b":" + (adu + bytes([lrc(adu)])).hex().upper().encode() + b"\r\n")
        if unit == 0:
            time.sleep(0.1)
            return None
        line = self.ser.read_until(b"\n")
        if not line.startswith(b":") or not line.endswith(b"\r\n"):
            raise ModbusError("response timeout" if not line else "bad ASCII frame")
        try:
            raw = bytes.fromhex(line[1:-2].decode())
        except ValueError:
            raise ModbusError("bad ASCII frame")
        if len(raw) < 3:
            raise ModbusError("bad ASCII frame")
        if lrc(raw[:-1]) != raw[-1]:
            raise ModbusError("LRC error")
        if raw[0] != unit:
            raise ModbusError("unexpected slave id")
        return raw[1:-1]

    def _read_rtu(self, unit, req_pdu):
        head = self.ser.read(2)
        if len(head) < 2:
            raise ModbusError("response timeout")
        if head[1] & 0x80:
            need = 3  # exc code + crc2
        else:
            fc = head[1]
            if fc in (1, 2, 3, 4, 23):
                b = self.ser.read(1)
                if not b:
                    raise ModbusError("response timeout")
                head += b
                need = b[0] + 2
            elif fc in (5, 6, 15, 16):
                need = 4 + 2
            elif fc == 22:
                need = 6 + 2
            else:
                raise ModbusError("unsupported function in response")
        rest = self.ser.read(need)
        frame = head + rest
        if len(rest) < need:
            raise ModbusError("short response")
        if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
            raise ModbusError("CRC error")
        if frame[0] != unit:
            raise ModbusError("unexpected slave id")
        return frame[1:-2]


# ---------- display formats ----------

FORMATS = ["Unsigned", "Signed", "Hex", "Binary",
           "Long", "Long Inverse", "Float", "Float Inverse", "Double", "Double Inverse"]


def format_registers(regs, fmt):
    """Return list of (first_register_index, text). Multi-word formats consume 2 or 4 words."""
    out = []
    if fmt == "Unsigned":
        return [(i, str(v)) for i, v in enumerate(regs)]
    if fmt == "Signed":
        return [(i, str(v - 65536 if v > 32767 else v)) for i, v in enumerate(regs)]
    if fmt == "Hex":
        return [(i, f"0x{v:04X}") for i, v in enumerate(regs)]
    if fmt == "Binary":
        return [(i, f"{v:016b}") for i, v in enumerate(regs)]
    width = 4 if fmt.startswith("Double") else 2
    inv = fmt.endswith("Inverse")
    for i in range(0, len(regs) - width + 1, width):
        w = regs[i:i + width]
        if inv:
            w = w[::-1]
        raw = b"".join(struct.pack(">H", x) for x in w)
        if fmt.startswith("Long"):
            out.append((i, str(struct.unpack(">i", raw)[0])))
        elif fmt.startswith("Float"):
            out.append((i, f"{struct.unpack('>f', raw)[0]:.9g}"))
        else:
            out.append((i, f"{struct.unpack('>d', raw)[0]:.17g}"))
    return out


def encode_value(text, fmt):
    """Parse user text into a list of 16-bit register words for the given format."""
    if fmt == "Unsigned":
        n = int(text, 0)
        if not 0 <= n <= 0xFFFF:
            raise ValueError(f"unsigned value out of range: {text}")
        return [n]
    if fmt == "Signed":
        n = int(text, 0)
        if not -32768 <= n <= 32767:
            raise ValueError(f"signed value out of range: {text}")
        return [n & 0xFFFF]
    if fmt == "Hex":
        return [int(text, 16) & 0xFFFF]
    if fmt == "Binary":
        return [int(text, 2) & 0xFFFF]
    inv = fmt.endswith("Inverse")
    try:
        if fmt.startswith("Long"):
            raw = struct.pack(">i", int(text, 0))
        elif fmt.startswith("Float"):
            raw = struct.pack(">f", float(text))
        else:
            raw = struct.pack(">d", float(text))
    except (struct.error, OverflowError) as e:
        raise ValueError(f"value out of range for {fmt}: {text}") from e
    words = list(struct.unpack(f">{len(raw) // 2}H", raw))
    return words[::-1] if inv else words


# ---------- simple slave (for self-tests and as a Modbus Slave-like simulator) ----------

class Faults:
    """Fault injection for the simulator, to test how a master copes with a bad line or a misbehaving device.
    Each rate is a probability per request (0 = never, 1 = always):

      no_reply    say nothing (master times out)          bad_crc    answer with a wrong CRC (RTU / ASCII)
      exception   answer exception `exception_code`       truncate   send only part of the answer
      garbage     send random bytes instead               wrong_unit answer with another Slave ID
      wrong_tid   TCP: answer with another transaction ID slow       answer after `slow_ms` (default 3000 ms)
      drop_conn   TCP: close the connection instead of answering
    counts[name] tells how often each fault was injected."""
    RATES = ("no_reply", "bad_crc", "exception", "truncate", "garbage", "wrong_unit", "wrong_tid", "slow", "drop_conn")

    def __init__(self, seed=None, exception_code=4, slow_ms=3000, **rates):
        import random
        bad = set(rates) - set(self.RATES)
        if bad:
            raise ValueError(f"unknown fault(s): {', '.join(sorted(bad))}")
        self.rates = {k: max(0.0, min(1.0, float(rates.get(k, 0.0)))) for k in self.RATES}
        self.exception_code = int(exception_code)
        self.slow_ms = float(slow_ms)
        self.rng = random.Random(seed)
        self.counts = {k: 0 for k in self.RATES}
        self._lock = threading.Lock()

    def roll(self, name):
        p = self.rates.get(name, 0.0)
        if p <= 0:
            return False
        with self._lock:
            hit = p >= 1 or self.rng.random() < p
            if hit:
                self.counts[name] += 1
        return hit

    def garbage(self, n=None):
        with self._lock:
            return bytes(self.rng.randrange(256) for _ in range(n or self.rng.randrange(3, 12)))

    def active(self):
        return {k: v for k, v in self.rates.items() if v > 0}


class SimSlave:
    """In-memory Modbus/TCP slave. 65536 coils/discretes/holding/input, one unit id (or all if None)."""

    def __init__(self, host="127.0.0.1", port=0, unit=None, listen=True):
        self.unit = unit
        self.requests = 0
        self.response_delay = 0.0  # seconds a real device would need to think before it answers
        self.faults = None  # Faults(...) to make the simulator misbehave on purpose
        self.traffic = collections.deque(maxlen=500)  # same entry shape as Master.traffic
        self._conns = []
        self._stop = False
        self.srv = None
        self.coils = [False] * 65536
        self.discrete = [False] * 65536
        self.holding = [0] * 65536
        self.input = [0] * 65536
        if not listen:
            return
        self.srv = socket.socket()
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: refuse a second listener on the same port
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((host, port))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def log_exchange(self, unit, req, resp):
        now = time.time()
        self.traffic.append({"t": now, "dir": "RX", "unit": unit, "pdu_hex": req.hex(" "),
                             "text": describe_pdu(req)})
        if resp is not None:
            self.traffic.append({"t": time.time(), "dir": "TX", "unit": unit, "pdu_hex": resp.hex(" "),
                                 "text": describe_pdu(resp, response=True)})

    def stop(self):
        self._stop = True
        if self.srv: self.srv.close()
        for c in list(self._conns):
            try:
                c.close()
            except OSError:
                pass

    def _accept(self):
        while not self._stop:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self._conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            while True:
                tid, _p, ln, unit = struct.unpack(">HHHB", _recv_exact(c, 7))
                pdu = _recv_exact(c, ln - 1)
                if self.unit is not None and unit != self.unit:
                    continue
                self.requests += 1
                resp = self.handle(pdu)
                self.log_exchange(unit, pdu, resp)
                f = self.faults
                if f is not None:
                    if f.roll("no_reply"):
                        continue
                    if f.roll("drop_conn"):
                        c.close()
                        return
                    if f.roll("garbage"):
                        c.sendall(f.garbage())
                        continue
                    if f.roll("wrong_tid"):
                        tid = (tid + 1) & 0xFFFF
                    if f.roll("wrong_unit"):
                        unit = (unit + 1) & 0xFF
                    adu = struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp
                    if f.roll("truncate"):
                        c.sendall(adu[:max(1, len(adu) // 2)])
                        c.close()
                        return
                    c.sendall(adu)
                    continue
                c.sendall(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
        except (ModbusError, OSError, struct.error):
            c.close()

    def handle(self, pdu):
        if self.response_delay > 0:
            time.sleep(self.response_delay)
        f = self.faults
        if f is not None and pdu:
            if f.roll("slow"):
                time.sleep(f.slow_ms / 1000.0)
            if f.roll("exception"):
                return bytes([pdu[0] | 0x80, f.exception_code & 0xFF])
        if not pdu:
            return b"\x80\x01"  # no function code: report illegal function
        fc = pdu[0]
        exc = lambda code: bytes([fc | 0x80, code])
        try:
            if fc in (1, 2, 3, 4):
                a, q = struct.unpack(">HH", pdu[1:5])
                if fc in (1, 2):
                    if not 1 <= q <= 2000: return exc(3)
                    if a + q > 65536: return exc(2)
                    d = pack_bits((self.coils if fc == 1 else self.discrete)[a:a + q])
                else:
                    if not 1 <= q <= 125: return exc(3)
                    if a + q > 65536: return exc(2)
                    regs = (self.holding if fc == 3 else self.input)[a:a + q]
                    d = b"".join(struct.pack(">H", r) for r in regs)
                return bytes([fc, len(d)]) + d
            if fc == 5:
                a, v = struct.unpack(">HH", pdu[1:5])
                if v not in (0, 0xFF00): return exc(3)
                self.coils[a] = v == 0xFF00
                return pdu[:5]
            if fc == 6:
                a, v = struct.unpack(">HH", pdu[1:5])
                self.holding[a] = v
                return pdu[:5]
            if fc == 15:
                a, q, n = struct.unpack(">HHB", pdu[1:6])
                if not 1 <= q <= 1968 or n != (q + 7) // 8 or len(pdu) != 6 + n: return exc(3)
                if a + q > 65536: return exc(2)
                self.coils[a:a + q] = unpack_bits(pdu[6:6 + n], q)
                return pdu[:5]
            if fc == 16:
                a, q, n = struct.unpack(">HHB", pdu[1:6])
                if not 1 <= q <= 123 or n != 2 * q or len(pdu) != 6 + n: return exc(3)
                if a + q > 65536: return exc(2)
                self.holding[a:a + q] = struct.unpack(f">{q}H", pdu[6:6 + n])
                return pdu[:5]
            if fc == 22:
                a, am, om = struct.unpack(">HHH", pdu[1:7])
                self.holding[a] = (self.holding[a] & am) | (om & ~am & 0xFFFF)
                return pdu[:7]
            if fc == 23:
                ra, rq, wa, wq, n = struct.unpack(">HHHHB", pdu[1:10])
                if not 1 <= rq <= 125 or not 1 <= wq <= 121 or n != 2 * wq or len(pdu) != 10 + n:
                    return exc(3)
                if ra + rq > 65536 or wa + wq > 65536: return exc(2)
                self.holding[wa:wa + wq] = struct.unpack(f">{wq}H", pdu[10:10 + n])
                d = b"".join(struct.pack(">H", r) for r in self.holding[ra:ra + rq])
                return bytes([fc, len(d)]) + d
        except (struct.error, IndexError):
            return exc(3)
        return exc(1)


def _rtu_response_len(buf):
    """Total length of an RTU ANSWER that starts in buf, or 0 when not known yet."""
    if len(buf) < 2:
        return 0
    fc = buf[1]
    if fc & 0x80:
        return 5
    if fc in (1, 2, 3, 4, 23):
        return 5 + buf[2] if len(buf) > 2 else 0
    if fc in (5, 6, 15, 16):
        return 8
    if fc == 22:
        return 10
    return 0


def _rtu_complete_len(buf):
    """Length of the first complete, CRC-valid frame (request or answer) at the start of buf, else 0."""
    for n in sorted({_rtu_request_len(buf), _rtu_response_len(buf)} - {0}):
        if len(buf) >= n >= 4 and crc16(buf[:n - 2]) == struct.unpack("<H", buf[n - 2:n])[0]:
            return n
    return 0


def _rtu_request_len(buf):
    """Total length of the RTU request that starts in buf (address, function, data, CRC), or 0 when it is not
    known yet or the function code is not one we can size."""
    if len(buf) < 2:
        return 0
    fc = buf[1]
    if fc in (1, 2, 3, 4, 5, 6):
        return 8
    if fc in (15, 16):
        return 9 + buf[6] if len(buf) > 6 else 0
    if fc == 22:
        return 10
    if fc == 23:
        return 13 + buf[10] if len(buf) > 10 else 0
    return 0


class SerialSlave(threading.Thread):
    """RTU slave on a serial port serving a SimSlave data store (answers only its unit id; 0 = broadcast, no reply)."""

    def __init__(self, port, unit=1, baud=9600, parity="N", stopbits=1, store=None, mode="rtu"):
        super().__init__(daemon=True)
        self.mode = mode
        import serial
        self.unit = unit
        self.store = store or SimSlave(listen=False)
        bits = 1 + 8 + (0 if parity == "N" else 1) + stopbits
        t35 = max(3.5 * bits / baud, 0.00175) if baud <= 19200 else 0.00175
        self.gap = max(t35, 0.02)  # silence that ends a frame; generous so USB adapters do not split frames
        self.ser = serial.serial_for_url(port, baudrate=baud, parity=parity, stopbits=stopbits, timeout=0.2)
        self._halt = False
        self.error = None
        self.start()

    def stop(self):
        self._halt = True
        self.join(1)
        self.ser.close()

    def _read_frame(self):
        """Next frame seen on the line. On a shared bus that is either a request from the master or ANOTHER
        slave's answer; both are cut at their protocol length when the CRC checks out, so an answer of slave A
        that arrives glued to the next request is not taken as one long broken frame (which made slaves miss
        requests when the master polled with a short gap). Unknown frames end at the line silence (self.gap)."""
        buf, self._pending = getattr(self, "_pending", b""), b""
        if not buf:
            self.ser.timeout = 0.2
            first = self.ser.read(1)
            if not first:
                return b""
            buf = first
        self.ser.timeout = self.gap
        while True:
            k = _rtu_complete_len(buf)
            if k:
                self._pending = buf[k:]
                return buf[:k]
            chunk = self.ser.read(max(1, self.ser.in_waiting))
            if not chunk:
                return buf
            buf += chunk

    def run(self):
        try:
            self._loop()
        except Exception as e:  # e.g. cable unplugged
            self.error = e

    def _loop_ascii(self):
        self.ser.timeout = 0.2
        buf, started = b"", 0.0
        while not self._halt:
            chunk = self.ser.read_until(b"\n")  # may return a partial line when the timeout hits mid-frame
            if chunk:
                if not buf:
                    started = time.monotonic()
                buf += chunk
            elif buf and time.monotonic() - started > 1.0:
                buf = b""  # an unfinished frame older than 1 s is garbage
            if not buf.endswith(b"\n"):
                continue
            line, buf = buf, b""
            i = line.rfind(b":")  # a frame starts at the last ':' (drops leading noise)
            if i < 0 or not line.endswith(b"\r\n"):
                continue
            try:
                raw = bytes.fromhex(line[i + 1:-2].decode())
            except ValueError:
                continue
            if len(raw) < 3 or lrc(raw[:-1]) != raw[-1] or raw[0] not in (self.unit, 0):
                continue
            self.store.requests += 1
            resp = self.store.handle(raw[1:-1])
            self.store.log_exchange(raw[0], raw[1:-1], None if raw[0] == 0 else resp)
            if raw[0] == 0:
                continue
            out = bytes([self.unit]) + resp
            self.ser.write(b":" + (out + bytes([lrc(out)])).hex().upper().encode() + b"\r\n")

    def _loop(self):
        if self.mode == "ascii":
            return self._loop_ascii()
        while not self._halt:
            frame = self._read_frame()
            if len(frame) < 4 or crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
                continue
            if frame[0] not in (self.unit, 0):
                continue
            self.store.requests += 1
            resp = self.store.handle(frame[1:-2])
            self.store.log_exchange(frame[0], frame[1:-2], None if frame[0] == 0 else resp)
            if frame[0] == 0:
                continue
            f = self.store.faults
            if f is not None and f.roll("no_reply"):
                continue
            uid = (self.unit + 1) & 0xFF if f is not None and f.roll("wrong_unit") else self.unit
            out = bytes([uid]) + resp
            adu = out + struct.pack("<H", crc16(out))
            if f is not None:
                if f.roll("garbage"):
                    adu = f.garbage()
                elif f.roll("bad_crc"):
                    adu = adu[:-2] + bytes([adu[-2] ^ 0x5A, adu[-1]])
                elif f.roll("truncate"):
                    adu = adu[:max(1, len(adu) // 2)]
            self.ser.write(adu)


class VirtualCable:
    """Software null-modem for testing RTU without a driver: two clients connect to the same TCP port and
    every byte one sends is delivered to the other. Use the port as 'socket://127.0.0.1:PORT' wherever a COM port
    name is accepted (master, RTU slave, GUI)."""

    def __init__(self, host="127.0.0.1", port=0, baud=None, bits_per_char=11):
        """baud: emulate a real line speed (every byte takes bits_per_char / baud seconds) so timing measurements
        resemble a real RS-485 bus; None = as fast as the PC can go."""
        self.baud, self.bits_per_char = baud, bits_per_char
        self.srv = socket.socket()
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.srv.bind((host, port))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.url = f"socket://{host}:{self.port}"
        self._clients = []
        self._lock = threading.Lock()
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._closed:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                self._clients.append(c)
            threading.Thread(target=self._pump, args=(c,), daemon=True).start()

    def _pump(self, c):
        try:
            while True:
                data = c.recv(4096)
                if not data:
                    break
                if self.baud:
                    time.sleep(len(data) * self.bits_per_char / self.baud)
                with self._lock:
                    others = [o for o in self._clients if o is not c]
                for o in others:
                    try:
                        o.sendall(data)
                    except OSError:
                        pass
        except OSError:
            pass
        finally:
            with self._lock:
                if c in self._clients:
                    self._clients.remove(c)
            c.close()

    def close(self):
        self._closed = True
        self.srv.close()
        with self._lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass


class PortHub:
    """One serial port, many users: the main Poll, poll tabs and devices on the same COM port share ONE SerialMaster,
    so their requests are sent one after another (Master.request holds a lock for the whole request/response, like a
    PLC working through its telegram list) instead of fighting for the port. Reference counted: the port is closed when
    the last user lets go."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ports = {}  # key -> [master, refcount, settings]

    @staticmethod
    def key(port):
        return str(port).strip().upper() if not str(port).lower().startswith(("socket://", "rfc2217://")) else str(port).strip()

    def acquire(self, port, settings, factory):
        """Shared master for `port`. settings: tuple that must match for everyone using the port (baud, parity, ...).
        factory() opens the real master when nobody has it. Raises ModbusError when the port is open with other
        settings."""
        k = self.key(port)
        with self._lock:
            entry = self._ports.get(k)
            if entry is not None:
                if entry[2] != settings:
                    raise ModbusError(f"{port} is already open with different settings ({_fmt_settings(entry[2])}); "
                                      "tabs and devices on the same COM port must use the same baud / parity / stop bits")
                entry[1] += 1
                master = entry[0]
            else:
                master = factory()
                self._ports[k] = [master, 1, settings]
        return HubMaster(self, k, master)

    def release(self, k, master):
        with self._lock:
            entry = self._ports.get(k)
            if entry is None or entry[0] is not master:
                return
            entry[1] -= 1
            if entry[1] > 0:
                return
            del self._ports[k]
        master.close()

    def users(self, port):
        with self._lock:
            entry = self._ports.get(self.key(port))
            return entry[1] if entry else 0


def _fmt_settings(s):
    return " ".join(str(x) for x in s)


class HubMaster:
    """What a user of a shared port holds: every call goes to the shared master; close() only gives the port back."""

    def __init__(self, hub, key, master):
        object.__setattr__(self, "_hub", hub)
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_m", master)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_m"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_m"), name, value)

    def close(self):
        if not object.__getattribute__(self, "_closed"):
            object.__setattr__(self, "_closed", True)
            object.__getattribute__(self, "_hub").release(object.__getattribute__(self, "_key"),
                                                          object.__getattribute__(self, "_m"))


HUB = PortHub()
