import json
import threading
import urllib.request
from datetime import datetime, timedelta

import pytest

from synla import demo, report
from synla.app import Synla
from synla.blocks import segments
from synla.classify import Classifier
from synla.context import features
from synla.server import ThreadingHTTPServer, make_handler
from synla.store import Store

# What a user would answer for each demo window: (project, kind)
TRUTH = {
    "quality-gate.py": ("Data platform", "project"), "job-risk.py": ("Data platform", "project"),
    "Lakehouse Sales": ("Data platform", "project"), "Pipeline run history": ("Data platform", "project"),
    "Supplier audit checklist": ("Supplier audit", "project"), "Supplier audit report": ("Supplier audit", "project"),
    "Monthly KPI report": ("Monthly report", "project"), "Board pack": ("Monthly report", "project"),
    "Chat | Service lead": ("Internal", "helping"), "Chat | Sales team": ("Internal", "helping"),
    "Chat | New colleague": ("Internal", "helping"), "Where is the delivery report": ("Monthly report", "chasing"),
    "supplier certificates": ("Supplier audit", "chasing"), "Invoice export failed": ("Data platform", "firefighting"),
    "Integration error log": ("Data platform", "firefighting"), "Customer list FINAL": ("Internal", "datafixing"),
    "Product codes cleanup": ("Internal", "datafixing"), "Inbox - Outlook": ("Internal", "admin"),
    "Weekly status meeting": ("Internal", "meeting"), "standup": ("Data platform", "meeting"),
    "Timesheet - Intranet": ("Internal", "admin"),
}


def truth(title):
    return next(v for k, v in TRUTH.items() if k in title)


MONDAY = datetime(2026, 11, 2).astimezone()


@pytest.fixture
def app(tmp_path):
    store = Store(tmp_path)
    demo.write(tmp_path / "w.json", MONDAY)
    store.save_config(dict(demo.DEMO_CONFIG, source=str(tmp_path / "w.json")))
    return Synla(store)


def test_features_pick_out_the_clues():
    f = features("EXCEL.EXE", "Copy of Customer list FINAL (2).xlsx - Excel")
    assert "file:copy of customer list final (2)" in f and "ext:xlsx" in f and "app:excel" in f
    f = features("OUTLOOK.EXE", "RE: Any update on the supplier certificates? - Outlook")
    assert "subject:any update on the supplier certificates?" in f and "is:asking" in f and "is:reply" in f
    assert "folder:data-platform" in features("Code.exe", "gold.py - data-platform - Visual Studio Code")


def test_idle_time_is_cut_out():
    t = MONDAY.replace(hour=9)
    win = [{"timestamp": t.isoformat(), "duration": 3600, "data": {"app": "a", "title": "x"}}]
    afk = [{"timestamp": (t + timedelta(minutes=20)).isoformat(), "duration": 600, "data": {"status": "afk"}}]
    segs = segments(win, afk)
    assert [round(s.minutes) for s in segs] == [20, 30]


def test_learns_from_one_answer():
    c = Classifier({"projects": {}})
    assert c.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel").project == "?"
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project")
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project")
    g = c.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel")
    assert (g.project, g.kind) == ("Budget", "project") and g.confidence >= 0.7
    assert any("budget 2027" in r for r in g.reasons)


def accuracy(app, days):
    right = total = 0.0
    for d in days:
        for b in app.day(d):
            for s in b.segments:
                total += s.minutes
                right += s.minutes * ((b.guess.project, b.guess.kind) == truth(s.title))
    return right / total


def test_one_day_of_answers_makes_the_rest_of_the_week_better(app):
    rest = [(MONDAY + timedelta(days=i)).date() for i in range(1, 5)]
    before = accuracy(app, rest)
    monday = MONDAY.date()
    for b in app.day(monday):                       # answer every block on Monday, as a user would
        app.answer(monday, b.id, *truth(b.main.title))
    after = accuracy(app, rest)
    print(f"\nshare of time guessed right, Tue-Fri: before {before:.0%}, after Monday's answers {after:.0%}")
    assert after > before + 0.1 and after > 0.8


def test_summary_finds_the_unplanned_work(app):
    monday = MONDAY.date()
    for i in range(5):
        d = monday + timedelta(days=i)
        for b in app.day(d):
            app.answer(d, b.id, *truth(b.main.title))
    s = app.summary(monday)
    assert 30 < s["hours"] < 50
    assert 0.05 < s["invisible_share"] < 0.5
    assert s["after_hours"] > 0.5                    # the evening catch-up on Monday and Thursday
    assert {r["kind"] for r in s["top_invisible"]} <= {"helping", "firefighting", "chasing", "datafixing"}
    ts = report.timesheet(app.week(monday))
    assert all(h * 4 == int(h * 4) for day in ts.values() for h in day.values())   # quarter hours
    assert app.accuracy()["answers"] > 0


def test_redact():
    assert report.redact("Mail from ola@firma.no, call +47 915 00 000, order 1234567") == \
        "Mail from [email], call [number], order [number]"


def test_review_page_and_answers_over_http(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    d = MONDAY.date()
    html = urllib.request.urlopen(f"{base}/day?date={d}").read().decode()
    assert "need your answer" in html
    b = app.day(d)[0]
    req = urllib.request.Request(f"{base}/api/answer", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"date": str(d), "id": b.id, "project": "Internal", "kind": "admin"}).encode())
    assert urllib.request.urlopen(req).read() == b"ok"
    assert app.day(d)[0].confirmed
    assert "unplanned work" in urllib.request.urlopen(f"{base}/week?date={d}").read().decode()
    assert urllib.request.urlopen(f"{base}/export.csv?date={d}").read().decode().startswith("date,project,hours")
    httpd.shutdown()
