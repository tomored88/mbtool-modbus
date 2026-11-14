"""Commissioning report: one self-contained HTML page (printable, A4) that documents a bus at handover - project
details, connection settings, every device with its state, counters, error types and response times, the values
read at that moment, measured PLC cycle vs estimate, the latest bus health test, the bus map, and sign-off boxes.
PDF: printed by Microsoft Edge in headless mode (always installed on Windows 10/11), or "Save as PDF" in a browser.

render(data, lang="en"|"th") -> html. data is plain dicts (see MasterApp.commission_data in mbtool.py):
  {"project", "site", "customer", "engineer", "notes", "generated", "version",
   "settings": {"scan_mode", "gap_ms", "target_ms"},
   "buses": [{"label", "conn", "devices": [names], "cycle": BusCycleStats.summary() | None, "estimate_ms"}],
   "devices": [{"name", "unit", "conn", "what", "state", "enabled", "ok", "errors", "quality": LineQuality.summary(),
                "last_t", "last_error", "values": [[point, text], ...]}],
   "health": bushealth report | None, "busmap": {"map": ..., "analysis": ...} | None}
"""
import glob
import html
import os
import subprocess
import time

T = {
    "en": {
        "title": "Modbus commissioning report", "project": "Project", "site": "Site", "customer": "Customer",
        "engineer": "Commissioned by", "date": "Date", "tool": "Tool", "summary": "Summary",
        "verdict_ok": "PASS - every enabled device answers, error rate below 1 %",
        "verdict_check": "CHECK - see the devices marked below",
        "devices_n": "devices", "buses_n": "buses", "ok_n": "OK", "problem_n": "need attention",
        "settings": "Polling settings", "scan_mode": "Scan mode", "gap": "Bus gap", "target": "Cycle target",
        "buses": "Buses", "bus": "Bus", "conn": "Connection", "devs": "Devices",
        "cycle": "Measured cycle (avg / min / max ms)", "estimate": "Estimate ms", "jitter": "Jitter ms",
        "overruns": "Overruns", "busy": "Line busy",
        "devices": "Devices", "name": "Device", "id": "ID", "reads": "Reads", "state": "State", "okc": "OK",
        "err": "Errors", "errpct": "Error %", "types": "Error types", "resp": "Response ms avg / p95 / max",
        "last": "Last update", "lasterr": "Last error", "values": "Values at the time of the report",
        "point": "Point", "value": "Value", "no_values": "no values read",
        "health": "Bus health test", "grade": "Grade", "turn": "Device delay ms", "need_to": "Timeout needed",
        "rec": "Recommendation", "rec_to": "Timeout", "rec_gap": "Gap", "rec_cycle": "Expected cycle",
        "advice": "Findings", "no_health": "No health test was run (Devices > Health test).",
        "busmap": "Bus map (as drawn by the user)", "trunk": "Trunk length", "stubs": "Stubs total",
        "farthest": "Farthest device", "rbaud": "Recommended max. baud", "uload": "Unit loads",
        "warnings": "Wiring notes", "master": "Master", "term": "120 Ω", "no_map": "No bus map drawn.",
        "notes": "Notes", "signoff": "Sign-off", "sig_comm": "Commissioned by", "sig_acc": "Accepted by",
        "sig_name": "Name", "sig_sign": "Signature", "sig_date": "Date",
        "disclaimer": "Values and counters are as read by mbtool at the time of the report. Bus map figures are "
                      "estimates from typical cable data. Presets marked unverified must be checked against the "
                      "device display.",
        "st_ok": "OK", "st_error": "ERROR", "st_stale": "no answer", "st_off": "stopped", "disabled": "disabled",
        "independent": "Independent (per-device scan)", "cyclemode": "PLC cycle", "none": "-",
        "kinds": {"timeout": "Timeout", "crc": "CRC / LRC", "exception": "Exception", "frame": "Bad frame",
                  "wrong_id": "Wrong ID", "link": "No connection", "other": "Other"},
        "since": "counted since", "more": "more values (see the device log / export)",
    },
    "th": {
        "title": "รายงานส่งมอบงานระบบ Modbus", "project": "โครงการ", "site": "สถานที่", "customer": "ลูกค้า",
        "engineer": "ผู้ทดสอบ", "date": "วันที่", "tool": "เครื่องมือ", "summary": "สรุป",
        "verdict_ok": "ผ่าน - อุปกรณ์ที่เปิดใช้ตอบครบทุกตัว อัตรา error ต่ำกว่า 1 %",
        "verdict_check": "ต้องตรวจสอบ - ดูอุปกรณ์ที่มีเครื่องหมายด้านล่าง",
        "devices_n": "อุปกรณ์", "buses_n": "บัส", "ok_n": "ปกติ", "problem_n": "ต้องตรวจสอบ",
        "settings": "การตั้งค่าการอ่าน", "scan_mode": "โหมดการอ่าน", "gap": "Bus gap", "target": "เป้าเวลารอบ",
        "buses": "บัส", "bus": "บัส", "conn": "การเชื่อมต่อ", "devs": "อุปกรณ์",
        "cycle": "เวลารอบที่วัดได้ (เฉลี่ย / ต่ำสุด / สูงสุด ms)", "estimate": "ค่าประมาณ ms", "jitter": "Jitter ms",
        "overruns": "เกินเป้า", "busy": "สายไม่ว่าง",
        "devices": "อุปกรณ์", "name": "อุปกรณ์", "id": "ID", "reads": "สิ่งที่อ่าน", "state": "สถานะ", "okc": "สำเร็จ",
        "err": "ผิดพลาด", "errpct": "Error %", "types": "ชนิด error", "resp": "เวลาตอบ ms เฉลี่ย / p95 / สูงสุด",
        "last": "อ่านล่าสุด", "lasterr": "error ล่าสุด", "values": "ค่าที่อ่านได้ ณ เวลาทำรายงาน",
        "point": "จุด", "value": "ค่า", "no_values": "ยังไม่มีค่า",
        "health": "ผลทดสอบสุขภาพบัส", "grade": "ผล", "turn": "เวลาคิดของอุปกรณ์ ms", "need_to": "timeout ที่ต้องใช้",
        "rec": "ค่าที่แนะนำ", "rec_to": "Timeout", "rec_gap": "Gap", "rec_cycle": "เวลารอบที่คาด",
        "advice": "ข้อสังเกต", "no_health": "ยังไม่ได้ทดสอบสุขภาพบัส (Devices > Health test)",
        "busmap": "แผนผังบัส (ตามที่ผู้ใช้วาด)", "trunk": "ความยาวสายหลัก", "stubs": "สายแยกรวม",
        "farthest": "อุปกรณ์ที่ไกลสุด", "rbaud": "baud สูงสุดที่แนะนำ", "uload": "Unit load",
        "warnings": "ข้อสังเกตการเดินสาย", "master": "Master", "term": "120 Ω", "no_map": "ยังไม่ได้วาดแผนผังบัส",
        "notes": "หมายเหตุ", "signoff": "ลงนาม", "sig_comm": "ผู้ทดสอบ / ส่งมอบ", "sig_acc": "ผู้รับมอบ",
        "sig_name": "ชื่อ", "sig_sign": "ลายมือชื่อ", "sig_date": "วันที่",
        "disclaimer": "ค่าและตัวนับเป็นค่าที่ mbtool อ่านได้ ณ เวลาทำรายงาน ตัวเลขของแผนผังบัสเป็นค่าประมาณจากข้อมูลสายทั่วไป "
                      "preset ที่ยังไม่ยืนยันต้องเทียบกับหน้าจอของอุปกรณ์",
        "st_ok": "ปกติ", "st_error": "ผิดพลาด", "st_stale": "ไม่ตอบ", "st_off": "หยุดอ่าน", "disabled": "ปิดใช้",
        "independent": "อิสระ (แต่ละตัวตาม scan rate)", "cyclemode": "PLC cycle", "none": "-",
        "kinds": {"timeout": "Timeout", "crc": "CRC / LRC", "exception": "Exception", "frame": "เฟรมเสีย",
                  "wrong_id": "ID ผิด", "link": "เชื่อมต่อไม่ได้", "other": "อื่น ๆ"},
        "since": "นับตั้งแต่", "more": "ค่าเพิ่มเติม (ดูใน log หรือ export ของอุปกรณ์)",
    },
}

