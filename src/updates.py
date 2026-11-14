"""Check for updates: asks GitHub which release is the latest and compares it with this program's version.

One HTTPS GET of the public release info (no account, no personal data; the User-Agent is only "mbtool/<version>").
Nothing is downloaded or installed automatically: the GUI shows the new version and opens the download link in the
browser when the user asks for it.

  python src/updates.py            -> prints the result
"""
import json
import re
import sys
import urllib.error
import urllib.request

import appinfo

REPO = "tomored88/mbtool-modbus"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY_S = 24 * 3600  # automatic check at most once a day


class UpdateError(Exception):
    pass


def parse_version(text):
    """'v1.2.3' / '1.2' / 'mbtool 1.2.3-beta' -> (1, 2, 3) (missing parts are 0); None when there is no number."""
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(text or ""))
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def fetch_latest(url=API_URL, timeout=6.0, opener=None):
    """Latest release info from GitHub -> dict (tag_name, html_url, body, assets ...). Raises UpdateError."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"mbtool/{appinfo.VERSION}"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise UpdateError(f"GitHub answered HTTP {e.code}")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        raise UpdateError(f"no connection to GitHub ({reason})")
    except ValueError:
        raise UpdateError("unexpected answer from GitHub")
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise UpdateError("unexpected answer from GitHub")
    return data


def check(current=None, fetch=None):
    """-> {newer, current, latest, page, setup_url, portable_url, notes, published}. Raises UpdateError."""
    current = current or appinfo.VERSION
    rel = (fetch or fetch_latest)()
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or [] if isinstance(a, dict)}
    latest = str(rel.get("tag_name", "")).lstrip("vV")
    return {"newer": is_newer(latest, current), "current": current, "latest": latest,
            "page": rel.get("html_url") or RELEASES_URL,
            "setup_url": assets.get("mbtool-setup.exe"), "portable_url": assets.get("mbtool-portable.zip"),
            "notes": str(rel.get("body") or "")[:4000], "published": str(rel.get("published_at") or "")[:10]}


def main(argv=None):
    try:
        r = check()
    except UpdateError as e:
        print(f"Could not check for updates: {e}")
        return 1
    if r["newer"]:
        print(f"mbtool {r['latest']} is available (you have {r['current']}): {r['page']}")
    else:
        print(f"mbtool {r['current']} is up to date (latest release {r['latest']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
