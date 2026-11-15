import base64
import json
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

import pytest

from synla import demo, notify, protect, record, recorder, report, workload
from synla.__main__ import verify
from synla.app import DELETED, Synla
from synla.model import KINDS, MANUAL_APP
from synla.blocks import segments
from synla.classify import Classifier
from synla.context import features
from synla.server import ThreadingHTTPServer, make_handler
from synla.source import SynlaRecorder
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
    "Weekly status meeting": ("Internal", "project"), "standup": ("Data platform", "project"),
    "Timesheet - Intranet": ("Internal", "admin"),
}


def truth(title):
    return next(v for k, v in TRUTH.items() if k in title)


MONDAY = datetime(2026, 11, 2).astimezone()
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@pytest.fixture
def app(tmp_path):
    store = Store(tmp_path)
    demo.write(tmp_path / "w.json", MONDAY)
    store.save_config(dict(demo.DEMO_CONFIG, source=str(tmp_path / "w.json")))
    return Synla(store)


GAP_TRUTH = ("Supplier audit", "project")        # the made-up week's meeting in a room, Thursday afternoon


def answer_gap(app, d, g, project="Supplier audit", kind="project", meeting=True):
    return app.answer_gap(d, g["code"], f"{g['start']:%H:%M}", f"{g['end']:%H:%M}", project, kind, meeting)


def answer_all(app, d):
    for b in app.day(d):
        if b.main.app != MANUAL_APP:
            app.answer_block(d, b.id, *truth(b.main.title))
    for g in app.gaps(d):
        answer_gap(app, d, g, *GAP_TRUTH)


def ev(t, minutes, **data):
    return {"timestamp": t.isoformat(), "duration": minutes * 60, "data": data}


# ---- clues and segments ------------------------------------------------------------------------------------------
def test_features_pick_out_the_clues():
    f = features("EXCEL.EXE", "Copy of Customer list FINAL (2).xlsx - Excel")
    assert "file:copy of customer list final (2)" in f and "ext:xlsx" in f and "app:excel" in f
    f = features("OUTLOOK.EXE", "RE: Any update on the supplier certificates? - Outlook")
    assert "subject:any update on the supplier certificates?" in f and "is:asking" in f and "is:reply" in f
    assert "folder:data-platform" in features("Code.exe", "gold.py - data-platform - Visual Studio Code")
    assert "is:meeting" in features("EXCEL.EXE", "Notes.xlsx - Excel", call=True)


def test_idle_time_is_cut_out():
    t = MONDAY.replace(hour=9)
    win = [ev(t, 60, app="a", title="x")]
    afk = [ev(t + timedelta(minutes=20), 10, status="afk")]
    assert [round(s.minutes) for s in segments(win, afk)] == [20, 30]


def test_meetings_are_not_cut_out_as_idle():
    t = MONDAY.replace(hour=10)
    afk = [ev(t + timedelta(minutes=10), 30, status="afk")]
    meeting = segments([ev(t, 60, app="ms-teams.exe", title="Q4 planning | Microsoft Teams")], afk)
    chat = segments([ev(t, 60, app="ms-teams.exe", title="Chat | Sales team | Microsoft Teams")], afk)
    assert round(sum(s.minutes for s in meeting)) == 60 and meeting[0].call
    assert round(sum(s.minutes for s in chat)) == 30
    long_away = [ev(t + timedelta(minutes=10), 150, status="afk")]       # over two hours: away, even in a call
    assert round(sum(s.minutes for s in segments([ev(t, 180, app="ms-teams.exe", title="Q4 | Microsoft Teams")],
                                                 long_away))) == 30


# ---- learning ------------------------------------------------------------------------------------------------------
def test_learns_from_one_answer():
    c = Classifier({"projects": {}})
    assert c.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel").project == "?"
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project")
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project")
    g = c.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel")
    assert (g.project, g.kind) == ("Budget", "project") and g.confidence >= 0.7
    assert any("budget 2027" in r for r in g.reasons)
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project", weight=-1)    # taking an answer back
    c.learn("EXCEL.EXE", "Budget 2027.xlsx - Excel", "Budget", "project", weight=-1)
    assert c.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel").project == "?"


def guessed_right(app, days):
    right = total = 0.0
    for d in days:
        for b in app.day(d):
            for s in b.segments:
                if s.app == MANUAL_APP:          # time you added yourself is yours, not a guess
                    continue
                total += s.minutes
                right += s.minutes * ((b.guess.project, b.guess.kind) == truth(s.title))
    return right / total


def test_one_day_of_answers_makes_the_rest_of_the_week_better(app):
    rest = [(MONDAY + timedelta(days=i)).date() for i in range(1, 5)]
    before = guessed_right(app, rest)
    answer_all(app, MONDAY.date())
    after = guessed_right(app, rest)
    print(f"\nshare of time guessed right, Tue-Fri: before {before:.0%}, after Monday's answers {after:.0%}")
    assert after > before + 0.1 and after > 0.8


def test_a_glance_is_not_learned(tmp_path):
    t = MONDAY.replace(hour=9)
    data = {"window": [ev(t, 20, app="EXCEL.EXE", title="Budget 2027.xlsx - Excel"),
                       ev(t + timedelta(minutes=20), 0.5, app="ms-teams.exe", title="Chat | Sales team | Microsoft Teams"),
                       ev(t + timedelta(minutes=20.5), 20, app="EXCEL.EXE", title="Budget 2027.xlsx - Excel")], "afk": []}
    (tmp_path / "g.json").write_text(json.dumps(data))
    store = Store(tmp_path)
    store.save_config({"projects": {"Budget": {"keywords": []}}, "source": str(tmp_path / "g.json")})
    a = Synla(store)
    blocks = a.day(MONDAY.date())
    assert len(blocks) == 1 and len(blocks[0].segments) == 3          # the glance joined the Excel block
    a.answer_block(MONDAY.date(), blocks[0].id, "Budget", "project")
    assert a.classifier.guess("ms-teams.exe", "Chat | Sales team | Microsoft Teams").project != "Budget"
    assert a.classifier.guess("EXCEL.EXE", "Budget 2027.xlsx - Excel").project == "Budget"


def test_answer_by_window_covers_the_whole_day(app):
    d = MONDAY.date()
    w = next(w for w in app.unsorted_windows(app.day(d)) if "Inbox" in w["title"])
    n = app.answer(d, w["ids"], "Internal", "admin", "window")
    assert n == len(w["ids"]) and n > 1
    assert all(b.confirmed for b in app.day(d) if b.main.title == "Inbox - Outlook")


