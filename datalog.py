"""CSV data logging with file rotation, per-device files and Excel-friendly formatting. No GUI code.

    log = CsvLog("C:/logs/meter.csv", rotate="daily", max_files=30, delimiter=";", decimal=",",
                 time_format="iso_ms", encoding="utf-8-sig")
    log.write(["Voltage", "Current"], [230.4, 12.5])      # header is written when it changes / a new file starts
    log.close()

Rotation (file names are derived from the base path):
    none  -> meter.csv                      (append to one file)
    daily -> meter_2026-11-03.csv           (a new file when the local date changes)
    size  -> meter_2026-11-03_001.csv       (a new file when max_bytes would be exceeded; index counts up per day)
    daily+size are combined by rotate="daily" with max_bytes set.
max_files (optional) deletes the oldest rotated files of THIS log (same folder, same base name pattern only).
"""
import csv
import datetime
import os
import re
import threading
import time

TIME_FORMATS = {
    "iso": lambda t: datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S"),
    "iso_ms": lambda t: datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    "unix": lambda t: f"{t:.3f}",
    # Excel date serial: days since 1899-12-30 in local time; format the cell as date/time in Excel
    "excel": lambda t: f"{(datetime.datetime.fromtimestamp(t) - datetime.datetime(1899, 12, 30)).total_seconds() / 86400:.8f}",
}


class LogError(OSError):
    pass


