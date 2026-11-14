"""The review page. Runs on your own computer only (127.0.0.1), opened in your browser.

Protection against other websites: a web page you have open could try to send requests to this page.
So every request must name this computer as 127.0.0.1 or localhost (stops DNS rebinding), and every change must carry
a secret token that only Synla's own pages know, sent as JSON (other sites cannot send that without asking first).
"""
import csv
import io
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import record, recorder, report, workload
from .classify import ASK_BELOW
from .model import KINDS, MANUAL_APP, UNKNOWN

CSS = """
:root{--paper:#f6f6f3;--surface:#fff;--ink:#0a1236;--muted:#585a60;--line:#e6ded6;--sea:#006690;--wave:#97c4c2;
 --sun:#f85e0a;--peach:#f8d2b8;--peach2:#f7e6da;--amber:#f0aa2a;--green:#2f9e44;--red:#d9381e}
@media (prefers-color-scheme:dark){:root{--paper:#080f2c;--surface:#111c44;--ink:#f1efe8;--muted:#a7abb8;--line:#222d5a;
 --sea:#5fb3d3;--wave:#006690;--peach:#3a2a3a;--peach2:#111c44}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 "Segoe UI",system-ui,sans-serif}
header{background:linear-gradient(180deg,var(--peach),var(--peach2) 70%,var(--paper));padding:18px 16px 14px}
.wrap{max-width:1040px;margin:0 auto;padding:0 16px}header .wrap{padding:0}
h1{margin:0;font-size:1.5rem}h1 small{font-weight:400;color:var(--muted);font-size:.95rem;margin-left:8px}
nav{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
a{color:var(--sea)}.btn{border:1px solid var(--line);background:var(--surface);color:var(--ink);border-radius:6px;padding:7px 12px;
 text-decoration:none;cursor:pointer;font:inherit;display:inline-block}.btn.primary{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.chip{font-size:.85rem;color:var(--muted);margin-left:auto}.chip b{color:var(--ink)}.chip.off b{color:var(--sun)}
.stats{display:flex;gap:18px;flex-wrap:wrap;align-items:center;margin:14px 0 6px;color:var(--muted)}.stats b{color:var(--ink)}
.blk,.win{display:grid;grid-template-columns:96px minmax(0,1fr) auto;gap:6px 14px;align-items:center;background:var(--surface);
 border:1px solid var(--line);border-left:5px solid var(--wave);border-radius:6px;padding:10px 12px;margin:8px 0}
.blk.ask,.win{border-left-color:var(--sun)}.blk.ok{border-left-color:var(--sea);opacity:.82}.blk.added{border-left-color:var(--amber)}
.when{font-variant-numeric:tabular-nums;color:var(--muted);font-size:.9rem}.when b{color:var(--ink);font-size:1rem}
.title{font-weight:600;overflow-wrap:anywhere}.why{font-size:.8rem;color:var(--muted)}
.why span{background:var(--paper);border:1px solid var(--line);border-radius:4px;padding:0 5px;margin:2px 4px 0 0;display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:bottom}
.pick{display:flex;gap:6px;flex-wrap:wrap;align-items:center;justify-content:flex-end}
select,input{font:inherit;padding:5px;border-radius:5px;border:1px solid var(--line);background:var(--surface);color:var(--ink);max-width:190px}
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
.box ul{margin:6px 0;padding-left:20px}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot.green{background:var(--green)}.dot.yellow{background:var(--amber)}.dot.red{background:var(--red)}
.lvl{font-weight:700;padding:2px 12px;border-radius:12px;color:#fff}.lvl.green{background:var(--green)}
.lvl.yellow{background:var(--amber);color:#2a1a00}.lvl.red{background:var(--red)}
.trend{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}a.tw{text-decoration:none;color:var(--ink)}.tw{background:var(--surface);border:1px solid var(--line);border-radius:6px;
 padding:6px 8px;text-align:center;font-size:.8rem;min-width:74px}.tw b{display:block;font-size:.95rem}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:.85rem;overflow-wrap:anywhere}
.ok-msg{color:var(--green);font-weight:600}.bad-msg{color:var(--red);font-weight:600}
.offscreen{position:absolute;left:-9999px;width:1px;height:1px}
@media (max-width:700px){.blk,.win{grid-template-columns:1fr}.pick{justify-content:flex-start}.part{grid-template-columns:1fr}}
@media print{nav,.btn,button,.pick,.noprint{display:none!important}header{background:none}body{background:#fff;color:#000}
 .box,table,.card{border-color:#999}}
"""

