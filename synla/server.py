"""The review page. Runs on your own computer only (127.0.0.1), opened in your browser.

Protection against other websites: a web page you have open could try to send requests to this page.
So every request must name this computer as 127.0.0.1 or localhost (stops DNS rebinding), and every change must carry
a secret token that only Synla's own pages know, sent as JSON (other sites cannot send that without asking first).
"""
import csv
import io
import json
import secrets
from datetime import date, datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import recorder, report
from .classify import ASK_BELOW
from .model import KINDS, UNKNOWN

CSS = """
:root{--paper:#f6f6f3;--surface:#fff;--ink:#0a1236;--muted:#585a60;--line:#e6ded6;--sea:#006690;--wave:#97c4c2;
 --sun:#f85e0a;--peach:#f8d2b8;--peach2:#f7e6da;--amber:#f0aa2a}
@media (prefers-color-scheme:dark){:root{--paper:#080f2c;--surface:#111c44;--ink:#f1efe8;--muted:#a7abb8;--line:#222d5a;
 --sea:#5fb3d3;--wave:#006690;--peach:#3a2a3a;--peach2:#111c44}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 "Segoe UI",system-ui,sans-serif}
header{background:linear-gradient(180deg,var(--peach),var(--peach2) 70%,var(--paper));padding:18px 16px 14px}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px}header .wrap{padding:0}
h1{margin:0;font-size:1.5rem}h1 small{font-weight:400;color:var(--muted);font-size:.95rem;margin-left:8px}
nav{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
a{color:var(--sea)}.btn{border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:6px;padding:7px 12px;
 text-decoration:none;cursor:pointer;font:inherit}.btn.primary{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.chip{font-size:.85rem;color:var(--muted);margin-left:auto}.chip b{color:var(--ink)}.chip.off b{color:var(--sun)}
.stats{display:flex;gap:18px;flex-wrap:wrap;align-items:center;margin:14px 0 6px;color:var(--muted)}.stats b{color:var(--ink)}
.blk,.win{display:grid;grid-template-columns:96px minmax(0,1fr) auto;gap:6px 14px;align-items:center;background:var(--surface);
 border:1px solid var(--line);border-left:5px solid var(--wave);border-radius:6px;padding:10px 12px;margin:8px 0}
.blk.ask,.win{border-left-color:var(--sun)}.blk.ok{border-left-color:var(--sea);opacity:.82}
.when{font-variant-numeric:tabular-nums;color:var(--muted);font-size:.9rem}.when b{color:var(--ink);font-size:1rem}
.title{font-weight:600;overflow-wrap:anywhere}.why{font-size:.8rem;color:var(--muted)}
.why span{background:var(--paper);border:1px solid var(--line);border-radius:4px;padding:0 5px;margin:2px 4px 0 0;display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom}
.pick{display:flex;gap:6px;flex-wrap:wrap;align-items:center;justify-content:flex-end}
select{font:inherit;padding:5px;border-radius:5px;border:1px solid var(--line);background:var(--surface);color:var(--ink);max-width:170px}
.conf{font-size:.8rem;color:var(--muted);min-width:46px;text-align:right}.blk.ask .conf{color:var(--sun);font-weight:600}
details{grid-column:1/-1;font-size:.9rem}summary{cursor:pointer;color:var(--sea)}
.part{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 12px;align-items:center;padding:6px 0;border-top:1px solid var(--line)}
.part .title{font-weight:400}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.card{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:12px}.card .n{font-size:1.7rem;font-weight:700}
.card .l{color:var(--muted);font-size:.85rem}.card.hot{border-top:4px solid var(--sun)}
table{border-collapse:collapse;width:100%;background:var(--surface);border:1px solid var(--line)}
td,th{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;font-variant-numeric:tabular-nums;vertical-align:top}th{color:var(--muted);font-weight:600}
td.num,th.num{text-align:right}.bar{height:10px;border-radius:3px;background:var(--sun);display:inline-block;vertical-align:middle}
.bar.p{background:var(--sea)}h2{font-size:1.1rem;margin:22px 0 8px}.note{color:var(--muted);font-size:.85rem}
.box{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:12px 16px;margin:10px 0}
.box ul{margin:6px 0;padding-left:20px}.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
@media (max-width:700px){.blk,.win{grid-template-columns:1fr}.pick{justify-content:flex-start}.part{grid-template-columns:1fr}}
"""