# ---- quality measure -----------------------------------------------------------------------------------------------
def test_bulk_approvals_do_not_count_as_right_guesses(app):
    d = (MONDAY + timedelta(days=1)).date()
    answer_all(app, MONDAY.date())
    mine = app.accuracy()["answers"]
    assert app.approve_confident(d) > 0
    acc = app.accuracy()
    assert acc["answers"] == mine and acc["bulk"] > 0 and acc["bulk_changed"] == 0
    b = next(b for b in app.day(d) if b.confirmed)
    app.answer_block(d, b.id, "Internal", "firefighting")              # you change a bulk approval later
    assert app.accuracy()["bulk_changed"] == 1


def test_summary_finds_the_unplanned_work(app):
    for i in range(5):
        answer_all(app, (MONDAY + timedelta(days=i)).date())
    s = app.summary(MONDAY.date())
    assert 30 < s["hours"] < 50
    assert 0.05 < s["invisible_share"] < 0.5
    assert s["after_hours"] > 0.5                    # the evening catch-up on Monday and Thursday
    assert {r["kind"] for r in s["top_invisible"]} <= {"helping", "firefighting", "chasing", "datafixing"}
    ts = report.timesheet(app.week(MONDAY.date()))
    assert all(h * 4 == int(h * 4) for day in ts.values() for h in day.values())   # quarter hours


# ---- privacy -------------------------------------------------------------------------------------------------------
def test_redact():
    assert report.redact("Mail from ola@firma.no, call +47 915 00 000, order 1234567") == \
        "Mail from [email], call [number], order [number]"


def test_learned_clues_are_not_readable(app):
    answer_all(app, MONDAY.date())
    text = (app.store.dir / "memory.json").read_text(encoding="utf-8").lower()
    # only codes and your own project names; nothing from the window titles
    assert not any(w in text for w in ("certificates", "invoice", "customer list", "quality-gate", "delivery report"))
    assert app.data_info()["clues"] > 0


def test_old_answers_and_log_are_deleted(app):
    answer_all(app, MONDAY.date())
    assert app.store.decisions() and app.store.corrections()
    app.store.forget(keep_days=-1)                   # everything is older than "tomorrow"
    assert not app.store.decisions() and not app.store.corrections()


def test_delete_everything(app):
    answer_all(app, MONDAY.date())
    app.store.delete_everything()
    assert not app.store.decisions() and not app.store.memory() and not app.store.corrections()


# ---- the built-in recorder, driven by a fake Windows -----------------------------------------------------------------
class FakeWindows:
    """Plays a script of (app, title, idle seconds, microphone in use), one line per sample."""

    def __init__(self, script):
        self.script, self.i = script, 0

    def now(self):
        return self.script[min(self.i, len(self.script) - 1)]

    def foreground(self):
        app, title, _, _ = self.now()
        return None if app is None else (app, title)

    def idle_seconds(self):
        return self.now()[2]

    def mic_in_use(self):
        return self.now()[3]


def play(store, script, start):
    probe, t = FakeWindows(script), [start.timestamp()]

    def sleep(_):
        probe.i += 1
        t[0] += recorder.POLL
    recorder.run(store, probe, clock=lambda: t[0], sleep=sleep, polls=len(script), seal_weeks=False, remind=False)


EXCEL = ("EXCEL.EXE", "Budget 2027.xlsx - Excel", 0, False)


def test_recorder_keeps_calls_skips_private_windows_and_away_time(tmp_path):
    store = Store(tmp_path)
    store.save_config({"source": "synla"})
    script = [EXCEL] * 6 + \
        [("ms-teams.exe", "Q4 planning | Microsoft Teams", 400, True)] * 3 + \
        [("EXCEL.EXE", "Budget 2027.xlsx - Excel", 400, False)] * 2 + \
        [("keepass.exe", "Passwords - KeePass", 0, False), ("msedge.exe", "Bank [InPrivate] - Microsoft Edge", 0, False)] + \
        [EXCEL] * 2 + [(None, None, 0, False)]
    start = MONDAY.replace(hour=9)
    play(store, script, start)
    db = recorder.connect(store)
    raw = db.execute("SELECT kind, app, title FROM activity").fetchall()
    db.close()
    assert not any(b"Bank" in bytes(r[2] or b"") or b"KeePass" in bytes(r[2] or b"") for r in raw)
    win, _ = SynlaRecorder(store).events(start, start + timedelta(hours=1))
    kinds = [(e["data"]["app"], e["data"]["call"]) for e in win]
    assert kinds == [("EXCEL.EXE", False), ("ms-teams.exe", True), ("Private", False), ("EXCEL.EXE", False)]
    assert win[2]["data"]["title"] == ""
    segs = segments(win, [])
    assert segs[1].call and round(segs[0].minutes * 60) == 25          # six samples, five seconds apart


def test_recorder_trims_to_the_last_input_and_respects_pause(tmp_path):
    store = Store(tmp_path)
    start = MONDAY.replace(hour=9)
    # five minutes of work, then the idle limit is passed: the stretch ends at the last input, 200 seconds earlier
    play(store, [EXCEL] * 60 + [("EXCEL.EXE", "Budget 2027.xlsx - Excel", 200, False)], start)
    win, _ = SynlaRecorder(store).events(start, start + timedelta(hours=1))
    assert len(win) == 1 and round(win[0]["duration"]) == 100
    store.pause(30)
    play(store, [EXCEL] * 5, start + timedelta(minutes=10))
    win2, _ = SynlaRecorder(store).events(start, start + timedelta(hours=1))
    assert len(win2) == len(win)


def test_window_names_expire_but_the_timesheet_stays(tmp_path):
    store = Store(tmp_path)
    store.save_config({"source": "synla", "projects": {"Budget": {"keywords": ["budget"]}}})
    start = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=1)
    play(store, [EXCEL] * 120, start)                                   # ten minutes of Excel yesterday
    a = Synla(store)
    d = start.date()
    for b in a.day(d):
        a.answer_block(d, b.id, "Budget", "project")
    minutes = sum(b.minutes for b in a.day(d))
    recorder.prune(store, 0)                                              # window names deleted
    a.reload()
    after = a.day(d)
    assert after and all(b.confirmed and b.main.title == DELETED for b in after)
    assert abs(sum(b.minutes for b in after) - minutes) < 0.1


def test_autostart_command_runs_the_recorder_without_a_window():
    cmd = recorder.autostart_command()
    assert "main(['record'])" in cmd and cmd.startswith('"')


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_encryption_round_trip():
    secret = "Budget 2027.xlsx - Excel"
    blob = protect.enc(secret)
    assert secret.encode() not in blob and protect.dec(blob) == secret


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_probe_answers():
    p = recorder.WindowsProbe()
    fg = p.foreground()
    assert fg is None or isinstance(fg[1], str)
    assert p.idle_seconds() >= 0
    assert p.mic_in_use() in (True, False)