CSS = """
:root { --ink:#1f2933; --muted:#616e7c; --line:#d5dae1; --ok:#1e7b34; --bad:#b42318; --warn:#a15c00;
        --head:#f2f4f7; --accent:#1d4ed8; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; color: var(--ink); background: #fff;
       font: 13px/1.45 "Segoe UI", "Leelawadee UI", Tahoma, sans-serif; }
.page { max-width: 1000px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 22px 0 8px; padding-bottom: 4px; border-bottom: 2px solid var(--ink); }
h3 { font-size: 14px; margin: 14px 0 6px; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 4px 18px;
        margin: 10px 0 0; }
.meta div span { color: var(--muted); margin-right: 6px; }
.verdict { margin: 14px 0 6px; padding: 10px 14px; border-radius: 6px; font-weight: 600; font-size: 14px; }
.verdict.ok { background: #e7f5ec; color: var(--ok); border: 1px solid #b5dfc2; }
.verdict.check { background: #fdecea; color: var(--bad); border: 1px solid #f3b8b1; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { border: 1px solid var(--line); border-radius: 6px; padding: 6px 12px; }
.chip b { font-size: 16px; margin-right: 4px; }
table { width: 100%; border-collapse: collapse; margin: 4px 0 8px; font-size: 12px; }
th, td { border: 1px solid var(--line); padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: var(--head); font-weight: 600; }
td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.bad td { background: #fdf3f2; } tr.warn td { background: #fff8e8; }
.s-ok { color: var(--ok); font-weight: 600; } .s-error { color: var(--bad); font-weight: 600; }
.s-stale { color: var(--warn); font-weight: 600; } .s-off { color: var(--muted); }
.small { color: var(--muted); font-size: 11px; }
ul { margin: 4px 0 8px 18px; padding: 0; }
.vals { columns: 2; column-gap: 24px; }
.vals table { break-inside: avoid; }
.sig { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 10px; }
.sig .box { border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; }
.sig .box div { border-bottom: 1px solid var(--line); height: 30px; margin-top: 6px; color: var(--muted);
                font-size: 11px; display: flex; align-items: flex-end; }
.notes { white-space: pre-wrap; border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; }
svg { width: 100%; height: auto; border: 1px solid var(--line); border-radius: 6px; background: #fbfcfd; }
footer { margin-top: 18px; color: var(--muted); font-size: 11px; }
@media print { body { padding: 0; } h2 { break-after: avoid; } table, .sig, svg { break-inside: avoid; }
               @page { size: A4; margin: 14mm 12mm; } }
@media (max-width: 640px) { body { padding: 12px; } .vals { columns: 1; } .sig { grid-template-columns: 1fr; } }
"""


