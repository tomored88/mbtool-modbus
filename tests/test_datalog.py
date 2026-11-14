import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import csv
import datetime
import os
import tempfile
import unittest

from datalog import CsvLog, DeviceLogs, LogError, safe_name


def ts(y, m, d, hh=12, mm=0, ss=0):
    return datetime.datetime(y, m, d, hh, mm, ss).timestamp()


def read(path, delimiter=",", encoding="utf-8"):
    with open(path, newline="", encoding=encoding) as f:
        return list(csv.reader(f, delimiter=delimiter))


class Datalog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.dir.name, "meter.csv")

    def tearDown(self):
        self.dir.cleanup()

    def test_plain_file_header_and_rows(self):
        lg = CsvLog(self.p)
        lg.write(["V", "A"], [230.4, 12.5], t=ts(2026, 11, 3))
        lg.write(["V", "A"], [230.5, 12.6], t=ts(2026, 11, 3, 12, 0, 1))
        lg.write(["V", "A", "W"], [1, 2, 3], t=ts(2026, 11, 3, 12, 0, 2))  # header change -> new header row
        lg.write_error("response timeout", t=ts(2026, 11, 3, 12, 0, 3))
        lg.close()
        rows = read(self.p)
        self.assertEqual(rows[0], ["Time", "V", "A"])
        self.assertEqual(rows[1], ["2026-11-03 12:00:00", "230.4", "12.5"])
        self.assertEqual(rows[3], ["Time", "V", "A", "W"])
        self.assertEqual(rows[-1][1:], ["ERROR", "response timeout"])

    def test_appending_writes_header_again_per_session(self):
        for _ in range(2):
            lg = CsvLog(self.p)
            lg.write(["V"], [1], t=ts(2026, 11, 3))
            lg.close()
        self.assertEqual([r[0] for r in read(self.p)], ["Time", "2026-11-03 12:00:00", "Time", "2026-11-03 12:00:00"])

    def test_daily_rotation(self):
        lg = CsvLog(self.p, rotate="daily")
        lg.write(["V"], [1], t=ts(2026, 11, 3, 23, 59, 59))
        lg.write(["V"], [2], t=ts(2026, 11, 4, 0, 0, 1))
        lg.close()
        names = sorted(os.listdir(self.dir.name))
        self.assertEqual(names, ["meter_2026-11-03.csv", "meter_2026-11-04.csv"])
        self.assertEqual(read(os.path.join(self.dir.name, names[1]))[0], ["Time", "V"])

    def test_size_rotation_and_max_files(self):
        lg = CsvLog(self.p, rotate="size", max_bytes=120, max_files=3)
        for i in range(60):
            lg.write(["Value"], [i], t=ts(2026, 11, 3, 12, 0, i))
        lg.close()
        files = sorted(os.listdir(self.dir.name))
        self.assertEqual(len(files), 3)  # older ones pruned
        self.assertTrue(all(f.startswith("meter_2026-11-03_") for f in files))
        total_rows = sum(len(read(os.path.join(self.dir.name, f))) for f in files)
        self.assertGreater(total_rows, 0)
        for f in files:  # every file starts with its own header
            self.assertEqual(read(os.path.join(self.dir.name, f))[0], ["Time", "Value"])

    def test_pruning_never_touches_unrelated_files(self):
        other = os.path.join(self.dir.name, "keep_me.csv")
        open(other, "w").write("x")
        open(os.path.join(self.dir.name, "meter_notes.txt"), "w").write("x")
        lg = CsvLog(self.p, rotate="daily", max_files=1)
        for d in (1, 2, 3):
            lg.write(["V"], [d], t=ts(2026, 11, d))
        lg.close()
        left = sorted(os.listdir(self.dir.name))
        self.assertIn("keep_me.csv", left)
        self.assertIn("meter_notes.txt", left)
        self.assertEqual([f for f in left if f.startswith("meter_2026")], ["meter_2026-11-03.csv"])

    def test_excel_friendly_options(self):
        lg = CsvLog(self.p, delimiter=";", decimal=",", time_format="excel", encoding="utf-8-sig")
        lg.write(["Temp ไทย"], [25.3], t=ts(2026, 11, 3, 12, 0, 0))
        lg.close()
        raw = open(self.p, "rb").read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # BOM so Excel detects UTF-8
        rows = read(self.p, ";", "utf-8-sig")
        self.assertEqual(rows[0], ["Time", "Temp ไทย"])
        self.assertEqual(rows[1][1], "25,3")
        serial = float(rows[1][0])
        self.assertAlmostEqual(serial, 46329.5, places=3)  # 2026-11-03 12:00 as an Excel date serial

    def test_time_formats(self):
        t = ts(2026, 11, 3, 1, 2, 3) + 0.456
        for fmt, want in (("iso", "2026-11-03 01:02:03"), ("iso_ms", "2026-11-03 01:02:03.456")):
            p = os.path.join(self.dir.name, fmt + ".csv")
            lg = CsvLog(p, time_format=fmt)
            lg.write(["x"], [1], t=t)
            lg.close()
            self.assertEqual(read(p)[1][0], want)

    def test_validation(self):
        for kw in ({"rotate": "weekly"}, {"rotate": "size"}, {"delimiter": "x"}, {"decimal": ","},
                   {"time_format": "nope"}):
            with self.assertRaises(ValueError, msg=kw):
                CsvLog(self.p, **kw)

    def test_write_failure_raises_logerror(self):
        lg = CsvLog(self.p)
        lg.write(["V"], [1], t=ts(2026, 11, 3))
        lg._f.close()  # simulate disk/handle failure
        with self.assertRaises(LogError):
            lg.write(["V"], [2], t=ts(2026, 11, 3, 12, 0, 1))

    def test_device_logs_one_file_each(self):
        d = DeviceLogs(self.dir.name, rotate="none")
        d.write("Meter A", ["V"], [1], t=ts(2026, 11, 3))
        d.write("Meter/B:1", ["V"], [2], t=ts(2026, 11, 3))
        d.write_error("Meter A", "timeout", t=ts(2026, 11, 3, 12, 0, 1))
        d.close()
        self.assertEqual(sorted(os.listdir(self.dir.name)), ["Meter A.csv", "Meter_B_1.csv"])
        self.assertEqual(read(os.path.join(self.dir.name, "Meter A.csv"))[-1][1], "ERROR")
        self.assertEqual(safe_name("  "), "device")


if __name__ == "__main__":
    unittest.main()