def safe_name(text):
    """Make a device name usable in a file name."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(text)).strip(" .")
    return s or "device"


class CsvLog:
    def __init__(self, path, rotate="none", max_bytes=None, max_files=None, delimiter=",", decimal=".",
                 time_format="iso", encoding="utf-8", clock=time.time):
        if rotate not in ("none", "daily", "size"):
            raise ValueError("rotate must be none, daily or size")
        if rotate == "size" and not max_bytes:
            raise ValueError("rotate='size' needs max_bytes")
        if time_format not in TIME_FORMATS:
            raise ValueError(f"time_format must be one of {sorted(TIME_FORMATS)}")
        if delimiter not in (",", ";", "\t", "|"):
            raise ValueError("delimiter must be comma, semicolon, tab or pipe")
        if decimal not in (".", ","):
            raise ValueError("decimal must be '.' or ','")
        if decimal == "," and delimiter == ",":
            raise ValueError("decimal comma needs a different delimiter (use ';' or tab)")
        self.base = os.path.abspath(path)
        self.rotate, self.max_bytes, self.max_files = rotate, max_bytes, max_files
        self.delimiter, self.decimal, self.encoding = delimiter, decimal, encoding
        self.fmt_time = TIME_FORMATS[time_format]
        self.clock = clock
        self.rows = 0
        self.current_path = None
        self._f = None
        self._w = None
        self._hdr = None
        self._day = None
        self._index = 0
        self._lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(self.base) or ".", exist_ok=True)
        except OSError as e:
            raise LogError(str(e)) from e

    # ---- naming ----
    def _stem(self):
        root, ext = os.path.splitext(self.base)
        return root, (ext or ".csv")

    def _day_str(self, t):
        return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d")

    def _path_for(self, t, index):
        root, ext = self._stem()
        if self.rotate == "none":
            return self.base
        name = f"{root}_{self._day_str(t)}"
        if self.max_bytes or self.rotate == "size":
            name += f"_{index:03d}"
        return name + ext

    def rotated_files(self):
        """Existing files of this log, oldest first (never matches unrelated files)."""
        if self.rotate == "none":
            return [self.base] if os.path.exists(self.base) else []
        root, ext = self._stem()
        folder, prefix = os.path.split(root)
        pat = re.compile(re.escape(prefix) + r"_\d{4}-\d{2}-\d{2}(_\d{3})?" + re.escape(ext) + "$")
        files = [os.path.join(folder, f) for f in os.listdir(folder or ".") if pat.match(f)]
        return sorted(files, key=lambda p: (os.path.getmtime(p), p))

    # ---- writing ----
    def _open(self, t):
        day = self._day_str(t)
        if day != self._day:
            self._day, self._index = day, 0
        if self.rotate != "none" and (self.max_bytes or self.rotate == "size"):
            self._index = max(self._index, 1)
            while self.max_bytes and os.path.exists(self._path_for(t, self._index))                     and os.path.getsize(self._path_for(t, self._index)) >= self.max_bytes:
                self._index += 1
        path = self._path_for(t, self._index)
        self._f = open(path, "a", newline="", encoding=self.encoding)
        self._w = csv.writer(self._f, delimiter=self.delimiter)
        self.current_path = path
        self._hdr = None  # forces a header row at the start of every session in every file
        self._prune()

    def _close_file(self):
        if self._f:
            try:
                self._f.close()
            except OSError:
                pass
        self._f = self._w = None

    def _prune(self):
        if not self.max_files or self.rotate == "none":
            return
        files = self.rotated_files()
        for old in files[:-self.max_files] if len(files) > self.max_files else []:
            if old != self.current_path:
                try:
                    os.remove(old)
                except OSError:
                    pass

    _NUMBER = re.compile(r"^-?\d+\.\d+(e[+-]?\d+)?$", re.I)

    def _cell(self, v):
        if self.decimal == ",":
            if isinstance(v, float):
                return repr(v).replace(".", ",")
            if isinstance(v, str) and self._NUMBER.match(v):
                return v.replace(".", ",")
        if isinstance(v, bool):
            return int(v)
        return v

    def write(self, header, values, t=None):
        """Append one row: time + values. A header row is written when a file starts or the header changes."""
        t = self.clock() if t is None else t
        with self._lock:
            try:
                need_new = self._f is None
                if not need_new and self.rotate in ("daily",) and self._day_str(t) != self._day:
                    need_new = True
                if not need_new and self.max_bytes and self._f.tell() >= self.max_bytes:
                    need_new = True
                if need_new:
                    self._close_file()
                    self._open(t)
                hdr = ["Time"] + list(header)
                if self._hdr != hdr:
                    self._w.writerow(hdr)
                    self._hdr = hdr
                self._w.writerow([self.fmt_time(t)] + [self._cell(v) for v in values])
                self._f.flush()
                self.rows += 1
            except (OSError, ValueError) as e:  # ValueError: handle already closed
                self._close_file()  # next write reopens the file
                raise LogError(str(e)) from e

    def write_error(self, message, t=None):
        """An ERROR row (kept in the same file so gaps are visible)."""
        t = self.clock() if t is None else t
        with self._lock:
            try:
                if self._f is None or (self.rotate == "daily" and self._day_str(t) != self._day) \
                        or (self.max_bytes and self._f.tell() >= self.max_bytes):
                    self._close_file()
                    self._open(t)
                self._w.writerow([self.fmt_time(t), "ERROR", message])
                self._f.flush()
            except (OSError, ValueError) as e:  # ValueError: handle already closed
                self._close_file()  # next write reopens the file
                raise LogError(str(e)) from e

    def close(self):
        with self._lock:
            self._close_file()


class DeviceLogs:
    """One CsvLog per device, files named <folder>/<device>[_date[_index]].csv. Thread-safe."""

    def __init__(self, folder, **options):
        self.folder, self.options = folder, options
        self._logs = {}
        self._lock = threading.Lock()

    def get(self, device):
        with self._lock:
            if device not in self._logs:
                self._logs[device] = CsvLog(os.path.join(self.folder, safe_name(device) + ".csv"), **self.options)
            return self._logs[device]

    def write(self, device, header, values, t=None):
        self.get(device).write(header, values, t)

    def write_error(self, device, message, t=None):
        self.get(device).write_error(message, t)

    def close(self):
        with self._lock:
            for lg in self._logs.values():
                lg.close()
            self._logs.clear()