def e(v):
    return html.escape("" if v is None else str(v))


def _num(v, d=1):
    return "-" if v is None else f"{v:.{d}f}"


def _kinds(kinds, t):
    return ", ".join(f"{t['kinds'].get(k, k)} {n}" for k, n in (kinds or {}).items() if n) or "-"


def device_problem(d):
    """True when a device needs attention in the report (enabled and not OK, or error rate >= 1 %)."""
    if not d.get("enabled", True):
        return False
    q = d.get("quality") or {}
    pct = q.get("error_pct")
    return d.get("state") != "ok" or (pct is not None and pct >= 1.0)


def verdict(data):
    devs = [d for d in data.get("devices", []) if d.get("enabled", True)]
    return bool(devs) and not any(device_problem(d) for d in devs)


def bus_svg(m, an, t):
    """Simple schematic of the drawn bus: master, trunk with segment lengths, devices, terminators."""
    nodes = m.get("nodes") or []
    n = len(nodes)
    if not n:
        return ""
    W, step = max(760, 140 + 130 * n), 130
    H = 170
    y = 60
    out = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="{e(t["busmap"])}" font-family="Segoe UI, Tahoma, sans-serif" font-size="12">']
    out.append(f'<rect x="10" y="{y - 22}" width="90" height="44" rx="6" fill="#e8eefc" stroke="#1d4ed8"/>'
               f'<text x="55" y="{y + 4}" text-anchor="middle" font-weight="600">{e(t["master"])}</text>')
    x_end = 100 + step * n - 40
    out.append(f'<line x1="100" y1="{y}" x2="{x_end}" y2="{y}" stroke="#1f2933" stroke-width="3"/>')
    if m.get("terminate_master_end"):
        out.append(f'<text x="104" y="{y - 8}" font-size="10" fill="#a15c00">{e(t["term"])}</text>')
    if m.get("terminate_far_end"):
        out.append(f'<text x="{x_end - 4}" y="{y - 8}" text-anchor="end" font-size="10" fill="#a15c00">'
                   f'{e(t["term"])}</text>')
    dist = 0.0
    prev = 100
    for i, nd in enumerate(nodes):
        x = 100 + step * i + 70
        dist += float(nd.get("segment_m", 0) or 0)
        out.append(f'<text x="{(prev + x) / 2:.0f}" y="{y - 8}" text-anchor="middle" fill="#616e7c">'
                   f'{e(format(float(nd.get("segment_m", 0) or 0), "g"))} m</text>')
        prev = x
        out.append(f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y + 40}" stroke="#1f2933" stroke-width="2"/>'
                   f'<circle cx="{x}" cy="{y}" r="4" fill="#fff" stroke="#1f2933"/>')
        out.append(f'<rect x="{x - 55}" y="{y + 40}" width="110" height="52" rx="6" fill="#fff" stroke="#9aa5b1"/>')
        name = str(nd.get("name") or "device")
        if len(name) > 16:
            name = name[:15] + "…"
        out.append(f'<text x="{x}" y="{y + 58}" text-anchor="middle" font-weight="600">{e(name)}</text>')
        unit = "-" if nd.get("unit") is None else nd["unit"]
        out.append(f'<text x="{x}" y="{y + 75}" text-anchor="middle" fill="#616e7c">ID {e(unit)} · '
                   f'{e(format(dist + float(nd.get("stub_m", 0) or 0), "g"))} m</text>')
        if nd.get("terminated"):
            out.append(f'<text x="{x + 6}" y="{y + 30}" font-size="10" fill="#a15c00">{e(t["term"])}</text>')
    out.append("</svg>")
    return "".join(out)


