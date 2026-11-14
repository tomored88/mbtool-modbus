import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import os
import re
import subprocess
import sys
import unittest

import helpdocs
import preset
from modbus_core import EXC_NAMES, FC_NAMES, FORMATS

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Manual(unittest.TestCase):
    def setUp(self):
        self.topics = helpdocs.load(reload=True)
        self.by_id = {t.id: t for t in self.topics}

    def test_loads_and_is_substantial(self):
        self.assertGreaterEqual(len(self.topics), 50)
        for t in self.topics:
            self.assertTrue(t.title and t.body and t.category, t.id)
            self.assertTrue(re.fullmatch(r"[a-z0-9._-]+", t.id), t.id)

    def test_all_links_resolve(self):
        for t in self.topics:
            for target in t.links():
                self.assertIn(target, self.by_id, f"{t.id} links to unknown [[{target}]]")

    def test_every_topic_is_reachable(self):
        linked = {x for t in self.topics for x in t.links()}
        # entry points shown in the topic list need no inbound link; all others should be linked from somewhere
        unlinked = [t.id for t in self.topics if t.id not in linked and t.id not in
                    ("quickstart", "glossary-am", "glossary-nz", "values-fields", "values-codes", "values-coil",
                     "values-alerts-log", "shortcuts", "trouble-exc2-block", "trouble-multimaster", "cli",
                     "exc04", "err-other", "err-timeout", "err-crc", "preset-howto", "mcp", "fc17", "fc43", "fc22",
                     "fc23", "fc15", "fc05", "fc06", "fc16", "fc02", "fc01", "grid", "slave-sim", "ai-assist",
                     "frame-rtu", "frame-ascii", "frame-tcp", "strings-bcd", "limits", "data-areas", "poll-panel")]
        self.assertEqual(unlinked, [], "topics nobody links to (add a link or list them as entry points)")

    def test_every_supported_function_code_is_documented(self):
        for fc in FC_NAMES:
            self.assertIn(f"fc{fc:02d}", self.by_id, f"FC{fc} has no topic")
        self.assertIn("FC43", self.by_id["fc43"].title)

    def test_every_exception_code_is_in_the_exception_table(self):
        body = self.by_id["exceptions"].body
        for code, name in EXC_NAMES.items():
            self.assertIn(name.lower(), body.lower(), f"exception {code} ({name}) missing")

    def test_display_formats_documented(self):
        body = self.by_id["display-formats"].body + self.by_id["values-fields"].body
        for f in FORMATS:
            self.assertIn(f.split()[0], body, f)

    def test_preset_fields_and_types_documented(self):
        src = open(os.path.join(HERE, "preset.py"), encoding="utf-8").read()
        point_src = src[src.index("class Point"):src.index("class Preset")]
        fields = set(re.findall(r'd\.get\("(\w+)"', point_src)) | set(re.findall(r'd\["(\w+)"\]', point_src))
        file_src = src[src.index("class Preset"):src.index("def load_preset")]
        fields |= set(re.findall(r'd\.get\("(\w+)"', file_src))
        body = self.by_id["preset-schema"].body
        missing = sorted(f for f in fields if f"`{f}`" not in body)
        self.assertEqual(missing, [], "preset fields not explained in the manual")
        for ty in preset.TYPE_WORDS:
            self.assertIn(ty, body, ty)

    def test_cli_commands_documented(self):
        body = self.by_id["cli"].body
        for want in ("sim", "vcom", "preset-list", "scanner.py", "testcenter.py", "interpret.py"):
            self.assertIn(want, body)

    def test_search_english_and_thai(self):
        def top(q, n=3):
            return [t.id for _, t, _ in helpdocs.search(q)[:n]]
        self.assertEqual(top("holding registers")[0], "fc03")
        self.assertIn("exc02", top("exception 02"))
        self.assertIn("exc02", top("illegal data address"))
        self.assertIn("word-order", top("abcd cdab"))
        self.assertIn("address-bases", top("40001"))
        self.assertIn("trouble-crc", top("crc error"))
        self.assertIn("rs485-wiring", top("termination"))
        self.assertIn("word-order", top("ลำดับ word"))
        self.assertIn("address-bases", top("เหลื่อม"))
        self.assertIn("scale-offset", top("ทศนิยม"))
        self.assertEqual(helpdocs.search("zzzz-not-there"), [])
        self.assertEqual(helpdocs.search("   "), [])
        # all words must match
        self.assertEqual(helpdocs.search("fc03 ทำไมถึงไม่มีคำนี้เลย"), [])

    def test_snippets_and_scores_are_sane(self):
        res = helpdocs.search("termination")
        self.assertTrue(all(len(sn) <= 140 and "**" not in sn for _, _, sn in res))
        self.assertGreaterEqual(res[0][0], res[-1][0])

    def test_categories_cover_every_topic(self):
        n = sum(len(ts) for _, ts in helpdocs.categories())
        self.assertEqual(n, len(self.topics))
        self.assertGreaterEqual(len(helpdocs.categories()), 8)

    def test_markdown_helpers(self):
        b = helpdocs.blocks("para one\ncontinues\n\n- a\n- b\n\n1. x\n2. y\n\n| h1 | h2 |\n|---|---|\n| c1 | c2 |\n\n```\ncode\n```")
        kinds = [x[0] for x in b]
        self.assertEqual(kinds, ["p", "ul", "ol", "table", "code"])
        self.assertEqual(b[0][1], "para one continues")
        self.assertEqual(b[3][1:], (["h1", "h2"], [["c1", "c2"]]))
        segs = helpdocs.inline("see [[fc03]] for **bold** and `code` text")
        self.assertEqual([s[0] for s in segs], ["text", "link", "text", "bold", "text", "code", "text"])
        self.assertEqual(segs[1][2], "fc03")
        for t in self.topics:  # every body parses without surprises
            self.assertTrue(helpdocs.blocks(t.body), t.id)

    def test_bad_topic_files_are_rejected(self):
        import tempfile
        d = tempfile.mkdtemp()
        open(os.path.join(d, "a.md"), "w", encoding="utf-8").write("## T\nno meta\n")
        with self.assertRaises(helpdocs.HelpError):
            helpdocs.load(d, reload=True)
        d2 = tempfile.mkdtemp()
        for n in ("a.md", "b.md"):
            open(os.path.join(d2, n), "w", encoding="utf-8").write("## T\n<!-- id: same | category: c -->\nbody\n")
        with self.assertRaises(helpdocs.HelpError):
            helpdocs.load(d2, reload=True)

    def test_cli(self):
        out = subprocess.run([sys.executable, "helpdocs.py", "search", "fc03"], cwd=HERE, capture_output=True,
                             text=True, encoding="utf-8").stdout
        self.assertIn("fc03", out)
        self.assertIn("Read Holding Registers", subprocess.run([sys.executable, "helpdocs.py", "show", "fc03"],
                      cwd=HERE, capture_output=True, text=True, encoding="utf-8").stdout)


if __name__ == "__main__":
    unittest.main()
