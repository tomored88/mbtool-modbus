"""GUI-independent pieces of the F1 manual window (monitor.py): error -> topic, history, plain text, table widths."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import unittest

import helpdocs
import monitor as M


class ErrorKeys(unittest.TestCase):
    def test_error_texts_map_to_topics(self):
        cases = {
            "Error: response timeout": "error.timeout",
            "Connection failed: Modbus Connection Failed: [WinError 10061] refused": "error.timeout",
            "Link lost, retrying: connection error: reset": "error.timeout",
            "Error: CRC error": "error.crc",
            "Error: LRC error": "error.crc",
            "Error: malformed response (byte count)": "error.crc",
            "Error: unexpected unit id in response": "error.crc",
            "Error: Exception 2: Illegal Data Address (FC 03)": "error.exception",
            "Error: Exception 6: Slave Device Busy (FC 03)": "error.exception",
            "Connection failed: COM3 is busy - it is already open in another program": "error.busy",
            "Poll definition has errors - see the red hints": None,
            "": None,
        }
        for text, key in cases.items():
            self.assertEqual(M.error_help_key(text), key, text)
        for key in {k for k in cases.values() if k}:
            self.assertIn(key, helpdocs.CONTEXT_TOPICS)
            helpdocs.topic_for(key)  # the topic exists


class History(unittest.TestCase):
    def test_back_forward(self):
        h = M.HelpHistory()
        self.assertIsNone(h.current)
        for t in ("a", "b", "c"):
            h.visit(t)
        h.visit("c")  # same topic twice is one entry
        self.assertEqual(h.items, ["a", "b", "c"])
        self.assertEqual(h.back(), "b")
        self.assertEqual(h.back(), "a")
        self.assertFalse(h.can_back())
        self.assertEqual(h.back(), "a")
        self.assertEqual(h.forward(), "b")
        h.visit("x")  # drops the forward part
        self.assertEqual(h.items, ["a", "b", "x"])
        self.assertFalse(h.can_forward())
        small = M.HelpHistory(limit=3)
        for t in "abcde":
            small.visit(t)
        self.assertEqual(small.items, ["c", "d", "e"])
        self.assertEqual(small.current, "e")


class PlainText(unittest.TestCase):
    def test_every_topic_has_plain_text_without_markup(self):
        by_id = {t.id: t for t in helpdocs.load()}
        title = lambda tid: by_id[tid].title
        for t in by_id.values():
            txt = M.help_plain_text(t, title)
            self.assertTrue(txt.startswith(t.title), t.id)
            self.assertNotIn("[[", txt, t.id)
            self.assertNotIn("**", txt, t.id)
        exc = M.help_plain_text(by_id["exceptions"], title)
        self.assertIn("02 | Illegal Data Address |", exc)
        self.assertIn(by_id["exc02"].title, exc)


class TableWidths(unittest.TestCase):
    def test_fit_and_shrink(self):
        self.assertEqual(M.table_widths([100, 200], 500), [100, 200])
        w = M.table_widths([60, 900, 600], 700)
        self.assertEqual(w[0], 60)              # narrow column keeps its width
        self.assertLessEqual(sum(w), 700 + 1)
        self.assertGreater(w[1], w[2])          # shared in proportion
        self.assertEqual(M.table_widths([], 100), [])
        tiny = M.table_widths([500, 500, 500], 90, min_w=50)
        self.assertTrue(all(x >= 50 for x in tiny))


if __name__ == "__main__":
    unittest.main()