def render(data, lang="en"):
    t = T.get(lang, T["en"])
    gen = data.get("generated") or time.time()
    devs = data.get("devices", [])
    buses = data.get("buses", [])
    ok_n = sum(1 for d in devs if d.get("enabled", True) and not device_problem(d))
    prob_n = sum(1 for d in devs if device_problem(d))
    h = [f'<!doctype html><html lang="{e(lang)}"><head><meta charset="utf-8">'
         '<meta name="viewport" content="width=device-width, initial-scale=1">'
         f'<title>{e(t["title"])}</title><style>{CSS}</style></head><body><div class="page">']
    h.append(f'<h1>{e(t["title"])}</h1>')
    meta = [(t["project"], data.get("project")), (t["site"], data.get("site")), (t["customer"], data.get("customer")),
            (t["engineer"], data.get("engineer")), (t["date"], time.strftime("%Y-%m-%d %H:%M", time.localtime(gen))),
            (t["tool"], f"mbtool {data.get('version', '')}")]
    h.append('<div class="meta">' + "".join(f'<div><span>{e(k)}</span>{e(v or "-")}</div>' for k, v in meta) + "</div>")
    good = verdict(data)
    h.append(f'<div class="verdict {"ok" if good else "check"}">{e(t["verdict_ok"] if good else t["verdict_check"])}</div>')
    h.append('<div class="chips">' + "".join(f'<div class="chip"><b>{n}</b>{e(lab)}</div>' for n, lab in (
        (len(devs), t["devices_n"]), (len(buses), t["buses_n"]), (ok_n, t["ok_n"]), (prob_n, t["problem_n"]))) + "</div>")

    st = data.get("settings") or {}
    h.append(f'<h2>{e(t["settings"])}</h2><table><tr><th>{e(t["scan_mode"])}</th><th>{e(t["gap"])}</th>'
             f'<th>{e(t["target"])}</th></tr><tr><td>'
             f'{e(t["cyclemode"] if st.get("scan_mode") == "cycle" else t["independent"])}</td>'
             f'<td class="n">{e(st.get("gap_ms", "-"))} ms</td>'
             f'<td class="n">{e(st.get("target_ms") or "-")}{" ms" if st.get("target_ms") else ""}</td></tr></table>')

    if buses:
        h.append(f'<h2>{e(t["buses"])}</h2><table><tr><th>{e(t["bus"])}</th><th>{e(t["conn"])}</th>'
                 f'<th>{e(t["devs"])}</th><th>{e(t["cycle"])}</th><th>{e(t["jitter"])}</th>'
                 f'<th>{e(t["estimate"])}</th><th>{e(t["overruns"])}</th><th>{e(t["busy"])}</th></tr>')
        for b in buses:
            c = b.get("cycle") or {}
            cyc = "-" if not c.get("cycles") else f"{_num(c.get('avg'))} / {_num(c.get('min'))} / {_num(c.get('max'))}"
            busy = "-" if c.get("busy_pct") is None else f"{c['busy_pct']:.0f} %"
            h.append(f'<tr><td>{e(b.get("label"))}</td><td>{e(b.get("conn"))}</td>'
                     f'<td>{e(", ".join(b.get("devices", [])))}</td><td class="n">{e(cyc)}</td>'
                     f'<td class="n">{e(_num(c.get("jitter")))}</td><td class="n">{e(_num(b.get("estimate_ms")))}</td>'
                     f'<td class="n">{e(c.get("overruns", "-"))}</td><td class="n">{e(busy)}</td></tr>')
        h.append("</table>")

    h.append(f'<h2>{e(t["devices"])}</h2><table><tr><th>{e(t["name"])}</th><th>{e(t["id"])}</th>'
             f'<th>{e(t["conn"])}</th><th>{e(t["reads"])}</th><th>{e(t["state"])}</th><th>{e(t["okc"])}</th>'
             f'<th>{e(t["err"])}</th><th>{e(t["errpct"])}</th><th>{e(t["types"])}</th><th>{e(t["resp"])}</th>'
             f'<th>{e(t["last"])}</th></tr>')
    for d in devs:
        q = d.get("quality") or {}
        state = d.get("state", "off")
        word = t["disabled"] if not d.get("enabled", True) else t.get(f"st_{state}", state)
        resp = "-" if q.get("avg") is None else f"{_num(q['avg'])} / {_num(q.get('p95'))} / {_num(q.get('max'))}"
        pct = q.get("error_pct")
        last = time.strftime("%H:%M:%S", time.localtime(d["last_t"])) if d.get("last_t") else "-"
        cls = "bad" if device_problem(d) else ""
        h.append(f'<tr class="{cls}"><td>{e(d.get("name"))}</td><td class="n">{e(d.get("unit"))}</td>'
                 f'<td>{e(d.get("conn"))}</td><td>{e(d.get("what"))}</td><td class="s-{e(state)}">{e(word)}</td>'
                 f'<td class="n">{e(d.get("ok", 0))}</td><td class="n">{e(d.get("errors", 0))}</td>'
                 f'<td class="n">{e("-" if pct is None else f"{pct:.2f}")}</td><td>{e(_kinds(q.get("kinds"), t))}</td>'
                 f'<td class="n">{e(resp)}</td><td class="n">{e(last)}</td></tr>')
        if d.get("last_error") and device_problem(d):
            h.append(f'<tr class="bad"><td colspan="11" class="small">{e(t["lasterr"])}: {e(d["last_error"])}</td></tr>')
    h.append("</table>")
    since = [d["quality"].get("since") for d in devs if (d.get("quality") or {}).get("since")]
    if since:
        h.append(f'<div class="small">{e(t["since"])} {e(time.strftime("%Y-%m-%d %H:%M", time.localtime(min(since))))}</div>')

    h.append(f'<h3>{e(t["values"])}</h3><div class="vals">')
    for d in devs:
        vals = d.get("values") or []
        h.append(f'<table><tr><th colspan="2">{e(d.get("name"))}</th></tr>')
        if not vals:
            h.append(f'<tr><td colspan="2" class="small">{e(t["no_values"])}</td></tr>')
        for k, v in vals:
            h.append(f'<tr><td>{e(k)}</td><td class="n">{e(v)}</td></tr>')
        more = (d.get("values_total") or len(vals)) - len(vals)
        if more > 0:
            h.append(f'<tr><td colspan="2" class="small">+ {more} {e(t["more"])}</td></tr>')
        h.append("</table>")
    h.append("</div>")

    h.append(f'<h2>{e(t["health"])}</h2>')
    rep = data.get("health")
    if not rep:
        h.append(f'<p class="small">{e(t["no_health"])}</p>')
    else:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rep.get("started", gen)))
        link = "TCP" if rep.get("kind") == "tcp" else f"{str(rep.get('kind', '')).upper()} {rep.get('baud')} baud"
        h.append(f'<div class="small">{e(link)} · {e(rep.get("samples"))} × · {e(when)}</div>')
        h.append(f'<table><tr><th>{e(t["name"])}</th><th>{e(t["id"])}</th><th>{e(t["grade"])}</th>'
                 f'<th>{e(t["okc"])}</th><th>{e(t["err"])}</th><th>{e(t["types"])}</th><th>{e(t["resp"])}</th>'
                 f'<th>{e(t["turn"])}</th><th>{e(t["need_to"])}</th></tr>')
        for d in rep.get("devices", []):
            r = d.get("rtt") or {}
            cls = {"good": "", "warn": "warn"}.get(d.get("grade"), "bad")
            h.append(f'<tr class="{cls}"><td>{e(d.get("name"))}</td><td class="n">{e(d.get("unit"))}</td>'
                     f'<td>{e(d.get("grade"))}</td><td class="n">{e(d.get("ok"))}</td>'
                     f'<td class="n">{e(d.get("errors"))}</td><td>{e(_kinds(d.get("kinds"), t))}</td>'
                     f'<td class="n">{e(_num(r.get("avg")))} / {e(_num(r.get("p95")))} / {e(_num(r.get("max")))}</td>'
                     f'<td class="n">{e(_num(d.get("turnaround_ms")))}</td>'
                     f'<td class="n">{e(d.get("timeout_ms") or "-")}</td></tr>')
        h.append("</table>")
        rec = [f'{t["rec_to"]}: {rep.get("recommended_timeout_ms") or "-"} ms']
        if rep.get("kind") != "tcp":
            g = rep.get("recommended_gap_ms")
            rec.append(f'{t["rec_gap"]}: {"-" if g is None else format(g, "g")} ms')
        if rep.get("expected_cycle_ms"):
            rec.append(f'{t["rec_cycle"]}: {rep["expected_cycle_ms"]:.0f} ms')
        h.append(f'<p><b>{e(t["rec"])}:</b> {e(" · ".join(rec))}</p>')
        if rep.get("advice"):
            h.append(f'<h3>{e(t["advice"])}</h3><ul>' + "".join(f"<li>{e(a)}</li>" for a in rep["advice"]) + "</ul>")

    h.append(f'<h2>{e(t["busmap"])}</h2>')
    bm = data.get("busmap")
    if not bm or not (bm.get("map") or {}).get("nodes"):
        h.append(f'<p class="small">{e(t["no_map"])}</p>')
    else:
        an = bm.get("analysis") or {}
        h.append(bus_svg(bm["map"], an, t))
        if an:
            h.append(f'<table><tr><th>{e(t["trunk"])}</th><th>{e(t["stubs"])}</th><th>{e(t["farthest"])}</th>'
                     f'<th>{e(t["rbaud"])}</th><th>{e(t["uload"])}</th></tr><tr>'
                     f'<td class="n">{e(_num(an.get("trunk_m")))} m</td><td class="n">{e(_num(an.get("stub_total_m")))} m</td>'
                     f'<td class="n">{e(_num(an.get("farthest_m")))} m</td><td class="n">{e(an.get("recommended_baud", "-"))}</td>'
                     f'<td class="n">{e(_num(an.get("unit_loads"), 2))} / {e(_num(an.get("unit_load_limit"), 0))}</td></tr></table>')
            warns = an.get("warnings") or []
            if warns:
                h.append(f'<h3>{e(t["warnings"])}</h3><ul>' + "".join(
                    f'<li>{e(w[1] if isinstance(w, (list, tuple)) else w)}</li>' for w in warns) + "</ul>")

    if data.get("notes"):
        h.append(f'<h2>{e(t["notes"])}</h2><div class="notes">{e(data["notes"])}</div>')
    h.append(f'<h2>{e(t["signoff"])}</h2><div class="sig">')
    for who in (t["sig_comm"], t["sig_acc"]):
        h.append(f'<div class="box"><b>{e(who)}</b><div>{e(t["sig_name"])}</div><div>{e(t["sig_sign"])}</div>'
                 f'<div>{e(t["sig_date"])}</div></div>')
    h.append("</div>")
    h.append(f'<footer>{e(t["disclaimer"])}</footer></div></body></html>')
    return "".join(h)


