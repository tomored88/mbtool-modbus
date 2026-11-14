"""Data export: send every device reading to an MQTT broker and / or a SQLite database, in a background thread so
polling never waits for the network or the disk.

MQTT: a small MQTT 3.1.1 client (CONNECT / PUBLISH QoS 0 / PINGREQ / DISCONNECT, optional TLS and user name) -
no extra package needed. Two payload styles:
  json    {prefix}/{device}            {"ts": "2026-11-04T10:00:00.123+07:00", "ok": true, "values": {"Voltage": 230.1}}
  topics  {prefix}/{device}/{point}    230.1          (one message per point; status in {prefix}/{device}/status)
SQLite: table readings(ts REAL, time TEXT, device TEXT, point TEXT, value REAL, text TEXT, ok INTEGER), batched.

The exporter keeps going when the broker is away (reconnect with back-off, readings queued up to a limit and then
the oldest dropped - counted in stats['dropped']).
"""
import collections
import datetime
import json
import math
import os
import queue
import re
import socket
import sqlite3
import ssl
import struct
import threading
import time

MQTT_DEFAULT = {"on": False, "host": "", "port": "1883", "tls": False, "user": "", "client_id": "",
                "prefix": "mbtool", "style": "json", "retain": False, "min_interval_s": "0"}
SQLITE_DEFAULT = {"on": False, "path": ""}
EXPORT_DEFAULT = {"mqtt": MQTT_DEFAULT, "sqlite": SQLITE_DEFAULT}


class ExportError(Exception):
    pass


def norm_export(d):
    """Saved export settings -> every key present with the right type (password is never part of it)."""
    out = json.loads(json.dumps(EXPORT_DEFAULT))
    if isinstance(d, dict):
        for sec in ("mqtt", "sqlite"):
            src = d.get(sec)
            if not isinstance(src, dict):
                continue
            for k, v in out[sec].items():
                x = src.get(k)
                if isinstance(v, bool):
                    if isinstance(x, bool):
                        out[sec][k] = x
                elif isinstance(x, (str, int, float)) and not isinstance(x, bool):
                    out[sec][k] = str(x)
    if out["mqtt"]["style"] not in ("json", "topics"):
        out["mqtt"]["style"] = "json"
    return out


def topic_part(name):
    """A device / point name made safe for one MQTT topic level (no / + # and no control characters)."""
    s = re.sub(r"[/+#\x00-\x1f]+", "_", str(name)).strip()
    return s or "_"


def check_mqtt(c):
    """Validate MQTT settings (strings) -> dict of errors {field: text}."""
    errs = {}
    if not c["host"].strip():
        errs["host"] = "broker host is required"
    try:
        p = int(c["port"])
        if not 1 <= p <= 65535:
            raise ValueError
    except ValueError:
        errs["port"] = "1 - 65535"
    pre = c["prefix"].strip()
    if not pre or any(ch in pre for ch in "+#") or pre.startswith("/") or pre.endswith("/"):
        errs["prefix"] = "e.g. plant1/line3 (no + or #, no leading / trailing /)"
    try:
        v = float(str(c["min_interval_s"]).replace(",", ".") or 0)
        if not 0 <= v <= 3600:
            raise ValueError
    except ValueError:
        errs["min_interval_s"] = "0 - 3600 s"
    return errs


# ---------------------------------------------------------------------------------------------------------------
# minimal MQTT 3.1.1 client
# ---------------------------------------------------------------------------------------------------------------

def _enc_str(s):
    b = s.encode("utf-8")
    if len(b) > 65535:
        raise ExportError("MQTT string too long")
    return struct.pack(">H", len(b)) + b


def _enc_len(n):
    out = bytearray()
    while True:
        d = n % 128
        n //= 128
        if n:
            d |= 0x80
        out.append(d)
        if not n:
            return bytes(out)


CONNACK_TEXT = {1: "unacceptable protocol version", 2: "client id rejected", 3: "server unavailable",
                4: "bad user name or password", 5: "not authorised"}