JS = """
const TOKEN=document.querySelector('meta[name=synla-token]').content;
async function post(url, body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Synla-Token':TOKEN},body:JSON.stringify(body)});
 if(!r.ok){alert(await r.text());return false} return true}
document.addEventListener('click', async e=>{
 const b=e.target.closest('[data-save]'); if(b){const row=b.closest('.ans');
  const ok=await post('/api/answer',{date:row.dataset.date,ids:JSON.parse(row.dataset.ids),how:row.dataset.how,
   project:row.querySelector('.p').value,kind:row.querySelector('.k').value});
  if(ok) location.reload(); return}
 const a=e.target.closest('[data-approve]'); if(a){if(await post('/api/approve',{date:a.dataset.approve})) location.reload(); return}
 const n=e.target.closest('[data-newproject]'); if(n){const name=prompt('Project name'); if(name && await post('/api/project',{name})) location.reload(); return}
 const p=e.target.closest('[data-pause]'); if(p){if(await post('/api/pause',{minutes:Number(p.dataset.pause)})) location.reload(); return}
 const d=e.target.closest('[data-delete]'); if(d){if(confirm(d.dataset.confirm) && await post('/api/delete',{what:d.dataset.delete})) location.reload(); return}
});
"""

CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
       "form-action 'none'; frame-ancestors 'none'; base-uri 'none'")


def page(title, body, token):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<meta name='synla-token' content='{escape(token)}'>"
            f"<title>{escape(title)} · Synla</title><style>{CSS}</style></head><body>{body}<script>{JS}</script></body></html>")


def options(values, current, labels=None):
    return "".join(f"<option value='{escape(v)}'{' selected' if v == current else ''}>{escape((labels or {}).get(v, v))}</option>"
                   for v in values)


def picker(app, project, kind, button, primary=False):
    p = project if project in app.projects else ""
    k = kind if kind in KINDS else ""
    return (f"<div class='pick'><select class='p' aria-label='Project'><option value=''>Project…</option>{options(app.projects, p)}</select>"
            f"<select class='k' aria-label='Kind of work'><option value=''>Kind…</option>{options(list(KINDS), k, KINDS)}</select>"
            f"{button}<button class='btn{' primary' if primary else ''}' data-save>Save</button></div>")


def ids_attr(ids) -> str:
    return escape(json.dumps(list(ids)), quote=True)


def status_chip(app) -> str:
    info = app.data_info()
    if info["paused_until"]:
        t = info["paused_until"].astimezone()
        return f"<span class='chip off'>Recording <b>paused until {t:%H:%M}</b> · <a href='#' data-pause='0'>resume</a></span>"
    last = info["recorder_last"]
    if last and datetime.now(timezone.utc) - last < timedelta(minutes=2):
        return "<span class='chip'>Recording: <b>on</b></span>"
    src = app.source_name
    return f"<span class='chip'>Reading from: <b>{escape(src)}</b></span>"


def header(app, title, sub, links, data_link=True):
    mine = "<a class='btn' href='/data'>Your data</a>" if data_link else ""
    return (f"<header><div class='wrap'><h1>{title}<small>{sub}</small></h1><nav>{links}"
            f"{mine}{status_chip(app)}</nav></div></header>")