JS = """
const TOKEN=document.querySelector('meta[name=synla-token]').content;
async function post(url, body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Synla-Token':TOKEN},body:JSON.stringify(body)});
 if(!r.ok){alert(await r.text());return false} return true}
const $=(el,q)=>el.querySelector(q);
document.addEventListener('click', async e=>{
 const on=q=>e.target.closest(q);
 let b;
 if(b=on('[data-save]')){const row=b.closest('.ans');
  if(await post('/api/answer',{date:row.dataset.date,ids:JSON.parse(row.dataset.ids),how:row.dataset.how,
   project:$(row,'.p').value,kind:$(row,'.k').value})) location.reload(); return}
 if(b=on('[data-approve]')){if(await post('/api/approve',{date:b.dataset.approve})) location.reload(); return}
 if(b=on('[data-newproject]')){const name=prompt('Project name'); if(name && await post('/api/project',{name})) location.reload(); return}
 if(b=on('[data-pause]')){e.preventDefault(); if(await post('/api/pause',{minutes:Number(b.dataset.pause)})) location.reload(); return}
 if(b=on('[data-delete]')){if(confirm(b.dataset.confirm) && await post('/api/delete',{what:b.dataset.delete})) location.reload(); return}
 if(b=on('[data-addtime]')){const f=b.closest('.addtime');
  if(await post('/api/time',{date:f.dataset.date,start:$(f,'.from').value,end:$(f,'.to').value,project:$(f,'.p').value,kind:$(f,'.k').value})) location.reload(); return}
 if(b=on('[data-removetime]')){if(await post('/api/time/remove',{start:b.dataset.removetime})) location.reload(); return}
 if(b=on('[data-seal]')){if(await post('/api/seal',{week:b.dataset.seal})) location.reload(); return}
 if(b=on('[data-copy]')){await navigator.clipboard.writeText(document.getElementById(b.dataset.copy).value); b.textContent='Copied'; return}
 if(b=on('[data-saveemail]')){if(await post('/api/settings',{recordEmail:document.getElementById('email').value})) location.reload(); return}
 if(b=on('[data-print]')){window.print(); return}
});
"""

CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
       "form-action 'none'; frame-ancestors 'none'; base-uri 'none'")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def page(title, body, token):
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<meta name='synla-token' content='{escape(token)}'>"
            f"<title>{escape(title)} · Synla</title><style>{CSS}</style></head><body>{body}<script>{JS}</script></body></html>")


def options(values, current, labels=None):
    return "".join(f"<option value='{escape(v)}'{' selected' if v == current else ''}>{escape((labels or {}).get(v, v))}</option>"
                   for v in values)


def selects(app, project="", kind=""):
    p = project if project in app.projects else ""
    k = kind if kind in KINDS else ""
    return (f"<select class='p' aria-label='Project'><option value=''>Project…</option>{options(app.projects, p)}</select>"
            f"<select class='k' aria-label='Kind of work'><option value=''>Kind…</option>{options(list(KINDS), k, KINDS)}</select>")


