"""The review page. Runs on your own computer only (127.0.0.1), opened in your browser."""
import csv
import io
import json
from datetime import date, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import report
from .classify import ASK_BELOW
from .model import INVISIBLE, KINDS, UNKNOWN

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
.stats{display:flex;gap:18px;flex-wrap:wrap;margin:14px 0 6px;color:var(--muted)}.stats b{color:var(--ink)}
.blk{display:grid;grid-template-columns:96px minmax(0,1fr) auto;gap:6px 14px;align-items:center;background:var(--surface);
 border:1px solid var(--line);border-left:5px solid var(--wave);border-radius:6px;padding:10px 12px;margin:8px 0}
.blk.ask{border-left-color:var(--sun)}.blk.ok{border-left-color:var(--sea);opacity:.82}
.when{font-variant-numeric:tabular-nums;color:var(--muted);font-size:.9rem}.when b{color:var(--ink);font-size:1rem}
.title{font-weight:600;overflow-wrap:anywhere}.why{font-size:.8rem;color:var(--muted)}
.why span{background:var(--paper);border:1px solid var(--line);border-radius:4px;padding:0 5px;margin:2px 4px 0 0;display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom}
.pick{display:flex;gap:6px;flex-wrap:wrap;align-items:center;justify-content:flex-end}
select{font:inherit;padding:5px;border-radius:5px;border:1px solid var(--line);background:var(--surface);color:var(--ink);max-width:170px}
.conf{font-size:.8rem;color:var(--muted);min-width:46px;text-align:right}.blk.ask .conf{color:var(--sun);font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.card{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:12px}.card .n{font-size:1.7rem;font-weight:700}
.card .l{color:var(--muted);font-size:.85rem}.card.hot{border-top:4px solid var(--sun)}
table{border-collapse:collapse;width:100%;background:var(--surface);border:1px solid var(--line)}
td,th{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;font-variant-numeric:tabular-nums}th{color:var(--muted);font-weight:600}
td.num,th.num{text-align:right}.bar{height:10px;border-radius:3px;background:var(--sun);display:inline-block;vertical-align:middle}
.bar.p{background:var(--sea)}h2{font-size:1.1rem;margin:22px 0 8px}.note{color:var(--muted);font-size:.85rem}
@media (max-width:700px){.blk{grid-template-columns:1fr}.pick{justify-content:flex-start}}
"""

JS = """
async function post(url, body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 if(!r.ok){alert(await r.text());return false} return true}
document.addEventListener('click', async e=>{
 const b=e.target.closest('[data-save]'); if(b){const row=b.closest('.blk');
  const ok=await post('/api/answer',{date:row.dataset.date,id:row.dataset.id,project:row.querySelector('.p').value,kind:row.querySelector('.k').value});
  if(ok) location.reload()}
 const a=e.target.closest('[data-approve]'); if(a){if(await post('/api/approve',{date:a.dataset.approve})) location.reload()}
 const n=e.target.closest('[data-newproject]'); if(n){const name=prompt('Project name'); if(name && await post('/api/project',{name})) location.reload()}
});
"""


def page(title, body):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)} · Synla</title><style>{CSS}</style></head><body>{body}<script>{JS}</script></body></html>")


def options(values, current, labels=None):
    return "".join(f"<option value='{escape(v)}'{' selected' if v == current else ''}>{escape((labels or {}).get(v, v))}</option>"
                   for v in values)


def day_page(app, d: date) -> str:
    bl = app.day(d)
    ask = [b for b in bl if not b.confirmed and b.guess.confidence < ASK_BELOW]
    sure = [b for b in bl if not b.confirmed and b.guess.confidence >= ASK_BELOW and b.guess.project != UNKNOWN]
    total = sum(b.minutes for b in bl) / 60
    projects = app.projects
    kinds = list(KINDS)
    rows = []
    for b in bl:
        g = b.guess
        cls = "ok" if b.confirmed else ("ask" if g.confidence < ASK_BELOW else "")
        p = g.project if g.project in projects else ""
        k = g.kind if g.kind in KINDS else ""
        why = ("Why: " + "".join(f"<span>{escape(r)}</span>" for r in g.reasons)) if g.reasons else "<span>no clues yet</span>"
        others = len({s.key for s in b.segments}) - 1
        rows.append(f"""<div class="blk {cls}" data-date="{d}" data-id="{b.id}">
 <div class="when"><b>{b.start:%H:%M}</b>-{b.end:%H:%M}<br>{b.minutes:.0f} min</div>
 <div><div class="title">{escape(b.main.title or b.main.app)}</div>
  <div class="why">{escape(b.main.app)}{f' · +{others} other windows' if others > 0 else ''} · {why}</div></div>
 <div class="pick"><select class="p" aria-label="Project"><option value="">Project…</option>{options(projects, p)}</select>
  <select class="k" aria-label="Kind of work"><option value="">Kind…</option>{options(kinds, k, KINDS)}</select>
  <span class="conf">{'yours' if b.confirmed else f'{g.confidence:.0%}'}</span>
  <button class="btn{' primary' if cls == 'ask' else ''}" data-save>{'Change' if b.confirmed else 'Save'}</button></div></div>""")
    prev, nxt = d - timedelta(days=1), d + timedelta(days=1)
    body = f"""<header><div class="wrap"><h1>Synla<small>{d:%A %d %B %Y}</small></h1>
<nav><a class="btn" href="/day?date={prev}">← {prev:%a}</a><a class="btn" href="/day?date={nxt}">{nxt:%a} →</a>
<a class="btn" href="/week?date={d}">Week summary</a><button class="btn" data-newproject>+ Project</button></nav></div></header>
<main class="wrap"><div class="stats"><span><b>{total:.1f} h</b> active</span><span><b>{len(bl)}</b> blocks</span>
<span><b>{len(ask)}</b> need your answer</span><span><b>{len(sure)}</b> Synla is sure about</span>
{f'<button class="btn primary" data-approve="{d}">Approve the {len(sure)} sure ones</button>' if sure else ''}</div>
{''.join(rows) or '<p class="note">Nothing recorded this day.</p>'}
<p class="note">Orange: Synla is unsure and needs you. Every answer teaches it. Everything stays on this computer.</p></main>"""
    return page(f"{d}", body)


def week_page(app, d: date) -> str:
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
    acc_txt = (f"Synla was right {acc['right_share']:.0%} of the time before you answered "
               f"({acc['answers']} answers; last 50: {acc['recent_right_share']:.0%})." if acc.get("answers") else
               "No answers yet. Synla's guesses get better with every block you answer.")
    body = f"""<header><div class="wrap"><h1>Synla<small>Week of {monday:%d %B %Y}</small></h1>
<nav><a class="btn" href="/week?date={monday - timedelta(days=7)}">← Previous week</a>
<a class="btn" href="/week?date={monday + timedelta(days=7)}">Next week →</a><a class="btn" href="/day?date={monday}">Review days</a>
<a class="btn" href="/export.csv?date={monday}">Timesheet CSV</a><a class="btn" href="/summary.txt?date={monday}">Summary as text</a></nav></div></header>
<main class="wrap"><div class="cards">
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
    return page(f"Week {monday}", body)


def csv_text(app, d: date) -> str:
    ts = report.timesheet(app.week(d), app.config.get("roundMinutes", 15))
    f = io.StringIO()
    wr = csv.writer(f)
    wr.writerow(["date", "project", "hours"])
    for day, rows in ts.items():
        for p, h in rows.items():
            wr.writerow([day.isoformat(), p, f"{h:g}"])
    return f.getvalue()


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _date(self, q):
            v = q.get("date", [None])[0]
            return date.fromisoformat(v) if v else date.today()

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    return self._send(302, "", extra={"Location": f"/day?date={date.today()}"})
                if u.path == "/day":
                    return self._send(200, day_page(app, self._date(q)))
                if u.path == "/week":
                    return self._send(200, week_page(app, self._date(q)))
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
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            try:
                if self.path == "/api/answer":
                    if not body.get("project") or body.get("kind") not in KINDS:
                        return self._send(400, "Pick both a project and a kind of work.", "text/plain")
                    app.answer(date.fromisoformat(body["date"]), body["id"], body["project"], body["kind"])
                elif self.path == "/api/approve":
                    app.approve_confident(date.fromisoformat(body["date"]))
                elif self.path == "/api/project":
                    app.add_project(body["name"].strip())
                else:
                    return self._send(404, "Not found", "text/plain")
                return self._send(200, "ok", "text/plain")
            except Exception as e:
                return self._send(500, str(e), "text/plain")
    return H


def serve(app, port=5700, open_browser=True, start=None):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/" + (f"day?date={start}" if start else "")
    print(f"Synla is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