def day_page(app, d: date, token: str) -> str:
    bl = app.day(d)
    ask = [b for b in bl if not b.confirmed and (b.guess.confidence < ASK_BELOW or b.guess.project == UNKNOWN)]
    sure = [b for b in bl if not b.confirmed and b.guess.confidence >= ASK_BELOW and b.guess.project != UNKNOWN]
    total = sum(b.minutes for b in bl) / 60

    windows = app.unsorted_windows(bl)
    win_rows = "".join(
        f"<div class='win ans' data-date='{d}' data-how='window' data-ids='{ids_attr(w['ids'])}'>"
        f"<div class='when'><b>{w['unsure']:.0f} min</b><br>unsorted</div>"
        f"<div><div class='title'>{escape(w['title'] or w['app'])}</div>"
        f"<div class='why'>{escape(w['app'])} · in {len(w['blocks'])} block{'s' if len(w['blocks']) != 1 else ''} today</div></div>"
        f"{picker(app, '', '', '')}</div>" for w in windows)

    rows = []
    for b in bl:
        g = b.guess
        cls = "ok" if b.confirmed else ("ask" if b in ask else "")
        why = ("Why: " + "".join(f"<span>{escape(r)}</span>" for r in g.reasons)) if g.reasons else "<span>no clues yet</span>"
        by_window = {}
        for s in b.segments:
            w = by_window.setdefault(s.key, {"seg": s, "minutes": 0.0, "ids": []})
            w["minutes"] += s.minutes
            w["ids"].append(s.id)
        split = ""
        if len(by_window) > 1:
            parts = "".join(
                f"<div class='part ans' data-date='{d}' data-how='split' data-ids='{ids_attr(w['ids'])}'>"
                f"<div><span class='title'>{escape(w['seg'].title or w['seg'].app)}</span> "
                f"<span class='note'>{w['minutes']:.0f} min</span></div>{picker(app, g.project, g.kind, '')}</div>"
                for w in sorted(by_window.values(), key=lambda w: -w["minutes"]))
            split = f"<details><summary>Split this block ({len(by_window)} windows)</summary>{parts}</details>"
        conf = f"<span class='conf'>{'yours' if b.confirmed else f'{int(g.confidence * 100)}%'}</span>"
        rows.append(
            f"<div class='blk ans {cls}' data-date='{d}' data-how='block' data-ids='{ids_attr(s.id for s in b.segments)}'>"
            f"<div class='when'><b>{b.start:%H:%M}</b>-{b.end:%H:%M}<br>{b.minutes:.0f} min</div>"
            f"<div><div class='title'>{escape(b.main.title or b.main.app)}</div>"
            f"<div class='why'>{escape(b.main.app)}{' · in a call' if any(s.call for s in b.segments) else ''}"
            f"{f' · +{len(by_window) - 1} other windows' if len(by_window) > 1 else ''} · {why}</div></div>"
            f"{picker(app, g.project, g.kind, conf, primary=(cls == 'ask'))}{split}</div>")

    approve = f"<button class='btn primary' data-approve='{d}'>Approve the {len(sure)} sure ones</button>" if sure else ""
    prev, nxt = d - timedelta(days=1), d + timedelta(days=1)
    links = (f"<a class='btn' href='/day?date={prev}'>← {prev:%a}</a><a class='btn' href='/day?date={nxt}'>{nxt:%a} →</a>"
             f"<a class='btn' href='/week?date={d}'>Week summary</a><button class='btn' data-newproject>+ Project</button>")
    body = (header(app, "Synla", f"{d:%A %d %B %Y}", links) +
            f"<main class='wrap'><div class='stats'><span><b>{total:.1f} h</b> active</span><span><b>{len(bl)}</b> blocks</span>"
            f"<span><b>{len(ask)}</b> need your answer</span><span><b>{len(sure)}</b> Synla is sure about</span>"
            f"{approve}</div>"
            + (f"<h2>Biggest unsorted windows</h2><p class='note'>One answer covers every stretch of that window today.</p>{win_rows}"
               f"<h2>All blocks</h2>" if win_rows else "")
            + ("".join(rows) or "<p class='note'>Nothing recorded this day.</p>")
            + "<p class='note'>Orange: Synla is unsure and needs you. Every answer teaches it. Everything stays on this computer.</p></main>")
    return page(f"{d}", body, token)