def picker(app, project, kind, button, primary=False):
    return (f"<div class='pick'>{selects(app, project, kind)}"
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
    return f"<span class='chip'>Reading from: <b>{escape(app.source_name)}</b></span>"


def header(app, title, sub, links, here=""):
    extra = "".join(f"<a class='btn' href='{u}'>{t}</a>" for u, t in (("/record", "Record book"), ("/data", "Your data"))
                    if u != here)
    return (f"<header><div class='wrap'><h1>{title}<small>{sub}</small></h1><nav>{links}"
            f"{extra}{status_chip(app)}</nav></div></header>")


def level_badge(level: str) -> str:
    return f"<span class='lvl {level}'>{level.capitalize()}</span>"


# ---- a day ---------------------------------------------------------------------------------------------------------
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
        when = f"<div class='when'><b>{b.start:%H:%M}</b>-{b.end:%H:%M}<br>{b.minutes:.0f} min</div>"
        if b.main.app == MANUAL_APP:
            rows.append(f"<div class='blk added'>{when}<div><div class='title'>{escape(b.main.title)}</div>"
                        f"<div class='why'>Added by you · {escape(g.project)} · {escape(KINDS.get(g.kind, g.kind))}</div></div>"
                        f"<div class='pick'><button class='btn' data-removetime='{escape(b.segments[0].id)}'>Remove</button></div></div>")
            continue
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
            f"{when}<div><div class='title'>{escape(b.main.title or b.main.app)}</div>"
            f"<div class='why'>{escape(b.main.app)}{' · in a call' if any(s.call for s in b.segments) else ''}"
            f"{f' · +{len(by_window) - 1} other windows' if len(by_window) > 1 else ''} · {why}</div></div>"
            f"{picker(app, g.project, g.kind, conf, primary=(cls == 'ask'))}{split}</div>")

    monday = d - timedelta(days=d.weekday())
    empty = "<p class='note'>Nothing recorded this day.</p>"
    if not bl and app.record.latest(monday):
        empty = (f"<p class='note'>The details for this day have been deleted. "
                 f"<a href='/week?date={monday}'>The record book has this week.</a></p>")
    add = (f"<div class='box addtime noprint' data-date='{d}'><b>Add time away from the computer</b> "
           f"<span class='note'>a site visit, a meeting in person, a phone call</span>"
           f"<div class='row'>From <input type='time' class='from' aria-label='From'> to <input type='time' class='to' aria-label='To'>"
           f"{selects(app)}<button class='btn' data-addtime>Add</button></div>"
           f"<p class='note'>Only the time, project and kind are stored, no notes.</p></div>")
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
            + ("".join(rows) or empty) + add
            + "<p class='note'>Orange: Synla is unsure and needs you. Every answer teaches it. Everything stays on this computer.</p></main>")
    return page(f"{d}", body, token)


# ---- a week --------------------------------------------------------------------------------------------------------
def workload_html(assessed: dict) -> str:
    rows = []
    for r in assessed["rows"]:
        lvl = workload.LEVELS[r["level"]]
        y, red = r["limits"]
        word = "from" if r["direction"] == "up" else "below"
        rows.append(f"<tr><td>{escape(r['name'])}<div class='note'>{escape(r['what'])}</div></td>"
                    f"<td class='num'>{workload.show(r['value'], r['format'])}</td>"
                    f"<td><span class='dot {lvl}'></span>{lvl.capitalize()}</td>"
                    f"<td class='note'>yellow {word} {workload.show(y, r['format'])}, red {word} {workload.show(red, r['format'])}</td></tr>")
    return ("<table><tr><th>Indicator</th><th class='num'>This week</th><th>Level</th><th>Limits</th></tr>" + "".join(rows)
            + "</table>")


def trend_html(rows: list) -> str:
    return "<div class='trend'>" + "".join(
        f"<a class='tw' href='/week?date={r['monday']}'>week {r['monday'].isocalendar()[1]}"
        f"<b><span class='dot {r['level']}'></span>{r['hours']:.0f} h</b>{r['unplanned']:.0%} unplanned</a>" for r in rows) + "</div>"


