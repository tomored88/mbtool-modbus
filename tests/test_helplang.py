"""Two-language manual: help/en mirrors help/*.md topic for topic, the language switch (helpdocs, Help window,
Help menu, saved setting) and the MCP help tools' lang argument."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import re
import tempfile
import time
import tkinter as tk
import unittest

import helpdocs

THAI = re.compile(r"[฀-๿]")


class Parity(unittest.TestCase):
    def setUp(self):
        self.th = helpdocs.load(lang="th", reload=True)
        self.en = helpdocs.load(lang="en", reload=True)

    def tearDown(self):
        helpdocs.set_lang("th")

    def test_same_topics_same_order_same_files(self):
        self.assertEqual([t.id for t in self.th], [t.id for t in self.en])
        self.assertEqual([t.file for t in self.th], [t.file for t in self.en])

    def test_links_tables_code_match(self):
        en = {t.id: t for t in self.en}
        for a in self.th:
            b = en[a.id]
            self.assertEqual(set(a.links()), set(b.links()), a.id)
            self.assertEqual(a.body.count("```"), b.body.count("```"), a.id)
            self.assertEqual(a.body.count("\n|"), b.body.count("\n|"), f"{a.id}: table rows")

    def test_english_text(self):
        for t in self.en:
            self.assertIsNone(THAI.search(t.category), t.id)
            self.assertIsNone(THAI.search(t.title), t.id)
            body = t.body.replace("ไทย", "")  # the language option is called ไทย in the program
            self.assertIsNone(THAI.search(body), f"{t.id}: Thai text left")

    def test_switch_search_and_fallback(self):
        self.assertEqual(helpdocs.set_lang("en"), "en")
        self.assertEqual(helpdocs.get("fc03").title, next(t.title for t in self.en if t.id == "fc03"))
        self.assertTrue(helpdocs.search("timeout"))
        self.assertEqual(helpdocs.topic_for("devices.health").id, "bus-health")
        self.assertEqual(helpdocs.set_lang("xx"), "th")
        self.assertIsNotNone(THAI.search(helpdocs.get("quickstart").body))
        empty = tempfile.mkdtemp()  # no English files -> Thai
        saved = helpdocs.HELP_DIR
        try:
            helpdocs.HELP_DIR = saved
            self.assertEqual(helpdocs._folder(None, "en"), os.path.join(saved, "en"))
        finally:
            helpdocs.HELP_DIR = saved
        self.assertTrue(os.path.isdir(empty))

    def test_mcp_lang(self):
        import mbtool_mcp
        fn = getattr(mbtool_mcp.get_help, "fn", mbtool_mcp.get_help)
        r = fn("fc03", lang="en")
        self.assertTrue(r["ok"])
        self.assertIsNone(THAI.search(r["title"] + r["body"]))
        r = fn("fc03", lang="th")
        self.assertTrue(r["ok"])
        sfn = getattr(mbtool_mcp.search_help, "fn", mbtool_mcp.search_help)
        self.assertTrue(sfn("crc", lang="en"))


class WindowSwitch(unittest.TestCase):
    def test_help_window_and_menu(self):
        import mbtool as MB
        d = tempfile.mkdtemp()
        saved = (MB.SETTINGS_FILE, MB.HOME_SETTINGS)
        MB.SETTINGS_FILE, MB.HOME_SETTINGS = os.path.join(d, "s.json"), os.path.join(d, "h.json")
        root = tk.Tk()
        root.withdraw()
        try:
            app = MB.MasterApp(root)
            self.assertEqual(app.cfg["help"]["lang"], "th")
            w = app.open_help(topic="fc03")
            root.update()
            th_title = w.cur.title
            w.v_lang.set("en")
            app.set_help_lang("en")
            root.update()
            self.assertEqual(w.cur.id, "fc03")  # same topic, other language
            self.assertNotEqual(w.cur.title, th_title)
            self.assertIsNone(THAI.search(w.cur.body))
            self.assertEqual(app.help_lang_var.get(), "en")
            w.v_q.set("timeout")
            w.fill_list()
            self.assertTrue(w.tree.get_children())
            import json
            with open(MB.SETTINGS_FILE, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["help"]["lang"], "en")
            app.set_help_lang("th")
            root.update()
            self.assertEqual(w.cur.title, th_title)
            self.assertEqual(w.v_lang.get(), "th")
            w.close()
        finally:
            helpdocs.set_lang("th")
            root.destroy()
            MB.SETTINGS_FILE, MB.HOME_SETTINGS = saved


if __name__ == "__main__":
    unittest.main(verbosity=0)