def find_edge():
    """Path of msedge.exe (or Chrome) for headless PDF printing, None when not found."""
    cands = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            cands += [os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(root, "Google", "Chrome", "Application", "chrome.exe")]
    for c in cands:
        if os.path.isfile(c):
            return c
    hits = glob.glob(r"C:\Program Files*\Microsoft\Edge\Application\msedge.exe")
    return hits[0] if hits else None


def to_pdf(html_path, pdf_path, browser=None, timeout=60):
    """Print an HTML file to PDF with a headless Edge / Chrome. Returns pdf_path; raises OSError on failure."""
    exe = browser or find_edge()
    if not exe:
        raise OSError("Microsoft Edge was not found - open the HTML report and use Print > Save as PDF")
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    import tempfile
    prof = tempfile.mkdtemp(prefix="mbtool_pdf_")
    cmd = [exe, "--headless", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
           f"--user-data-dir={prof}", f"--print-to-pdf={os.path.abspath(pdf_path)}", url]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(cmd, timeout=timeout, capture_output=True, creationflags=flags)
        # msedge.exe hands the job to a background process and returns at once: wait for the finished file
        end, last = time.time() + timeout, -1
        while time.time() < end:
            size = os.path.getsize(pdf_path) if os.path.isfile(pdf_path) else -1
            if size > 500 and size == last:
                break
            last = size
            time.sleep(0.4)
    except subprocess.TimeoutExpired:
        raise OSError("PDF printing timed out")
    finally:
        import shutil
        shutil.rmtree(prof, ignore_errors=True)
    if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) < 500:
        raise OSError("PDF was not created - open the HTML report and use Print > Save as PDF")
    return pdf_path


def save(data, path, lang="en", pdf=False):
    """Write the HTML report (and a PDF next to it when pdf=True). Returns (html_path, pdf_path or None)."""
    root, ext = os.path.splitext(path)
    html_path = path if ext.lower() in (".html", ".htm") else root + ".html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render(data, lang))
    pdf_path = None
    if pdf:
        pdf_path = to_pdf(html_path, root + ".pdf")
    return html_path, pdf_path