def record_box(app, monday: date, status: dict) -> str:
    seal = status["seal"]
    if status["state"] == "open":
        st = app.statement(monday)
        guesses = (f" {st['unconfirmedHours']:.1f} h are still Synla's guesses; they will be marked as guesses in the record. "
                   f"You can answer them first, or seal now and seal the change later.") if st["unconfirmedHours"] else ""
        return (f"<div class='box'><b>Record book:</b> this week is not sealed yet.{escape(guesses)}"
                f"<div class='row'><button class='btn primary' data-seal='{monday}'>Seal this week</button></div>"
                f"<p class='note'>Weeks you don't seal are sealed automatically {7} days after they end, as they stand.</p></div>")
    subject, text = record.email_text(seal)
    to = app.config.get("recordEmail", "")
    sealed_by = "automatically" if seal["data"].get("sealedBy") == "auto" else "by you"
    changed = ""
    if status["state"] == "changed":
        changed = (f"<p class='bad-msg'>Changed since it was sealed.</p><div class='row'>"
                   f"<button class='btn primary' data-seal='{monday}'>Seal the change</button></div>"
                   f"<p class='note'>The sealed version stays in the record book; the change becomes version {seal['version'] + 1}.</p>")
    return (f"<div class='box'><b>Record book:</b> sealed {_when_iso(seal['sealedAt'])} {sealed_by}, version {seal['version']}."
            f"<div class='mono'>Fingerprint {seal['fingerprint']}</div>{changed}"
            f"<div class='row noprint'><a class='btn' href='{escape(record.mailto(to, seal), quote=True)}'>Send to yourself</a>"
            f"<button class='btn' data-copy='mailtext'>Copy text</button>"
            f"<a class='btn' href='/record/statement.json?seq={seal['seq']}'>Download record</a></div>"
            f"<textarea id='mailtext' class='offscreen' aria-hidden='true'>{escape(text)}</textarea>"
            f"<p class='note'>{'Sends to ' + escape(to) + '.' if to else 'Add your private email on the Record book page.'} "
            f"The email's date shows that this record existed this week.</p></div>")


def recorded_week_page(app, monday: date, token: str, links: str) -> str:
    seal = app.record.latest(monday)
    st = seal["data"]
    a = workload.assess(st.get("workload", {}), app.config)
    day_rows = "".join(f"<tr><td>{date.fromisoformat(x['date']):%a %d.%m}</td><td class='num'>{x['hours']:.2f} h</td>"
                       f"<td class='num'>{x.get('outsideHours', 0):.2f} h</td></tr>" for x in st["days"])
    proj_rows = "".join(f"<tr><td>{escape(p)}</td><td class='num'>{h:.2f} h</td></tr>" for p, h in st["projects"].items())
    body = header(app, "Synla", f"Week of {monday:%d %B %Y}", links) + f"""<main class="wrap">
<div class="box">The details for this week have been deleted. This is the week as recorded in your record book.</div>
{record_box(app, monday, {"state": "sealed", "seal": seal})}
<h2>Workload: {level_badge(a['level'])}</h2>{workload_html(a)}
<h2>Day by day</h2><table><tr><th>Day</th><th class="num">Hours</th><th class="num">Outside working hours</th></tr>{day_rows}</table>
<h2>Projects</h2><table><tr><th>Project</th><th class="num">Hours</th></tr>{proj_rows}</table></main>"""
    return page(f"Week {monday}", body, token)


