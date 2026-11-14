"""Built-in manual: topics stored as Markdown in help/*.md, with search, a topic list by category and
a small renderer helper for GUIs.

Each topic starts with a '## Title' line followed by a metadata comment:
    ## FC03 Read Holding Registers
    <!-- id: fc03 | category: Function codes | keywords: holding registers 4x -->
    body in Markdown (paragraphs, bullets, numbered lists, tables, code blocks, **bold**, `code`, [[other-id]] links)

Two languages: Thai in help/*.md, English in help/en/*.md (same topic ids). set_lang("en") switches the default;
every function also takes lang=. A topic missing in English falls back to the Thai one.

  python helpdocs.py [--lang en] list                 all topics grouped by category
  python helpdocs.py [--lang en] search "exception 2" ranked results with a snippet
  python helpdocs.py [--lang en] show fc03
"""
import glob
import os
import re
import sys

import appinfo

HELP_DIR = os.path.join(appinfo.resource_dir(), "help")
LANGS = {"th": "ไทย", "en": "English"}
_LANG = ["th"]


def lang_dir(lang):
    return HELP_DIR if lang != "en" else os.path.join(HELP_DIR, "en")


def set_lang(lang):
    """Default language of the manual ('th' or 'en'); unknown values mean Thai."""
    _LANG[0] = lang if lang in LANGS else "th"
    return _LANG[0]


def current_lang():
    return _LANG[0]


def _folder(folder, lang):
    if folder:
        return folder
    d = lang_dir(lang or _LANG[0])
    if d != HELP_DIR and not glob.glob(os.path.join(d, "*.md")):
        return HELP_DIR  # no English files installed: Thai
    return d
META = re.compile(r"^<!--\s*(.*?)\s*-->\s*$")
LINK = re.compile(r"\[\[([a-z0-9._-]+)\]\]")


class Topic:
    def __init__(self, id, title, category, keywords, body, file):
        self.id, self.title, self.category, self.keywords, self.body, self.file = id, title, category, keywords, body, file

    def as_dict(self, with_body=True):
        d = {"id": self.id, "title": self.title, "category": self.category, "keywords": self.keywords}
        if with_body:
            d["body"] = self.body
        return d

    def links(self):
        return LINK.findall(self.body)


class HelpError(ValueError):
    pass