# ---- the review page ---------------------------------------------------------------------------------------------
@pytest.fixture
def server(app):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, "secret-token", port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", port
    httpd.shutdown()


def post(url, body, headers):
    return post_json(url, body, headers)[0]


def post_json(url, body, headers):
    req = urllib.request.Request(url, method="POST", headers=headers, data=json.dumps(body).encode())
    try:
        with LOCAL.open(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, None


OK = {"Content-Type": "application/json", "X-Synla-Token": "secret-token"}


def test_today_page_asks_and_answers_over_http(app, server):
    base, _ = server
    d = MONDAY.date()
    html = LOCAL.open(f"{base}/day?date={d}").read().decode()
    assert "Five quick questions" in html and "What was this?" in html and "secret-token" in html
    q = app.questions(d)["items"][0]
    status, reply = post_json(f"{base}/api/answer", {"date": str(d), "ids": q["ids"], "project": "Internal",
                                                     "kind": "firefighting", "how": "window", "asked": True}, OK)
    assert status == 200 and reply["undo"]
    assert "Four quick questions" in LOCAL.open(f"{base}/day?date={d}").read().decode()
    assert post(f"{base}/api/undo", {"token": reply["undo"]}, OK) == 200
    assert "Five quick questions" in LOCAL.open(f"{base}/day?date={d}").read().decode()
    assert post(f"{base}/api/skip", {"date": str(d), "code": q["code"]}, OK) == 200
    assert app.questions(d)["items"][0]["code"] != q["code"]
    status, reply = post_json(f"{base}/api/project", {"name": "  Budget   2027 "}, OK)
    assert reply["name"] == "Budget 2027" and "Budget 2027" in app.projects
    week = LOCAL.open(f"{base}/week?date={d}").read().decode()
    assert "Unplanned work" in week and "Not sealed yet" in week
    assert "Never recorded" in LOCAL.open(f"{base}/data").read().decode()
    assert "Copy table" in LOCAL.open(f"{base}/week/timesheet?date={d}").read().decode()
    assert "Focus time" in LOCAL.open(f"{base}/week/indicators?date={d}").read().decode()
    assert LOCAL.open(f"{base}/export.csv?date={d}").read().decode().startswith("date,project,hours")
    assert LOCAL.open(f"{base}/ping").read() == b"synla"


def test_other_websites_cannot_change_anything(app, server):
    base, port = server
    body = {"date": str(MONDAY.date()), "ids": [], "project": "Internal", "kind": "admin"}
    assert post(f"{base}/api/answer", body, {"Content-Type": "application/json"}) == 403              # no token
    assert post(f"{base}/api/answer", body, {"Content-Type": "text/plain", "X-Synla-Token": "secret-token"}) == 403
    assert post(f"{base}/api/delete", {"what": "all"}, {"Content-Type": "application/json",
                                                         "X-Synla-Token": "wrong"}) == 403
    req = urllib.request.Request(f"{base}/day", headers={"Host": f"evil.example:{port}"})
    with pytest.raises(urllib.error.HTTPError) as e:
        LOCAL.open(req)
    assert e.value.code == 403


# ---- workload ------------------------------------------------------------------------------------------------------
def test_workload_on_the_demo_week(app):
    for i in range(5):
        answer_all(app, (MONDAY + timedelta(days=i)).date())
    w = app.workload(MONDAY.date())
    levels = {r["key"]: r["level"] for r in w["rows"]}
    assert levels["longDays"] == 2              # Monday and Thursday run into the evening
    assert levels["focus"] == 1 and levels["unplanned"] == 0
    assert w["level"] == "yellow"


def test_your_own_limits_win(app):
    c = app.store.config()
    c["indicators"] = {"longDays": [5, 6]}
    app.store.save_config(c)
    app.reload()
    levels = {r["key"]: r["level"] for r in app.workload(MONDAY.date())["rows"]}
    assert levels["longDays"] == 0


# ---- record book ---------------------------------------------------------------------------------------------------
def test_a_sealed_week_checks_out_and_tampering_shows(app):
    for i in range(5):
        answer_all(app, (MONDAY + timedelta(days=i)).date())
    s = app.seal(MONDAY.date())
    assert s["version"] == 1 and app.record.verify()["ok"]
    assert s["data"]["unconfirmedHours"] == 0 and "Window" not in s["statement"]
    db = __import__("sqlite3").connect(app.store.dir / "record.db")
    with db:
        db.execute("UPDATE seals SET statement = replace(statement, ?, ?)",
                   (f'"totalHours":{json.dumps(s["data"]["totalHours"])}', '"totalHours":30.5'))
    db.close()
    v = app.record.verify()
    assert not v["ok"] and "changed after it was sealed" in v["problems"][0]


def test_a_change_after_sealing_becomes_a_new_version(app):
    d = MONDAY.date()
    answer_all(app, d)
    app.seal(d)
    assert app.week_status(d)["state"] == "sealed"
    b = app.day(d)[0]
    app.answer_block(d, b.id, "Internal", "firefighting")
    assert app.week_status(d)["state"] == "changed"
    assert app.seal(d)["version"] == 2
    assert [s["version"] for s in app.record.seals(d)] == [1, 2] and app.record.verify()["ok"]


def test_unchecked_guesses_are_marked_in_the_record(app):
    s = app.seal(MONDAY.date())
    by = {x["by"] for day in s["data"]["days"] for x in day["stretches"]}
    assert "guess" in by and s["data"]["unconfirmedHours"] > 0


def test_weeks_are_sealed_automatically_a_week_after_they_end(app):
    assert app.auto_seal(today=(MONDAY + timedelta(days=12)).date()) == []      # ended only 6 days ago
    assert app.auto_seal(today=(MONDAY + timedelta(days=15)).date()) == [MONDAY.date()]
    assert app.record.latest(MONDAY.date())["data"]["sealedBy"] == "auto"
    assert app.auto_seal(today=(MONDAY + timedelta(days=15)).date()) == []      # only once


def test_an_old_week_comes_from_the_record_book(app, tmp_path):
    for i in range(5):
        answer_all(app, (MONDAY + timedelta(days=i)).date())
    sealed = app.seal(MONDAY.date())["data"]
    app.store.forget(keep_days=-1)                           # answers gone
    (tmp_path / "empty.json").write_text('{"window": [], "afk": []}')
    c = app.store.config()
    c["source"] = str(tmp_path / "empty.json")               # window names gone
    app.store.save_config(c)
    app.reload()
    status = app.week_status(MONDAY.date())
    assert status["state"] == "sealed" and not status["live"]
    row = next(r for r in app.year(2026) if r["monday"] == MONDAY.date())
    assert row["hours"] == sealed["totalHours"] and row["status"] == "sealed"


def test_old_records_are_deleted_and_the_chain_still_checks(tmp_path):
    book = record.RecordBook(Store(tmp_path))
    for week in ("2018-01-01", "2018-01-08", "2026-11-02"):
        book.seal({"record": record.FORMAT, "week": week, "days": [], "totalHours": 1.0})
    book.prune(years=5)
    assert [s["week"] for s in book.seals()] == ["2026-11-02"] and book.verify()["ok"]
    book.seal({"record": record.FORMAT, "week": "2026-11-09", "days": [], "totalHours": 1.0})
    assert book.verify()["ok"]


def test_verify_finds_a_fingerprint_and_checks_a_downloaded_record(app, tmp_path, capsys):
    s = app.seal(MONDAY.date())
    verify(app.store, s["fingerprint"][:12])
    out = capsys.readouterr().out
    assert "checks out" in out and f"week of {MONDAY.date()}" in out
    doc = {"fingerprint": s["fingerprint"], "previous": s["prev"], "record": s["data"]}
    (tmp_path / "rec.json").write_text(json.dumps(doc), encoding="utf-8")
    verify(app.store, str(tmp_path / "rec.json"))
    assert "matches its fingerprint" in capsys.readouterr().out
    doc["record"]["totalHours"] = 99
    (tmp_path / "rec.json").write_text(json.dumps(doc), encoding="utf-8")
    verify(app.store, str(tmp_path / "rec.json"))
    assert "does NOT match" in capsys.readouterr().out


def test_the_email_to_yourself(app):
    s = app.seal(MONDAY.date())
    subject, body = record.email_text(s)
    assert "week of 02 November 2026" in subject and s["fingerprint"] in body and "Total" in body
    assert record.mailto("me@example.com", s).startswith("mailto:me@example.com?subject=")


# ---- time away from the computer -----------------------------------------------------------------------------------
def test_time_away_from_the_computer(app):
    d = MONDAY.date()
    app.add_time(d, "08:05", "08:20", "Internal", "meeting")
    bl = app.day(d)
    added = [b for b in bl if b.main.app == MANUAL_APP]
    assert len(added) == 1 and added[0].confirmed and round(added[0].minutes) == 15
    start, end = added[0].start, added[0].end
    assert not any(s.start < end and start < s.end for b in bl if b.main.app != MANUAL_APP for s in b.segments)
    with pytest.raises(ValueError):
        app.add_time(d, "08:10", "08:30", "Internal", "meeting")
    st = app.statement(d)
    assert any(x["by"] == "added" for day in st["days"] for x in day["stretches"])
    app.remove_time(added[0].segments[0].id)
    assert not any(b.main.app == MANUAL_APP for b in app.day(d))


def test_record_pages_over_http(app, server):
    base, _ = server
    ok = {"Content-Type": "application/json", "X-Synla-Token": "secret-token"}
    assert post(f"{base}/api/time", {"date": str(MONDAY.date()), "start": "06:00", "end": "07:00",
                                      "project": "Internal", "kind": "admin"}, ok) == 200
    assert post(f"{base}/api/time", {"date": str(MONDAY.date()), "start": "07:00", "end": "06:00",
                                      "project": "Internal", "kind": "admin"}, ok) == 400
    assert post(f"{base}/api/seal", {"week": str(MONDAY.date())}, ok) == 200
    assert post(f"{base}/api/settings", {"recordEmail": "not an email"}, ok) == 400
    assert post(f"{base}/api/settings", {"recordEmail": "me@example.com"}, ok) == 200
    week = LOCAL.open(f"{base}/week?date={MONDAY.date()}").read().decode()
    assert "Sealed in your record book" in week and "Fingerprint" in week and "mailto:me@example.com" in week
    assert "The record checks out" in LOCAL.open(f"{base}/record").read().decode()
    assert "Year report" in LOCAL.open(f"{base}/year?y=2026").read().decode()
    seq = app.record.seals()[0]["seq"]
    doc = json.loads(LOCAL.open(f"{base}/record/statement.json?seq={seq}").read().decode())
    assert record.fingerprint(doc["previous"], record.canonical(doc["record"])) == doc["fingerprint"]
    assert LOCAL.open(f"{base}/year.csv?y=2026").read().decode().startswith("week,monday,hours")


def test_outside_hours_count_only_the_part_outside_the_working_day():
    from datetime import time as t
    from synla.model import Segment
    start = MONDAY.replace(hour=6, minute=30)
    seg = Segment(start, start + timedelta(hours=1), "a", "x")
    assert round(report.outside_minutes(seg, t(7, 0), t(17, 0))) == 30
    sunday = (MONDAY - timedelta(days=1)).replace(hour=10)
    assert round(report.outside_minutes(Segment(sunday, sunday + timedelta(hours=1), "a", "x"), t(7, 0), t(17, 0))) == 60


# ---- Synla asks, you tap ---------------------------------------------------------------------------------------------
def answer_questions(app, d):
    n = 0
    while app.questions(d)["items"]:
        it = app.questions(d)["items"][0]
        if it["type"] == "gap":
            answer_gap(app, d, it, *GAP_TRUTH)
        else:
            app.answer(d, it["ids"], *truth(it["title"]), how="window", asked=True)
        n += 1
    return n


def test_synla_asks_about_a_few_big_windows_and_less_every_day(app):
    q = app.questions(MONDAY.date())
    assert len(q["items"]) == 5                                     # a new user: at most five questions
    assert all(it["size"] >= 5 for it in q["items"])
    assert [it["size"] for it in q["items"]] == sorted((it["size"] for it in q["items"]), reverse=True)
    asked = [answer_questions(app, (MONDAY + timedelta(days=i)).date()) for i in range(5)]
    print(f"\nquestions per day, Monday to Friday: {asked}")
    assert asked[0] == 5 and sum(asked[1:]) <= 6 and asked[4] <= 1
    assert guessed_right(app, [(MONDAY + timedelta(days=i)).date() for i in range(5)]) > 0.95


def test_one_answer_is_enough_for_the_same_window(app):
    d = MONDAY.date()
    inbox = next(it for it in app.questions(d)["items"] if it["title"] == "Inbox - Outlook")
    app.answer(d, inbox["ids"], "Internal", "admin", how="window", asked=True)
    tuesday = (MONDAY + timedelta(days=1)).date()
    assert not any(it["title"] == "Inbox - Outlook" for it in app.questions(tuesday)["items"])


def test_a_call_is_a_meeting_but_never_the_kind_of_work(app):
    d = MONDAY.date()
    standup = next(w for w in app.unsorted_windows(app.day(d), 50) if "standup" in w["title"])
    app.answer(d, standup["ids"], "Data platform", "project", how="window")
    g = app.classifier.guess("Code.exe", "quality-gate.py - data-platform - Visual Studio Code")
    assert (g.project, g.kind) == ("Data platform", "project") and g.confidence >= 0.7
    other = app.classifier.guess("ms-teams.exe", "Weekly status meeting | Microsoft Teams", True)
    assert other.kind != "meeting" and other.confidence < 0.7      # another call: Synla asks what it was for
    app.classifier.learn("ms-teams.exe", "Old call | Microsoft Teams", "Internal", "meeting", call=True)
    assert app.classifier.guess("ms-teams.exe", "Old call | Microsoft Teams", True).kind != "meeting"


def test_the_daily_limit_and_asking_for_more(app):
    d = (MONDAY + timedelta(days=3)).date()
    c = app.store.config()
    c["projects"] = {"Internal": {"keywords": []}}                  # no keywords: everything is unsure
    app.store.save_config(c)
    app.reload()
    for _ in range(5):
        it = app.questions(d)["items"][0]
        if it["type"] == "gap":
            answer_gap(app, d, it, "Internal", "meeting")
        else:
            app.answer(d, it["ids"], "Internal", "admin", how="window", asked=True)
    q = app.questions(d)
    assert q["items"] == [] and q["waiting"] > 0
    app.ask_more(d)
    assert len(app.questions(d)["items"]) == q["waiting"]


def test_skip_keeps_the_guess_and_is_stored_as_a_code(app):
    d = MONDAY.date()
    first = app.questions(d)["items"][0]
    app.skip(d, first["code"])
    assert all(it["code"] != first["code"] for it in app.questions(d)["items"])
    assert "Invoice" not in (app.store.dir / "days.json").read_text(encoding="utf-8")
    app.undo(app.last_undo)
    assert app.questions(d)["items"][0]["code"] == first["code"]


def test_undo_takes_back_the_answer_and_what_was_learned(app):
    d = MONDAY.date()
    plain = lambda m: {k: v for k, v in m.items() if k != "version"}
    memory, log = plain(app.store.memory()), len(app.store.corrections())
    it = app.questions(d)["items"][0]
    app.answer(d, it["ids"], "Internal", "admin", how="window", asked=True)
    token = app.last_undo
    assert plain(app.store.memory()) != memory and app.store.day_state(d)["answered"] == 1
    app.undo(token)
    assert plain(app.store.memory()) == memory and len(app.store.corrections()) == log
    assert not app.store.day_state(d).get("answered") and not any(b.confirmed for b in app.day(d))
    with pytest.raises(ValueError):
        app.undo(token)                                             # only once


def test_the_week_in_one_sentence(app):
    for i in range(5):
        answer_all(app, (MONDAY + timedelta(days=i)).date())
    assert workload.sentence(app.workload(MONDAY.date())) == "Two long days and little time for focused work."
    assert workload.sentence({"level": "green", "rows": []}) == "Nothing stands out."


class AtTheComputer:
    def __init__(self, idle=0, mic=False):
        self.idle, self.mic = idle, mic

    def idle_seconds(self):
        return self.idle

    def mic_in_use(self):
        return self.mic


def test_the_end_of_day_reminder(app):
    from datetime import time as t
    assert notify.reminder_time(app.config) == t(16, 15)            # 45 minutes before the workday ends
    four = MONDAY.replace(hour=16, minute=20)
    assert not notify.due(MONDAY.replace(hour=15), app.config, None)
    assert notify.due(four, app.config, None) and not notify.due(four, app.config, str(four.date()))
    assert not notify.due((MONDAY + timedelta(days=5)).replace(hour=17), app.config, None)      # Saturday
    assert notify.reminder_time({"reminder": "off"}) is None
    shown = []
    assert notify.maybe_remind(app.store, AtTheComputer(mic=True), app.config, four, show=lambda *a: shown.append(a)) is None
    assert notify.maybe_remind(app.store, AtTheComputer(idle=600), app.config, four, show=lambda *a: shown.append(a)) is None
    assert notify.maybe_remind(app.store, AtTheComputer(), app.config, four, show=lambda *a: shown.append(a)) == 5
    assert notify.maybe_remind(app.store, AtTheComputer(), app.config, four, show=lambda *a: shown.append(a)) is None
    assert shown == [("5 quick questions about today", "Takes less than a minute. Click to answer.")]
    xml = notify.toast_xml(*shown[0])
    assert 'launch="synla:open"' in xml
    script = base64.b64decode(notify.command(xml)[-1]).decode("utf-16-le")
    assert "CreateToastNotifier('Synla')" in script and "5 quick questions" in script


# ---- numbers as clues ------------------------------------------------------------------------------------------------
def test_item_order_and_change_numbers_are_clues():
    from synla.context import numbers
    assert numbers("Display Material 4500-1203") == ["4500-1203"]
    assert numbers("ECR-2026-0153 Gearbox housing material change") == ["ecr-2026-0153"]
    assert numbers("RE: Delivery date castings PO 778812") == ["778812"]
    assert numbers("RE: Seal kit for pump P-45, when?") == ["p-45"]
    for plain in ("Budget 2027 v7.xlsx", "Board pack Q4.pptx", "1:1 Kari", "Meeting 15.11.2026 10:15", "Report 2026-11-15",
                  "Price 12.50", "Python 3.10.12", "Inbox (3)", "Week 45", "Sheet2", "Inbox - ola123@example.com"):
        assert numbers(plain) == [], plain
    assert "id:5523019" in features("ifs.exe", "Customer Order 5523019 - IFS Cloud")


def test_a_number_links_windows_and_keywords_can_match_it():
    from synla.classify import Classifier
    c = Classifier({"projects": {"Gearbox G2": {"keywords": ["gearbox"]}, "Internal": {"keywords": []}}}, {}, lambda s: s)
    assert c.guess("saplogon.exe", "Display Material 4500-1203").project == "?"
    c.learn("EXCEL.EXE", "BOM 4500-1203 rev C.xlsx - Excel", "Gearbox G2", "project")
    g = c.guess("saplogon.exe", "Display Material 4500-1203")
    assert g.project == "Gearbox G2" and g.reasons[0] == 'number "4500-1203"'
    family = Classifier({"projects": {"Gearbox G2": {"keywords": ["4500-"]}}}, {}, lambda s: s)
    assert family.guess("saplogon.exe", "Display Material 4500-7777").project == "Gearbox G2"


def test_an_opened_email_is_the_same_thread_as_in_the_inbox():
    a = features("OUTLOOK.EXE", "RE: Seal kit for pump P-45, when? - Message (HTML)")
    b = features("OUTLOOK.EXE", "SV: Seal kit for pump P-45, when? - Melding (HTML)")
    assert "subject:seal kit for pump p-45, when?" in a and "subject:seal kit for pump p-45, when?" in b


# ---- time away from the computer -------------------------------------------------------------------------------------
def away_days(tmp_path, days, gap=(9, 10)):
    """A plain store: work 08:00 to 16:00 on the given days, away from gap[0] to gap[1] o'clock."""
    win = []
    for d in days:
        t = MONDAY + timedelta(days=d)
        win.append(ev(t.replace(hour=8), (gap[0] - 8) * 60, app="EXCEL.EXE", title="Budget 2027.xlsx - Excel"))
        win.append(ev(t.replace(hour=gap[1]), (16 - gap[1]) * 60, app="EXCEL.EXE", title="Budget 2027.xlsx - Excel"))
    (tmp_path / "away.json").write_text(json.dumps({"window": win, "afk": []}))
    store = Store(tmp_path / "s")
    store.save_config(dict(demo.DEMO_CONFIG, source=str(tmp_path / "away.json")))
    return Synla(store)


def test_a_meeting_in_a_room_becomes_a_question(app):
    thursday = (MONDAY + timedelta(days=3)).date()
    gap = next(it for it in app.questions(thursday)["items"] if it["type"] == "gap")
    assert 70 <= gap["minutes"] <= 80                       # the supplier meeting, 75 minutes
    assert not app.gaps(MONDAY.date())                      # lunch, and leaving at 16:20 for the evening: not asked
    start = answer_gap(app, thursday, gap, *GAP_TRUTH)
    assert not app.gaps(thursday)
    added = next(b for b in app.day(thursday) if b.main.app == MANUAL_APP)
    assert (added.guess.project, added.guess.kind, added.confirmed) == ("Supplier audit", "project", True)
    assert added.segments[0].call                           # noted as a meeting: the time only, never what was said
    assert any(x["by"] == "added" for day in app.statement(MONDAY.date())["days"] for x in day["stretches"])
    app.undo(app.last_undo)
    assert not any(b.main.app == MANUAL_APP for b in app.day(thursday)) and app.gaps(thursday)
    assert start


def test_a_shorter_answer_does_not_ask_again_about_the_rest(app):
    thursday = (MONDAY + timedelta(days=3)).date()
    gap = app.gaps(thursday)[0]
    end = (gap["start"] + timedelta(minutes=30)).strftime("%H:%M")
    app.answer_gap(thursday, gap["code"], f"{gap['start']:%H:%M}", end, "Supplier audit", "project")
    assert not app.gaps(thursday)


def test_not_work_is_enough_and_keeps_no_reason(tmp_path):
    a = away_days(tmp_path, [0, 1, 2])
    mon, tue, wed = (MONDAY + timedelta(days=i) for i in range(3))
    g = a.gaps(mon.date())[0]
    assert (g["start"].hour, g["minutes"]) == (9, 60)
    a.gap_not_work(mon.date(), g["code"])
    assert not a.gaps(mon.date()) and not a.store.manual()
    kept = a.store.day_state(mon.date())["away"]
    assert kept == [[kept[0][0], kept[0][1], "break"]]      # when, and that it was not work: nothing else
    a.gap_not_work(tue.date(), a.gaps(tue.date())[0]["code"])
    assert not a.gaps(wed.date())                           # twice is a habit: Synla stops asking


def test_paused_time_is_never_asked_about_or_shown(tmp_path):
    a = away_days(tmp_path, [0], gap=(9, 10))
    p0 = (MONDAY + timedelta(hours=9, minutes=15)).astimezone(timezone.utc)
    paused = [[p0.isoformat(), (p0 + timedelta(minutes=30)).isoformat()],
              [(p0 + timedelta(hours=3)).isoformat(), (p0 + timedelta(hours=4)).isoformat()]]
    a.store._write("pause.json", {"until": None, "history": paused})
    a.reload()
    assert not a.gaps(MONDAY.date())                        # the gap overlaps a pause
    segs = [s for b in a.day(MONDAY.date()) for s in b.segments]
    assert not any(s.start < p0 + timedelta(hours=4) and p0 + timedelta(hours=3) < s.end for s in segs)


def test_no_questions_about_time_away_when_turned_off_or_before_this_version(tmp_path, app):
    a = away_days(tmp_path, [0])
    c = a.store.config()
    c["askAboutGaps"] = False
    a.store.save_config(c)
    a.reload()
    assert not a.gaps(MONDAY.date())
    app.config["source"] = "auto"                           # the real recorder: older days are never asked about
    assert app.store.gaps_since() == date.today().isoformat()
    assert not app.gaps((MONDAY + timedelta(days=3)).date())


def test_time_away_over_http(app, server):
    base, _ = server
    thursday = (MONDAY + timedelta(days=3)).date()
    for it in app.questions(thursday)["items"]:                      # bigger windows may come first
        if it["type"] == "gap":
            break
        assert post(f"{base}/api/skip", {"date": str(thursday), "code": it["code"]}, OK) == 200
    html = LOCAL.open(f"{base}/day?date={thursday}").read().decode()
    assert "Away from the computer" in html and "It was a meeting" in html
    gap = app.gaps(thursday)[0]
    status, reply = post_json(f"{base}/api/gap", {"date": str(thursday), "code": gap["code"], "notWork": True}, OK)
    assert status == 200 and reply["undo"] and not app.gaps(thursday)
    assert post(f"{base}/api/undo", {"token": reply["undo"]}, OK) == 200
    body = {"date": str(thursday), "code": gap["code"], "project": "Supplier audit", "kind": "project", "meeting": True,
            "start": f"{gap['start']:%H:%M}", "end": f"{gap['end']:%H:%M}"}
    assert post(f"{base}/api/gap", body, OK) == 200 and not app.gaps(thursday)
    assert [e.get("meeting") for e in app.store.manual()] == [True]
    body = {"date": str(thursday), "start": "18:00", "end": "18:30", "project": "Supplier audit", "kind": "travel"}
    assert post(f"{base}/api/time", body, OK) == 200
    assert [e.get("meeting") for e in app.store.manual()] == [True, None]        # travel home is not a meeting
    assert " · meeting" in LOCAL.open(f"{base}/day?date={thursday}").read().decode()
    assert post(f"{base}/api/settings", {"askAboutGaps": False}, OK) == 200 and app.config["askAboutGaps"] is False


# ---- what a window is, its stretches, and what keeps coming back -----------------------------------------------------
def test_outlook_windows_say_what_they_are():
    from synla.context import outlook_item
    from synla.server import where
    assert outlook_item("OUTLOOK.EXE", "URGENT: Invoice export failed - Message (HTML)") == "email"
    assert outlook_item("OUTLOOK.EXE", "Leveringsdato - Melding (HTML)") == "email"
    assert outlook_item("OUTLOOK.EXE", "Weekly status - Meeting") == "invite"
    assert outlook_item("OUTLOOK.EXE", "Statusmøte - Møte") == "invite"
    assert outlook_item("OUTLOOK.EXE", "Calendar - me@example.com - Outlook") == "calendar"
    assert outlook_item("OUTLOOK.EXE", "Inbox - Outlook") == "inbox"
    assert where("OUTLOOK.EXE", "URGENT: Invoice export failed - Message (HTML)") == "Email in Outlook"
    assert where("EXCEL.EXE", "Budget 2027.xlsx - Excel") == "Excel"
    f = features("OUTLOOK.EXE", "URGENT: Invoice export failed - Message (HTML)")
    assert "subject:urgent: invoice export failed" in f       # a new email has a thread too, not only replies


def test_the_word_meeting_is_a_meeting_only_in_a_meeting_app():
    assert "is:meeting" in features("ms-teams.exe", "Weekly status meeting | Microsoft Teams")
    assert "is:meeting" not in features("OUTLOOK.EXE", "RE: meeting notes - Message (HTML)")
    assert "is:meeting" not in features("msedge.exe", "Pull request review - Microsoft Edge")
    invite = features("OUTLOOK.EXE", "Weekly status - Meeting")
    assert "is:invite" in invite and "is:meeting" not in invite
    assert "is:meeting" in features("chrome.exe", "Anything at all", call=True)   # a real call always counts


def test_the_question_shows_each_stretch(app):
    from synla.server import when_text
    it = next(i for i in app.questions(MONDAY.date())["items"] if "Invoice" in i.get("title", ""))
    assert when_text(it["spans"]) == "33 min at 13:34 and 23 min at 21:00"
    t = MONDAY.replace(hour=8)
    many = [(t + timedelta(minutes=30 * i), t + timedelta(minutes=30 * i + 5)) for i in range(5)]
    assert when_text(many) == "25 min in 5 stretches between 08:00 and 10:05"


def test_what_keeps_coming_back_is_kept_as_codes_only(app, tmp_path):
    for i in range(5):
        answer_questions(app, (MONDAY + timedelta(days=i)).date())
    sunday = (MONDAY + timedelta(days=6)).date()
    back = app.comes_back("URGENT: Invoice export failed", sunday)
    assert back["days"] >= 2 and back["since"] == MONDAY.date()
    assert app.comes_back("RE: URGENT: Invoice export failed 2026-12-01", sunday) == back    # written another way
    kept = (app.store.dir / "repeats.json").read_text(encoding="utf-8")
    assert "Invoice" not in kept and "invoice" not in kept
    (tmp_path / "gone.json").write_text('{"window": [], "afk": []}')                    # window names deleted
    c = app.store.config()
    c["source"] = str(tmp_path / "gone.json")
    app.store.save_config(c)
    app.reload()
    app.day(MONDAY.date())
    assert app.comes_back("URGENT: Invoice export failed", sunday) == back


def test_stretches_and_repeats_on_the_pages(app, server):
    base, _ = server
    page = LOCAL.open(f"{base}/day?date={MONDAY.date()}").read().decode()
    for i in range(5):
        answer_questions(app, (MONDAY + timedelta(days=i)).date())
    week = LOCAL.open(f"{base}/week?date={MONDAY.date()}").read().decode()
    assert "Came back on" in week
    assert "Email in Outlook" in page and "Message (HTML)" not in page


# ---- roles, and the choices you see ----------------------------------------------------------------------------------
def test_each_role_gets_its_own_words_and_choices(app):
    from synla import roles
    expect = {"specialist": ("Project", "Planned work"), "product": ("Product", "Product work"),
              "sales": ("Customer", "Customer work"), "leader": ("Area", "Strategy and planning")}
    for key, (word, planned) in expect.items():
        app.config["role"] = key
        shown, more = app.kind_choices()
        assert roles.word(app.config) == word and roles.say(app.config, "project") == planned
        assert shown == list(roles.ROLES[key]["kinds"]) and len(shown) <= 8
        assert set(shown) | set(more) == set(KINDS) - {"meeting"}           # a meeting is a format, never a kind
    assert "travel" in app.kind_choices()[1] and "people" in roles.ROLES["leader"]["kinds"]
    app.config["role"] = "leader"
    assert "datafixing" not in app.kind_choices()[0] and "datafixing" in app.kind_choices("datafixing")[0]
    app.config["kindNames"] = {"firefighting": "Error handling"}
    assert roles.say(app.config, "firefighting") == "Error handling"


def test_projects_show_the_five_you_use_most_in_a_steady_order(app):
    c = app.store.config()
    c["projects"] = {p: {"keywords": []} for p in ("Alfa", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf")}
    app.store.save_config(c)
    app.reload()
    assert app.project_choices()[0] == ["Alfa", "Bravo", "Charlie", "Delta", "Echo"]        # new: your first five
    d = MONDAY.date()
    for b, p in zip(app.day(d), ["Golf", "Golf", "Foxtrot", "Echo", "Delta", "Charlie"]):
        app.answer_block(d, b.id, p, "project")
    shown, more = app.project_choices()
    assert shown == ["Charlie", "Delta", "Echo", "Foxtrot", "Golf"] and more == ["Alfa", "Bravo"]
    assert app.project_choices("Alfa")[0][-1] == "Alfa"                                     # Synla's guess always shows


def test_kinds_you_never_use_fold_away_after_four_weeks(app):
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    recent = datetime.now(timezone.utc) - timedelta(days=3)
    app.store.save_decisions({old: {"end": old, "project": "Internal", "kind": "admin", "how": "block"},
                              recent.isoformat(): {"end": (recent + timedelta(hours=1)).isoformat(),
                                                   "project": "Internal", "kind": "project", "how": "block"}})
    shown, more = app.kind_choices()
    assert shown == ["project", "helping", "firefighting", "chasing", "datafixing"]   # unplanned kinds always stay
    assert "admin" in more and "meeting" not in set(more) | set(app.kind_choices("meeting")[0])


def test_roles_over_http(app, server):
    base, _ = server
    assert "Analyst or specialist" in LOCAL.open(f"{base}/role").read().decode()
    assert post(f"{base}/api/settings", {"role": "nobody"}, OK) == 400
    assert post(f"{base}/api/settings", {"role": "sales", "kindNames": {"admin": "  CRM   and admin ", "x": "y"}}, OK) == 200
    assert app.config["role"] == "sales" and app.config["kindNames"] == {"admin": "CRM and admin"}
    page = LOCAL.open(f"{base}/day?date={MONDAY.date()}").read().decode()
    assert "<span class='lab'>Customer</span>" in page and "Customer work" in page and "CRM and admin" in page
    assert "Which fits your work best?" not in page


# ---- meetings are noted, urgent work pushes planned work, Dynamics 365 -----------------------------------------------
def test_dynamics_365_tabs_say_what_they_are():
    from synla.context import d365, strip_app
    from synla.server import where
    ce = "Account: Havbruk Vest AS - Dynamics 365 - Microsoft Edge"
    fo = "Sales order: 000123 -- Finance and Operations - Microsoft Edge"
    assert d365(ce) == ("ce", "Account", "Havbruk Vest AS") and where("msedge.exe", ce) == "Account in Dynamics 365"
    assert d365(fo) == ("fo", "Sales order", "000123") and where("msedge.exe", fo) == "Sales order in Finance and Operations"
    assert d365("All customers - Dynamics 365 Finance - Microsoft Edge") == ("fo", "", "All customers")
    assert d365("Opportunity: New pumps | Sales Hub - Microsoft Edge")[:2] == ("ce", "Opportunity")
    assert d365("Help - Customer Service - Microsoft Edge") is None and d365("Budget 2027.xlsx - Excel") is None
    assert strip_app(ce) == "Account: Havbruk Vest AS"
    f = features("msedge.exe", "Case: URGENT pump stopped - Dynamics 365 - Microsoft Edge")
    assert {"rec:case", "record:urgent pump stopped", "is:urgent"} <= set(f)


def test_the_record_type_teaches_the_kind_and_the_record_the_customer(app):
    c = app.classifier
    for name in ("Pump stopped", "Valve leak"):
        c.learn("msedge.exe", f"Case: {name} - Dynamics 365 - Microsoft Edge", "Havbruk Vest", "firefighting")
    g = c.guess("msedge.exe", "Case: Alarm on site - Dynamics 365 - Microsoft Edge")
    assert g.kind == "firefighting"                                 # a case, like the last ones
    c.learn("msedge.exe", "Account: Fjordlaks AS - Dynamics 365 - Microsoft Edge", "Fjordlaks", "project")
    assert c.guess("msedge.exe", "Account: Fjordlaks AS | Sales Hub - Microsoft Edge").project == "Fjordlaks"


def test_a_meeting_is_asked_by_what_it_was_for(app, server):
    base, _ = server
    thursday = (MONDAY + timedelta(days=3)).date()
    call = next(it for it in app.questions(thursday)["items"] if "Invoice export failed |" in it.get("title", ""))
    assert call["call"] and call["guess"].kind == "firefighting"    # an escalation call is urgent work
    for it in app.questions(thursday)["items"]:
        if it is call or it["code"] == call["code"]:
            break
        app.skip(thursday, it["code"])
    html = LOCAL.open(f"{base}/day?date={thursday}").read().decode()
    assert "What was this meeting for?" in html and "Invoice export failed" in html
    assert "meeting" not in [k for k in app.kind_choices()[0] + app.kind_choices()[1]]
    app.answer(thursday, call["ids"], "Data platform", "firefighting", how="window", asked=True)
    other = app.classifier.guess("ms-teams.exe", "Weekly status meeting | Microsoft Teams", True)
    assert other.kind != "firefighting"                             # one escalation call does not make every call one


def test_meetings_count_by_the_clock_and_unplanned_ones_count_as_unplanned(app):
    for i in range(5):
        answer_all(app, (MONDAY + timedelta(days=i)).date())
    s = app.summary(MONDAY.date())
    days = app.week(MONDAY.date())
    calls = sum(x.minutes for bl in days.values() for b in bl for x in b.segments if x.call) / 60
    assert abs(s["meeting_hours"] - calls) < 0.01 and s["meeting_hours"] > 2      # calls and the meeting in a room
    thursday = days[(MONDAY + timedelta(days=3)).date()]
    escalation = next(b for b in thursday if "Invoice export failed |" in b.main.title)
    assert escalation.guess.kind == "firefighting"                  # it counts as unplanned work, not as a meeting
    assert "meeting" not in s["by_kind"]


def test_focus_time_leaves_out_meetings():
    from synla.model import Block, Guess, Segment
    t = MONDAY.replace(hour=9)
    talk = Segment(t, t + timedelta(minutes=90), "ms-teams.exe", "Data platform review | Microsoft Teams", True)
    code = Segment(t + timedelta(hours=2), t + timedelta(hours=3), "Code.exe", "quality-gate.py - Visual Studio Code")
    blocks = [Block("a", [talk], Guess("Data platform", "project", 1.0), True),
              Block("b", [code], Guess("Data platform", "project", 1.0), True)]
    s = report.summary({MONDAY.date(): blocks})
    assert s["focus_blocks"] == 1 and abs(s["focus_hours"] - 1) < 0.01 and abs(s["meeting_hours"] - 1.5) < 0.01


def test_urgent_work_pushes_planned_work(app, server):
    base, _ = server
    for i in range(5):
        answer_all(app, (MONDAY + timedelta(days=i)).date())
    s = app.summary(MONDAY.date())
    assert s["urgent_hours"] == s["by_kind"]["firefighting"] and 0 < s["pushed_hours"] < s["urgent_hours"]
    assert "pushed" in report.text(s)
    html = LOCAL.open(f"{base}/week?date={MONDAY.date()}").read().decode()
    assert "Firefighting pushed" in html and "ran into the evening" in html
    app.config["kindNames"] = {"firefighting": "Error handling"}
    assert "Error handling pushed" in LOCAL.open(f"{base}/week?date={MONDAY.date()}").read().decode()
    live = server_week(app)
    app.seal(MONDAY.date())
    Path(app.config["source"]).write_text(json.dumps({"window": [], "afk": []}))      # the details are gone
    app.store.save_manual([])
    app.store.save_decisions({})
    app.reload()
    sealed = server_week(app)
    assert not sealed["live"] and set(sealed["days"]) == set(live["days"])
    for d, x in live["days"].items():
        assert abs(sealed["days"][d]["urgent"] - x["urgent"]) < 0.05
        assert abs(sealed["days"][d]["pushed"] - x["pushed"]) < 0.25
    assert "Firefighting pushed" in LOCAL.open(f"{base}/week?date={MONDAY.date()}").read().decode()


def server_week(app):
    from synla.server import week_view
    return week_view(app, MONDAY.date())