def week_page(app, d: date, token: str) -> str:
    monday = d - timedelta(days=d.weekday())
    links = (f"<a class='btn' href='/week?date={monday - timedelta(days=7)}'>← Previous week</a>"
             f"<a class='btn' href='/week?date={monday + timedelta(days=7)}'>Next week →</a><a class='btn' href='/day?date={monday}'>Review days</a>"
             f"<a class='btn' href='/export.csv?date={monday}'>Timesheet CSV</a><a class='btn' href='/summary.txt?date={monday}'>Summary as text</a>")
    days = app.week(monday)
    status = app.week_status(monday)
    if not status["live"] and status["seal"]:
        return recorded_week_page(app, monday, token, links)
    s = report.summary(days, app.workday)
    assessed = workload.assess(workload.measure(days, s), app.config)
    ts = report.timesheet(days, app.config.get("roundMinutes", 15))
    acc = app.accuracy()
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
    body = header(app, "Synla", f"Week of {monday:%d %B %Y}", links) + f"""<main class="wrap">
<h2>Workload this week: {level_badge(assessed['level'])}</h2>
{workload_html(assessed)}
<p class="note">Leading indicators: the conditions that come before overload. Red when two are red or the score reaches 6,
yellow when one is red or the score reaches 3. Change the limits under "indicators" in config.json.</p>
<h2>The last 8 weeks</h2>{trend_html(app.trend(monday))}
{record_box(app, monday, status) if days else ''}
<div class="cards">
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


# ---- the record book and the year ----------------------------------------------------------------------------------
def _when_iso(s: str) -> str:
    return datetime.fromisoformat(s).astimezone().strftime("%d %b %Y %H:%M")


def _when(t):
    return t.astimezone().strftime("%d %b %Y %H:%M") if t else "nothing yet"


def record_page(app, token: str) -> str:
    v = app.record.verify()
    seals = list(reversed(app.record.seals()))
    to = app.config.get("recordEmail", "")
    if v["ok"]:
        check = (f"<p class='ok-msg'>{'All ' + str(v['seals']) + ' records check' if v['seals'] > 1 else 'The record checks'} out: "
                 f"nothing was changed after sealing.</p>" if v["seals"] else
                 "<p class='note'>No sealed weeks yet. Seal a week from its week summary.</p>")
    else:
        check = "<p class='bad-msg'>Some records do not check out:</p><ul>" + "".join(f"<li>{escape(p)}</li>" for p in v["problems"]) + "</ul>"
    rows = "".join(
        f"<tr><td><a href='/week?date={s['week']}'>{date.fromisoformat(s['week']):%d %b %Y}</a></td><td class='num'>{s['version']}</td>"
        f"<td>{_when_iso(s['sealedAt'])}{' (automatic)' if s['data'].get('sealedBy') == 'auto' else ''}</td>"
        f"<td class='num'>{s['data']['totalHours']:.2f} h</td><td class='num'>{s['data']['unconfirmedHours']:.2f} h</td>"
        f"<td class='mono'>{s['fingerprint'][:16]}</td>"
        f"<td class='noprint'><a href='/record/statement.json?seq={s['seq']}'>Download</a> · "
        f"<a href='{escape(record.mailto(to, s), quote=True)}'>Email</a></td></tr>" for s in seals)
    years = sorted({date.fromisoformat(s["week"]).year for s in seals} | {date.today().year}, reverse=True)
    body = header(app, "Record book", "your sealed weeks", "<a class='btn' href='/'>Back to today</a>", here="/record") + f"""
<main class="wrap">
<div class="box">{check}<div class="row">{''.join(f"<a class='btn' href='/year?y={y}'>Year report {y}</a>" for y in years)}</div></div>
<div class="box noprint"><b>Your private email</b> <span class="note">for sending each week's fingerprint to yourself</span>
<div class="row"><input type="email" id="email" value="{escape(to)}" placeholder="you@example.com" aria-label="Private email">
<button class="btn" data-saveemail>Save</button></div>
<p class="note">Synla never sends anything itself. It opens a ready email in your mail app, and you press send.
Use a private address, not your work email.</p></div>
<h2>Sealed weeks</h2>
<table><tr><th>Week of</th><th class="num">Version</th><th>Sealed</th><th class="num">Hours</th><th class="num">Guesses</th><th>Fingerprint</th><th></th></tr>
{rows or '<tr><td colspan=7>None yet.</td></tr>'}</table>
<h2>How it works</h2>
<div class="box"><ul>
<li>Each week's record holds the times, projects and kinds of work, and who decided each one: you, time you added, or
Synla's guess you never checked. It holds no window names.</li>
<li>Each record gets a fingerprint made from the record and the fingerprint before it. Changing an old record afterwards
breaks the chain, and the check above shows where.</li>
<li>You can still correct a sealed week. The correction becomes a new version; the old one stays.</li>
<li>The chain lives on this computer, so on its own it cannot rule out that someone rebuilt it. The email you send
yourself each week can: its date is kept by your email provider, outside Synla and outside your employer.</li>
<li>Records are kept for {app.config.get('keepRecordYears', 5):g} years. This is your own record. Your employer keeps the
official record of working hours.</li></ul></div></main>"""
    return page("Record book", body, token)


def year_page(app, y: int, token: str) -> str:
    rows = app.year(y)
    weekly = float(app.config.get("weeklyHours", 37.5))
    tot = {k: sum(r[k] for r in rows) for k in ("hours", "over", "outside", "unconfirmed")}
    projects = {}
    for r in rows:
        for p, h in r["projects"].items():
            projects[p] = projects.get(p, 0.0) + h
    body_rows = "".join(
        f"<tr><td>{r['monday'].isocalendar()[1]}</td><td><a href='/week?date={r['monday']}'>{r['monday']:%d %b}</a></td>"
        f"<td class='num'>{r['hours']:.2f}</td><td class='num'>{r['over']:.2f}</td><td class='num'>{r['outside']:.2f}</td>"
        f"<td class='num'>{r['unplanned']:.0%}</td><td><span class='dot {r['level']}'></span>{r['level'].capitalize()}</td>"
        f"<td>{'version ' + str(r['seal']['version']) if r['seal'] else 'not sealed yet'}</td>"
        f"<td class='mono'>{r['seal']['fingerprint'][:12] if r['seal'] else ''}</td></tr>" for r in rows)
    proj_rows = "".join(f"<tr><td>{escape(p)}</td><td class='num'>{h:.2f} h</td></tr>"
                        for p, h in sorted(projects.items(), key=lambda kv: -kv[1]))
    links = (f"<a class='btn' href='/year?y={y - 1}'>← {y - 1}</a><a class='btn' href='/year?y={y + 1}'>{y + 1} →</a>"
             f"<button class='btn' data-print>Print or save as PDF</button><a class='btn' href='/year.csv?y={y}'>CSV</a>")
    body = header(app, "Year report", str(y), links) + f"""<main class="wrap">