def parse_file(path):
    topics, cur = [], None
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            if cur:
                topics.append(cur)
            cur = {"title": line[3:].strip(), "meta": None, "body": []}
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            m = META.match(lines[i]) if i < len(lines) else None
            if not m:
                raise HelpError(f"{os.path.basename(path)}: topic '{cur['title']}' has no <!-- id: ... --> line")
            meta = {}
            for part in m.group(1).split("|"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    meta[k.strip()] = v.strip()
            for need in ("id", "category"):
                if not meta.get(need):
                    raise HelpError(f"{os.path.basename(path)}: topic '{cur['title']}' is missing '{need}'")
            cur["meta"] = meta
        elif cur is not None:
            cur["body"].append(line)
        i += 1
    if cur:
        topics.append(cur)
    out = []
    for t in topics:
        m = t["meta"]
        out.append(Topic(m["id"], t["title"], m["category"], m.get("keywords", ""),
                         "\n".join(t["body"]).strip(), os.path.basename(path)))
    return out


_CACHE = {}


def load(folder=None, reload=False, lang=None):
    folder = _folder(folder, lang)
    if reload or folder not in _CACHE:
        topics, seen = [], {}
        for path in sorted(glob.glob(os.path.join(folder, "*.md"))):
            for t in parse_file(path):
                if t.id in seen:
                    raise HelpError(f"duplicate topic id '{t.id}' in {t.file} and {seen[t.id]}")
                seen[t.id] = t.file
                topics.append(t)
        _CACHE[folder] = topics
    return _CACHE[folder]


def get(topic_id, folder=None, lang=None):
    for t in load(folder, lang=lang):
        if t.id == topic_id:
            return t
    if not folder and _folder(None, lang) != HELP_DIR:  # not translated yet: the Thai topic
        return get(topic_id, HELP_DIR)
    raise KeyError(topic_id)


def categories(folder=None, lang=None):
    """[(category, [Topic, ...])] in the order the categories first appear."""
    out = {}
    for t in load(folder, lang=lang):
        out.setdefault(t.category, []).append(t)
    return list(out.items())


def _tokens(q):
    return [t for t in re.split(r"\s+", q.strip().lower()) if t]


def search(query, folder=None, limit=20, lang=None):
    """Every word must appear somewhere in the topic (title, id, keywords or body; Thai and English both work).
    Score: title 10, id 8, keywords 6, category 3, body 1 per hit (max 5), +8 when the whole phrase is in the title.
    Returns [(score, Topic, snippet)] best first."""
    toks = _tokens(query)
    if not toks:
        return []
    phrase = query.strip().lower()
    results = []
    for t in load(folder, lang=lang):
        title, tid, kw, cat, body = t.title.lower(), t.id.lower(), t.keywords.lower(), t.category.lower(), t.body.lower()
        score, ok = 0, True
        for w in toks:
            s = 0
            if w in title: s += 10
            if w in tid: s += 8
            if w in kw: s += 6
            if w in cat: s += 3
            n = body.count(w)
            if n: s += min(n, 5)
            if not s:
                ok = False
                break
            score += s
        if not ok:
            continue
        if phrase in title:
            score += 8
        results.append((score, t, snippet(t.body, toks)))
    results.sort(key=lambda r: (-r[0], r[1].title))
    return results[:limit]


def snippet(body, toks, width=110):
    low = body.lower()
    pos = min((low.find(w) for w in toks if low.find(w) >= 0), default=-1)
    if pos < 0:
        text = body[:width]
    else:
        a = max(0, pos - width // 3)
        text = ("…" if a else "") + body[a:a + width] + ("…" if a + width < len(body) else "")
    return re.sub(r"\s+", " ", text.replace("**", "").replace("`", "")).strip()


# Which topic F1 opens for a given control (GUI widget key -> topic id). Unknown keys fall back to "quickstart".
CONTEXT_TOPICS = {
    "poll.unit": "slave-id", "poll.function": "data-areas", "poll.address": "address-bases",
    "poll.quantity": "limits", "poll.scan": "poll-panel", "poll.display": "display-formats",
    "poll.rows": "poll-panel", "poll.base": "address-bases", "poll.grid": "grid",
    "poll.multi": "multi-view", "poll.tabs": "poll-tabs",
    "setup.tcp": "connect-tcp", "setup.serial": "connect-serial", "setup.baud": "serial-settings",
    "setup.timeout": "values-fields", "setup.parity": "serial-settings", "setup.stop": "serial-settings", "setup.mode": "rtu-vs-ascii",
    "write": "write-dialog", "inspector": "inspector", "traffic": "traffic-monitor", "preset": "preset-view",
    "preset.editor": "preset-editor", "trend": "trend", "alerts": "alerts", "devices": "devices",
    "devices.tabs": "devices-tabs", "devices.scan": "devices-tabs", "devices.cycle": "plc-cycle", "devices.health": "bus-health", "report": "commission-report", "export": "data-export",
    "bus.map": "bus-map",
    "log": "logging", "workspace": "workspace", "slavesim": "slave-sim", "slavesim.faults": "sim-faults", "ai": "ai-assist",
    "error.timeout": "trouble-no-response", "error.crc": "trouble-crc", "error.exception": "exceptions",
    "error.busy": "trouble-port-busy",
}


def topic_for(context_key, folder=None, lang=None):
    """Topic for an F1 press on a control; falls back to the quick start."""
    return get(CONTEXT_TOPICS.get(context_key, "quickstart"), folder, lang=lang)


# ---------- rendering helpers for GUIs ----------
def inline(text):
    """Split a line into [(kind, text, target)] with kind in text|bold|code|link."""
    out, pos = [], 0
    pat = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\[\[([a-z0-9._-]+)\]\]")
    for m in pat.finditer(text):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()], None))
        if m.group(1) is not None:
            out.append(("bold", m.group(1), None))
        elif m.group(2) is not None:
            out.append(("code", m.group(2), None))
        else:
            out.append(("link", m.group(3), m.group(3)))
        pos = m.end()
    if pos < len(text):
        out.append(("text", text[pos:], None))
    return out


def blocks(body):
    """Body -> [('p', text) | ('ul', [items]) | ('ol', [items]) | ('table', header, rows) | ('code', text)]."""
    out, lines, i = [], body.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.startswith("```"):
            j, buf = i + 1, []
            while j < len(lines) and not lines[j].startswith("```"):
                buf.append(lines[j])
                j += 1
            out.append(("code", "\n".join(buf)))
            i = j + 1
        elif ln.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            out.append(("table", rows[0], rows[1:]))
        elif re.match(r"^\s*[-*] ", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*] ", lines[i]):
                items.append(re.sub(r"^\s*[-*] ", "", lines[i]))
                i += 1
            out.append(("ul", items))
        elif re.match(r"^\s*\d+\. ", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\. ", lines[i]):
                items.append(re.sub(r"^\s*\d+\. ", "", lines[i]))
                i += 1
            out.append(("ol", items))
        else:
            buf = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("```", "|")) \
                    and not re.match(r"^\s*([-*]|\d+\.) ", lines[i]):
                buf.append(lines[i].strip())
                i += 1
            out.append(("p", " ".join(buf)))
    return out


def main(argv=None):
    try:  # Thai text must survive a Windows console / pipe with a legacy code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) >= 2 and a[0] == "--lang":
        set_lang(a[1])
        a = a[2:]
    if not a or a[0] not in ("list", "search", "show"):
        print(__doc__)
        return 2
    if a[0] == "list":
        for cat, ts in categories():
            print(f"\n[{cat}]")
            for t in ts:
                print(f"  {t.id:<22} {t.title}")
    elif a[0] == "search":
        res = search(" ".join(a[1:]))
        if not res:
            print("no topic matches")
            return 1
        for score, t, sn in res:
            print(f"{t.id:<22} {t.title}   [{t.category}]\n    {sn}")
    else:
        try:
            t = get(a[1])
        except (IndexError, KeyError):
            print("unknown topic id; use 'list'")
            return 1
        print(f"# {t.title}   [{t.category}]\n\n{t.body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