def week_page(app, d: date, token: str) -> str:
    days = app.week(d)
    w = app.config.get("workday", {})
    s = report.summary(days, (w.get("start", "07:00"), w.get("end", "17:00")))
    ts = report.timesheet(days, app.config.get("roundMinutes", 15))
    acc = app.accuracy()
    monday = d - timedelta(days=d.weekday())
    inv_rows = "".join(f"<tr><td>{escape(KINDS.get(r['kind'], r['kind']))}</td><td>{escape(r['what'])}</td>"
                       f"<td class='num'>{r['hours']:.1f} h</td></tr>" for r in s["top_invisible"])
    mx = max([v["hours"] for v in s["per_day"].values()] or [1])
    day_rows = "".join(
        f"<tr><td><a href='/day?date={day}'>{day:%a %d.%m}</a></td><td class='num'>{v['hours']:.1f} h</td>"
        f"<td><span class='bar p' style='width:{v['hours'] / mx * 180 * (1 - v['invisible_share']):.0f}px'></span>"
        f"<span class='bar' style='width:{v['hours'] / mx * 180 * v['invisible_share']:.0f}px'></span></td>"
        f"<td class='num'>{v['invisible_share']:.0%}</td></tr>" for day, v in s["per_day"].items())
    projects = sorted({p for v in ts.values() for p in v})
    ts_head = "".join(f"<th class='num'>{day:%a}</th>" for day in ts)
    ts_rows = "".join(f"<tr><td>{escape(p)}</td>" + "".join(f"<td class='num'>{ts[day].get(p, 0) or '':}</td>" for day in ts)
                      + f"<td class='num'><b>{sum(ts[day].get(p, 0) for day in ts):g}</b></td></tr>" for p in projects)
    if acc.get("answers"):
        acc_txt = (f"On the {acc['answers']} blocks you checked yourself, Synla's guess was right {acc['right_share']:.0%} "
                   f"of the time (last 50: {acc['recent_right_share']:.0%}).")
    else:
        acc_txt = "No answers yet. Synla's guesses get better with every block you answer."
    if acc.get("bulk"):
        acc_txt += (f" {acc['bulk']} blocks were approved in bulk without checking; "
                    f"{acc['bulk_changed']} of them were changed later.")
    links = (f"<a class='btn' href='/week?date={monday - timedelta(days=7)}'>← Previous week</a>"
             f"<a class='btn' href='/week?date={monday + timedelta(days=7)}'>Next week →</a><a class='btn' href='/day?date={monday}'>Review days</a>"
             f"<a class='btn' href='/export.csv?date={monday}'>Timesheet CSV</a><a class='btn' href='/summary.txt?date={monday}'>Summary as text</a>")
    body = header(app, "Synla", f"Week of {monday:%d %B %Y}", links) + f"""<main class="wrap"><div class="cards">
<div class="card"><div class="n">{s['hours']:.1f} h</div><div class="l">active time</div></div>
<div class="card hot"><div class="n">{s['invisible_share']:.0%}</div><div class="l">unplanned work ({s['invisible_hours']:.1f} h): helping, firefighting, chasing, fixing data</div></div>
<div class="card"><div class="n">{s['focus_blocks']}</div><div class="l">focus blocks of 45+ min ({s['focus_hours']:.1f} h)</div></div>
<div class="card"><div class="n">{s['switches_per_hour']:.0f}</div><div class="l">window switches per hour</div></div>
<div class="card"><div class="n">{s['after_hours']:.1f} h</div><div class="l">outside working hours</div></div>
<div class="card"><div class="n">{s['unsure_blocks']}</div><div class="l">blocks still waiting for your answer</div></div></div>
<h2>Where the unplanned work came from</h2><table><tr><th>Kind</th><th>What</th><th class="num">Time</th></tr>{inv_rows or '<tr><td colspan=3>None this week.</td></tr>'}</table>
<h2>Day by day</h2><table><tr><th>Day</th><th class="num">Active</th><th>Planned <span class='bar p' style='width:12px'></span> unplanned <span class='bar' style='width:12px'></span></th><th class="num">Unplanned</th></tr>{day_rows}</table>
<h2>Timesheet</h2><table><tr><th>Project</th>{ts_head}<th class="num">Total</th></tr>{ts_rows}</table>
<p class="note">Rounded to {app.config.get('roundMinutes', 15)} minutes per project and day. Unanswered blocks use Synla's guess. {acc_txt}</p></main>"""
    return page(f"Week {monday}", body, token)