<div class="cards">
<div class="card"><div class="n">{tot['hours']:.1f} h</div><div class="l">active time in {len(rows)} week{'s' if len(rows) != 1 else ''}</div></div>
<div class="card"><div class="n">{tot['over']:.1f} h</div><div class="l">above {weekly:g} h in a week</div></div>
<div class="card"><div class="n">{tot['outside']:.1f} h</div><div class="l">outside working hours</div></div>
<div class="card"><div class="n">{sum(r['level'] == 'red' for r in rows)}</div><div class="l">red weeks</div></div></div>
<table><tr><th>Week</th><th>Monday</th><th class="num">Hours</th><th class="num">Above {weekly:g}</th><th class="num">Outside hours</th>
<th class="num">Unplanned</th><th>Workload</th><th>Record</th><th>Fingerprint</th></tr>{body_rows or '<tr><td colspan=9>Nothing this year.</td></tr>'}</table>
<h2>Projects</h2><table><tr><th>Project</th><th class="num">Hours</th></tr>{proj_rows}</table>
<p class="note">Active time on the computer, plus time you added. It is not the same as paid working time. Guesses you never
checked: {tot['unconfirmed']:.1f} h. Made from your record book on {date.today():%d %B %Y}.</p></main>"""
    return page(f"Year {y}", body, token)


def year_csv(app, y: int) -> str:
    f = io.StringIO()
    wr = csv.writer(f)
    wr.writerow(["week", "monday", "hours", "aboveWeeklyHours", "outsideHours", "unplannedShare", "workload", "version",
                 "fingerprint"])
    for r in app.year(y):
        wr.writerow([r["monday"].isocalendar()[1], r["monday"].isoformat(), f"{r['hours']:.2f}", f"{r['over']:.2f}",
                     f"{r['outside']:.2f}", f"{r['unplanned']:.4f}", r["level"], r["seal"]["version"] if r["seal"] else "",
                     r["seal"]["fingerprint"] if r["seal"] else ""])
    return f.getvalue()


def statement_file(app, seq: int) -> tuple[str, str]:
    s = next(x for x in app.record.seals() if x["seq"] == seq)
    doc = {"fingerprint": s["fingerprint"], "previous": s["prev"], "record": s["data"],
           "howToCheck": "SHA-256 of previous + record written as JSON with sorted keys and no spaces equals fingerprint."}
    return f"synla-week-{s['week']}-v{s['version']}.json", json.dumps(doc, ensure_ascii=False, indent=1)


# ---- your data -----------------------------------------------------------------------------------------------------
def data_page(app, token: str) -> str:
    i = app.data_info()
    p = i["privacy"]
    recording = ("paused until " + i["paused_until"].astimezone().strftime("%H:%M")) if i["paused_until"] else \
        ("on" if i["recorder_last"] and datetime.now(timezone.utc) - i["recorder_last"] < timedelta(minutes=2) else "not running")
    aw_note = ("<p class='note'>ActivityWatch keeps its own history, separate from Synla. Synla cannot delete it; "
               "you can, in ActivityWatch.</p>") if i["source"] in ("auto", "activitywatch") else ""
    body = header(app, "Your data", "what Synla keeps, and your controls", "<a class='btn' href='/'>Back to today</a>", here="/data") + f"""
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
<tr><td>Time you added away from the computer (no notes)</td><td class="num">{i['added']:,}</td><td></td><td>{i['keepDays']} days</td></tr>
<tr><td>Answer log, used to measure how often Synla is right</td><td class="num">{i['log_entries']:,}</td><td></td><td>{i['keepDays']} days</td></tr>
<tr><td>Learned clues, stored as one-way codes, not readable names</td><td class="num">{i['clues']:,}</td><td></td><td>until you delete them</td></tr>
<tr><td>Record book: sealed weeks with times, projects and kinds, no window names</td><td class="num">{i['seals']:,}</td><td>{i['seals_first'] or 'nothing yet'}</td><td>{i['keepRecordYears']:g} years</td></tr></table>
<p class="note">After {i['keepWindowDays']} days the window names are gone. Your timesheet and record book stay.</p>
{aw_note}