class MqttClient:
    """Publish-only MQTT 3.1.1 client, QoS 0. Not thread safe: used by the exporter thread only."""

    def __init__(self, host, port=1883, client_id="", user="", password="", tls=False, keepalive=60, timeout=5.0):
        self.host, self.port = host, int(port)
        self.client_id = client_id or f"mbtool-{os.getpid()}-{int(time.time()) % 100000}"
        self.user, self.password, self.tls = user, password, tls
        self.keepalive, self.timeout = keepalive, timeout
        self.sock = None
        self.last_io = 0.0

    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            if self.tls:
                ctx = ssl.create_default_context()
                raw = ctx.wrap_socket(raw, server_hostname=self.host)
            flags = 0x02  # clean session
            payload = _enc_str(self.client_id)
            if self.user:
                flags |= 0x80
                payload += _enc_str(self.user)
                if self.password:
                    flags |= 0x40
                    payload += _enc_str(self.password)
            var = _enc_str("MQTT") + bytes([4, flags]) + struct.pack(">H", self.keepalive)
            body = var + payload
            raw.sendall(bytes([0x10]) + _enc_len(len(body)) + body)
            resp = self._recv(raw, 4)
            if resp[0] != 0x20:
                raise ExportError("broker did not answer CONNECT (is it an MQTT broker?)")
            if resp[3] != 0:
                raise ExportError(f"broker refused: {CONNACK_TEXT.get(resp[3], resp[3])}")
        except Exception:
            raw.close()
            raise
        self.sock = raw
        self.last_io = time.monotonic()

    @staticmethod
    def _recv(sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ExportError("broker closed the connection")
            buf += chunk
        return buf

    def publish(self, topic, payload, retain=False):
        if self.sock is None:
            raise ExportError("not connected")
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        body = _enc_str(topic) + payload
        self.sock.sendall(bytes([0x30 | (1 if retain else 0)]) + _enc_len(len(body)) + body)
        self.last_io = time.monotonic()

    def ping_if_idle(self):
        if self.sock is not None and time.monotonic() - self.last_io > self.keepalive / 2:
            self.sock.sendall(b"\xc0\x00")
            self.last_io = time.monotonic()
            self._drain()

    def _drain(self):
        """Read and ignore what the broker sent (PINGRESP); a closed socket raises."""
        self.sock.setblocking(False)
        try:
            while True:
                try:
                    data = self.sock.recv(4096)
                except (BlockingIOError, ssl.SSLWantReadError):
                    return
                if not data:
                    raise ExportError("broker closed the connection")
        finally:
            self.sock.setblocking(True)
            self.sock.settimeout(self.timeout)

    def close(self):
        s, self.sock = self.sock, None
        if s is not None:
            try:
                s.sendall(b"\xe0\x00")  # DISCONNECT
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------------------------------------------

class SqliteSink:
    def __init__(self, path):
        folder = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(folder):
            raise ExportError(f"folder does not exist: {folder}")
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS readings (ts REAL NOT NULL, time TEXT NOT NULL, device TEXT NOT NULL,"
                        " point TEXT NOT NULL, value REAL, text TEXT, ok INTEGER NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS readings_dev_ts ON readings(device, ts)")
        self.db.commit()

    def write(self, rows):
        self.db.executemany("INSERT INTO readings VALUES (?,?,?,?,?,?,?)", rows)
        self.db.commit()

    def close(self):
        try:
            self.db.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------------------------------------------
# the exporter
# ---------------------------------------------------------------------------------------------------------------

def iso(t):
    return datetime.datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def json_value(v, text):
    """A reading as a JSON value: numbers stay numbers (NaN / inf -> text), booleans stay booleans."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v if not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else str(v)
    return text if text is not None else (None if v is None else str(v))


class Exporter(threading.Thread):
    """submit(device, t, values{point: value}, texts{point: text}, ok, error) from the GUI thread; this thread sends.
    cfg: norm_export() dict; password: MQTT password (kept in memory only)."""
    QUEUE_MAX = 5000

    def __init__(self, cfg, password="", mqtt_factory=MqttClient, clock=time.monotonic):
        super().__init__(daemon=True)
        self.cfg = norm_export(cfg)
        self.password = password
        self.mqtt_factory, self.clock = mqtt_factory, clock
        self.q = collections.deque()
        self.cv = threading.Condition()
        self.halt = False
        self.mqtt = None
        self.sql = None
        self.retry_at = 0.0
        self.backoff = 1.0
        self.last_pub = {}
        self.stats = {"queued": 0, "mqtt_sent": 0, "mqtt_errors": 0, "mqtt_connected": False, "sql_rows": 0,
                      "sql_errors": 0, "dropped": 0, "skipped": 0, "last_error": None, "last_error_t": None,
                      "last_sent_t": None, "mqtt_backlog": 0}
        self.mpend = collections.deque()  # readings waiting for the broker
        m = self.cfg["mqtt"]
        self.mqtt_on = bool(m["on"])
        self.sql_on = bool(self.cfg["sqlite"]["on"])
        if self.mqtt_on:
            errs = check_mqtt(m)
            if errs:
                raise ExportError("MQTT: " + "; ".join(f"{k}: {v}" for k, v in errs.items()))
            self.min_iv = float(str(m["min_interval_s"]).replace(",", ".") or 0)
        else:
            self.min_iv = 0.0
        if self.sql_on:
            if not self.cfg["sqlite"]["path"].strip():
                raise ExportError("SQLite: choose a database file")
            self.sql = SqliteSink(self.cfg["sqlite"]["path"].strip())  # opened now: a bad path fails at Start
        if not (self.mqtt_on or self.sql_on):
            raise ExportError("nothing to export: switch on MQTT and / or SQLite")

    # ----- GUI thread -----
    def submit(self, device, t, values, texts, ok, error=None):
        with self.cv:
            if len(self.q) >= self.QUEUE_MAX:
                self.q.popleft()
                self.stats["dropped"] += 1
            self.q.append((device, t, dict(values or {}), dict(texts or {}), bool(ok), error))
            self.stats["queued"] = len(self.q)
            self.cv.notify()

    def stop(self, wait=3.0):
        with self.cv:
            self.halt = True
            self.cv.notify()
        if self.is_alive():
            self.join(wait)

    # ----- worker -----
    def _error(self, text):
        self.stats["last_error"] = text
        self.stats["last_error_t"] = time.time()

    def _ensure_mqtt(self):
        if self.mqtt is not None:
            return True
        if self.clock() < self.retry_at:
            return False
        m = self.cfg["mqtt"]
        cl = self.mqtt_factory(m["host"].strip(), int(m["port"]), m["client_id"].strip(), m["user"].strip(),
                               self.password, bool(m["tls"]))
        try:
            cl.connect()
        except Exception as e:  # noqa: BLE001 - reported in the window, retried with back-off
            self._error(f"MQTT connect: {e}")
            self.stats["mqtt_errors"] += 1
            self.retry_at = self.clock() + self.backoff
            self.backoff = min(self.backoff * 2, 30.0)
            return False
        self.mqtt, self.backoff = cl, 1.0
        self.stats["mqtt_connected"] = True
        return True

    def _drop_mqtt(self, err):
        self._error(f"MQTT: {err}")
        self.stats["mqtt_errors"] += 1
        self.stats["mqtt_connected"] = False
        if self.mqtt is not None:
            self.mqtt.close()
        self.mqtt = None
        self.retry_at = self.clock() + self.backoff
        self.backoff = min(self.backoff * 2, 30.0)

    def mqtt_messages(self, item):
        """One reading -> [(topic, payload)] for the configured style."""
        device, t, values, texts, ok, error = item
        m = self.cfg["mqtt"]
        base = f"{m['prefix'].strip()}/{topic_part(device)}"
        if m["style"] == "topics":
            out = [(f"{base}/status", json.dumps({"ts": iso(t), "ok": ok, "error": error}))]
            if ok:
                for k, v in values.items():
                    jv = json_value(v, texts.get(k))
                    out.append((f"{base}/{topic_part(k)}", jv if isinstance(jv, str) else json.dumps(jv)))
            return out
        body = {"ts": iso(t), "ok": ok}
        if ok:
            body["values"] = {str(k): json_value(v, texts.get(k)) for k, v in values.items()}
        else:
            body["error"] = error
        return [(base, json.dumps(body, ensure_ascii=False))]

    def sql_rows(self, item):
        device, t, values, texts, ok, error = item
        when = iso(t)
        if not ok:
            return [(t, when, device, "", None, error or "error", 0)]
        rows = []
        for k, v in values.items():
            num = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else (
                float(v) if isinstance(v, bool) else None)
            if num is not None and (math.isnan(num) or math.isinf(num)):
                num = None
            rows.append((t, when, device, str(k), num, texts.get(k), 1))
        return rows

    def run(self):
        try:
            while True:
                with self.cv:
                    while not self.q and not self.halt:
                        self.cv.wait(1.0)
                        if self.mqtt is not None or self.mpend:
                            break  # keep-alive, or a backlog waiting for the broker
                    if self.halt and not self.q:
                        break
                    batch = list(self.q)
                    self.q.clear()
                    self.stats["queued"] = 0
                if self.mqtt_on:
                    self._send_mqtt(batch)
                if self.sql_on and batch:
                    rows = [r for it in batch for r in self.sql_rows(it)]
                    try:
                        self.sql.write(rows)
                        self.stats["sql_rows"] += len(rows)
                    except sqlite3.Error as e:
                        self.stats["sql_errors"] += 1
                        self._error(f"SQLite: {e}")
                if self.halt:
                    break
        finally:
            if self.mqtt is not None:
                self.mqtt.close()
                self.mqtt = None
            self.stats["mqtt_connected"] = False
            if self.sql is not None:
                self.sql.close()

    def _send_mqtt(self, batch):
        """Readings -> the MQTT backlog (min interval per device applied), then publish the backlog when the broker
        is reachable. The backlog survives a broker outage (bounded: the oldest are dropped)."""
        now = time.monotonic()
        pend = self.mpend
        for it in batch:
            dev = it[0]
            if self.min_iv and it[4] and now - self.last_pub.get(dev, -1e9) < self.min_iv:
                self.stats["skipped"] += 1
                continue
            self.last_pub[dev] = now
            if len(pend) >= self.QUEUE_MAX:
                pend.popleft()
                self.stats["dropped"] += 1
            pend.append(it)
        self.stats["mqtt_backlog"] = len(pend)
        if not self._ensure_mqtt():
            if pend:
                time.sleep(min(0.5, max(0.0, self.retry_at - self.clock())))
            return
        try:
            while pend:
                it = pend[0]
                for topic, payload in self.mqtt_messages(it):
                    self.mqtt.publish(topic, payload, retain=bool(self.cfg["mqtt"]["retain"]))
                    self.stats["mqtt_sent"] += 1
                pend.popleft()
                self.stats["last_sent_t"] = time.time()
            self.stats["mqtt_backlog"] = 0
            self.mqtt.ping_if_idle()
        except (OSError, ExportError) as e:
            self._drop_mqtt(e)
            self.stats["mqtt_backlog"] = len(pend)


def test_mqtt(cfg, password="", factory=MqttClient):
    """Connect + publish one test message + disconnect. -> None or an error text."""
    m = norm_export(cfg)["mqtt"]
    errs = check_mqtt(m)
    if errs:
        return "; ".join(f"{k}: {v}" for k, v in errs.items())
    cl = factory(m["host"].strip(), int(m["port"]), m["client_id"].strip(), m["user"].strip(), password, bool(m["tls"]))
    try:
        cl.connect()
        cl.publish(f"{m['prefix'].strip()}/_test", json.dumps({"ts": iso(time.time()), "from": "mbtool"}))
    except Exception as e:  # noqa: BLE001
        return str(e) or repr(e)
    finally:
        cl.close()
    return None