def _when(t):
    return t.astimezone().strftime("%d %b %Y %H:%M") if t else "nothing yet"


def data_page(app, token: str) -> str:
    i = app.data_info()
    p = i["privacy"]
    recording = ("paused until " + i["paused_until"].astimezone().strftime("%H:%M")) if i["paused_until"] else \
        ("on" if i["recorder_last"] and datetime.now(timezone.utc) - i["recorder_last"] < timedelta(minutes=2) else "not running")
    aw_note = ("<p class='note'>ActivityWatch keeps its own history, separate from Synla. Synla cannot delete it; "
               "you can, in ActivityWatch.</p>") if i["source"] in ("auto", "activitywatch") else ""
    body = header(app, "Your data", "what Synla keeps, and your controls", "<a class='btn' href='/'>Back to today</a>", False) + f"""
<main class="wrap">
<div class="box"><b>Where:</b> {escape(i['folder'])}<br>
<span class="note">Only on this computer. Nothing is sent anywhere, and there is no account, server or admin.
{'Window names are encrypted with your Windows login.' if i['encrypted'] else 'The folder can only be opened by your account.'}</span></div>

<div class="box"><b>Recording:</b> {recording}
<div class="row"><button class="btn" data-pause="60">Pause 1 hour</button><button class="btn" data-pause="480">Pause 8 hours</button>
<button class="btn" data-pause="0">Resume</button></div>
<p class="note">Microphone check for calls: {'on' if i['detectCalls'] else 'off'}. Synla only asks Windows whether an app is
using the microphone, never what is said.</p></div>

<h2>What is stored</h2>
<table><tr><th>What</th><th class="num">Amount</th><th>Oldest</th><th>Kept for</th></tr>
<tr><td>Window names and times from the recorder</td><td class="num">{i['recorder_rows']:,} stretches</td><td>{_when(i['recorder_first'])}</td><td>{i['keepWindowDays']} days</td></tr>
<tr><td>Your answers: project and kind for each stretch of time</td><td class="num">{i['answers']:,}</td><td>{_when(i['answers_first'])}</td><td>{i['keepDays']} days</td></tr>
<tr><td>Answer log, used to measure how often Synla is right</td><td class="num">{i['log_entries']:,}</td><td></td><td>{i['keepDays']} days</td></tr>
<tr><td>Learned clues, stored as one-way codes, not readable names</td><td class="num">{i['clues']:,}</td><td></td><td>until you delete them</td></tr></table>
<p class="note">After {i['keepWindowDays']} days the window names are gone. Your timesheet for those days is kept, built from your answers alone.</p>
{aw_note}

<h2>Never recorded</h2>
<div class="box"><ul><li>Keystrokes, screenshots, the clipboard, file contents or sound</li>
<li>Web addresses (only the window title)</li>
<li>Private windows: apps {escape(', '.join(p.get('skipApps', [])))}; titles with {escape(', '.join(p.get('skipTitleWords', [])))}.
Change the list in config.json.</li></ul></div>

<h2>Delete</h2>
<div class="box"><div class="row">
<button class="btn" data-delete="windows" data-confirm="Delete all window names now? Your answers and timesheets stay.">Delete all window names</button>
<button class="btn" data-delete="all" data-confirm="Delete everything Synla has stored: window names, answers, log and learned clues? This cannot be undone.">Delete everything</button></div>
<p class="note">Deleting is final. Your settings (projects and keywords) stay.</p></div>

<h2>Built for you, not for monitoring</h2>
<div class="box"><ul><li>No account, server or admin mode, and no way to collect or compare data from several people.</li>
<li>Nothing leaves this computer unless you copy it out yourself, for example the weekly summary.</li>
<li>Every number comes from your own recording and your own answers, and you can see and delete all of it.</li></ul></div>
</main>"""
    return page("Your data", body, token)