<h2>Never recorded</h2>
<div class="box"><ul><li>Keystrokes, screenshots, the clipboard, file contents or sound</li>
<li>Web addresses (only the window title)</li>
<li>Private windows: apps {escape(', '.join(p.get('skipApps', [])))}; titles with {escape(', '.join(p.get('skipTitleWords', [])))}.
Change the list in config.json.</li></ul></div>

<h2>Delete</h2>
<div class="box"><div class="row">
<button class="btn" data-delete="windows" data-confirm="Delete all window names now? Your answers, timesheets and record book stay.">Delete all window names</button>
<button class="btn" data-delete="all" data-confirm="Delete window names, answers, added time, the log and learned clues? Your record book stays. This cannot be undone.">Delete everything except the record book</button>
<button class="btn" data-delete="record" data-confirm="Delete the record book with all sealed weeks? Emails you sent yourself are not affected. This cannot be undone.">Delete the record book</button></div>
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


# ---- the server ----------------------------------------------------------------------------------------------------
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
                if u.path == "/record":
                    return self._send(200, record_page(app, token))
                if u.path == "/year":
                    return self._send(200, year_page(app, int(q.get("y", [date.today().year])[0]), token))
                if u.path == "/year.csv":
                    y = int(q.get("y", [date.today().year])[0])
                    return self._send(200, year_csv(app, y), "text/csv; charset=utf-8",
                                      {"Content-Disposition": f"attachment; filename=synla-year-{y}.csv"})
                if u.path == "/record/statement.json":
                    name, text = statement_file(app, int(q["seq"][0]))
                    return self._send(200, text, "application/json; charset=utf-8",
                                      {"Content-Disposition": f"attachment; filename={name}"})
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
                return self._send(500, f"Synla could not show this page: {e}", "text/plain; charset=utf-8")

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
                elif self.path == "/api/time":
                    app.add_time(date.fromisoformat(body["date"]), body.get("start", ""), body.get("end", ""),
                                 body.get("project", ""), body.get("kind", ""))
                elif self.path == "/api/time/remove":
                    app.remove_time(body["start"])
                elif self.path == "/api/seal":
                    app.seal(date.fromisoformat(body["week"]))
                elif self.path == "/api/settings":
                    email = (body.get("recordEmail") or "").strip()
                    if email and not EMAIL.match(email):
                        return self._send(400, "That does not look like an email address.", "text/plain")
                    c = app.store.config()
                    c["recordEmail"] = email
                    app.store.save_config(c)
                    app.reload()
                elif self.path == "/api/delete":
                    what = body.get("what")
                    if what == "windows":
                        recorder.prune(app.store, 0)
                        app.reload()
                    elif what == "all":
                        app.store.delete_everything()
                        app.reload()
                    elif what == "record":
                        app.record.delete_all()
                    else:
                        return self._send(400, "Unknown delete request.", "text/plain")
                else:
                    return self._send(404, "Not found", "text/plain")
                return self._send(200, "ok", "text/plain")
            except ValueError as e:
                return self._send(400, str(e) or "Something in the request was not valid.", "text/plain")
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