def csv_text(app, d: date) -> str:
    ts = report.timesheet(app.week(d), app.config.get("roundMinutes", 15))
    f = io.StringIO()
    wr = csv.writer(f)
    wr.writerow(["date", "project", "hours"])
    for day, rows in ts.items():
        for p, h in rows.items():
            wr.writerow([day.isoformat(), p, f"{h:g}"])
    return f.getvalue()


def make_handler(app, token: str, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def _date(self, q):
            v = q.get("date", [None])[0]
            return date.fromisoformat(v) if v else date.today()

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "Synla only answers on 127.0.0.1 or localhost.", "text/plain")
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    return self._send(302, "", extra={"Location": f"/day?date={date.today()}"})
                if u.path == "/day":
                    return self._send(200, day_page(app, self._date(q), token))
                if u.path == "/week":
                    return self._send(200, week_page(app, self._date(q), token))
                if u.path == "/data":
                    return self._send(200, data_page(app, token))
                if u.path == "/export.csv":
                    d = self._date(q)
                    return self._send(200, csv_text(app, d), "text/csv; charset=utf-8",
                                      {"Content-Disposition": f"attachment; filename=timesheet-{d}.csv"})
                if u.path == "/summary.txt":
                    return self._send(200, report.text(app.summary(self._date(q))), "text/plain; charset=utf-8")
                return self._send(404, "Not found", "text/plain")
            except Exception as e:     # show the problem instead of a blank page
                return self._send(500, f"Synla could not read your activity: {e}", "text/plain; charset=utf-8")

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, "Synla only answers on 127.0.0.1 or localhost.", "text/plain")
            if not secrets.compare_digest(self.headers.get("X-Synla-Token", ""), token) or \
                    not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(403, "This request did not come from Synla's own page.", "text/plain")
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/answer":
                    if not body.get("project") or body.get("kind") not in KINDS:
                        return self._send(400, "Pick both a project and a kind of work.", "text/plain")
                    how = body.get("how") if body.get("how") in ("block", "window", "split") else "block"
                    app.answer(date.fromisoformat(body["date"]), body["ids"], body["project"], body["kind"], how)
                elif self.path == "/api/approve":
                    app.approve_confident(date.fromisoformat(body["date"]))
                elif self.path == "/api/project":
                    app.add_project(body["name"].strip())
                elif self.path == "/api/pause":
                    app.store.pause(float(body.get("minutes") or 0) or None)
                elif self.path == "/api/delete":
                    if body.get("what") == "windows":
                        recorder.prune(app.store, 0)
                    elif body.get("what") == "all":
                        app.store.delete_everything()
                        app.reload()
                    else:
                        return self._send(400, "Unknown delete request.", "text/plain")
                else:
                    return self._send(404, "Not found", "text/plain")
                return self._send(200, "ok", "text/plain")
            except Exception as e:
                return self._send(500, str(e), "text/plain")
    return H


def serve(app, port=5700, open_browser=True, start=None):
    token = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, token, port))
    url = f"http://127.0.0.1:{port}/" + (f"day?date={start}" if start else "")
    print(f"Synla is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
